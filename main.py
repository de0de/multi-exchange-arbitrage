import asyncio
import logging
import sqlite3
import signal
import time
from src.api.exchanges.cex.binance.binance_spot_api import BinanceSpotAPI
from src.api.exchanges.cex.kucoin.kucoin_spot_api import KuCoinSpotAPI
from src.data.collectors.cex.binance_collector import BinanceCollector
from src.data.collectors.cex.kucoin_collector import KuCoinCollector
from src.database.market_repository import MarketRepository
from src.database.currencies_repository import CurrenciesRepository
from src.database.exchanges_repository import ExchangesRepository
from src.database.trading_pairs_repository import TradingPairsRepository
from src.utils.logger import setup_logging
from src.utils.health_monitor import health_monitor
from config.settings import DATABASE_URL

# Обработчики сигналов для корректного завершения
shutdown_event = asyncio.Event()

def handle_shutdown_signal(sig, frame):
    """Обработчик сигнала завершения."""
    logger = logging.getLogger(__name__)
    logger.info(f"Получен сигнал завершения {sig}. Начинаем корректное завершение работы...")
    shutdown_event.set()

# Регистрируем обработчики для сигналов SIGINT и SIGTERM
signal.signal(signal.SIGINT, handle_shutdown_signal)
signal.signal(signal.SIGTERM, handle_shutdown_signal)

async def main():
    # Настраиваем логирование и замеряем время выполнения
    setup_logging(log_dir='logs')
    logger = logging.getLogger(__name__)
    start_time = time.time()
    
    logger.info("Запуск приложения для арбитража криптовалют")
    
    # Создаем единое подключение к базе данных
    db_path = DATABASE_URL.replace('sqlite:///', '')
    conn = sqlite3.connect(db_path)
    
    # Создаем экземпляры API
    binance_api = BinanceSpotAPI()
    kucoin_api = KuCoinSpotAPI()
    
    # Создаем экземпляры репозиториев с общим подключением
    logger.info("Инициализация репозиториев")
    market_repo_binance = MarketRepository(db_path, "binance")
    market_repo_kucoin = MarketRepository(db_path, "kucoin")
    currencies_repo = CurrenciesRepository(conn)
    exchanges_repo = ExchangesRepository(db_path)
    trading_pairs_repo = TradingPairsRepository(conn)
    
    # Запускаем мониторинг здоровья бирж
    await health_monitor.start_monitoring(report_interval=300)  # Отчет каждые 5 минут
    health_monitor.register_exchange("Binance")
    health_monitor.register_exchange("KuCoin")
    
    # Создаем коллекторы
    binance_collector = BinanceCollector(binance_api, market_repo_binance, exchanges_repo)
    kucoin_collector = KuCoinCollector(kucoin_api, market_repo_kucoin, exchanges_repo)
    
    try:
        # Сначала собираем данные о сетях и торговых парах
        logger.info("Начинаем сбор данных")
        await binance_collector.collect_data()
        await kucoin_collector.collect_data()
        
        # Создаем и заполняем таблицу currencies
        logger.info("Извлекаем уникальные валюты")
        unique_currencies = currencies_repo.extract_unique_currencies()
        logger.info(f"Извлечено уникальных валют: {len(unique_currencies)}")
        
        logger.info("Заполняем таблицу валют")
        currencies_repo.populate_currencies_table(unique_currencies)
        logger.info("Таблица валют успешно заполнена")
        
        # Создаем и заполняем таблицу unique_trading_pairs
        logger.info("Извлекаем уникальные торговые пары")
        trading_tables = ["binance_trading_pairs", "kucoin_trading_pairs"]
        unique_pairs = trading_pairs_repo.extract_unique_trading_pairs(trading_tables)
        logger.info(f"Извлечено уникальных торговых пар: {len(unique_pairs)}")
        
        logger.info("Заполняем таблицу уникальных торговых пар")
        trading_pairs_repo.populate_unique_trading_pairs_table(unique_pairs)
        logger.info("Таблица уникальных торговых пар успешно заполнена")
        
        # Обновляем currency_id в таблицах trading_pairs
        logger.info("Обновляем ID валют в таблицах торговых пар")
        market_repo_binance.update_currency_ids()
        market_repo_kucoin.update_currency_ids()
        logger.info("ID валют успешно обновлены")

        # Обновляем pair_id в таблицах trading_pairs
        logger.info("Обновляем ID торговых пар в таблицах")
        market_repo_binance.update_pair_ids()
        market_repo_kucoin.update_pair_ids()
        logger.info("ID торговых пар успешно обновлены")

        # Интервал обновления в секундах
        update_interval = 5
        
        logger.info(f"Инициализация завершена за {time.time() - start_time:.2f} сек. Начинаем циклический сбор данных.")
        
        # Основной цикл сбора данных
        while not shutdown_event.is_set():
            cycle_start = time.time()
            
            # Сбор данных с Binance
            try:
                logger.debug("Сбор данных с Binance")
                await binance_collector.collect_data()
                # Записываем успешный запрос в мониторинг
                request_time = (time.time() - cycle_start) * 1000  # в миллисекундах
                health_monitor.record_request("Binance", True, request_time)
            except Exception as e:
                logger.error(f"Ошибка при сборе данных с Binance: {str(e)}")
                health_monitor.record_request("Binance", False, 0, str(e))
            
            # Сбор данных с KuCoin
            try:
                logger.debug("Сбор данных с KuCoin")
                await kucoin_collector.collect_data()
                # Записываем успешный запрос в мониторинг
                request_time = (time.time() - cycle_start) * 1000  # в миллисекундах
                health_monitor.record_request("KuCoin", True, request_time)
            except Exception as e:
                logger.error(f"Ошибка при сборе данных с KuCoin: {str(e)}")
                health_monitor.record_request("KuCoin", False, 0, str(e))
            
            # Расчет времени до следующего обновления
            elapsed = time.time() - cycle_start
            sleep_time = max(0.1, update_interval - elapsed)
            logger.debug(f"Цикл сбора данных выполнен за {elapsed:.2f} сек. Ожидание {sleep_time:.2f} сек.")
            
            # Ждем до следующего обновления или до сигнала завершения
            try:
                await asyncio.wait_for(shutdown_event.wait(), timeout=sleep_time)
            except asyncio.TimeoutError:
                pass  # Это ожидаемо, если shutdown_event не установлен
            
    except Exception as e:
        logger.exception(f"Необработанное исключение в основном цикле: {str(e)}")
    finally:
        logger.info("Завершение работы, закрытие ресурсов...")
        await health_monitor.stop_monitoring()
        await binance_api.close_session()
        await kucoin_api.close_session()
        conn.close()
        logger.info("Все ресурсы успешно закрыты. Приложение завершено.")

if __name__ == "__main__":
    asyncio.run(main())