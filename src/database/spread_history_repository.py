"""
Репозиторий истории спредов (spot-spot агрегаты по паре).

Таблица: spread_history — см. DATA_SPECIFICATION.md, раздел 3.
Одна строка на пару за цикл: лучший bid/ask среди бирж (НЕ сырые котировки
по каждой бирже — решение по объёму, раздел 2 спецификации).

Использует единое соединение sqlite3, переданное из main.py.
"""
import logging
import sqlite3
from typing import List, Tuple


class SpreadHistoryRepository:
    """INSERT-only история спредов + retention-очистка."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self.cursor = conn.cursor()
        self.logger = logging.getLogger(__name__)
        self._create_table()

    def _create_table(self):
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS spread_history (
                id                 INTEGER PRIMARY KEY AUTOINCREMENT,
                standardized_pair  TEXT NOT NULL,
                best_bid           REAL,
                best_bid_exchange  TEXT,
                best_ask           REAL,
                best_ask_exchange  TEXT,
                raw_spread_percent REAL,
                n_exchanges        INTEGER,
                is_snapshot        INTEGER DEFAULT 0,
                suspected_collision INTEGER DEFAULT 0,
                timestamp          REAL NOT NULL
            )
        """)
        self.cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_spread_history_pair_ts
            ON spread_history(standardized_pair, timestamp)
        """)
        self.conn.commit()

    def save_rows(self, rows: List[Tuple]):
        """
        Пакетный INSERT строк истории (executemany, один commit на цикл).

        Формат строки: (standardized_pair, best_bid, best_bid_exchange,
        best_ask, best_ask_exchange, raw_spread_percent, n_exchanges,
        is_snapshot, suspected_collision, timestamp).
        """
        if not rows:
            return
        self.cursor.executemany("""
            INSERT INTO spread_history (
                standardized_pair, best_bid, best_bid_exchange,
                best_ask, best_ask_exchange, raw_spread_percent,
                n_exchanges, is_snapshot, suspected_collision, timestamp
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, rows)
        self.conn.commit()

    def delete_older_than(self, cutoff_timestamp: float) -> int:
        """
        Retention: удаляет записи старше cutoff. Возвращает число удалённых.

        Блокирующее условие VPS-прогона (DATA_SPECIFICATION.md, раздел 6):
        без очистки история заполняет диск за считанные недели.
        """
        self.cursor.execute(
            "DELETE FROM spread_history WHERE timestamp < ?", (cutoff_timestamp,)
        )
        deleted = self.cursor.rowcount
        self.conn.commit()
        if deleted > 0:
            self.conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
            self.logger.info(f"spread_history retention: удалено {deleted} записей")
        return deleted
