import logging
from src.data.base_collector import BaseDataCollector
from src.api.exchanges.cex.kucoin.kucoin_futures_api import KuCoinFuturesAPI
from src.database.market_repository import MarketRepository
from src.database.exchanges_repository import ExchangesRepository


class KuCoinFuturesCollector(BaseDataCollector):
    def __init__(self, futures_api: KuCoinFuturesAPI, market_repo: MarketRepository, exchanges_repo: ExchangesRepository):
        self.logger = logging.getLogger(__name__)
        self.futures_api = futures_api
        self.market_repo = market_repo
        self.exchanges_repo = exchanges_repo

    async def collect_data(self):
        # Комиссии KuCoin Futures (из /api/v1/contracts/active): maker 0.02%, taker 0.06%
        exchange_id = self.exchanges_repo.get_or_create_exchange_id(
            "KuCoin Futures", maker_fee=0.0002, taker_fee=0.0006
        )

        pairs = await self.futures_api.fetch_trading_pairs()
        if pairs:
            self.market_repo.save_trading_pairs(pairs)
            self.logger.debug(f"Collected {len(pairs)} trading pairs from KuCoin Futures")