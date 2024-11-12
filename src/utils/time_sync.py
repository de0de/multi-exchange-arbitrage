import aiohttp
import time
import logging
from datetime import datetime
from typing import List
import os
import importlib

class TimeSync:
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.time_offset = None
        self.server_time_urls = self._find_server_time_urls()

    def _find_server_time_urls(self) -> List[str]:
        urls = []
        base_path = os.path.join(os.path.dirname(__file__), '../api/exchanges')
        
        for root, dirs, files in os.walk(base_path):
            for file in files:
                if file.endswith('_constants.py'):
                    module_path = os.path.relpath(os.path.join(root, file), start=os.path.dirname(__file__))
                    module_name = module_path.replace('/', '.').replace('\\', '.').replace('.py', '')
                    
                    try:
                        module = importlib.import_module(module_name)
                        if hasattr(module, 'ENDPOINTS') and 'server_time' in module.ENDPOINTS:
                            base_url = getattr(module, 'SPOT_BASE_URL', None) or getattr(module, 'SAPI_BASE_URL', None)
                            if base_url:
                                urls.append(f"{base_url}{module.ENDPOINTS['server_time']}")
                    except Exception as e:
                        self.logger.error(f"Error importing module {module_name}: {e}")
        
        return urls

    async def sync_time(self) -> bool:
        for url in self.server_time_urls:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(url) as response:
                        if response.status == 200:
                            data = await response.json()
                            server_time = data['serverTime'] / 1000
                            local_time = time.time()
                            self.time_offset = server_time - local_time
                            self.logger.info(f"Time synchronized. Offset: {self.time_offset:.3f} seconds")
                            return True
                self.logger.error(f"Failed to get server time from {url}: {response.status}")
            except Exception as e:
                self.logger.error(f"Time sync error with {url}: {e}")
        
        return False
    
    def get_current_time(self) -> float:
        if self.time_offset is None:
            return time.time()
        return time.time() + self.time_offset
    
    def get_timestamp(self) -> int:
        return int(self.get_current_time() * 1000)