import aiohttp
import logging
import hmac
import hashlib
import time
from typing import Dict, Any
from src.utils.retry import async_retry
from src.utils.health_monitor import health_monitor

class BaseExchangeAPI:
    BASE_URL = ""
    EXCHANGE_NAME = ""

    def __init__(self, api_key: str = None, secret_key: str = None):
        self.api_key = api_key
        self.secret_key = secret_key
        self.session = None
        self.logger = logging.getLogger(__name__)
        self.exchange_name = self.EXCHANGE_NAME

    async def init_session(self):
        if self.session is None:
            self.session = aiohttp.ClientSession()
            self.logger.info(f"Инициализирована сессия для {self.exchange_name}")

    async def close_session(self):
        if self.session:
            await self.session.close()
            self.session = None
            self.logger.info(f"Закрыта сессия для {self.exchange_name}")

    @async_retry(
        retries=3, 
        delay=0.5, 
        backoff_factor=2.0, 
        exceptions=(aiohttp.ClientError, TimeoutError, ConnectionError),
        error_message="Ошибка при выполнении API-запроса"
    )
    async def _make_request(self, method: str, endpoint: str, params: Dict[str, Any] = None, auth_required: bool = False) -> Dict[str, Any]:
        url = f"{self.BASE_URL}{endpoint}"
        params = params or {}
        headers = {}
        start_time = time.time()
        success = False
        error_message = None

        if auth_required and self.api_key and self.secret_key:
            timestamp = int(time.time() * 1000)
            params['timestamp'] = timestamp
            query_string = '&'.join([f"{key}={value}" for key, value in params.items()])
            signature = hmac.new(self.secret_key.encode('utf-8'), query_string.encode('utf-8'), hashlib.sha256).hexdigest()
            params['signature'] = signature
            headers['X-MBX-APIKEY'] = self.api_key

        try:
            async with self.session.request(method, url, params=params, headers=headers, timeout=10) as response:
                if response.status == 200:
                    data = await response.json()
                    success = True
                    return data
                else:
                    response_text = await response.text()
                    error_message = f"API запрос не удался: HTTP {response.status}, Ответ: {response_text}"
                    self.logger.error(error_message)
                    raise aiohttp.ClientResponseError(
                        request_info=response.request_info,
                        history=response.history,
                        status=response.status,
                        message=response_text
                    )
        except Exception as e:
            error_message = f"Ошибка запроса: {e.__class__.__name__}: {str(e)}"
            self.logger.error(error_message)
            raise
        finally:
            # Записываем метрики о запросе в мониторинг здоровья
            latency_ms = (time.time() - start_time) * 1000
            health_monitor.record_request(self.exchange_name, success, latency_ms, error_message)

    async def fetch_exchange_fees(self):
        raise NotImplementedError("Этот метод должен быть переопределен в подклассах")