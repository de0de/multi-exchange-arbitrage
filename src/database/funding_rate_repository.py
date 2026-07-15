import logging
from datetime import datetime
from typing import List

import psycopg

from src.core.models.funding_rate import FundingRateData


class FundingRateRepository:
    """Репозиторий для хранения funding rate фьючерсных пар.

    Таблица: {exchange}_funding_rates
    Схема повторяет паттерн MarketRepository, но для данных funding rate.
    """

    def __init__(self, conn: psycopg.Connection, exchange_name: str):
        self.conn = conn
        self.cursor = conn.cursor()
        self.exchange_name = exchange_name.lower()
        self.logger = logging.getLogger(__name__)
        self._create_table()

    def _create_table(self):
        table_name = f"{self.exchange_name}_funding_rates"
        self.cursor.execute(f'''
            CREATE TABLE IF NOT EXISTS {table_name} (
                id BIGSERIAL PRIMARY KEY,
                exchange_id BIGINT,
                original_pair TEXT,
                standardized_pair TEXT,
                pair_id BIGINT,
                funding_rate DOUBLE PRECISION,
                funding_interval_hours DOUBLE PRECISION,
                mark_price DOUBLE PRECISION,
                next_funding_time DOUBLE PRECISION,
                timestamp DOUBLE PRECISION,
                readable_time TEXT,
                UNIQUE(exchange_id, original_pair)
            )
        ''')
        self.conn.commit()

    def save_funding_rates(self, rates: List[FundingRateData]):
        """Пакетный UPSERT (INSERT ... ON CONFLICT DO UPDATE, один executemany)."""
        if not rates:
            return
        table_name = f"{self.exchange_name}_funding_rates"
        now = datetime.now().timestamp()
        readable = datetime.fromtimestamp(now).strftime('%Y-%m-%d %H:%M:%S')

        # Все ставки в списке — от одной биржи (один API-клиент на репозиторий)
        self.cursor.execute(
            'SELECT id FROM exchanges WHERE name = %s', (rates[0].exchange,)
        )
        row = self.cursor.fetchone()
        if not row:
            self.logger.warning(f"Exchange '{rates[0].exchange}' not found in database, skipping")
            return
        exchange_id = row[0]

        rows = [
            (
                exchange_id, rate.original_pair, rate.standardized_pair,
                rate.funding_rate, rate.funding_interval_hours, rate.mark_price,
                rate.next_funding_time,
                rate.timestamp if rate.timestamp is not None else now,
                rate.readable_time if rate.readable_time is not None else readable,
            )
            for rate in rates
        ]
        self.cursor.executemany(f'''
            INSERT INTO {table_name} (
                exchange_id, original_pair, standardized_pair,
                funding_rate, funding_interval_hours, mark_price,
                next_funding_time, timestamp, readable_time
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (exchange_id, original_pair) DO UPDATE SET
                funding_rate = EXCLUDED.funding_rate,
                funding_interval_hours = EXCLUDED.funding_interval_hours,
                mark_price = EXCLUDED.mark_price,
                next_funding_time = EXCLUDED.next_funding_time,
                timestamp = EXCLUDED.timestamp,
                readable_time = EXCLUDED.readable_time
        ''', rows)
        self.conn.commit()
        self.logger.debug(f"Saved {len(rates)} funding rates in the database")
