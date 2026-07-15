"""
Репозиторий истории спредов (spot-spot агрегаты по паре).

Таблица: spread_history — см. DATA_SPECIFICATION.md, раздел 3.
Одна строка на пару за цикл: лучший bid/ask среди бирж (НЕ сырые котировки
по каждой бирже — решение по объёму, раздел 2 спецификации).

Использует единое соединение (psycopg), переданное из main.py.
"""
import logging
from typing import List, Tuple

import psycopg


class SpreadHistoryRepository:
    """INSERT-only история спредов + retention-очистка."""

    def __init__(self, conn: psycopg.Connection):
        self.conn = conn
        self.cursor = conn.cursor()
        self.logger = logging.getLogger(__name__)
        self._create_table()

    def _create_table(self):
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS spread_history (
                id                 BIGSERIAL PRIMARY KEY,
                standardized_pair  TEXT NOT NULL,
                best_bid           DOUBLE PRECISION,
                best_bid_exchange  TEXT,
                best_ask           DOUBLE PRECISION,
                best_ask_exchange  TEXT,
                raw_spread_percent DOUBLE PRECISION,
                n_exchanges        INTEGER,
                is_snapshot        INTEGER DEFAULT 0,
                suspected_collision INTEGER DEFAULT 0,
                timestamp          DOUBLE PRECISION NOT NULL
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
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, rows)
        self.conn.commit()

    def delete_older_than(self, cutoff_timestamp: float) -> int:
        """
        Retention: удаляет записи старше cutoff. Возвращает число удалённых.

        Используется HistoryArchiver ПОСЛЕ экспорта строк в .csv.gz
        (DATA_SPECIFICATION.md, раздел 6).
        """
        self.cursor.execute(
            "DELETE FROM spread_history WHERE timestamp < %s", (cutoff_timestamp,)
        )
        deleted = self.cursor.rowcount
        self.conn.commit()
        if deleted > 0:
            self.logger.info(f"spread_history retention: удалено {deleted} записей")
        return deleted
