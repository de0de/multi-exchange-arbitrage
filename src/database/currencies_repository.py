import logging
from typing import List

import psycopg

from src.core.models.currencies import Currency

class CurrenciesRepository:
    def __init__(self, conn: psycopg.Connection):
        self.conn = conn
        self.cursor = self.conn.cursor()
        self.logger = logging.getLogger(__name__)
        self.create_table()

    def create_table(self):
        try:
            self.cursor.execute('''
                CREATE TABLE IF NOT EXISTS currencies (
                    id BIGSERIAL PRIMARY KEY,
                    name TEXT UNIQUE NOT NULL
                )
            ''')
            self.conn.commit()
            self.logger.info("Currencies table created successfully")
        except psycopg.Error as e:
            self.conn.rollback()
            self.logger.error(f"Error creating currencies table: {e}")

    def extract_unique_currencies(self):
        try:
            trading_tables = self.get_trading_tables()
            unique_currencies = set()
            for table in trading_tables:
                self.logger.debug(f"Extracting base currencies from {table}")
                self.cursor.execute(f'SELECT DISTINCT base_currency FROM {table}')
                currencies = {row[0] for row in self.cursor.fetchall()}
                unique_currencies.update(currencies)
            return unique_currencies
        except psycopg.Error as e:
            self.conn.rollback()
            self.logger.error(f"Error extracting unique currencies: {e}")
            return set()

    def populate_currencies_table(self, currencies: List[str]):
        try:
            # Пакетно: построчный SELECT+INSERT давал тысячи round-trip'ов
            self.cursor.executemany(
                'INSERT INTO currencies (name) VALUES (%s) ON CONFLICT (name) DO NOTHING',
                [(c,) for c in currencies if c is not None]
            )
            self.conn.commit()
            self.logger.info(f"Inserted {len(currencies)} unique currencies into the table")
        except psycopg.Error as e:
            self.conn.rollback()
            self.logger.error(f"Error inserting currencies: {e}")

    def get_trading_tables(self):
        self.cursor.execute("""
            SELECT table_name FROM information_schema.tables
            WHERE table_schema = 'public' AND table_name LIKE '%trading_pairs'
        """)
        tables = [row[0] for row in self.cursor.fetchall()]
        self.logger.debug(f"Found trading pair tables: {tables}")
        return tables
