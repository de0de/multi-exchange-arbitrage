"""
Ежесуточная сводка работы бота в лог.

Одна INFO-сводка в сутки (первая — сразу при старте процесса): счётчики за
последние 24 часа по всем потокам данных и paper trading + размер файла БД.
Нужна для автономного прогона на VPS: состояние системы читается из лога
за секунды, без ручных SQL-запросов.
"""
import logging
import os
import sqlite3
import time
from typing import Optional


class DailyReport:
    """Сводка по БД раз в сутки; вызывается из main loop дешёвой проверкой."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        db_path: str,
        interval: float = 86400.0,
    ):
        self.conn = conn
        self.cursor = conn.cursor()
        self.db_path = db_path
        self.interval = interval
        self.logger = logging.getLogger(__name__)
        self._last_run = 0.0

    def log_if_due(self, now: Optional[float] = None) -> bool:
        now = now if now is not None else time.time()
        if now - self._last_run < self.interval:
            return False
        self._last_run = now
        try:
            self._log_report(now - 86400)
        except sqlite3.Error as e:
            self.logger.error(f"DailyReport: ошибка построения сводки: {e}")
        return True

    def _count(self, sql: str, params: tuple = ()) -> int:
        self.cursor.execute(sql, params)
        row = self.cursor.fetchone()
        return row[0] if row and row[0] is not None else 0

    def _log_report(self, since: float):
        opps = self._count(
            "SELECT COUNT(*) FROM arbitrage_opportunities WHERE timestamp > ?", (since,))
        collisions = self._count(
            "SELECT COUNT(*) FROM arbitrage_opportunities WHERE timestamp > ? AND suspected_collision = 1", (since,))
        spread_rows = self._count(
            "SELECT COUNT(*) FROM spread_history WHERE timestamp > ?", (since,))
        futures_rows = self._count(
            "SELECT COUNT(*) FROM futures_spread_history WHERE timestamp > ?", (since,))
        funding_rows = self._count(
            "SELECT COUNT(*) FROM funding_rate_history WHERE timestamp > ?", (since,))
        opened = self._count(
            "SELECT COUNT(*) FROM simulated_trades WHERE entry_detected_at > ?", (since,))
        open_now = self._count(
            "SELECT COUNT(*) FROM simulated_trades WHERE status = 'open'")

        self.cursor.execute("""
            SELECT outcome, COUNT(*), ROUND(AVG(realized_profit_percent), 3)
            FROM simulated_trades WHERE closed_at > ? GROUP BY outcome
        """, (since,))
        outcomes = self.cursor.fetchall()

        try:
            db_mb = os.path.getsize(self.db_path) / (1024 * 1024)
        except OSError:
            db_mb = 0.0

        if outcomes:
            closed_str = ", ".join(
                f"{outcome}: {n}" + (f" (avg {avg:+.3f}%)" if avg is not None else "")
                for outcome, n, avg in outcomes
            )
        else:
            closed_str = "0"

        self.logger.info(
            f"СУТОЧНАЯ СВОДКА (последние 24 ч):\n"
            f"    возможности: {opps} (из них коллизий: {collisions})\n"
            f"    история: spread={spread_rows}, futures={futures_rows}, "
            f"funding_changes={funding_rows}\n"
            f"    paper trading: открыто {opened}, сейчас открытых позиций {open_now}\n"
            f"    закрыто за сутки: {closed_str}\n"
            f"    размер БД: {db_mb:.0f} МБ"
        )
