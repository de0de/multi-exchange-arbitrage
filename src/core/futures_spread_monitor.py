"""
Мониторинг спот-фьюч и фьюч-фьюч расхождений (basis).

ТОЛЬКО детекция и запись в futures_spread_history — без симуляции:
Фаза 2 paper trading (funding-driven hold/close) проектируется позже
на данных, которые копит этот монитор (см. DATA_SPECIFICATION.md, п.4,
и QUESTIONS.md Q-004).

Источники — те же таблицы БД, что у SpreadMonitor/коллекторов:
- ноги читаются из {exchange}_trading_pairs по колонке standardized_pair,
  уже нормализованной коллекторами при записи (XBT→BTC для KuCoin Futures
  выполняет API-класс — здесь стандартизация не дублируется);
- снимок funding rate берётся из UPSERT-таблиц {exchange}_funding_rates,
  которые main loop обновляет ДО вызова scan() — без новых HTTP-запросов.

Дополнительно ведёт funding_rate_history: INSERT при изменении ставки.

Коллизии тикеров возможны и здесь (сопоставление по текстовому
standardized_pair): |basis| >= порога -> флаг suspected_collision.
"""
import logging
import sqlite3
import time
from typing import Dict, List, Optional, Tuple

from src.database.futures_spread_history_repository import FuturesSpreadHistoryRepository
from src.database.funding_rate_history_repository import FundingRateHistoryRepository


class FuturesSpreadMonitor:
    """
    Синхронный монитор basis: читает БД, пишет историю. Вызывается из
    main loop после обновления funding rate и сбора котировок.
    """

    # (нога A, нога B): спот↔фьюч в рамках одной биржи
    SPOT_FUTURES_PAIRS: List[Tuple[str, str]] = [
        ("binance", "binance_futures"),
        ("kucoin", "kucoin_futures"),
    ]
    # фьюч↔фьюч между биржами
    FUTURES_FUTURES_PAIRS: List[Tuple[str, str]] = [
        ("binance_futures", "kucoin_futures"),
    ]
    # Таблицы funding (UPSERT, обновляются main loop каждый цикл)
    FUNDING_EXCHANGES: List[str] = ["binance_futures", "kucoin_futures"]

    def __init__(
        self,
        conn: sqlite3.Connection,
        min_basis_percent: float = 0.2,
        snapshot_interval: float = 300.0,
        retention_days: float = 14.0,
        max_staleness_seconds: float = 15.0,
        allowed_quote_currencies: Optional[List[str]] = None,
        suspected_collision_threshold_percent: float = 20.0,
        funding_material_delta: float = 1e-4,
        funding_drift_delta: float = 1e-6,
        funding_drift_min_interval: float = 1800.0,
    ):
        self.conn = conn
        self.cursor = conn.cursor()
        self.logger = logging.getLogger(__name__)

        self.min_basis_percent = min_basis_percent
        self.snapshot_interval = snapshot_interval
        self.retention_days = retention_days
        self.max_staleness_seconds = max_staleness_seconds
        self.allowed_quote_currencies = allowed_quote_currencies or ["USDT", "USDC", "BTC", "ETH"]
        self.collision_threshold = suspected_collision_threshold_percent

        self.history_repo = FuturesSpreadHistoryRepository(conn)
        self.funding_history_repo = FundingRateHistoryRepository(conn)

        # Правило записи изменений funding: биржи отдают ПРОГНОЗНЫЕ ставки,
        # которые дрейфуют на 1e-6..1e-5 каждый цикл (замер 2026-07-14:
        # ~45 «изменений»/цикл без фильтра = ~780 тыс. строк/сутки).
        # Материальное изменение (>= material_delta) пишется сразу,
        # мелкий дрейф — не чаще раза в drift_min_interval на контракт.
        self.funding_material_delta = funding_material_delta
        self.funding_drift_delta = funding_drift_delta
        self.funding_drift_min_interval = funding_drift_min_interval

        self._last_snapshot = 0.0
        self._last_retention_check = 0.0
        # Последние записанные ставки (+момент записи) — восстанавливаются
        # из истории, чтобы рестарт не порождал дубликаты «изменений»
        self._last_funding: Dict[Tuple[str, str], Tuple[float, float]] = \
            self.funding_history_repo.load_last_rates()

    # ------------------------------------------------------------------
    # Чтение источников
    # ------------------------------------------------------------------

    def _read_legs(self, exchange_key: str, now: float) -> Dict[str, dict]:
        """
        Свежие ноги из {exchange_key}_trading_pairs:
        {standardized_pair: {bid, ask, quote_currency}}.
        Протухшие (старше max_staleness_seconds) и без цен — отбрасываются.
        """
        table = f"{exchange_key}_trading_pairs"
        try:
            self.cursor.execute(f"""
                SELECT standardized_pair, bid, ask, quote_currency, timestamp
                FROM {table}
            """)
        except sqlite3.OperationalError as e:
            self.logger.warning(f"FuturesSpreadMonitor: не читается {table}: {e}")
            return {}

        legs = {}
        for pair, bid, ask, quote, ts in self.cursor.fetchall():
            if pair is None or bid is None or ask is None or bid <= 0 or ask <= 0:
                continue
            if ts is None or (now - ts) > self.max_staleness_seconds:
                continue
            if quote not in self.allowed_quote_currencies:
                continue
            legs[pair] = {"bid": bid, "ask": ask, "quote": quote}
        return legs

    def _read_funding(self) -> Dict[Tuple[str, str], dict]:
        """
        Текущие funding rate из UPSERT-таблиц:
        {(exchange_key, standardized_pair): {rate, next_time, original_pair}}.
        """
        funding = {}
        for exchange_key in self.FUNDING_EXCHANGES:
            table = f"{exchange_key}_funding_rates"
            try:
                self.cursor.execute(f"""
                    SELECT standardized_pair, original_pair, funding_rate, next_funding_time
                    FROM {table}
                """)
            except sqlite3.OperationalError as e:
                self.logger.debug(f"FuturesSpreadMonitor: не читается {table}: {e}")
                continue
            for std_pair, orig_pair, rate, next_time in self.cursor.fetchall():
                if std_pair is None or rate is None:
                    continue
                funding[(exchange_key, std_pair)] = {
                    "rate": rate,
                    "next_time": next_time,
                    "original_pair": orig_pair,
                }
        return funding

    # ------------------------------------------------------------------
    # Основной цикл
    # ------------------------------------------------------------------

    def scan(self):
        """Один проход: запись basis-истории + отслеживание изменений funding."""
        now = time.time()
        snapshot_due = (now - self._last_snapshot) >= self.snapshot_interval

        funding = self._read_funding()
        self._record_funding_changes(funding, now)

        legs_cache: Dict[str, Dict[str, dict]] = {}

        def legs(exchange_key: str) -> Dict[str, dict]:
            if exchange_key not in legs_cache:
                legs_cache[exchange_key] = self._read_legs(exchange_key, now)
            return legs_cache[exchange_key]

        rows: List[tuple] = []
        comparisons = (
            [("spot_futures", a, b) for a, b in self.SPOT_FUTURES_PAIRS]
            + [("futures_futures", a, b) for a, b in self.FUTURES_FUTURES_PAIRS]
        )

        for comparison_type, a_key, b_key in comparisons:
            legs_a = legs(a_key)
            legs_b = legs(b_key)
            for std_pair in legs_a.keys() & legs_b.keys():
                a = legs_a[std_pair]
                b = legs_b[std_pair]
                mid_a = (a["bid"] + a["ask"]) / 2.0
                mid_b = (b["bid"] + b["ask"]) / 2.0
                if mid_a <= 0:
                    continue
                basis = (mid_b - mid_a) / mid_a * 100.0

                if not snapshot_due and abs(basis) < self.min_basis_percent:
                    continue

                fund_a = funding.get((a_key, std_pair)) if comparison_type == "futures_futures" else None
                fund_b = funding.get((b_key, std_pair))

                rows.append((
                    std_pair,
                    comparison_type,
                    a_key,
                    a["bid"], a["ask"],
                    b_key,
                    b["bid"], b["ask"],
                    basis,
                    fund_a["rate"] if fund_a else None,
                    fund_b["rate"] if fund_b else None,
                    fund_b["next_time"] if fund_b else None,
                    1 if snapshot_due else 0,
                    1 if abs(basis) >= self.collision_threshold else 0,
                    now,
                ))

        try:
            self.history_repo.save_rows(rows)
            if snapshot_due:
                self._last_snapshot = now
                self.logger.info(
                    f"futures_spread_history: полный снэпшот, записано {len(rows)} строк"
                )
            elif rows:
                self.logger.debug(f"futures_spread_history: записано {len(rows)} строк")

            if now - self._last_retention_check >= 86400:
                self._last_retention_check = now
                self.history_repo.delete_older_than(now - self.retention_days * 86400)
        except sqlite3.Error as e:
            self.logger.error(f"futures_spread_history: ошибка записи: {e}")

    def _record_funding_changes(self, funding: Dict[Tuple[str, str], dict], now: float):
        """
        INSERT в funding_rate_history для изменившихся ставок.

        Фильтр дрейфа: материальное изменение (>= funding_material_delta)
        пишется сразу; мелкий дрейф (>= funding_drift_delta) — только если
        с последней записи по контракту прошло funding_drift_min_interval.
        """
        changes = []
        for (exchange_key, std_pair), info in funding.items():
            key = (exchange_key, info["original_pair"] or std_pair)
            prev = self._last_funding.get(key)
            if prev is not None:
                prev_rate, prev_ts = prev
                delta = abs(info["rate"] - prev_rate)
                if delta < self.funding_drift_delta:
                    continue
                if delta < self.funding_material_delta and (now - prev_ts) < self.funding_drift_min_interval:
                    continue
            self._last_funding[key] = (info["rate"], now)
            changes.append((exchange_key, key[1], info["rate"], info["next_time"], now))
        try:
            self.funding_history_repo.save_changes(changes)
        except sqlite3.Error as e:
            self.logger.error(f"funding_rate_history: ошибка записи: {e}")
