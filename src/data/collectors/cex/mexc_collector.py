import logging
from src.data.base_collector import BaseDataCollector
from src.api.exchanges.cex.mexc.mexc_spot_api import MexcSpotAPI
from src.database.market_repository import MarketRepository
from src.database.exchanges_repository import ExchangesRepository

class MexcCollector(BaseDataCollector):
    def __init__(self, mexc_api: MexcSpotAPI, market_repo: MarketRepository, exchanges_repo: ExchangesRepository):
        self.logger = logging.getLogger(__name__)
        self.spot_api = mexc_api
        self.market_repo = market_repo
        self.exchanges_repo = exchanges_repo

    async def collect_data(self):
        # Комиссии MEXC по данным API (exchangeInfo, BTCUSDT): maker 0%, taker 0.05%.
        # ВНИМАНИЕ: расходится с наблюдением "постоянный 0% taker" из PLAN.md 5.2 —
        # у MEXC комиссия задана per-symbol (makerCommission/takerCommission),
        # уточнение через override — задача 5.3 (льготные комиссии).
        exchange_id = self.exchanges_repo.get_or_create_exchange_id("MEXC", maker_fee=0.0, taker_fee=0.0005)

        pairs = await self.spot_api.fetch_trading_pairs()
        if pairs:
            self.market_repo.save_trading_pairs(pairs)
            self.logger.info(f"Collected {len(pairs)} trading pairs from MEXC")
