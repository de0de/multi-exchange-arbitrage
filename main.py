import asyncio
from src.api.exchanges.binance.binance_spot_api import BinanceSpotAPI
from src.data.collectors.binance_collector import BinanceCollector
from src.database.market_repository import MarketRepository
from src.database.fee_repository import FeeRepository
from src.database.network_repository import NetworkRepository
from src.database.currencies_repository import CurrenciesRepository
from src.utils.logger import setup_logging
from config.settings import DATABASE_URL

async def main():
    setup_logging()
    
    # Создаем экземпляр API
    binance_api = BinanceSpotAPI()
    
    # Создаем экземпляры репозиториев
    market_repo = MarketRepository(DATABASE_URL)
    fee_repo = FeeRepository(DATABASE_URL.replace('sqlite:///', ''))
    network_repo = NetworkRepository(DATABASE_URL.replace('sqlite:///', ''))
    currencies_repo = CurrenciesRepository(DATABASE_URL.replace('sqlite:///', ''))
    
    # Передаем репозитории в коллектор
    collector = BinanceCollector(binance_api, market_repo, fee_repo, network_repo)
    
    # Сначала собираем данные о сетях и торговых парах
    await collector.collect_data()  # Убедитесь, что этот метод заполняет таблицы networks и trading_pairs
    
    # Создаем и заполняем таблицу currencies
    unique_currencies = currencies_repo.extract_unique_currencies()
    currencies_repo.populate_currencies_table(unique_currencies)
    
    # Интервал обновления в секундах
    update_interval = 5

    try:
        while True:
            await collector.collect_data()  # Сбор данных о торговых парах, комиссиях и сетях
            await asyncio.sleep(update_interval)
    except asyncio.CancelledError:
        pass
    finally:
        await binance_api.close_session()
        market_repo.close()
        fee_repo.close()
        network_repo.close()
        currencies_repo.close()

if __name__ == "__main__":
    asyncio.run(main())