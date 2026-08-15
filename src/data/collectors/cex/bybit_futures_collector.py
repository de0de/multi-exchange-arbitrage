import logging
from src.data.base_collector import BaseDataCollector
from src.api.exchanges.cex.bybit.bybit_futures_api import BybitFuturesAPI
from src.database.market_repository import MarketRepository
from src.database.exchanges_repository import ExchangesRepository


class BybitFuturesCollector(BaseDataCollector):
    def __init__(self, bybit_futures_api: BybitFuturesAPI, market_repo: MarketRepository, exchanges_repo: ExchangesRepository):
        self.logger = logging.getLogger(__name__)
        self.futures_api = bybit_futures_api
        self.market_repo = market_repo
        self.exchanges_repo = exchanges_repo

    async def collect_data(self):
        # Комиссии Bybit USDT-перпетуалов: базовый уровень (non-VIP)
        # 0.02% maker / 0.055% taker. Источник тот же, что у спота, —
        # публичная тарифная сетка: /v5/account/fee-rate требует ключей,
        # а market data собирается без них.
        exchange_id = self.exchanges_repo.get_or_create_exchange_id("Bybit Futures", maker_fee=0.0002, taker_fee=0.00055)

        pairs = await self.futures_api.fetch_trading_pairs()
        if pairs:
            self.market_repo.save_trading_pairs(pairs)
            self.logger.debug(f"Collected {len(pairs)} trading pairs from Bybit Futures")
