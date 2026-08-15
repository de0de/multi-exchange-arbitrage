"""Bybit V5 spot: справочник инструментов + все тикеры одним запросом."""
from datetime import datetime
from typing import List

from src.api.exchanges.cex.bybit.bybit_base_api import BybitBaseAPI
from src.core.models.pair_data import PairData


class BybitSpotAPI(BybitBaseAPI):
    EXCHANGE_NAME = "Bybit"
    CATEGORY = "spot"

    async def fetch_trading_pairs(self) -> List[PairData]:
        """
        GET /v5/market/instruments-info?category=spot — baseCoin/quoteCoin/status
        GET /v5/market/tickers?category=spot — ВСЕ пары одним запросом

        Два запроса на цикл независимо от числа пар (на 2026-08-15 их 556),
        причём справочник берётся из часового кеша — то есть в устоявшемся
        режиме это ОДИН запрос за цикл.

        В отличие от KuCoin/Gate.io Spot, у Bybit в тикере есть настоящие
        размеры на лучших уровнях (`bid1Size`/`ask1Size`), поэтому в
        bid_volume/ask_volume идут они, а не суточный объём-заглушка.
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
                    # Тикер есть, а инструмент не торгуется (или свежий
                    # листинг, ещё не попавший в кеш) — пропускаем: без
                    # baseCoin/quoteCoin пару всё равно не нормализовать
                    continue
                try:
                    timestamp = datetime.now().timestamp()
                    readable_time = datetime.fromtimestamp(timestamp).strftime('%Y-%m-%d %H:%M:%S')

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
                        timestamp=timestamp,
                        readable_time=readable_time,
                    ))
                except (KeyError, ValueError, TypeError) as e:
                    self.logger.warning(f"Пропущена пара {symbol}: {e}")
                    continue

            self.logger.debug(f"Successfully fetched {len(pairs)} trading pairs from Bybit")
            return pairs
        except Exception as e:
            self.logger.error(f"Error fetching Bybit trading pairs: {e}")
            return []
