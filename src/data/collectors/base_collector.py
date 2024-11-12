import logging

class BaseDataCollector:
    def __init__(self, api):
        self.api = api
        self.logger = logging.getLogger(__name__)

    async def collect_data(self):
        raise NotImplementedError("This method should be overridden by subclasses") 