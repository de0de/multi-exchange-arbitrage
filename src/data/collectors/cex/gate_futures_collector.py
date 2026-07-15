import logging
from src.data.base_collector import BaseDataCollector
from src.api.exchanges.cex.gate.gate_futures_api import GateFuturesAPI
from src.database.market_repository import MarketRepository
from src.database.exchanges_repository import ExchangesRepository

class GateFuturesCollector(BaseDataCollector):
    def __init__(self, gate_futures_api: GateFuturesAPI, market_repo: MarketRepository, exchanges_repo: ExchangesRepository):
        self.logger = logging.getLogger(__name__)
        self.futures_api = gate_futures_api
        self.market_repo = market_repo
        self.exchanges_repo = exchanges_repo

    async def collect_data(self):
        # Комиссии Gate.io Futures по данным API (contracts, BTC_USDT):
        # maker -0.01% (рибейт консервативно не закладываем -> 0), taker 0.075%
        exchange_id = self.exchanges_repo.get_or_create_exchange_id("Gate.io Futures", maker_fee=0.0, taker_fee=0.00075)

        pairs = await self.futures_api.fetch_trading_pairs()
        if pairs:
            self.market_repo.save_trading_pairs(pairs)
            self.logger.debug(f"Collected {len(pairs)} trading pairs from Gate.io Futures")
