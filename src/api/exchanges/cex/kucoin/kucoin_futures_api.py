from src.api.exchanges.cex.base_cex_exchange import BaseExchangeAPI
from src.core.models.pair_data import PairData
from src.core.models.funding_rate import FundingRateData
from src.core.models.order_book_data import OrderBookData, OrderBookLevel
from typing import List, Dict
from datetime import datetime


class KuCoinFuturesAPI(BaseExchangeAPI):
    BASE_URL = "https://api-futures.kucoin.com"
    EXCHANGE_NAME = "KuCoin Futures"

    def __init__(self):
        super().__init__(None, None)  # Публичные данные не требуют ключей
        self._contracts_cache: Dict[str, dict] = {}
        # Поля в cache: multiplier, lotSize, fundingFeeRate, fundingInterval, nextFundingRateTime

    async def fetch_trading_pairs(self) -> List[PairData]:
        await self.init_session()
        try:
            # 1. Получаем контракты (multiplier, lotSize, 24h volume)
            contracts_data = await self._make_request('GET', '/api/v1/contracts/active')
            self.logger.debug(f"Contracts response: {contracts_data}")

            if 'data' not in contracts_data:
                self.logger.error("Key 'data' not found in contracts response")
                return []

            # Строим кеш контрактов и список активных символов
            contracts = {}
            for c in contracts_data['data']:
                if c.get('status') == 'Open':
                    symbol = c['symbol']
                    contracts[symbol] = c

            # 2. Получаем allTickers (цены)
            ticker_data = await self._make_request('GET', '/api/v1/allTickers')
            self.logger.debug(f"AllTickers response: {ticker_data}")

            if 'data' not in ticker_data:
                self.logger.error("Key 'data' not found in allTickers response")
                return []

            tickers = {t['symbol']: t for t in ticker_data['data']}

            pairs = []
            for symbol, contract in contracts.items():
                if symbol not in tickers:
                    continue

                ticker = tickers[symbol]
                try:
                    multiplier = float(contract['multiplier'])
                    lot_size = float(contract['lotSize'])

                    # Кешируем все поля контракта, включая funding и mark_price из allTickers
                    self._contracts_cache[symbol] = {
                        'multiplier': multiplier,
                        'lotSize': lot_size,
                        'fundingFeeRate': contract.get('fundingFeeRate'),
                        'fundingInterval': contract.get('fundingInterval'),
                        'nextFundingRateTime': contract.get('nextFundingRateTime'),
                        'mark_price': float(ticker.get('price', 0)),
                    }

                    # Стандартизация: XBT -> BTC, убрать суффикс M
                    standardized = self._standardize_symbol(symbol)

                    # 24h объём в контрактах из contracts/active
                    volume_contracts = float(contract.get('volumeOf24h', 0))
                    # real volume = contracts * multiplier (т.к. multiplier — стоимость одного контракта в quote)
                    volume = volume_contracts * multiplier

                    timestamp = datetime.now().timestamp()
                    readable_time = datetime.fromtimestamp(timestamp).strftime('%Y-%m-%d %H:%M:%S')

                    pairs.append(PairData(
                        exchange=self.EXCHANGE_NAME,
                        original_pair=symbol,
                        standardized_pair=standardized,
                        base_currency=contract.get('baseCurrency', standardized.split('USDT')[0]),
                        quote_currency=contract.get('quoteCurrency', 'USDT'),
                        price=float(ticker.get('price', 0)),
                        volume=volume,
                        bid=float(ticker['bestBidPrice']),
                        ask=float(ticker['bestAskPrice']),
                        bid_volume=float(ticker.get('bestBidSize', 0)),
                        ask_volume=float(ticker.get('bestAskSize', 0)),
                        multiplier=multiplier,
                        lot_size=lot_size,
                        timestamp=timestamp,
                        readable_time=readable_time
                    ))
                except (KeyError, ValueError, TypeError) as e:
                    self.logger.warning(f"Пропущена пара {symbol}: {e}")
                    continue

            self.logger.info(f"Successfully fetched {len(pairs)} trading pairs from KuCoin Futures")
            return pairs

        except Exception as e:
            self.logger.error(f"Error fetching KuCoin Futures trading pairs: {e}")
            return []

    def _standardize_symbol(self, symbol: str) -> str:
        """
        Стандартизация символа KuCoin Futures.
        - XBT -> BTC
        - Убираем суффикс M
        Пример: XBTUSDTM -> BTCUSDT
        """
        s = symbol.replace('XBT', 'BTC')
        if s.endswith('M'):
            s = s[:-1]
        return s

    def get_contract_info(self, symbol: str) -> dict:
        """
        Возвращает кешированную информацию о контракте.
        Если символ не найден — возвращает dict с None-значениями.
        """
        return self._contracts_cache.get(symbol, {
            'multiplier': 1.0,
            'lotSize': None,
            'fundingFeeRate': None,
            'fundingInterval': None,
            'nextFundingRateTime': None,
        })

    async def fetch_funding_rates(self) -> List[FundingRateData]:
        """
        Возвращает funding rate из кеша _contracts_cache (заполняется при fetch_trading_pairs).

        KuCoin возвращает funding rate в составе каждого контракта (fundingFeeRate),
        поэтому отдельного эндпоинта не требуется — данные уже есть.

        Формат времени: nextFundingRateTime в миллисекундах → приводим к секундам.
        """
        now = datetime.now().timestamp()
        readable_time = datetime.fromtimestamp(now).strftime('%Y-%m-%d %H:%M:%S')

        rates = []
        for symbol, info in self._contracts_cache.items():
            funding_fee_rate = info.get('fundingFeeRate')
            if funding_fee_rate is None:
                continue

            funding_interval = info.get('fundingInterval')
            # fundingInterval может быть None, в документации KuCoin: 8 для 8-часовых контрактов,
            # для некоторых контрактов — 4 (4-часовые)
            if funding_interval is None:
                funding_interval = 8.0
            else:
                funding_interval = float(funding_interval)

            next_funding_time_ms = info.get('nextFundingRateTime')
            if next_funding_time_ms is not None:
                next_funding_time = float(next_funding_time_ms) / 1000.0
            else:
                next_funding_time = None

            standardized = self._standardize_symbol(symbol)

            rates.append(FundingRateData(
                exchange=self.EXCHANGE_NAME,
                original_pair=symbol,
                standardized_pair=standardized,
                funding_rate=float(funding_fee_rate),
                funding_interval_hours=funding_interval,
                mark_price=info.get('mark_price'),  # mark_price из allTickers
                next_funding_time=next_funding_time,
                timestamp=now,
                readable_time=readable_time
            ))

        self.logger.info(f"Fetched {len(rates)} funding rates from KuCoin Futures cache")
        return rates

    async def fetch_order_book(self, symbol: str, limit: int = 20) -> OrderBookData:
        """
        Возвращает order book depth для указанного символа KuCoin Futures.

        Эндпоинт: GET /api/v1/level2/depth20?symbol={symbol}
        Документация: https://www.kucoin.com/docs/rest/futures-trading/market-data/get-part-order-book-level2

        Ответ: {"code":"200000", "data": {"bids": [[price, size], ...], "asks": [[price, size], ...]}}
        KuCoin Futures возвращает ровно 20 уровней (limit игнорируется, оставлен для совместимости).
        Символ ожидается в формате KuCoin (например XBTUSDTM).
        """
        await self.init_session()
        try:
            data = await self._make_request('GET', '/api/v1/level2/depth20', params={
                'symbol': symbol
            })

            now = datetime.now().timestamp()
            readable = datetime.fromtimestamp(now).strftime('%Y-%m-%d %H:%M:%S')

            # Ответ KuCoin Futures: {"code":"200000", "data": {"bids": [...], "asks": [...]}}
            inner = data.get('data', {})
            raw_bids = inner.get('bids', [])
            raw_asks = inner.get('asks', [])

            # Формат: [["price", "size"], ...]
            bids = [OrderBookLevel(price=float(p[0]), volume=float(p[1])) for p in raw_bids]
            asks = [OrderBookLevel(price=float(p[0]), volume=float(p[1])) for p in raw_asks]

            return OrderBookData(
                exchange=self.EXCHANGE_NAME,
                original_pair=symbol,
                standardized_pair=self._standardize_symbol(symbol),
                bids=bids,
                asks=asks,
                timestamp=now,
                readable_time=readable
            )
        except Exception as e:
            self.logger.error(f"Error fetching order book for {symbol}: {e}")
            return OrderBookData(
                exchange=self.EXCHANGE_NAME,
                original_pair=symbol,
                standardized_pair=self._standardize_symbol(symbol),
                bids=[],
                asks=[]
            )
