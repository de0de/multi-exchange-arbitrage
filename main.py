import asyncio
import logging
import signal
import sys
import time

from src.api.exchanges.cex.binance.binance_spot_api import BinanceSpotAPI
from src.api.exchanges.cex.binance.binance_futures_api import BinanceFuturesAPI
from src.api.exchanges.cex.kucoin.kucoin_spot_api import KuCoinSpotAPI
from src.api.exchanges.cex.kucoin.kucoin_futures_api import KuCoinFuturesAPI
from src.api.exchanges.cex.gate.gate_spot_api import GateSpotAPI
from src.api.exchanges.cex.gate.gate_futures_api import GateFuturesAPI
from src.api.exchanges.cex.mexc.mexc_spot_api import MexcSpotAPI
from src.api.exchanges.cex.mexc.mexc_futures_api import MexcFuturesAPI
from src.data.collectors.cex.binance_collector import BinanceCollector
from src.data.collectors.cex.binance_futures_collector import BinanceFuturesCollector
from src.data.collectors.cex.kucoin_collector import KuCoinCollector
from src.data.collectors.cex.kucoin_futures_collector import KuCoinFuturesCollector
from src.data.collectors.cex.gate_collector import GateCollector
from src.data.collectors.cex.gate_futures_collector import GateFuturesCollector
from src.data.collectors.cex.mexc_collector import MexcCollector
from src.data.collectors.cex.mexc_futures_collector import MexcFuturesCollector
from src.data.collectors.cex.order_book_collector import OrderBookCollector
from src.data.history_archiver import HistoryArchiver
from src.utils.daily_report import DailyReport
from src.database.market_repository import MarketRepository
from src.database.funding_rate_repository import FundingRateRepository
from src.database.currencies_repository import CurrenciesRepository
from src.database.exchanges_repository import ExchangesRepository
from src.database.trading_pairs_repository import TradingPairsRepository
from src.database.order_book_repository import OrderBookRepository
from src.database.simulated_trade_repository import SimulatedTradeRepository
from src.core.spread_monitor import SpreadMonitor
from src.core.futures_spread_monitor import FuturesSpreadMonitor
from src.core.paper_trading.spot_spot_strategy import SpotSpotStrategy
from src.utils.logger import setup_logging
from src.utils.health_monitor import health_monitor
from src.utils.uptime_push import UptimePush
from src.database import db
from config.settings import (
    MIN_SPREAD_PERCENT, MIN_VOLUME_USDT, MAX_STALENESS_SECONDS,
    OB_TTL_SECONDS, TRADE_SIZE_USDT, RETENTION_DAYS,
    UPTIME_KUMA_PUSH_URL,
)

# Обработчики сигналов для корректного завершения
shutdown_event = asyncio.Event()

# Архиватор виден обработчику сигнала: он работает в фоновом потоке
# (asyncio.to_thread), и остановить его запрос можно только отменой на стороне
# PostgreSQL. Без этого systemctl restart во время архивации оставлял
# зомби-транзакцию, которая жила часами после смерти процесса (PLAN.md 5.5).
history_archiver = None
# Суточная сводка — по той же причине: её запросы тоже идут в фоновом потоке
daily_report = None

def handle_shutdown_signal(sig, frame):
    """Обработчик сигнала завершения."""
    logger = logging.getLogger(__name__)
    logger.info(f"Получен сигнал завершения {sig}. Начинаем корректное завершение работы...")
    if history_archiver is not None:
        history_archiver.cancel_running()
    if daily_report is not None:
        daily_report.cancel_running()
    shutdown_event.set()

# Регистрируем обработчики для сигналов SIGINT и SIGTERM
signal.signal(signal.SIGINT, handle_shutdown_signal)
signal.signal(signal.SIGTERM, handle_shutdown_signal)


async def main():
    # history_archiver и daily_report — модульного уровня: до них должен
    # дотянуться обработчик сигнала, чтобы отменить их запросы при остановке
    global history_archiver, daily_report

    # Настраиваем логирование и замеряем время выполнения
    setup_logging(log_dir='logs')
    logger = logging.getLogger(__name__)
    start_time = time.time()

    logger.info("Запуск приложения для арбитража криптовалют")

    # Создаем единое подключение к базе данных (PostgreSQL/TimescaleDB,
    # docker-compose.yml; настройки - config/settings.py / .env)
    conn = db.connect()

    # Создаем экземпляры API
    binance_api = BinanceSpotAPI()
    binance_futures_api = BinanceFuturesAPI()
    kucoin_api = KuCoinSpotAPI()
    kucoin_futures_api = KuCoinFuturesAPI()
    gate_api = GateSpotAPI()
    gate_futures_api = GateFuturesAPI()
    mexc_api = MexcSpotAPI()
    mexc_futures_api = MexcFuturesAPI()

    # Создаем экземпляры репозиториев с общим подключением
    logger.info("Инициализация репозиториев")
    # Порядок важен: exchanges/currencies/unique_pairs создаются первыми,
    # на них смотрят запросы остальных репозиториев
    exchanges_repo = ExchangesRepository(conn)
    currencies_repo = CurrenciesRepository(conn)
    trading_pairs_repo = TradingPairsRepository(conn)
    market_repo_binance = MarketRepository(conn, "binance")
    market_repo_binance_futures = MarketRepository(conn, "binance_futures")
    market_repo_kucoin = MarketRepository(conn, "kucoin")
    market_repo_kucoin_futures = MarketRepository(conn, "kucoin_futures")
    market_repo_gate = MarketRepository(conn, "gate")
    market_repo_gate_futures = MarketRepository(conn, "gate_futures")
    market_repo_mexc = MarketRepository(conn, "mexc")
    market_repo_mexc_futures = MarketRepository(conn, "mexc_futures")
    funding_repo_binance_futures = FundingRateRepository(conn, "binance_futures")
    funding_repo_kucoin_futures = FundingRateRepository(conn, "kucoin_futures")
    funding_repo_gate_futures = FundingRateRepository(conn, "gate_futures")
    funding_repo_mexc_futures = FundingRateRepository(conn, "mexc_futures")

    # Репозитории Order Book (общее соединение — PostgreSQL штатно
    # обслуживает всех писателей процесса)
    order_book_repo_binance = OrderBookRepository(conn, "binance")
    order_book_repo_binance_futures = OrderBookRepository(conn, "binance_futures")
    order_book_repo_kucoin = OrderBookRepository(conn, "kucoin")
    order_book_repo_kucoin_futures = OrderBookRepository(conn, "kucoin_futures")
    order_book_repo_gate = OrderBookRepository(conn, "gate")
    order_book_repo_gate_futures = OrderBookRepository(conn, "gate_futures")
    order_book_repo_mexc = OrderBookRepository(conn, "mexc")
    order_book_repo_mexc_futures = OrderBookRepository(conn, "mexc_futures")

    # Создаем OrderBookCollector и регистрируем источники
    ob_collector = OrderBookCollector()
    ob_collector.add_source(binance_api, order_book_repo_binance)
    ob_collector.add_source(binance_futures_api, order_book_repo_binance_futures)
    ob_collector.add_source(kucoin_api, order_book_repo_kucoin)
    ob_collector.add_source(kucoin_futures_api, order_book_repo_kucoin_futures)
    ob_collector.add_source(gate_api, order_book_repo_gate)
    ob_collector.add_source(gate_futures_api, order_book_repo_gate_futures)
    ob_collector.add_source(mexc_api, order_book_repo_mexc)
    ob_collector.add_source(mexc_futures_api, order_book_repo_mexc_futures)

    # Словари для передачи в SpreadMonitor
    apis_dict = {
        "Binance": binance_api,
        "Binance Futures": binance_futures_api,
        "KuCoin": kucoin_api,
        "KuCoin Futures": kucoin_futures_api,
        "Gate.io": gate_api,
        "Gate.io Futures": gate_futures_api,
        "MEXC": mexc_api,
        "MEXC Futures": mexc_futures_api,
    }
    order_book_repos_dict = {
        "binance": order_book_repo_binance,
        "binance_futures": order_book_repo_binance_futures,
        "kucoin": order_book_repo_kucoin,
        "kucoin_futures": order_book_repo_kucoin_futures,
        "gate": order_book_repo_gate,
        "gate_futures": order_book_repo_gate_futures,
        "mexc": order_book_repo_mexc,
        "mexc_futures": order_book_repo_mexc_futures,
    }

    # Создаем SpreadMonitor
    spread_monitor = SpreadMonitor(
        conn=conn,
        apis=apis_dict,
        order_book_repos=order_book_repos_dict,
        order_book_collector=ob_collector,
        min_spread_percent=MIN_SPREAD_PERCENT,
        min_volume_usdt=MIN_VOLUME_USDT,
        max_staleness_seconds=MAX_STALENESS_SECONDS,
        allowed_quote_currencies=["USDT", "USDC", "BTC", "ETH"],
        ob_ttl_seconds=OB_TTL_SECONDS,
        suspected_collision_threshold_percent=20.0,
        max_opportunities=100,
    )

    # Архиватор истории: раз в сутки экспортирует устаревшие строки
    # (spread_history, futures_spread_history, arbitrage_opportunities)
    # в data/archive/*.csv.gz и только затем удаляет их. Горизонт — из .env
    # (RETENTION_DAYS, по умолчанию 7 суток); раньше был зашит дефолтом
    # конструктора 14.0 и отсюда не передавался вообще
    history_archiver = HistoryArchiver(conn, retention_days=RETENTION_DAYS)

    # Суточная сводка в лог (первая — при старте): счётчики всех потоков
    # данных и paper trading, размер БД
    daily_report = DailyReport(conn)

    # Heartbeat в Uptime Kuma. Пингуется в КОНЦЕ итерации главного цикла —
    # намеренно не из health_monitor, тот живёт своей фоновой корутиной и
    # горел бы зелёным при параличе главного цикла (см. докстроку
    # src/utils/uptime_push.py). Пустой URL = мониторинг выключен
    uptime_push = UptimePush(UPTIME_KUMA_PUSH_URL)

    # Мониторинг спот-фьюч / фьюч-фьюч basis: только запись истории
    # (futures_spread_history, funding_rate_history), без симуляции —
    # см. DATA_SPECIFICATION.md, разделы 4-5
    futures_spread_monitor = FuturesSpreadMonitor(conn)

    # Paper Trading (Фаза 1, spot-spot): симуляция исполнения найденных
    # возможностей с реалистичной задержкой перевода между биржами
    simulated_trade_repo = SimulatedTradeRepository(conn)
    paper_strategy = SpotSpotStrategy(
        conn=conn,
        spread_monitor=spread_monitor,
        trade_repo=simulated_trade_repo,
        trade_size_usdt=TRADE_SIZE_USDT,
        min_profit_threshold_percent=0.1,
    )

    # Запускаем мониторинг здоровья бирж
    await health_monitor.start_monitoring(report_interval=300)  # Отчет каждые 5 минут
    health_monitor.register_exchange("Binance")
    health_monitor.register_exchange("Binance Futures")
    health_monitor.register_exchange("KuCoin")
    health_monitor.register_exchange("KuCoin Futures")
    health_monitor.register_exchange("Gate.io")
    health_monitor.register_exchange("Gate.io Futures")
    health_monitor.register_exchange("MEXC")
    health_monitor.register_exchange("MEXC Futures")

    # Создаем коллекторы
    binance_collector = BinanceCollector(binance_api, market_repo_binance, exchanges_repo)
    binance_futures_collector = BinanceFuturesCollector(binance_futures_api, market_repo_binance_futures, exchanges_repo)
    kucoin_collector = KuCoinCollector(kucoin_api, market_repo_kucoin, exchanges_repo)
    kucoin_futures_collector = KuCoinFuturesCollector(kucoin_futures_api, market_repo_kucoin_futures, exchanges_repo)
    gate_collector = GateCollector(gate_api, market_repo_gate, exchanges_repo)
    gate_futures_collector = GateFuturesCollector(gate_futures_api, market_repo_gate_futures, exchanges_repo)
    mexc_collector = MexcCollector(mexc_api, market_repo_mexc, exchanges_repo)
    mexc_futures_collector = MexcFuturesCollector(mexc_futures_api, market_repo_mexc_futures, exchanges_repo)

    crashed = False
    try:
        # Сначала собираем данные о сетях и торговых парах параллельно
        logger.info("Начинаем сбор данных (параллельно)")
        await asyncio.gather(
            binance_collector.collect_data(),
            binance_futures_collector.collect_data(),
            kucoin_collector.collect_data(),
            kucoin_futures_collector.collect_data(),
            gate_collector.collect_data(),
            gate_futures_collector.collect_data(),
            mexc_collector.collect_data(),
            mexc_futures_collector.collect_data()
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
        trading_tables = ["binance_trading_pairs", "binance_futures_trading_pairs", "kucoin_trading_pairs", "kucoin_futures_trading_pairs", "gate_trading_pairs", "gate_futures_trading_pairs", "mexc_trading_pairs", "mexc_futures_trading_pairs"]
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
        market_repo_gate_futures.update_currency_ids()
        market_repo_mexc.update_currency_ids()
        market_repo_mexc_futures.update_currency_ids()
        logger.info("ID валют успешно обновлены")

        # Обновляем pair_id в таблицах trading_pairs
        logger.info("Обновляем ID торговых пар в таблицах")
        market_repo_binance.update_pair_ids()
        market_repo_binance_futures.update_pair_ids()
        market_repo_kucoin.update_pair_ids()
        market_repo_kucoin_futures.update_pair_ids()
        market_repo_gate.update_pair_ids()
        market_repo_gate_futures.update_pair_ids()
        market_repo_mexc.update_pair_ids()
        market_repo_mexc_futures.update_pair_ids()
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
                gate_futures_collector.collect_data(),
                mexc_collector.collect_data(),
                mexc_futures_collector.collect_data(),
                return_exceptions=True
            )

            # Обрабатываем результаты для каждой биржи
            for exchange_name, result in zip(["Binance", "Binance Futures", "KuCoin", "KuCoin Futures", "Gate.io", "Gate.io Futures", "MEXC", "MEXC Futures"], results):
                request_time = (time.time() - cycle_start) * 1000  # в миллисекундах
                if isinstance(result, Exception):
                    logger.error(f"Ошибка при сборе данных с {exchange_name}: {str(result)}")
                    health_monitor.record_request(exchange_name, False, 0, str(result))
                else:
                    health_monitor.record_request(exchange_name, True, request_time)

            # Сбор funding rate (только futures биржи). Параллельно, тот же
            # паттерн, что и для основного сбора данных выше — раньше это были
            # 4 последовательных await (из них реальный HTTP-запрос делает
            # только Binance Futures; KuCoin/Gate.io/MEXC Futures читают
            # funding rate из in-memory кеша, заполненного fetch_trading_pairs
            # в этом же цикле, — экономия в основном за счёт Binance).
            logger.debug("Сбор funding rate с Futures бирж")
            funding_results = await asyncio.gather(
                binance_futures_api.fetch_funding_rates(),
                kucoin_futures_api.fetch_funding_rates(),
                gate_futures_api.fetch_funding_rates(),
                mexc_futures_api.fetch_funding_rates(),
                return_exceptions=True,
            )
            funding_repos = [
                ("Binance Futures", funding_repo_binance_futures),
                ("KuCoin Futures", funding_repo_kucoin_futures),
                ("Gate.io Futures", funding_repo_gate_futures),
                ("MEXC Futures", funding_repo_mexc_futures),
            ]
            for (exchange_name, repo), result in zip(funding_repos, funding_results):
                if isinstance(result, Exception):
                    logger.error(f"Ошибка при сборе funding rate с {exchange_name}: {result}")
                elif result:
                    repo.save_funding_rates(result)
                    logger.debug(f"Saved {len(result)} funding rates from {exchange_name}")

            # Запись спот-фьюч / фьюч-фьюч basis-истории.
            # ВАЖНО: сразу после сохранения funding (снимок текущего цикла) и
            # ДО спот-скана с paper trading — те занимают десятки секунд, и
            # данные ног успели бы протухнуть для фильтра свежести
            try:
                futures_spread_monitor.scan()
            except Exception as e:
                logger.error(f"FuturesSpreadMonitor: ошибка сканирования: {e}")

            # Мониторинг спредов (сканирование найденных расхождений)
            logger.debug("Сканирование арбитражных возможностей...")
            opportunities = await spread_monitor.scan()
            if opportunities:
                opportunity_ids = spread_monitor.save_results(opportunities)
                spread_monitor.log_top_opportunities(opportunities, top_n=5)

                # Paper trading: открываем симулированные позиции по новым возможностям
                await paper_strategy.open_positions(list(zip(opportunity_ids, opportunities)))

            # Paper trading: закрываем позиции, у которых истекло время перевода
            # (проверяется каждый цикл, независимо от наличия новых возможностей)
            await paper_strategy.close_ready_positions()

            # Архивация+retention раз в сутки. Отключалась 2026-08-01 из-за
            # трёх зависаний подряд; корневая причина найдена и устранена -
            # неиндексированный FK simulated_trades.opportunity_id заставлял
            # Postgres делать полный seq scan на КАЖДУЮ удаляемую строку
            # (см. PLAN.md 5.5). to_thread() оставлен: event loop не
            # блокируется, а остановка сервиса теперь снимает запрос через
            # handle_shutdown_signal -> history_archiver.cancel_running().
            await asyncio.to_thread(history_archiver.run_if_due)

            # Суточная сводка в лог. Через to_thread по той же причине, что и
            # архиватор: вызывалась синхронно и блокировала event loop целиком
            # на всё время своих COUNT(*) — 4 мин 45 с при каждом старте
            # процесса (замер на проде 2026-08-01, см. PLAN.md 5.5).
            await asyncio.to_thread(daily_report.log_if_due)

            # Heartbeat: итерация дошла до конца. Стоит именно здесь, ПОСЛЕ
            # всей работы цикла — так пинг доказывает, что цикл живой, а не
            # что процесс существует. Раз в сутки архивация занимает 7-10
            # минут и пингов в это время нет: heartbeat-интервал в Kuma
            # выставлен с запасом (~15 мин), иначе получали бы ложный алерт
            # каждые сутки, а монитор, который врёт по расписанию, перестают
            # читать
            await uptime_push.ping()

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
        # Отличаем неожиданный сбой (падение БД, необработанное исключение)
        # от намеренной остановки по SIGTERM/SIGINT (shutdown_event.set()) —
        # раньше оба пути завершались с exit code 0, и systemd Restart=on-failure
        # не перезапускал бота при реальном сбое (см. PLAN.md, раздел 6).
        crashed = True
        logger.exception(f"Необработанное исключение в основном цикле: {str(e)}")
    finally:
        logger.info("Завершение работы, закрытие ресурсов...")
        await health_monitor.stop_monitoring()
        await binance_api.close_session()
        await binance_futures_api.close_session()
        await kucoin_api.close_session()
        await kucoin_futures_api.close_session()
        await gate_api.close_session()
        await gate_futures_api.close_session()
        await mexc_api.close_session()
        await mexc_futures_api.close_session()
        await uptime_push.close()
        # Все репозитории работают через единое соединение — закрывается одно
        conn.close()
        logger.info("Все ресурсы успешно закрыты. Приложение завершено.")

    return 1 if crashed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))