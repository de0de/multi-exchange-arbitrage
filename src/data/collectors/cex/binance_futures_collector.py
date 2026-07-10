import logging
from src.data.base_collector import BaseDataCollector
from src.api.exchanges.cex.binance.binance_futures_api import BinanceFuturesAPI
from src.database.market_repository import MarketRepository
from src.database.exchanges_repository import ExchangesRepository

class BinanceFuturesCollector(BaseDataCollector):
    def __init__(self, futures_api: BinanceFuturesAPI, market_repo: MarketRepository, exchanges_repo: ExchangesRepository):
        self.logger = logging.getLogger(__name__)
        self.futures_api = futures_api
        self.market_repo = market_repo
        self.exchanges_repo = exchanges_repo

    async def collect_data(self):
        # Комиссии Binance Futures: maker 0.02%, taker 0.04%
        exchange_id = self.exchanges_repo.get_or_create_exchange_id(
            "Binance Futures", maker_fee=0.0002, taker_fee=0.0004
        )

        pairs = await self.futures_api.fetch_trading_pairs()
        if pairs:
            self.market_repo.save_trading_pairs(pairs)
            self.logger.info(f"Collected {len(pairs)} trading pairs from Binance Futures")