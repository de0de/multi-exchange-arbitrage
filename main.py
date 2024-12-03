import asyncio
import logging
from src.api.exchanges.cex.binance.binance_spot_api import BinanceSpotAPI
from src.api.exchanges.cex.kucoin.kucoin_spot_api import KuCoinSpotAPI
from src.data.collectors.cex.binance_collector import BinanceCollector
from src.data.collectors.cex.kucoin_collector import KuCoinCollector
from src.database.market_repository import MarketRepository
from src.database.currencies_repository import CurrenciesRepository
from src.database.exchanges_repository import ExchangesRepository
from src.utils.logger import setup_logging
from config.settings import DATABASE_URL

async def main():
    setup_logging()
    logger = logging.getLogger(__name__)
    
    # Создаем экземпляры API
    binance_api = BinanceSpotAPI()
    kucoin_api = KuCoinSpotAPI()
    
    # Создаем экземпляры репозиториев
    logger.info("Initializing repositories")
    market_repo_binance = MarketRepository(DATABASE_URL, "binance")
    market_repo_kucoin = MarketRepository(DATABASE_URL, "kucoin")
    currencies_repo = CurrenciesRepository(DATABASE_URL.replace('sqlite:///', ''))
    exchanges_repo = ExchangesRepository(DATABASE_URL.replace('sqlite:///', ''))
    
    # Создаем коллекторы
    binance_collector = BinanceCollector(binance_api, market_repo_binance, exchanges_repo)
    kucoin_collector = KuCoinCollector(kucoin_api, market_repo_kucoin, exchanges_repo)
    
    # Сначала собираем данные о сетях и торговых парах
    logger.info("Starting data collection")
    await binance_collector.collect_data()
    await kucoin_collector.collect_data()
    
    # Создаем и заполняем таблицу currencies
    unique_currencies = currencies_repo.extract_unique_currencies()
    currencies_repo.populate_currencies_table(unique_currencies)
    
    # Обновляем currency_id в таблицах trading_pairs
    market_repo_binance.update_currency_ids()
    market_repo_kucoin.update_currency_ids()

    # Интервал обновления в секундах
    update_interval = 5

    try:
        while True:
            await binance_collector.collect_data()  # Сбор данных о торговых парах и сетях для Binance
            await kucoin_collector.collect_data()   # Сбор данных о торговых парах и сетях для KuCoin
            await asyncio.sleep(update_interval)
    except asyncio.CancelledError:
        pass
    finally:
        await binance_api.close_session()
        await kucoin_api.close_session()
        market_repo_binance.close()
        market_repo_kucoin.close()
        currencies_repo.close()
        exchanges_repo.close()

if __name__ == "__main__":
    asyncio.run(main())