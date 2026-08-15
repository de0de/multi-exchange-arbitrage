import logging
from src.data.base_collector import BaseDataCollector
from src.api.exchanges.cex.bybit.bybit_spot_api import BybitSpotAPI
from src.database.market_repository import MarketRepository
from src.database.exchanges_repository import ExchangesRepository


class BybitCollector(BaseDataCollector):
    def __init__(self, bybit_api: BybitSpotAPI, market_repo: MarketRepository, exchanges_repo: ExchangesRepository):
        self.logger = logging.getLogger(__name__)
        self.spot_api = bybit_api
        self.market_repo = market_repo
        self.exchanges_repo = exchanges_repo

    async def collect_data(self):
        # Комиссии Bybit Spot: базовый уровень (non-VIP) 0.1% maker / 0.1% taker.
        # Взято из публичной тарифной сетки, а не из API: единственный
        # эндпоинт с реальными ставками (/v5/account/fee-rate) требует
        # ключей, а market data мы собираем без них. Консервативно —
        # реальная ставка при VIP/скидках может быть только ниже.
        exchange_id = self.exchanges_repo.get_or_create_exchange_id("Bybit", maker_fee=0.001, taker_fee=0.001)

        pairs = await self.spot_api.fetch_trading_pairs()
        if pairs:
            self.market_repo.save_trading_pairs(pairs)
            self.logger.debug(f"Collected {len(pairs)} trading pairs from Bybit")
