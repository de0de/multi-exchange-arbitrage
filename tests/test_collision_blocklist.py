"""
Тесты collision_blocklist.py и точки его интеграции в SpreadMonitor.scan()
(src/core/spread_monitor.py, фильтр перед сравнением ног — см. PLAN.md
раздел 6, "Коллизии в arbitrage_opportunities концентрированы...").

Без сетевых вызовов и без реальной БД: конструктор SpreadMonitor трогает
psycopg-соединение (курсор, репозитории) — здесь это MagicMock, а не
настоящая БД. _build_pairs_map() подменяется напрямую контролируемыми
данными, минуя чтение из БД целиком — тестируется именно логика фильтра
по блоклисту, а не остальной SpreadMonitor (Order Book, paper trading
и т.д. здесь не участвуют).

ВАЖНО про модель блоклиста (проверено по факту кода, не по описанию
задачи): запись в COLLISION_BLOCKLIST исключает НОГУ ЦЕЛИКОМ — цену
указанной биржи для этого тикера убирает из ВСЕХ сравнений (Gate.io
для VANRYUSDT исключается и против Binance, и против KuCoin, и против
MEXC одновременно), а не одну конкретную пару бирж. Тесты ниже это
явно проверяют.
"""
import asyncio
import time
from unittest.mock import MagicMock, patch

import pytest

from config.collision_blocklist import COLLISION_BLOCKLIST
from src.core.spread_monitor import SpreadMonitor


# --- Часть 1: сам словарь, без SpreadMonitor ---------------------------

def test_blocklist_structure():
    assert len(COLLISION_BLOCKLIST) == 8
    for ticker, exchange in COLLISION_BLOCKLIST.items():
        assert ticker.endswith("USDT")
        assert exchange in {"Gate.io", "KuCoin", "MEXC", "Binance"}


def test_blocklist_contains_todays_confirmed_tickers():
    expected = {
        "VANRYUSDT": "Gate.io",
        "EDGEUSDT": "Gate.io",
        "ELONUSDT": "Gate.io",
        "RWAUSDT": "KuCoin",
        "SIRENUSDT": "MEXC",
        "TROLLUSDT": "MEXC",
        "UPUSDT": "KuCoin",
        "VONUSDT": "MEXC",
    }
    assert COLLISION_BLOCKLIST == expected


# --- Часть 2: точка интеграции в SpreadMonitor.scan() -------------------

def _mock_conn():
    """Соединение-заглушка: cursor().fetchall() отдаёт пустой список
    (нет комиссий бирж в БД -> используются дефолты 0.1% в scan())."""
    conn = MagicMock()
    cursor = conn.cursor.return_value
    cursor.fetchall.return_value = []
    cursor.description = []
    return conn


def _row(base, quote, bid, ask, price, volume=500_000.0, ts=None):
    return {
        "original_pair": f"{base}-{quote}",
        "standardized_pair": f"{base}{quote}",
        "base_currency": base,
        "quote_currency": quote,
        "price": price,
        "volume": volume,
        "bid": bid,
        "ask": ask,
        "bid_volume": volume,
        "ask_volume": volume,
        "timestamp": ts if ts is not None else time.time(),
    }


@pytest.fixture
def monitor():
    """SpreadMonitor с замоканными БД-зависимостями (репозитории и
    соединение), без настоящих API-клиентов Order Book (apis/order_book_repos
    пустые -> _fetch_order_book_with_cache возвращает None рано, без
    обращения к order_book_collector)."""
    with patch("src.core.spread_monitor.ArbitrageOpportunityRepository"), \
         patch("src.core.spread_monitor.SpreadHistoryRepository"):
        m = SpreadMonitor(
            conn=_mock_conn(),
            apis={},
            order_book_repos={},
            order_book_collector=MagicMock(),
        )
    return m


def _pairs(exchange_prices: dict) -> dict:
    """exchange_prices: {exchange_key: (bid, ask, price)} для одного тикера."""
    now = time.time()
    return {
        exch: _row("VANRY", "USDT", bid, ask, price, ts=now)
        for exch, (bid, ask, price) in exchange_prices.items()
    }


def test_blocked_leg_excluded_from_all_comparisons(monitor):
    """
    VANRYUSDT на Gate.io — подтверждённая коллизия (блоклист). При этом
    Gate.io исключается из сравнений ПОЛНОСТЬЮ: ни против Binance, ни
    против MEXC кандидаты с Gate.io появляться не должны.
    """
    pairs_map = {
        "VANRYUSDT": {
            # Gate.io: намеренно совсем другая цена — если бы фильтр не
            # сработал, это дало бы кандидатов (возможно suspected_collision).
            "gate": _row("VANRY", "USDT", 0.0001, 0.00011, 0.000105, volume=20_000_000.0),
            "binance": _row("VANRY", "USDT", 0.0047, 0.00475, 0.004725),
            "mexc": _row("VANRY", "USDT", 0.0043, 0.00435, 0.004325),
        }
    }
    monitor._build_pairs_map = lambda: pairs_map

    results = asyncio.run(monitor.scan())

    for opp in results:
        assert opp.exchange_buy != "Gate.io"
        assert opp.exchange_sell != "Gate.io"


def test_same_ticker_on_unblocked_leg_not_excluded(monitor):
    """
    Ключевой тест: тот же VANRYUSDT на паре бирж, НЕ входящей в блок
    (MEXC <-> Binance — ни одна из них не значится как "плохая" биржа
    для VANRYUSDT) — должен нормально появиться в результатах. Это
    отличает "блок по (тикер, конкретная биржа)" от гипотетического
    "блок по тикеру целиком на любой бирже".
    """
    pairs_map = {
        "VANRYUSDT": {
            "gate": _row("VANRY", "USDT", 0.0001, 0.00011, 0.000105, volume=20_000_000.0),
            "binance": _row("VANRY", "USDT", 0.0047, 0.00475, 0.004725),
            "mexc": _row("VANRY", "USDT", 0.0043, 0.00435, 0.004325),
        }
    }
    monitor._build_pairs_map = lambda: pairs_map

    results = asyncio.run(monitor.scan())

    mexc_binance = [
        opp for opp in results
        if {opp.exchange_buy, opp.exchange_sell} == {"MEXC", "Binance"}
    ]
    assert len(mexc_binance) >= 1
    assert all(not opp.suspected_collision for opp in mexc_binance)


def test_ticker_outside_blocklist_unaffected(monitor):
    """
    AIUSDT сознательно НЕ в блоклисте (неровная доля коллизий, похоже на
    реальную волатильность — см. PLAN.md). Фильтр не должен его трогать:
    большой синтетический спред между Gate.io и Binance должен дать
    кандидата с suspected_collision=True, а не быть тихо исключённым.
    """
    pairs_map = {
        "AIUSDT": {
            # volume поднят относительно других тестов: при цене ~0.001-0.01
            # 500k монет не хватает для прохождения min_volume_usdt=1000 USDT
            "gate": _row("AI", "USDT", 0.001, 0.0011, 0.00105, volume=2_000_000.0),
            "binance": _row("AI", "USDT", 0.01, 0.0105, 0.01025, volume=2_000_000.0),
        }
    }
    monitor._build_pairs_map = lambda: pairs_map

    results = asyncio.run(monitor.scan())

    ai_candidates = [opp for opp in results if opp.standardized_pair == "AIUSDT"]
    assert len(ai_candidates) >= 1
    assert any(opp.suspected_collision for opp in ai_candidates)
    # обе биржи по-прежнему участвуют в сравнении (не вырезаны фильтром)
    involved_exchanges = {opp.exchange_buy for opp in ai_candidates} | {
        opp.exchange_sell for opp in ai_candidates
    }
    assert "Gate.io" in involved_exchanges
    assert "Binance" in involved_exchanges
