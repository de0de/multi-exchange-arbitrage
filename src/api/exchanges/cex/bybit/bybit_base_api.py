"""
Общая часть Bybit V5 для spot и futures.

ПОЧЕМУ ОДИН БАЗОВЫЙ КЛАСС, А НЕ ДВА НЕЗАВИСИМЫХ (как у Binance/Gate.io):
у Bybit V5 спот и фьючерсы — это ОДНИ И ТЕ ЖЕ пути REST, различающиеся
только параметром `category` (`spot` / `linear` / `inverse`). Разносить их
по независимым классам означало бы дублировать разбор ответов целиком.
Hummingbot держит два коннектора (`exchange/bybit`, `derivative/
bybit_perpetual`), но исключительно из-за своих базовых классов —
REST-слой у него в обоих почти идентичен (разведка 2026-08-15).

BULK — ОСНОВА АРХИТЕКТУРЫ, НЕ ОПТИМИЗАЦИЯ. `/v5/market/tickers` без
параметра `symbol` возвращает ВСЕ пары категории одним запросом (проверено
живым вызовом 2026-08-15: spot 556, linear 826). Именно так здесь и
сделано. Поштучный запрос на символ — как в Hummingbot — дал бы 500+
запросов за цикл и упёрся бы в лимит биржи.

ГРАБЛЯ, РАДИ КОТОРОЙ ЗДЕСЬ ОТДЕЛЬНЫЙ `_request_v5`: Bybit отвечает
HTTP 200 даже при ошибке, а настоящий признак — поле `retCode` в теле.
Базовый `_make_request` проверяет только HTTP-статус, поэтому без этой
обёртки ошибка выглядела бы как успех, и в БД молча шли бы пустые списки
(набито в копитрейдинг-проекте, `market_data/bybit.py`).

Rate limit: ~600 запросов / 5 с на IP (общий). При bulk-подходе цикл
тратит единицы запросов, так что до лимита далеко.
"""
from datetime import datetime
from typing import Any, Dict, List, Optional

from src.api.exchanges.cex.base_cex_exchange import BaseExchangeAPI
from src.core.models.order_book_data import OrderBookData, OrderBookLevel


class BybitAPIError(Exception):
    """Bybit вернул retCode != 0 (HTTP при этом 200)."""

    def __init__(self, ret_code: int, ret_msg: str, endpoint: str = ""):
        self.ret_code = ret_code
        self.ret_msg = ret_msg
        super().__init__(f"Bybit retCode={ret_code} на {endpoint}: {ret_msg}")


class BybitBaseAPI(BaseExchangeAPI):
    BASE_URL = "https://api.bybit.com"
    # Переопределяется в подклассах: "spot" | "linear"
    CATEGORY = ""

    _RET_CODE_OK = 0
    # Единственный код, актуальный для публичных вызовов: неизвестный
    # символ/параметр. Остальные (10002 timestamp, 10003-10005 auth)
    # относятся к приватным эндпоинтам, которых здесь нет — market data
    # у Bybit публичный, ключи не нужны вообще.
    _RET_CODE_BAD_PARAM = 10001

    # Инструменты меняются редко (листинги/делистинги), а нужны каждый цикл
    # ради baseCoin/quoteCoin — держим в кеше и обновляем раз в час.
    INSTRUMENTS_TTL_SECONDS = 3600.0

    def __init__(self):
        super().__init__(None, None)  # Публичные данные не требуют ключей
        self._instruments: Dict[str, dict] = {}
        self._instruments_loaded_at = 0.0

    async def _request_v5(
        self, endpoint: str, params: Optional[Dict[str, Any]] = None
    ) -> dict:
        """
        GET к V5 с обязательной проверкой `retCode`.

        Возвращает содержимое `result`. HTTP-ошибки и ретраи остаются на
        базовом `_make_request` (там же ведётся health_monitor).
        """
        payload = await self._make_request('GET', endpoint, params=params or {})
        ret_code = int(payload.get('retCode', -1))
        if ret_code != self._RET_CODE_OK:
            raise BybitAPIError(ret_code, str(payload.get('retMsg', '')), endpoint)
        return payload.get('result', {}) or {}

    async def _fetch_instruments(self) -> Dict[str, dict]:
        """
        Справочник инструментов категории: symbol -> запись биржи.

        Курсорная пагинация поддержана СОЗНАТЕЛЬНО, хотя сейчас не нужна:
        на 2026-08-15 spot отдаёт 556 записей, linear — 821, и при
        `limit=1000` обе категории влезают в одну страницу с пустым
        `nextPageCursor`. Но число пар растёт, а копитрейдинг уже налетал
        на порог >500 при меньшем лимите — дешевле поддержать сразу.
        """
        instruments: Dict[str, dict] = {}
        cursor = ""
        while True:
            params = {'category': self.CATEGORY, 'limit': '1000'}
            if cursor:
                params['cursor'] = cursor
            result = await self._request_v5('/v5/market/instruments-info', params)
            for entry in result.get('list', []):
                if not self._instrument_is_tradable(entry):
                    continue
                instruments[entry['symbol']] = entry
            cursor = result.get('nextPageCursor', '') or ''
            if not cursor:
                break
        return instruments

    def _instrument_is_tradable(self, entry: dict) -> bool:
        """Фильтр инструментов. Переопределяется в подклассах."""
        return entry.get('status') == 'Trading'

    async def _ensure_instruments(self, force: bool = False) -> None:
        now = datetime.now().timestamp()
        stale = (
            not self._instruments
            or now - self._instruments_loaded_at > self.INSTRUMENTS_TTL_SECONDS
        )
        if force or stale:
            self._instruments = await self._fetch_instruments()
            self._instruments_loaded_at = now
            self.logger.debug(
                f"{self.EXCHANGE_NAME}: загружено {len(self._instruments)} инструментов"
            )

    async def _fetch_tickers(self) -> List[dict]:
        """Все тикеры категории ОДНИМ запросом (без параметра `symbol`)."""
        result = await self._request_v5(
            '/v5/market/tickers', {'category': self.CATEGORY}
        )
        return result.get('list', []) or []

    @staticmethod
    def _to_float(value: Any, default: float = 0.0) -> float:
        """Bybit отдаёт все числа строками, пустая строка = нет значения."""
        if value in (None, ""):
            return default
        return float(value)

    async def fetch_order_book(self, symbol: str, limit: int = 20) -> OrderBookData:
        """
        GET /v5/market/orderbook?category=...&symbol=...&limit=...

        ВАЖНО: уровни лежат в ключах `b` (bids) и `a` (asks), а НЕ
        `bids`/`asks`, как у остальных наших бирж. Формат уровня —
        ["цена", "объём"], обе величины строками. `ts` в миллисекундах.
        """
        await self.init_session()
        try:
            result = await self._request_v5('/v5/market/orderbook', {
                'category': self.CATEGORY,
                'symbol': symbol,
                'limit': limit,
            })

            ts_ms = result.get('ts')
            now = float(ts_ms) / 1000.0 if ts_ms else datetime.now().timestamp()
            readable = datetime.fromtimestamp(now).strftime('%Y-%m-%d %H:%M:%S')

            bids = [
                OrderBookLevel(price=float(level[0]), volume=float(level[1]))
                for level in result.get('b', [])
            ]
            asks = [
                OrderBookLevel(price=float(level[0]), volume=float(level[1]))
                for level in result.get('a', [])
            ]

            return OrderBookData(
                exchange=self.EXCHANGE_NAME,
                original_pair=symbol,
                standardized_pair=self._standardized_pair(symbol),
                bids=bids,
                asks=asks,
                timestamp=now,
                readable_time=readable,
            )
        except Exception as e:
            self.logger.error(f"Error fetching order book for {symbol}: {e}")
            return OrderBookData(
                exchange=self.EXCHANGE_NAME,
                original_pair=symbol,
                standardized_pair=self._standardized_pair(symbol),
                bids=[],
                asks=[],
            )

    def _standardized_pair(self, symbol: str) -> str:
        """
        base+quote из справочника инструментов, а НЕ парсинг строки символа.

        Эвристика «отрезать USDT с конца» ломается на монетах вроде
        `1000000BABYDOGEUSDT` и на квартальных контрактах
        `BTCUSDT-04SEP26`. К тому же выводу независимо пришли и Hummingbot,
        и копитрейдинг-проект. Если инструмента нет в кеше — возвращаем
        символ как есть, это честнее выдуманного разбиения.
        """
        entry = self._instruments.get(symbol)
        if not entry:
            return symbol
        return f"{entry.get('baseCoin', '')}{entry.get('quoteCoin', '')}" or symbol
