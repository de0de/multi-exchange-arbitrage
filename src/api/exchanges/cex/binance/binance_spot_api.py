from src.api.exchanges.cex.base_cex_exchange import BaseExchangeAPI
from src.core.models.pair_data import PairData
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
                if pair_data['symbol'] in symbols:
                    timestamp = datetime.now().timestamp()
                    readable_time = datetime.fromtimestamp(timestamp).strftime('%Y-%m-%d %H:%M:%S')
                    pairs.append(PairData(
                        exchange=self.EXCHANGE_NAME,
                        original_pair=pair_data['symbol'],
                        standardized_pair=pair_data['symbol'],
                        base_currency=symbols[pair_data['symbol']]['baseAsset'],
                        quote_currency=symbols[pair_data['symbol']]['quoteAsset'],
                        price=float(pair_data['lastPrice']),
                        volume=float(pair_data['volume']),
                        bid=float(book_dict[pair_data['symbol']]['bidPrice']),
                        ask=float(book_dict[pair_data['symbol']]['askPrice']),
                        bid_volume=float(book_dict[pair_data['symbol']]['bidQty']),
                        ask_volume=float(book_dict[pair_data['symbol']]['askQty']),
                        timestamp=timestamp,
                        readable_time=readable_time
                    ))
            self.logger.info(f"Successfully fetched {len(pairs)} trading pairs")
            return pairs
        except Exception as e:
            self.logger.error(f"Error fetching trading pairs: {e}")
            return []