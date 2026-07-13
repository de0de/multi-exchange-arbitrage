import asyncio
import logging
import sqlite3
import signal
import time

from src.api.exchanges.cex.binance.binance_spot_api import BinanceSpotAPI
from src.api.exchanges.cex.binance.binance_futures_api import BinanceFuturesAPI
from src.api.exchanges.cex.kucoin.kucoin_spot_api import KuCoinSpotAPI
from src.api.exchanges.cex.kucoin.kucoin_futures_api import KuCoinFuturesAPI
from src.api.exchanges.cex.gate.gate_spot_api import GateSpotAPI
from src.api.exchanges.cex.mexc.mexc_spot_api import MexcSpotAPI
from src.data.collectors.cex.binance_collector import BinanceCollector
from src.data.collectors.cex.binance_futures_collector import BinanceFuturesCollector
from src.data.collectors.cex.kucoin_collector import KuCoinCollector
from src.data.collectors.cex.kucoin_futures_collector import KuCoinFuturesCollector
from src.data.collectors.cex.gate_collector import GateCollector
from src.data.collectors.cex.mexc_collector import MexcCollector
from src.data.collectors.cex.order_book_collector import OrderBookCollector
from src.database.market_repository import MarketRepository
from src.database.funding_rate_repository import FundingRateRepository
from src.database.currencies_repository import CurrenciesRepository
from src.database.exchanges_repository import ExchangesRepository
from src.database.trading_pairs_repository import TradingPairsRepository
from src.database.order_book_repository import OrderBookRepository
from src.database.simulated_trade_repository import SimulatedTradeRepository
from src.core.spread_monitor import SpreadMonitor
from src.core.paper_trading.spot_spot_strategy import SpotSpotStrategy
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
    conn.execute("PRAGMA journal_mode=WAL;")

    # Создаем экземпляры API
    binance_api = BinanceSpotAPI()
    binance_futures_api = BinanceFuturesAPI()
    kucoin_api = KuCoinSpotAPI()
    kucoin_futures_api = KuCoinFuturesAPI()
    gate_api = GateSpotAPI()
    mexc_api = MexcSpotAPI()

    # Создаем экземпляры репозиториев с общим подключением
    logger.info("Инициализация репозиториев")
    market_repo_binance = MarketRepository(db_path, "binance")
    market_repo_binance_futures = MarketRepository(db_path, "binance_futures")
    market_repo_kucoin = MarketRepository(db_path, "kucoin")
    market_repo_kucoin_futures = MarketRepository(db_path, "kucoin_futures")
    market_repo_gate = MarketRepository(db_path, "gate")
    market_repo_mexc = MarketRepository(db_path, "mexc")
    funding_repo_binance_futures = FundingRateRepository(db_path, "binance_futures")
    funding_repo_kucoin_futures = FundingRateRepository(db_path, "kucoin_futures")
    currencies_repo = CurrenciesRepository(conn)
    exchanges_repo = ExchangesRepository(db_path)
    trading_pairs_repo = TradingPairsRepository(conn)

    # Создаем репозитории Order Book (каждый со своим соединением)
    order_book_repo_binance = OrderBookRepository(db_path, "binance")
    order_book_repo_binance_futures = OrderBookRepository(db_path, "binance_futures")
    order_book_repo_kucoin = OrderBookRepository(db_path, "kucoin")
    order_book_repo_kucoin_futures = OrderBookRepository(db_path, "kucoin_futures")
    order_book_repo_gate = OrderBookRepository(db_path, "gate")
    order_book_repo_mexc = OrderBookRepository(db_path, "mexc")

    # Создаем OrderBookCollector и регистрируем источники
    ob_collector = OrderBookCollector()
    ob_collector.add_source(binance_api, order_book_repo_binance)
    ob_collector.add_source(binance_futures_api, order_book_repo_binance_futures)
    ob_collector.add_source(kucoin_api, order_book_repo_kucoin)
    ob_collector.add_source(kucoin_futures_api, order_book_repo_kucoin_futures)
    ob_collector.add_source(gate_api, order_book_repo_gate)
    ob_collector.add_source(mexc_api, order_book_repo_mexc)

    # Словари для передачи в SpreadMonitor
    apis_dict = {
        "Binance": binance_api,
        "Binance Futures": binance_futures_api,
        "KuCoin": kucoin_api,
        "KuCoin Futures": kucoin_futures_api,
        "Gate.io": gate_api,
        "MEXC": mexc_api,
    }
    order_book_repos_dict = {
        "binance": order_book_repo_binance,
        "binance_futures": order_book_repo_binance_futures,
        "kucoin": order_book_repo_kucoin,
        "kucoin_futures": order_book_repo_kucoin_futures,
        "gate": order_book_repo_gate,
        "mexc": order_book_repo_mexc,
    }

    # Создаем SpreadMonitor
    spread_monitor = SpreadMonitor(
        conn=conn,
        apis=apis_dict,
        order_book_repos=order_book_repos_dict,
        order_book_collector=ob_collector,
        min_spread_percent=0.5,
        min_volume_usdt=1000.0,
        max_staleness_seconds=15.0,
        allowed_quote_currencies=["USDT", "USDC", "BTC", "ETH"],
        ob_ttl_seconds=5.0,
        suspected_collision_threshold_percent=20.0,
        max_opportunities=100,
    )

    # Paper Trading (Фаза 1, spot-spot): симуляция исполнения найденных
    # возможностей с реалистичной задержкой перевода между биржами
    simulated_trade_repo = SimulatedTradeRepository(conn)
    paper_strategy = SpotSpotStrategy(
        conn=conn,
        spread_monitor=spread_monitor,
        trade_repo=simulated_trade_repo,
        trade_size_usdt=1000.0,  # рабочий депозит
        min_profit_threshold_percent=0.1,
    )

    # Запускаем мониторинг здоровья бирж
    await health_monitor.start_monitoring(report_interval=300)  # Отчет каждые 5 минут
    health_monitor.register_exchange("Binance")
    health_monitor.register_exchange("Binance Futures")
    health_monitor.register_exchange("KuCoin")
    health_monitor.register_exchange("KuCoin Futures")
    health_monitor.register_exchange("Gate.io")
    health_monitor.register_exchange("MEXC")

    # Создаем коллекторы
    binance_collector = BinanceCollector(binance_api, market_repo_binance, exchanges_repo)
    binance_futures_collector = BinanceFuturesCollector(binance_futures_api, market_repo_binance_futures, exchanges_repo)
    kucoin_collector = KuCoinCollector(kucoin_api, market_repo_kucoin, exchanges_repo)
    kucoin_futures_collector = KuCoinFuturesCollector(kucoin_futures_api, market_repo_kucoin_futures, exchanges_repo)
    gate_collector = GateCollector(gate_api, market_repo_gate, exchanges_repo)
    mexc_collector = MexcCollector(mexc_api, market_repo_mexc, exchanges_repo)

    try:
        # Сначала собираем данные о сетях и торговых парах параллельно
        logger.info("Начинаем сбор данных (параллельно)")
        await asyncio.gather(
            binance_collector.collect_data(),
            binance_futures_collector.collect_data(),
            kucoin_collector.collect_data(),
            kucoin_futures_collector.collect_data(),
            gate_collector.collect_data(),
            mexc_collector.collect_data()
        )

        # Создаем и заполняем таблицу currencies
        logger.info("Извлекаем уникальные валюты")
        unique_currencies = currencies_repo.extract_unique_currencies()
        logger.info(f"Извлечено уникальных валют: {len(unique_currencies)}")

        logger.info("Заполняем таблицу валют")
        currencies_repo.populate_currencies_table(list(unique_currencies))
        logger.info("Таблица валют успешно заполнена")

        # Создаем и заполняем таблицу unique_trading_pairs
        logger.info("Извлекаем уникальные торговые пары")
        trading_tables = ["binance_trading_pairs", "binance_futures_trading_pairs", "kucoin_trading_pairs", "kucoin_futures_trading_pairs", "gate_trading_pairs", "mexc_trading_pairs"]
        unique_pairs = trading_pairs_repo.extract_unique_trading_pairs(trading_tables)
        logger.info(f"Извлечено уникальных торговых пар: {len(unique_pairs)}")

        logger.info("Заполняем таблицу уникальных торговых пар")
        trading_pairs_repo.populate_unique_trading_pairs_table(unique_pairs)
        logger.info("Таблица уникальных торговых пар успешно заполнена")

        # Обновляем currency_id в таблицах trading_pairs
        logger.info("Обновляем ID валют в таблицах торговых пар")
        market_repo_binance.update_currency_ids()
        market_repo_binance_futures.update_currency_ids()
        market_repo_kucoin.update_currency_ids()
        market_repo_kucoin_futures.update_currency_ids()
        market_repo_gate.update_currency_ids()
        market_repo_mexc.update_currency_ids()
        logger.info("ID валют успешно обновлены")

        # Обновляем pair_id в таблицах trading_pairs
        logger.info("Обновляем ID торговых пар в таблицах")
        market_repo_binance.update_pair_ids()
        market_repo_binance_futures.update_pair_ids()
        market_repo_kucoin.update_pair_ids()
        market_repo_kucoin_futures.update_pair_ids()
        market_repo_gate.update_pair_ids()
        market_repo_mexc.update_pair_ids()
        logger.info("ID торговых пар успешно обновлены")

        # Интервал обновления в секундах
        update_interval = 5

        logger.info(f"Инициализация завершена за {time.time() - start_time:.2f} сек. Начинаем циклический сбор данных.")

        # Основной цикл сбора данных
        while not shutdown_event.is_set():
            cycle_start = time.time()

            # Параллельный сбор данных со всех бирж
            logger.debug("Параллельный сбор данных с Binance, Binance Futures, KuCoin и KuCoin Futures")
            results = await asyncio.gather(
                binance_collector.collect_data(),
                binance_futures_collector.collect_data(),
                kucoin_collector.collect_data(),
                kucoin_futures_collector.collect_data(),
                gate_collector.collect_data(),
                mexc_collector.collect_data(),
                return_exceptions=True
            )

            # Обрабатываем результаты для каждой биржи
            for exchange_name, result in zip(["Binance", "Binance Futures", "KuCoin", "KuCoin Futures", "Gate.io", "MEXC"], results):
                request_time = (time.time() - cycle_start) * 1000  # в миллисекундах
                if isinstance(result, Exception):
                    logger.error(f"Ошибка при сборе данных с {exchange_name}: {str(result)}")
                    health_monitor.record_request(exchange_name, False, 0, str(result))
                else:
                    health_monitor.record_request(exchange_name, True, request_time)

            # Сбор funding rate (только futures биржи)
            logger.debug("Сбор funding rate с Futures бирж")
            bf_funding = await binance_futures_api.fetch_funding_rates()
            if bf_funding:
                funding_repo_binance_futures.save_funding_rates(bf_funding)
                logger.debug(f"Saved {len(bf_funding)} funding rates from Binance Futures")

            kf_funding = await kucoin_futures_api.fetch_funding_rates()
            if kf_funding:
                funding_repo_kucoin_futures.save_funding_rates(kf_funding)
                logger.debug(f"Saved {len(kf_funding)} funding rates from KuCoin Futures")

            # Мониторинг спредов (сканирование найденных расхождений)
            logger.debug("Сканирование арбитражных возможностей...")
            opportunities = await spread_monitor.scan()
            if opportunities:
                opportunity_ids = spread_monitor.save_results(opportunities)
                spread_monitor.log_top_opportunities(opportunities, top_n=10)

                # Paper trading: открываем симулированные позиции по новым возможностям
                await paper_strategy.open_positions(list(zip(opportunity_ids, opportunities)))

            # Paper trading: закрываем позиции, у которых истекло время перевода
            # (проверяется каждый цикл, независимо от наличия новых возможностей)
            await paper_strategy.close_ready_positions()

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
        await binance_futures_api.close_session()
        await kucoin_api.close_session()
        await kucoin_futures_api.close_session()
        await gate_api.close_session()
        await mexc_api.close_session()
        funding_repo_binance_futures.close()
        funding_repo_kucoin_futures.close()
        order_book_repo_binance.close()
        order_book_repo_binance_futures.close()
        order_book_repo_kucoin.close()
        order_book_repo_kucoin_futures.close()
        order_book_repo_gate.close()
        order_book_repo_mexc.close()
        conn.close()
        logger.info("Все ресурсы успешно закрыты. Приложение завершено.")


if __name__ == "__main__":
    asyncio.run(main())