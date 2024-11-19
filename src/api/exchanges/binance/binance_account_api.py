from src.api.base_exchange import BaseExchangeAPI
from src.core.models.exchanges import Exchange
from src.core.models.exchange_fee import ExchangeFee
from src.core.models.network import Network
from datetime import datetime
import os
from dotenv import load_dotenv
from typing import List
import aiohttp
import asyncio
import json

# Загружаем переменные окружения из .env
load_dotenv()

class BinanceAccountAPI(BaseExchangeAPI):
    BASE_URL = "https://api.binance.com"
    EXCHANGE_NAME = "Binance"

    def __init__(self):
        api_key = os.getenv("BINANCE_API_KEY")
        secret_key = os.getenv("BINANCE_API_SECRET")
        if not api_key or not secret_key:
            raise ValueError("API key and secret key must be set")
        super().__init__(api_key, secret_key)

    async def fetch_account_balance(self) -> Exchange:
        await self.init_session()
        
        # Получаем спотовый баланс
        account_info = await self._make_request('GET', '/api/v3/account', auth_required=True)
        
        # Получаем баланс только USDT
        usdt_balance = sum(float(balance['free']) for balance in account_info['balances'] if balance['asset'] == 'USDT')
        
        # Получаем общий баланс всех активов в эквиваленте USDT
        prices = await self._make_request('GET', '/api/v3/ticker/price')
        price_dict = {price['symbol']: float(price['price']) for price in prices}
        
        spot_balance_usdt = 0.0
        for balance in account_info['balances']:
            asset = balance['asset']
            amount = float(balance['free']) + float(balance['locked'])
            if asset == 'USDT':
                spot_balance_usdt += amount
            else:
                pair = f"{asset}USDT"
                if pair in price_dict:
                    spot_balance_usdt += amount * price_dict[pair]

        return Exchange(
            id=None,
            name=self.EXCHANGE_NAME,
            usdt_balance=usdt_balance,
            spot_balance_usdt=spot_balance_usdt,
            additional_info=""
        )

    async def fetch_exchange_fees(self, original_pairs: List[str]) -> List[ExchangeFee]:
        await self.init_session()
        response = await self._make_request('GET', '/sapi/v1/asset/tradeFee', auth_required=True)
        fees = []
        for item in response:
            if item['symbol'] in original_pairs:
                exchange_id = self.get_exchange_id(item['symbol'])
                base_currency, quote_currency = self.get_currencies(item['symbol'])
                
                fees.append(ExchangeFee(
                    id=None,
                    exchange_id=exchange_id,
                    original_pair=item['symbol'],
                    standardized_pair=item['symbol'],
                    base_currency=base_currency,
                    quote_currency=quote_currency,
                    maker_fee=float(item['makerCommission']),
                    taker_fee=float(item['takerCommission']),
                    timestamp=datetime.now().timestamp(),
                    readable_time=datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                ))
        self.logger.info(f"Fetched {len(fees)} exchange fees")
        return fees

    async def create_listen_key(self) -> str:
        response = await self._make_request('POST', '/api/v3/userDataStream', auth_required=True)
        return response.get('listenKey')

    async def keep_alive_listen_key(self, listen_key: str):
        await self._make_request('PUT', '/api/v3/userDataStream', params={'listenKey': listen_key}, auth_required=True)

    async def listen_user_data_stream(self, listen_key: str):
        url = f"wss://stream.binance.com:9443/ws/{listen_key}"
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(url) as ws:
                async for msg in ws:
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        data = json.loads(msg.data)
                        if data['e'] == 'outboundAccountPosition':
                            print("Account Update:", data)
                        elif data['e'] == 'balanceUpdate':
                            print("Balance Update:", data)

    def get_exchange_id(self, symbol: str) -> int:
        return 1  # Пример: возвращаем фиксированный id

    def get_currencies(self, symbol: str) -> tuple:
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