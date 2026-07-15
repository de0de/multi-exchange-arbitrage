import logging
from src.data.base_collector import BaseDataCollector
from src.api.exchanges.cex.gate.gate_spot_api import GateSpotAPI
from src.database.market_repository import MarketRepository
from src.database.exchanges_repository import ExchangesRepository

class GateCollector(BaseDataCollector):
    def __init__(self, gate_api: GateSpotAPI, market_repo: MarketRepository, exchanges_repo: ExchangesRepository):
        self.logger = logging.getLogger(__name__)
        self.spot_api = gate_api
        self.market_repo = market_repo
        self.exchanges_repo = exchanges_repo

    async def collect_data(self):
        # Комиссии Gate.io: VIP0 spot 0.2% maker/taker (поле fee в currency_pairs)
        exchange_id = self.exchanges_repo.get_or_create_exchange_id("Gate.io", maker_fee=0.002, taker_fee=0.002)

        pairs = await self.spot_api.fetch_trading_pairs()
        if pairs:
            self.market_repo.save_trading_pairs(pairs)
            self.logger.debug(f"Collected {len(pairs)} trading pairs from Gate.io")
