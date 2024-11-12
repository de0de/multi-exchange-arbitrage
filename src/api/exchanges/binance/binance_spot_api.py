from src.api.base_exchange import BaseExchangeAPI
from src.api.exchanges.binance.binance_constants import BASE_URL, EXCHANGE_NAME
from src.core.models.pair_data import PairData
from typing import List
from datetime import datetime

class BinanceSpotAPI(BaseExchangeAPI):
    BASE_URL = BASE_URL
    EXCHANGE_NAME = EXCHANGE_NAME

    async def fetch_trading_pairs(self) -> List[PairData]:
        await self.init_session()
        exchange_info = await self._make_request('GET', '/api/v3/exchangeInfo')
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