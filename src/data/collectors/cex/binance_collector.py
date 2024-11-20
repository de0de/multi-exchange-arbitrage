import logging
from src.data.base_collector import BaseDataCollector
from src.api.exchanges.cex.binance.binance_spot_api import BinanceSpotAPI
from src.api.exchanges.cex.binance.binance_account_api import BinanceAccountAPI
from src.database.market_repository import MarketRepository
from src.database.fee_repository import FeeRepository
from src.database.network_repository import NetworkRepository
from src.database.exchanges_repository import ExchangesRepository

class BinanceCollector(BaseDataCollector):
    def __init__(self, binance_api: BinanceSpotAPI, market_repo: MarketRepository, fee_repo: FeeRepository, network_repo: NetworkRepository, exchanges_repo: ExchangesRepository):
        self.logger = logging.getLogger(__name__)
        self.spot_api = binance_api
        self.account_api = BinanceAccountAPI()
        self.market_repo = market_repo
        self.fee_repo = fee_repo
        self.network_repo = network_repo
        self.exchanges_repo = exchanges_repo

    async def collect_data(self):
        pairs = await self.spot_api.fetch_trading_pairs()
        if pairs:
            self.market_repo.save_trading_pairs(pairs)
            self.logger.info(f"Collected {len(pairs)} trading pairs from Binance")
            self.market_repo.copy_trading_pairs_to_fees()

        original_pairs = self.fee_repo.get_original_pairs()

        fees = await self.account_api.fetch_exchange_fees(original_pairs)
        if fees:
            self.fee_repo.save_fees(fees)
            self.logger.info(f"Collected {len(fees)} exchange fees from Binance")

        networks = await self.account_api.fetch_currency_networks()
        if networks:
            self.network_repo.save_networks(networks)
            self.logger.info(f"Collected {len(networks)} networks from Binance")

        exchange_data = await self.account_api.fetch_account_balance()
        self.logger.debug(f"Fetched exchange data: {exchange_data}")
        self.exchanges_repo.save_or_update_exchange(exchange_data)
        self.logger.info(f"Collected exchange data for {exchange_data.name}")