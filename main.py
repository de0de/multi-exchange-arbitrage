import asyncio
from src.api.exchanges.binance.binance_spot_api import BinanceSpotAPI
from src.data.collectors.binance_collector import BinanceCollector
from src.database.market_repository import MarketRepository
from src.utils.logger import setup_logging
from config.settings import BINANCE_API_KEY, BINANCE_API_SECRET, DATABASE_URL

async def main():
    setup_logging()
    
    # Создаем экземпляр API
    binance_api = BinanceSpotAPI(BINANCE_API_KEY, BINANCE_API_SECRET)
    
    # Создаем экземпляр репозитория
    market_repo = MarketRepository(DATABASE_URL)
    
    # Передаем репозиторий в коллектор
    collector = BinanceCollector(binance_api, market_repo)
    
    # Интервал обновления в секундах
    update_interval = 5

    try:
        while True:
            await collector.collect_data()
            await asyncio.sleep(update_interval)
    except asyncio.CancelledError:
        pass
    finally:
        await binance_api.close_session()

if __name__ == "__main__":
    asyncio.run(main())