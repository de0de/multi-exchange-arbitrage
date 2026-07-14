"""
Репозиторий истории спот-фьюч / фьюч-фьюч спредов (basis).

Таблица: futures_spread_history — см. DATA_SPECIFICATION.md, раздел 4.
Снимок funding rate встраивается в каждую строку (embedded-решение из
раздела 5 спецификации) — существующие UPSERT-таблицы funding не трогаются.

Использует единое соединение sqlite3, переданное из main.py.
"""
import logging
import sqlite3
from typing import List, Tuple


class FuturesSpreadHistoryRepository:
    """INSERT-only история basis + retention-очистка."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self.cursor = conn.cursor()
        self.logger = logging.getLogger(__name__)
        self._create_table()

    def _create_table(self):
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS futures_spread_history (
                id                 INTEGER PRIMARY KEY AUTOINCREMENT,
                standardized_pair  TEXT NOT NULL,
                comparison_type    TEXT NOT NULL,
                leg_a_exchange     TEXT NOT NULL,
                leg_a_bid          REAL,
                leg_a_ask          REAL,
                leg_b_exchange     TEXT NOT NULL,
                leg_b_bid          REAL,
                leg_b_ask          REAL,
                basis_percent      REAL,
                leg_a_funding_rate REAL,
                leg_b_funding_rate REAL,
                leg_b_next_funding_time REAL,
                is_snapshot        INTEGER DEFAULT 0,
                suspected_collision INTEGER DEFAULT 0,
                timestamp          REAL NOT NULL
            )
        """)
        self.cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_futures_spread_pair_ts
            ON futures_spread_history(standardized_pair, timestamp)
        """)
        self.conn.commit()

    def save_rows(self, rows: List[Tuple]):
        """
        Пакетный INSERT. Формат строки — порядок колонок таблицы без id:
        (standardized_pair, comparison_type, leg_a_exchange, leg_a_bid,
        leg_a_ask, leg_b_exchange, leg_b_bid, leg_b_ask, basis_percent,
        leg_a_funding_rate, leg_b_funding_rate, leg_b_next_funding_time,
        is_snapshot, suspected_collision, timestamp).
        """
        if not rows:
            return
        self.cursor.executemany("""
            INSERT INTO futures_spread_history (
                standardized_pair, comparison_type,
                leg_a_exchange, leg_a_bid, leg_a_ask,
                leg_b_exchange, leg_b_bid, leg_b_ask,
                basis_percent,
                leg_a_funding_rate, leg_b_funding_rate, leg_b_next_funding_time,
                is_snapshot, suspected_collision, timestamp
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, rows)
        self.conn.commit()

    def delete_older_than(self, cutoff_timestamp: float) -> int:
        """Retention (см. DATA_SPECIFICATION.md, раздел 6)."""
        self.cursor.execute(
            "DELETE FROM futures_spread_history WHERE timestamp < ?", (cutoff_timestamp,)
        )
        deleted = self.cursor.rowcount
        self.conn.commit()
        if deleted > 0:
            self.conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
            self.logger.info(f"futures_spread_history retention: удалено {deleted} записей")
        return deleted
