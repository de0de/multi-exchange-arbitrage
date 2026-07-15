from src.api.exchanges.cex.base_cex_exchange import BaseExchangeAPI
from src.core.models.pair_data import PairData
from src.core.models.order_book_data import OrderBookData, OrderBookLevel
from typing import List
from datetime import datetime
from src.api.exchanges.cex.kucoin.kucoin_constants import BASE_URL, EXCHANGE_NAME, ENDPOINTS


class KuCoinSpotAPI(BaseExchangeAPI):
    BASE_URL = BASE_URL
    EXCHANGE_NAME = EXCHANGE_NAME

    def __init__(self):
        super().__init__(None, None)  # Публичные данные не требуют ключей

    async def fetch_trading_pairs(self) -> List[PairData]:
        await self.init_session()
        try:
            exchange_info = await self._make_request('GET', ENDPOINTS['exchange_info'])
            self.logger.debug(f"Exchange info response: {exchange_info}")

            if 'data' not in exchange_info:
                self.logger.error("Key 'data' not found in exchange info response")
                return []

            symbols = {s['symbol']: s for s in exchange_info['data'] if s['enableTrading']}
            ticker_data = await self._make_request('GET', ENDPOINTS['ticker'])
            pairs = []
            for pair_data in ticker_data['data']['ticker']:
                symbol = pair_data['symbol']
                if symbol not in symbols:
                    continue
                try:
                    timestamp = datetime.now().timestamp()
                    readable_time = datetime.fromtimestamp(timestamp).strftime('%Y-%m-%d %H:%M:%S')
                    pairs.append(PairData(
                        exchange=self.EXCHANGE_NAME,
                        original_pair=symbol,
                        standardized_pair=symbol.replace('-', ''),
                        base_currency=symbols[symbol]['baseCurrency'],
                        quote_currency=symbols[symbol]['quoteCurrency'],
                        price=float(pair_data['last']),
                        volume=float(pair_data['vol']),
                        bid=float(pair_data['buy']),
                        ask=float(pair_data['sell']),
                        bid_volume=float(pair_data['vol']),
                        ask_volume=float(pair_data['vol']),
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
        Возвращает order book depth для указанного символа KuCoin Spot.

        KuCoin имеет готовый эндпоинт для 20 уровней:
        GET /api/v1/market/orderbook/level2_20?symbol={symbol}

        Документация: https://www.kucoin.com/docs/rest/spot-trading/market-data/get-part-order-book-aggregated
        """
        await self.init_session()
        try:
            data = await self._make_request('GET', '/api/v1/market/orderbook/level2_20', params={
                'symbol': symbol
            })

            now = datetime.now().timestamp()
            readable = datetime.fromtimestamp(now).strftime('%Y-%m-%d %H:%M:%S')

            # Ответ KuCoin Spot: {"code":"200000", "data": {"bids": [...], "asks": [...]}}
            inner = data.get('data', {})
            raw_bids = inner.get('bids', [])
            raw_asks = inner.get('asks', [])

            # Формат KuCoin: [["price", "size"], ...]
            bids = [OrderBookLevel(price=float(p[0]), volume=float(p[1])) for p in raw_bids]
            asks = [OrderBookLevel(price=float(p[0]), volume=float(p[1])) for p in raw_asks]

            return OrderBookData(
                exchange=self.EXCHANGE_NAME,
                original_pair=symbol,
                standardized_pair=symbol.replace('-', ''),
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
                standardized_pair=symbol.replace('-', ''),
                bids=[],
                asks=[]
            )