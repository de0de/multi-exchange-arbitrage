from src.api.base_exchange import BaseExchangeAPI
from src.api.exchanges.binance.binance_constants import BASE_URL, EXCHANGE_NAME
from src.core.models.pair_data import PairData
from src.core.models.exchange_fee import ExchangeFee
from typing import List
from datetime import datetime
import os

class BinanceSpotAPI(BaseExchangeAPI):
    BASE_URL = BASE_URL
    EXCHANGE_NAME = EXCHANGE_NAME

    def __init__(self):
        self.api_key = "gqpdIUz8ZW39a9y34a8o9Wz3VJMk1McsEo7lyfbnQ7qJvTEcdyE8qIYW9COzHMUR"
        self.secret_key = "yIRccSqMKVoJoYeviLqeWaCMNNOjmKtAWqhzAnh0LJ4YoA0jDwDMJv8OotzLn71c"
        super().__init__(self.api_key, self.secret_key)

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

    async def fetch_exchange_fees(self, original_pairs: List[str]) -> List[ExchangeFee]:
        await self.init_session()
        response = await self._make_request('GET', '/sapi/v1/asset/tradeFee', auth_required=True)
        fees = []
        for item in response:
            if item['symbol'] in original_pairs:
                exchange_id = self.get_exchange_id(item['symbol'])
                base_currency, quote_currency = self.get_currencies(item['symbol'])
                
                fees.append(ExchangeFee(
                    id=None,  # Если id генерируется автоматически, можно оставить None
                    exchange_id=exchange_id,
                    original_pair=item['symbol'],
                    standardized_pair=item['symbol'],  # Или используйте вашу логику стандартизации
                    base_currency=base_currency,
                    quote_currency=quote_currency,
                    maker_fee=float(item['makerCommission']),
                    taker_fee=float(item['takerCommission']),
                    timestamp=datetime.now().timestamp(),
                    readable_time=datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                ))
        self.logger.info(f"Fetched {len(fees)} exchange fees")
        return fees

    def get_exchange_id(self, symbol: str) -> int:
        # Реализуйте логику для получения exchange_id
        # Например, вы можете использовать словарь или запрос к базе данных
        return 1  # Пример: возвращаем фиксированный id

    def get_currencies(self, symbol: str) -> tuple:
        # Реализуйте логику для получения base_currency и quote_currency
        # Например, вы можете использовать словарь или запрос к базе данных
        return "BTC", "USDT"  # Пример: возвращаем фиксированные валюты