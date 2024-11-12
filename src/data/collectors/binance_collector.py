from src.data.collectors.base_collector import BaseDataCollector
from src.api.exchanges.binance.binance_spot_api import BinanceSpotAPI
from src.database.market_repository import MarketRepository

class BinanceCollector(BaseDataCollector):
    def __init__(self, api: BinanceSpotAPI, market_repo: MarketRepository):
        super().__init__(api)
        self.market_repo = market_repo

    async def collect_data(self):
        pairs = await self.api.fetch_trading_pairs()
        if pairs:
            self.market_repo.save_trading_pairs(pairs)
            self.logger.info(f"Collected {len(pairs)} trading pairs from Binance")