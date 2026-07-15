"""
Репозиторий истории изменений funding rate.

Таблица: funding_rate_history — см. DATA_SPECIFICATION.md, раздел 5.
INSERT происходит с фильтром дрейфа (правило — в FuturesSpreadMonitor).
Существующие UPSERT-таблицы {exchange}_funding_rates не изменяются.

Использует единое соединение (psycopg), переданное из main.py.
"""
import logging
from typing import Dict, List, Tuple

import psycopg


class FundingRateHistoryRepository:
    """INSERT-on-change история funding rate."""

    def __init__(self, conn: psycopg.Connection):
        self.conn = conn
        self.cursor = conn.cursor()
        self.logger = logging.getLogger(__name__)
        self._create_table()

    def _create_table(self):
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS funding_rate_history (
                id            BIGSERIAL PRIMARY KEY,
                exchange      TEXT NOT NULL,
                original_pair TEXT NOT NULL,
                funding_rate  DOUBLE PRECISION NOT NULL,
                next_funding_time DOUBLE PRECISION,
                timestamp     DOUBLE PRECISION NOT NULL
            )
        """)
        self.cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_funding_history_pair_ts
            ON funding_rate_history(exchange, original_pair, timestamp)
        """)
        self.conn.commit()

    def load_last_rates(self) -> Dict[Tuple[str, str], Tuple[float, float]]:
        """
        Последняя записанная ставка и её момент по каждому контракту —
        чтобы рестарт процесса не порождал дубликаты «изменений».

        Возвращает {(exchange, original_pair): (funding_rate, timestamp)}.
        """
        self.cursor.execute("""
            SELECT f.exchange, f.original_pair, f.funding_rate, f.timestamp
            FROM funding_rate_history f
            JOIN (
                SELECT MAX(id) AS mid FROM funding_rate_history
                GROUP BY exchange, original_pair
            ) m ON f.id = m.mid
        """)
        return {(ex, pair): (rate, ts) for ex, pair, rate, ts in self.cursor.fetchall()}

    def save_changes(self, rows: List[Tuple]):
        """
        Пакетный INSERT изменившихся ставок.
        Формат: (exchange, original_pair, funding_rate, next_funding_time, timestamp).
        """
        if not rows:
            return
        self.cursor.executemany("""
            INSERT INTO funding_rate_history (
                exchange, original_pair, funding_rate, next_funding_time, timestamp
            ) VALUES (%s, %s, %s, %s, %s)
        """, rows)
        self.conn.commit()
        self.logger.debug(f"funding_rate_history: записано {len(rows)} изменений ставок")
