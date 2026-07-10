from src.api.exchanges.cex.base_cex_exchange import BaseExchangeAPI
from src.core.models.pair_data import PairData
from src.core.models.order_book_data import OrderBookData, OrderBookLevel
from typing import List
from datetime import datetime

class BinanceSpotAPI(BaseExchangeAPI):
    BASE_URL = "https://api.binance.com"
    EXCHANGE_NAME = "Binance"

    def __init__(self):
        super().__init__(None, None)  # Публичные данные не требуют ключей

    async def fetch_trading_pairs(self) -> List[PairData]:
        await self.init_session()
        try:
            exchange_info = await self._make_request('GET', '/api/v3/exchangeInfo')
            self.logger.debug(f"Exchange info response: {exchange_info}")

            if 'symbols' not in exchange_info:
                self.logger.error("Key 'symbols' not found in exchange info response")
                return []

            symbols = {s['symbol']: s for s in exchange_info['symbols'] if s['status'] == 'TRADING'}
            book_tickers = await self._make_request('GET', '/api/v3/ticker/bookTicker')
            book_dict = {t['symbol']: t for t in book_tickers}
            price_data = await self._make_request('GET', '/api/v3/ticker/24hr')
            pairs = []
            for pair_data in price_data:
                symbol = pair_data['symbol']
                if symbol not in symbols:
                    continue
                try:
                    book = book_dict[symbol]
                    timestamp = datetime.now().timestamp()
                    readable_time = datetime.fromtimestamp(timestamp).strftime('%Y-%m-%d %H:%M:%S')
                    pairs.append(PairData(
                        exchange=self.EXCHANGE_NAME,
                        original_pair=symbol,
                        standardized_pair=symbol,
                        base_currency=symbols[symbol]['baseAsset'],
                        quote_currency=symbols[symbol]['quoteAsset'],
                        price=float(pair_data['lastPrice']),
                        volume=float(pair_data['volume']),
                        bid=float(book['bidPrice']),
                        ask=float(book['askPrice']),
                        bid_volume=float(book['bidQty']),
                        ask_volume=float(book['askQty']),
                        timestamp=timestamp,
                        readable_time=readable_time
                    ))
                except (KeyError, ValueError) as e:
                    self.logger.warning(f"Пропущена пара {symbol}: {e}")
                    continue
            self.logger.info(f"Successfully fetched {len(pairs)} trading pairs")
            return pairs
        except Exception as e:
            self.logger.error(f"Error fetching trading pairs: {e}")
            return []

    async def fetch_order_book(self, symbol: str, limit: int = 20) -> OrderBookData:
        """
        Возвращает order book depth для указанного символа Binance Spot.

        GET /api/v3/depth?symbol={symbol}&limit={limit}
        Документация: https://binance-docs.github.io/apidocs/spot/en/#order-book
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
            # Возвращаем пустой стакан
            return OrderBookData(
                exchange=self.EXCHANGE_NAME,
                original_pair=symbol,
                standardized_pair=symbol,
                bids=[],
                asks=[]
            )
