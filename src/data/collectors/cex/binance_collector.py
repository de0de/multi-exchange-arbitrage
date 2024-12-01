import logging
from src.data.base_collector import BaseDataCollector
from src.api.exchanges.cex.binance.binance_spot_api import BinanceSpotAPI
from src.database.market_repository import MarketRepository
from src.database.exchanges_repository import ExchangesRepository

class BinanceCollector(BaseDataCollector):
    def __init__(self, binance_api: BinanceSpotAPI, market_repo: MarketRepository, exchanges_repo: ExchangesRepository):
        self.logger = logging.getLogger(__name__)
        self.spot_api = binance_api
        self.market_repo = market_repo
        self.exchanges_repo = exchanges_repo

    async def collect_data(self):
        # Установите комиссии для Binance
        exchange_id = self.exchanges_repo.get_or_create_exchange_id("Binance", maker_fee=0.001, taker_fee=0.001)
        
        pairs = await self.spot_api.fetch_trading_pairs()
        if pairs:
            self.market_repo.save_trading_pairs(pairs)
            self.logger.info(f"Collected {len(pairs)} trading pairs from Binance")