import logging
from src.data.base_collector import BaseDataCollector
from src.api.exchanges.cex.mexc.mexc_futures_api import MexcFuturesAPI
from src.database.market_repository import MarketRepository
from src.database.exchanges_repository import ExchangesRepository

class MexcFuturesCollector(BaseDataCollector):
    def __init__(self, mexc_futures_api: MexcFuturesAPI, market_repo: MarketRepository, exchanges_repo: ExchangesRepository):
        self.logger = logging.getLogger(__name__)
        self.futures_api = mexc_futures_api
        self.market_repo = market_repo
        self.exchanges_repo = exchanges_repo

    async def collect_data(self):
        # Комиссии MEXC Futures по данным API (contract/detail, BTC_USDT):
        # maker 0%, taker 0.01%
        exchange_id = self.exchanges_repo.get_or_create_exchange_id("MEXC Futures", maker_fee=0.0, taker_fee=0.0001)

        pairs = await self.futures_api.fetch_trading_pairs()
        if pairs:
            self.market_repo.save_trading_pairs(pairs)
            self.logger.debug(f"Collected {len(pairs)} trading pairs from MEXC Futures")
