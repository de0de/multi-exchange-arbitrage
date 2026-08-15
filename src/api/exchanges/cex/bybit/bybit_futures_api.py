"""Bybit V5 linear (USDT-перпетуалы): тикеры и funding одним запросом."""
from datetime import datetime
from typing import Dict, List

from src.api.exchanges.cex.bybit.bybit_base_api import BybitBaseAPI
from src.core.models.funding_rate import FundingRateData
from src.core.models.pair_data import PairData


class BybitFuturesAPI(BybitBaseAPI):
    EXCHANGE_NAME = "Bybit Futures"
    CATEGORY = "linear"

    def __init__(self):
        super().__init__()
        # Funding приходит прямо в тикерах — отдельный запрос не нужен.
        # Кеш заполняется в fetch_trading_pairs, читается fetch_funding_rates:
        # тот же паттерн, что у Gate.io Futures и KuCoin Futures.
        self._funding_cache: Dict[str, dict] = {}

    def _instrument_is_tradable(self, entry: dict) -> bool:
        """
        Только БЕССРОЧНЫЕ контракты (`LinearPerpetual`).

        КРИТИЧНО, не вкусовщина: в категории `linear` живут ещё и
        квартальные `LinearFutures` (`BTCUSDT-04SEP26`, 40 штук на
        2026-08-15), у которых `baseCoin`/`quoteCoin` РОВНО ТЕ ЖЕ, что у
        бессрочного `BTCUSDT`. Без этого фильтра их `standardized_pair`
        совпал бы с перпетуалом, и монитор спредов сравнивал бы истёкающий
        квартальник с бессрочным контрактом как одну пару — ложные
        «арбитражные возможности» на ровном месте (класс проблемы из
        PLAN.md, раздел 6, про коллизии тикеров). У квартальников к тому же
        `fundingInterval = 0` — funding у них не начисляется.
        """
        return (
            entry.get('status') == 'Trading'
            and entry.get('contractType') == 'LinearPerpetual'
        )

    async def fetch_trading_pairs(self) -> List[PairData]:
        """
        GET /v5/market/instruments-info?category=linear — контракты
        GET /v5/market/tickers?category=linear — котировки И funding сразу

        Размеры у linear-перпетуалов Bybit уже в БАЗОВОЙ МОНЕТЕ
        (`lotSizeFilter.minOrderQty` для BTCUSDT = 0.001 BTC), контрактного
        множителя нет — в отличие от Gate.io Futures, где размеры приходят
        в контрактах и требуют пересчёта через `quanto_multiplier`.
        Поэтому multiplier здесь всегда 1.0.
        """
        await self.init_session()
        try:
            await self._ensure_instruments()
            tickers = await self._fetch_tickers()

            pairs = []
            for ticker in tickers:
                symbol = ticker.get('symbol')
                instrument = self._instruments.get(symbol)
                if instrument is None:
                    # Не перпетуал (квартальник) либо не Trading — см.
                    # _instrument_is_tradable
                    continue
                try:
                    timestamp = datetime.now().timestamp()
                    readable_time = datetime.fromtimestamp(timestamp).strftime('%Y-%m-%d %H:%M:%S')

                    self._funding_cache[symbol] = {
                        'standardized_pair': f"{instrument['baseCoin']}{instrument['quoteCoin']}",
                        'funding_rate': ticker.get('fundingRate'),
                        # fundingInterval в справочнике — в МИНУТАХ
                        'funding_interval_minutes': instrument.get('fundingInterval'),
                        'next_funding_time': ticker.get('nextFundingTime'),
                        'mark_price': self._to_float(ticker.get('markPrice')),
                    }

                    lot = instrument.get('lotSizeFilter', {}) or {}
                    pairs.append(PairData(
                        exchange=self.EXCHANGE_NAME,
                        original_pair=symbol,
                        standardized_pair=f"{instrument['baseCoin']}{instrument['quoteCoin']}",
                        base_currency=instrument['baseCoin'],
                        quote_currency=instrument['quoteCoin'],
                        price=self._to_float(ticker.get('lastPrice')),
                        volume=self._to_float(ticker.get('volume24h')),
                        bid=self._to_float(ticker.get('bid1Price')),
                        ask=self._to_float(ticker.get('ask1Price')),
                        bid_volume=self._to_float(ticker.get('bid1Size')),
                        ask_volume=self._to_float(ticker.get('ask1Size')),
                        multiplier=1.0,
                        lot_size=self._to_float(lot.get('minOrderQty'), 1.0) or 1.0,
                        timestamp=timestamp,
                        readable_time=readable_time,
                    ))
                except (KeyError, ValueError, TypeError) as e:
                    self.logger.warning(f"Пропущена пара {symbol}: {e}")
                    continue

            self.logger.debug(
                f"Successfully fetched {len(pairs)} trading pairs from Bybit Futures"
            )
            return pairs
        except Exception as e:
            self.logger.error(f"Error fetching Bybit Futures trading pairs: {e}")
            return []

    async def fetch_funding_rates(self) -> List[FundingRateData]:
        """
        Funding из кеша, заполненного fetch_trading_pairs.

        У Bybit `fundingRate` и `nextFundingTime` приходят прямо в тикере,
        поэтому отдельного сетевого вызова здесь нет вообще — в отличие от
        Binance Futures, где под funding идёт свой запрос. `nextFundingTime`
        биржа отдаёт в МИЛЛИСЕКУНДАХ, модель хранит секунды.
        """
        now = datetime.now().timestamp()
        readable_time = datetime.fromtimestamp(now).strftime('%Y-%m-%d %H:%M:%S')

        rates = []
        for symbol, info in self._funding_cache.items():
            funding_rate = info.get('funding_rate')
            if funding_rate in (None, ""):
                continue

            interval_minutes = info.get('funding_interval_minutes')
            interval_hours = (
                float(interval_minutes) / 60.0 if interval_minutes else 8.0
            )

            next_funding_ms = info.get('next_funding_time')
            next_funding_time = (
                float(next_funding_ms) / 1000.0 if next_funding_ms else None
            )

            rates.append(FundingRateData(
                exchange=self.EXCHANGE_NAME,
                original_pair=symbol,
                standardized_pair=info['standardized_pair'],
                funding_rate=float(funding_rate),
                funding_interval_hours=interval_hours,
                mark_price=info.get('mark_price'),
                next_funding_time=next_funding_time,
                timestamp=now,
                readable_time=readable_time,
            ))

        self.logger.debug(f"Fetched {len(rates)} funding rates from Bybit Futures cache")
        return rates
