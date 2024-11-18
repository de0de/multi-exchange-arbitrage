from src.api.base_exchange import BaseExchangeAPI
from src.api.exchanges.binance.binance_constants import BASE_URL, EXCHANGE_NAME
from src.core.models.pair_data import PairData
from src.core.models.exchange_fee import ExchangeFee
from src.core.models.network import Network
from typing import List
from datetime import datetime
import os
from dotenv import load_dotenv
from src.core.models.exchanges import Exchange

# Загружаем переменные окружения из .env
load_dotenv()

class BinanceSpotAPI(BaseExchangeAPI):
    BASE_URL = BASE_URL
    EXCHANGE_NAME = EXCHANGE_NAME

    def __init__(self):
        # Получаем ключи из переменных окружения
        self.api_key = os.getenv("BINANCE_API_KEY")
        self.secret_key = os.getenv("BINANCE_API_SECRET")
        super().__init__(self.api_key, self.secret_key)

    async def fetch_trading_pairs(self) -> List[PairData]:
        await self.init_session()
        try:
            exchange_info = await self._make_request('GET', '/api/v3/exchangeInfo')
            
            # Логируем полный ответ для диагностики
            self.logger.debug(f"Exchange info response: {exchange_info}")

            # Проверяем наличие ключа 'symbols'
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

    async def fetch_currency_networks(self) -> List[Network]:
        await self.init_session()
        response = await self._make_request('GET', '/sapi/v1/capital/config/getall', auth_required=True)
        networks = []
        exchange_id = self.get_exchange_id(self.EXCHANGE_NAME)
        for coin in response:
            for network in coin['networkList']:
                timestamp = datetime.now().timestamp()
                readable_time = datetime.fromtimestamp(timestamp).strftime('%Y-%m-%d %H:%M:%S')
                networks.append(Network(
                    currency=coin['coin'],
                    network=network['network'],
                    name=network.get('name', network['network']),
                    withdraw_fee=float(network['withdrawFee']),
                    min_withdraw=float(network['withdrawMin']),
                    deposit_enabled=network['depositEnable'],
                    withdraw_enabled=network['withdrawEnable'],
                    timestamp=timestamp,
                    readable_time=readable_time,
                    exchange_id=exchange_id
                ))
        self.logger.info(f"Fetched network info for {len(networks)} networks")
        return networks

    async def fetch_account_balance(self) -> Exchange:
        await self.init_session()
        account_info = await self._make_request('GET', '/api/v3/account', auth_required=True)
        
        usdt_balance = 0.0
        spot_balance_usdt = 0.0
        total_balance_usdt = 0.0
        futures_balance_usdt = 0.0  # Если нужно, добавьте логику для получения фьючерсного баланса

        # Получаем цены для конвертации в USDT
        prices = await self._make_request('GET', '/api/v3/ticker/price')
        price_dict = {item['symbol']: float(item['price']) for item in prices}

        for balance in account_info['balances']:
            asset = balance['asset']
            free_amount = float(balance['free'])
            
            if asset == 'USDT':
                usdt_balance = free_amount
                spot_balance_usdt += free_amount
            else:
                # Конвертируем другие активы в эквивалент USDT
                symbol = f"{asset}USDT"
                if symbol in price_dict:
                    spot_balance_usdt += free_amount * price_dict[symbol]

        total_balance_usdt = spot_balance_usdt + futures_balance_usdt

        return Exchange(
            id=None,  # Установите соответствующий ID, если нужно
            name=self.EXCHANGE_NAME,
            usdt_balance=usdt_balance,
            total_balance_usdt=total_balance_usdt,
            spot_balance_usdt=spot_balance_usdt,
            futures_balance_usdt=futures_balance_usdt,
            additional_info=""
        )