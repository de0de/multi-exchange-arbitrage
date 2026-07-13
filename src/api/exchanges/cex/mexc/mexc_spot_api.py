from src.api.exchanges.cex.base_cex_exchange import BaseExchangeAPI
from src.core.models.pair_data import PairData
from src.core.models.order_book_data import OrderBookData, OrderBookLevel
from typing import List
from datetime import datetime


class MexcSpotAPI(BaseExchangeAPI):
    BASE_URL = "https://api.mexc.com"
    EXCHANGE_NAME = "MEXC"

    def __init__(self):
        super().__init__(None, None)  # Публичные данные не требуют ключей

    async def fetch_trading_pairs(self) -> List[PairData]:
        """
        GET /api/v3/exchangeInfo — справочник пар (status "1" = торгуется)
        GET /api/v3/ticker/24hr — lastPrice, volume + bid/ask с объёмами

        API Binance-совместимый, но 24hr у MEXC уже содержит bidPrice/bidQty/
        askPrice/askQty — отдельный запрос bookTicker не нужен.
        """
        await self.init_session()
        try:
            exchange_info = await self._make_request('GET', '/api/v3/exchangeInfo')
            if 'symbols' not in exchange_info:
                self.logger.error("Key 'symbols' not found in exchange info response")
                return []

            symbols = {
                s['symbol']: s for s in exchange_info['symbols']
                if s.get('status') == '1' and s.get('isSpotTradingAllowed', False)
            }

            price_data = await self._make_request('GET', '/api/v3/ticker/24hr')
            pairs = []
            for ticker in price_data:
                symbol = ticker.get('symbol')
                if symbol not in symbols:
                    continue
                try:
                    timestamp = datetime.now().timestamp()
                    readable_time = datetime.fromtimestamp(timestamp).strftime('%Y-%m-%d %H:%M:%S')
                    pairs.append(PairData(
                        exchange=self.EXCHANGE_NAME,
                        original_pair=symbol,
                        standardized_pair=symbol,
                        base_currency=symbols[symbol]['baseAsset'],
                        quote_currency=symbols[symbol]['quoteAsset'],
                        price=float(ticker['lastPrice']),
                        volume=float(ticker['volume']),
                        bid=float(ticker['bidPrice']),
                        ask=float(ticker['askPrice']),
                        bid_volume=float(ticker['bidQty']),
                        ask_volume=float(ticker['askQty']),
                        timestamp=timestamp,
                        readable_time=readable_time
                    ))
                except (KeyError, TypeError, ValueError) as e:
                    self.logger.warning(f"Пропущена пара {symbol}: {e}")
                    continue
            self.logger.info(f"Successfully fetched {len(pairs)} trading pairs")
            return pairs
        except Exception as e:
            self.logger.error(f"Error fetching trading pairs: {e}")
            return []

    async def fetch_order_book(self, symbol: str, limit: int = 20) -> OrderBookData:
        """
        Возвращает order book depth для указанного символа MEXC Spot.

        GET /api/v3/depth?symbol={symbol}&limit={limit}
        Формат уровней Binance-совместимый: [["price", "qty"], ...]
        Документация: https://mexcdevelop.github.io/apidocs/spot_v3_en/#order-book
        """
        await self.init_session()
        try:
            data = await self._make_request('GET', '/api/v3/depth', params={
                'symbol': symbol,
                'limit': limit
            })

            now = datetime.now().timestamp()
            readable = datetime.fromtimestamp(now).strftime('%Y-%m-%d %H:%M:%S')

            bids = [OrderBookLevel(price=float(p[0]), volume=float(p[1])) for p in data.get('bids', [])]
            asks = [OrderBookLevel(price=float(p[0]), volume=float(p[1])) for p in data.get('asks', [])]

            return OrderBookData(
                exchange=self.EXCHANGE_NAME,
                original_pair=symbol,
                standardized_pair=symbol,
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
                standardized_pair=symbol,
                bids=[],
                asks=[]
            )
