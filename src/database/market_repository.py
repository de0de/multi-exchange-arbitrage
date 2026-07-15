import logging
from typing import List

import psycopg

from src.database.base_repository import BaseRepository
from src.core.models.pair_data import PairData

class MarketRepository(BaseRepository):
    def __init__(self, conn: psycopg.Connection, exchange_name: str):
        self.conn = conn
        self.cursor = conn.cursor()
        self.exchange_name = exchange_name.lower()
        self.logger = logging.getLogger(__name__)
        self._create_table()
        self._migrate_existing_tables()

    def _create_table(self):
        # FK-клаузы из SQLite-версии не переносятся: SQLite их никогда не
        # проверял (enforcement выключен по умолчанию), а в PostgreSQL они
        # ломали бы порядок создания таблиц (exchanges создаётся позже)
        table_name = f"{self.exchange_name}_trading_pairs"
        self.cursor.execute(f'''
            CREATE TABLE IF NOT EXISTS {table_name} (
                id BIGSERIAL PRIMARY KEY,
                exchange_id BIGINT,
                original_pair TEXT,
                standardized_pair TEXT,
                pair_id BIGINT,
                base_currency TEXT,
                base_currency_id BIGINT,
                quote_currency TEXT,
                quote_currency_id BIGINT,
                price DOUBLE PRECISION,
                volume DOUBLE PRECISION,
                bid DOUBLE PRECISION,
                ask DOUBLE PRECISION,
                bid_volume DOUBLE PRECISION,
                ask_volume DOUBLE PRECISION,
                multiplier DOUBLE PRECISION DEFAULT 1.0,
                lot_size DOUBLE PRECISION,
                timestamp DOUBLE PRECISION,
                readable_time TEXT,
                UNIQUE(exchange_id, original_pair)
            )
        ''')
        self.conn.commit()

    def _migrate_existing_tables(self):
        """ALTER TABLE для таблиц, созданных до добавления multiplier/lot_size."""
        table_name = f"{self.exchange_name}_trading_pairs"
        for column, col_type in [("multiplier", "DOUBLE PRECISION DEFAULT 1.0"), ("lot_size", "DOUBLE PRECISION")]:
            self.cursor.execute(f"ALTER TABLE {table_name} ADD COLUMN IF NOT EXISTS {column} {col_type}")
        self.conn.commit()

    def get_or_create_exchange_id(self, exchange_name: str) -> int:
        self.cursor.execute('SELECT id FROM exchanges WHERE name = %s', (exchange_name,))
        result = self.cursor.fetchone()
        if result:
            return result[0]
        else:
            self.cursor.execute('INSERT INTO exchanges (name) VALUES (%s) RETURNING id', (exchange_name,))
            exchange_id = self.cursor.fetchone()[0]
            self.conn.commit()
            return exchange_id

    def save_trading_pairs(self, pairs: List[PairData]):
        """
        Пакетный UPSERT (INSERT ... ON CONFLICT DO UPDATE, один executemany).

        Построчный UPDATE+INSERT из SQLite-версии на PostgreSQL давал
        ~25 тыс. round-trip'ов за цикл и растягивал цикл до 40+ секунд.
        Колонки pair_id/base_currency_id/quote_currency_id при конфликте
        не перезаписываются (их заполняют update_pair_ids/update_currency_ids).
        """
        if not pairs:
            return
        table_name = f"{self.exchange_name}_trading_pairs"
        exchange_id = self.get_or_create_exchange_id(pairs[0].exchange)
        rows = [
            (
                exchange_id, pair.original_pair, pair.standardized_pair, pair.base_currency,
                pair.quote_currency, pair.price, pair.volume, pair.bid, pair.ask,
                pair.bid_volume, pair.ask_volume,
                pair.multiplier, pair.lot_size,
                pair.timestamp, pair.readable_time
            )
            for pair in pairs
        ]
        self.cursor.executemany(f'''
            INSERT INTO {table_name} (
                exchange_id, original_pair, standardized_pair, base_currency,
                quote_currency, price, volume, bid, ask, bid_volume, ask_volume,
                multiplier, lot_size, timestamp, readable_time
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (exchange_id, original_pair) DO UPDATE SET
                standardized_pair = EXCLUDED.standardized_pair,
                base_currency = EXCLUDED.base_currency,
                quote_currency = EXCLUDED.quote_currency,
                price = EXCLUDED.price,
                volume = EXCLUDED.volume,
                bid = EXCLUDED.bid,
                ask = EXCLUDED.ask,
                bid_volume = EXCLUDED.bid_volume,
                ask_volume = EXCLUDED.ask_volume,
                multiplier = EXCLUDED.multiplier,
                lot_size = EXCLUDED.lot_size,
                timestamp = EXCLUDED.timestamp,
                readable_time = EXCLUDED.readable_time
        ''', rows)
        self.conn.commit()
        self.logger.debug(f"Updated {len(pairs)} trading pairs in the database")

    def update_currency_ids(self):
        table_name = f"{self.exchange_name}_trading_pairs"
        try:
            self.cursor.execute(f'''
                UPDATE {table_name}
                SET base_currency_id = (
                    SELECT id FROM currencies WHERE currencies.name = {table_name}.base_currency
                ),
                quote_currency_id = (
                    SELECT id FROM currencies WHERE currencies.name = {table_name}.quote_currency
                )
            ''')
            self.conn.commit()
            self.logger.info("Updated currency_id values in trading pairs table")
        except psycopg.Error as e:
            self.conn.rollback()
            self.logger.error(f"Error updating currency_id values: {e}")

    def update_pair_ids(self):
        table_name = f"{self.exchange_name}_trading_pairs"
        try:
            self.logger.info(f"Updating pair_id in {table_name}")
            self.cursor.execute(f'''
                UPDATE {table_name}
                SET pair_id = (
                    SELECT id
                    FROM unique_pairs
                    WHERE unique_pairs.standardized_pair = {table_name}.standardized_pair
                )
            ''')
            self.conn.commit()
            self.logger.info(f"Successfully updated pair_id values in {table_name}")
        except psycopg.Error as e:
            self.conn.rollback()
            self.logger.error(f"Error updating pair_id values in {table_name}: {e}")

    def get_trading_tables(self):
        self.cursor.execute("""
            SELECT table_name FROM information_schema.tables
            WHERE table_schema = 'public' AND table_name LIKE '%trading_pairs'
        """)
        tables = [row[0] for row in self.cursor.fetchall()]
        self.logger.info(f"Found trading pair tables: {tables}")
        return tables
