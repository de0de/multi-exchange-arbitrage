import os
import sqlite3
import logging
from datetime import datetime
from typing import List

from src.core.models.funding_rate import FundingRateData


class FundingRateRepository:
    """Репозиторий для хранения funding rate фьючерсных пар.

    Таблица: {exchange}_funding_rates
    Схема повторяет паттерн MarketRepository, но для данных funding rate.
    """

    def __init__(self, db_path: str, exchange_name: str):
        self.db_path = db_path
        self.exchange_name = exchange_name.lower()
        self.conn: sqlite3.Connection
        self.logger = logging.getLogger(__name__)
        self._connect()
        self._create_table()

    def _connect(self):
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.execute("PRAGMA journal_mode=WAL;")
        self.cursor = self.conn.cursor()

    def _create_table(self):
        table_name = f"{self.exchange_name}_funding_rates"
        self.cursor.execute(f'''
            CREATE TABLE IF NOT EXISTS {table_name} (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                exchange_id INTEGER,
                original_pair TEXT,
                standardized_pair TEXT,
                pair_id INTEGER,
                funding_rate REAL,
                funding_interval_hours REAL,
                mark_price REAL,
                next_funding_time REAL,
                timestamp REAL,
                readable_time TEXT,
                FOREIGN KEY (exchange_id) REFERENCES exchanges(id),
                FOREIGN KEY (pair_id) REFERENCES unique_pairs(id),
                UNIQUE(exchange_id, original_pair)
            )
        ''')
        self.conn.commit()

    def save_funding_rates(self, rates: List[FundingRateData]):
        """UPSERT: обновляет существующую или вставляет новую запись."""
        table_name = f"{self.exchange_name}_funding_rates"
        now = datetime.now().timestamp()
        readable = datetime.fromtimestamp(now).strftime('%Y-%m-%d %H:%M:%S')

        for rate in rates:
            # Получаем exchange_id через прямой запрос (другой репозиторий не передаём)
            self.cursor.execute(
                'SELECT id FROM exchanges WHERE name = ?', (rate.exchange,)
            )
            row = self.cursor.fetchone()
            if row:
                exchange_id = row[0]
            else:
                self.logger.warning(f"Exchange '{rate.exchange}' not found in database, skipping")
                continue

            ts = rate.timestamp if rate.timestamp is not None else now
            rt = rate.readable_time if rate.readable_time is not None else readable

            self.cursor.execute(f'''
                UPDATE {table_name}
                SET funding_rate = ?, funding_interval_hours = ?, mark_price = ?,
                    next_funding_time = ?, timestamp = ?, readable_time = ?
                WHERE exchange_id = ? AND original_pair = ?
            ''', (
                rate.funding_rate, rate.funding_interval_hours, rate.mark_price,
                rate.next_funding_time, ts, rt,
                exchange_id, rate.original_pair
            ))
            if self.cursor.rowcount == 0:
                self.cursor.execute(f'''
                    INSERT INTO {table_name} (
                        exchange_id, original_pair, standardized_pair,
                        funding_rate, funding_interval_hours, mark_price,
                        next_funding_time, timestamp, readable_time
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    exchange_id, rate.original_pair, rate.standardized_pair,
                    rate.funding_rate, rate.funding_interval_hours, rate.mark_price,
                    rate.next_funding_time, ts, rt
                ))
        self.conn.commit()
        self.logger.debug(f"Saved {len(rates)} funding rates in the database")

    def close(self):
        self.conn.close()
        self.logger.info("FundingRateRepository connection closed")