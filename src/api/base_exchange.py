import aiohttp
import logging
import hmac
import hashlib
import time
from typing import Dict, Any, List
from src.core.models.exchange_fee import ExchangeFee

class BaseExchangeAPI:
    BASE_URL = ""
    EXCHANGE_NAME = ""

    def __init__(self, api_key: str, secret_key: str):
        self.api_key = api_key
        self.secret_key = secret_key
        self.session = None
        self.logger = logging.getLogger(__name__)

    async def init_session(self):
        if self.session is None:
            self.session = aiohttp.ClientSession()

    async def close_session(self):
        if self.session:
            await self.session.close()
            self.session = None

    async def _make_request(self, method: str, endpoint: str, params: Dict[str, Any] = None, auth_required: bool = False) -> Dict[str, Any]:
        url = f"{self.BASE_URL}{endpoint}"
        params = params or {}
        headers = {}

        if auth_required:
            timestamp = int(time.time() * 1000)
            params['timestamp'] = timestamp
            query_string = '&'.join([f"{key}={value}" for key, value in params.items()])
            signature = hmac.new(self.secret_key.encode('utf-8'), query_string.encode('utf-8'), hashlib.sha256).hexdigest()
            params['signature'] = signature
            headers['X-MBX-APIKEY'] = self.api_key

        try:
            async with self.session.request(method, url, params=params, headers=headers) as response:
                if response.status == 200:
                    return await response.json()
                else:
                    response_text = await response.text()
                    self.logger.error(f"API request failed: {response.status}, Response: {response_text}")
                    return {}
        except Exception as e:
            self.logger.error(f"Request error: {e}")
            return {}

    async def fetch_exchange_fees(self) -> List[ExchangeFee]:
        raise NotImplementedError("This method should be overridden by subclasses")