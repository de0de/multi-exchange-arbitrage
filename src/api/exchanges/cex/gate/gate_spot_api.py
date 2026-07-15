from src.api.exchanges.cex.base_cex_exchange import BaseExchangeAPI
from src.core.models.pair_data import PairData
from src.core.models.order_book_data import OrderBookData, OrderBookLevel
from typing import List
from datetime import datetime


class GateSpotAPI(BaseExchangeAPI):
    BASE_URL = "https://api.gateio.ws"
    EXCHANGE_NAME = "Gate.io"

    def __init__(self):
        super().__init__(None, None)  # Публичные данные не требуют ключей

    async def fetch_trading_pairs(self) -> List[PairData]:
        """
        GET /api/v4/spot/currency_pairs — справочник пар (base/quote, trade_status)
        GET /api/v4/spot/tickers — last, lowest_ask, highest_bid, base_volume

        В tickers нет объёмов на лучших bid/ask — как и у KuCoin Spot,
        в bid_volume/ask_volume подставляется суточный объём пары.
        """
        await self.init_session()
        try:
            currency_pairs = await self._make_request('GET', '/api/v4/spot/currency_pairs')
            symbols = {p['id']: p for p in currency_pairs if p.get('trade_status') == 'tradable'}

            tickers = await self._make_request('GET', '/api/v4/spot/tickers')
            pairs = []
            for ticker in tickers:
                symbol = ticker.get('currency_pair')
                if symbol not in symbols:
                    continue
                try:
                    volume = float(ticker['base_volume'])
                    timestamp = datetime.now().timestamp()
                    readable_time = datetime.fromtimestamp(timestamp).strftime('%Y-%m-%d %H:%M:%S')
                    pairs.append(PairData(
                        exchange=self.EXCHANGE_NAME,
                        original_pair=symbol,
                        standardized_pair=symbol.replace('_', ''),
                        base_currency=symbols[symbol]['base'],
                        quote_currency=symbols[symbol]['quote'],
                        price=float(ticker['last']),
                        volume=volume,
                        bid=float(ticker['highest_bid']),
                        ask=float(ticker['lowest_ask']),
                        bid_volume=volume,
                        ask_volume=volume,
                        timestamp=timestamp,
                        readable_time=readable_time
                    ))
                except (KeyError, ValueError) as e:
                    self.logger.warning(f"Пропущена пара {symbol}: {e}")
                    continue
            self.logger.debug(f"Successfully fetched {len(pairs)} trading pairs")
            return pairs
        except Exception as e:
            self.logger.error(f"Error fetching trading pairs: {e}")
            return []

    async def fetch_order_book(self, symbol: str, limit: int = 20) -> OrderBookData:
        """
        Возвращает order book depth для указанного символа Gate.io Spot.

        GET /api/v4/spot/order_book?currency_pair={symbol}&limit={limit}
        Формат уровней: [["price", "amount"], ...]
        Документация: https://www.gate.com/docs/developers/apiv4/#retrieve-order-book
        """
        await self.init_session()
        try:
            data = await self._make_request('GET', '/api/v4/spot/order_book', params={
                'currency_pair': symbol,
                'limit': limit
            })

            now = datetime.now().timestamp()
            readable = datetime.fromtimestamp(now).strftime('%Y-%m-%d %H:%M:%S')

            bids = [OrderBookLevel(price=float(p[0]), volume=float(p[1])) for p in data.get('bids', [])]
            asks = [OrderBookLevel(price=float(p[0]), volume=float(p[1])) for p in data.get('asks', [])]

            return OrderBookData(
                exchange=self.EXCHANGE_NAME,
                original_pair=symbol,
                standardized_pair=symbol.replace('_', ''),
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
                standardized_pair=symbol.replace('_', ''),
                bids=[],
                asks=[]
            )
