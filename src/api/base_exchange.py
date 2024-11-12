import aiohttp
import logging
from typing import Dict, Any

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
            # Добавьте логику для аутентификации, если необходимо
            pass

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