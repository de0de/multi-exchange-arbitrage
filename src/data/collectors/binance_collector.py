from src.data.collectors.base_collector import BaseDataCollector
from src.api.exchanges.binance.binance_spot_api import BinanceSpotAPI
from src.database.market_repository import MarketRepository
from src.database.fee_repository import FeeRepository

class BinanceCollector(BaseDataCollector):
    def __init__(self, api: BinanceSpotAPI, market_repo: MarketRepository, fee_repo: FeeRepository):
        super().__init__(api)
        self.market_repo = market_repo
        self.fee_repo = fee_repo

    async def collect_data(self):
        pairs = await self.api.fetch_trading_pairs()
        if pairs:
            self.market_repo.save_trading_pairs(pairs)
            self.logger.info(f"Collected {len(pairs)} trading pairs from Binance")

            # Копируем данные в exchange_fees
            self.market_repo.copy_trading_pairs_to_fees()

        # Получаем original_pair из exchange_fees
        original_pairs = self.fee_repo.get_original_pairs()

        # Получаем комиссии для этих пар
        fees = await self.api.fetch_exchange_fees(original_pairs)
        if fees:
            self.fee_repo.save_fees(fees)
            self.logger.info(f"Collected {len(fees)} exchange fees from Binance")