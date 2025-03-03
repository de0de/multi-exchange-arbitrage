import sqlite3
import logging
from typing import List
from src.core.models.currencies import Currency

class CurrenciesRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self.cursor = self.conn.cursor()
        self.logger = logging.getLogger(__name__)
        self.create_table()

    def create_table(self):
        try:
            with self.conn:
                self.cursor.execute('''
                    CREATE TABLE IF NOT EXISTS currencies (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        name TEXT UNIQUE NOT NULL
                    )
                ''')
            self.logger.info("Currencies table created successfully")
        except sqlite3.Error as e:
            self.logger.error(f"Error creating currencies table: {e}")

    def extract_unique_currencies(self):
        try:
            trading_tables = self.get_trading_tables()
            unique_currencies = set()
            for table in trading_tables:
                self.logger.info(f"Extracting base currencies from {table}")
                self.cursor.execute(f'SELECT DISTINCT base_currency FROM {table}')
                currencies = {row[0] for row in self.cursor.fetchall()}
                unique_currencies.update(currencies)
            return unique_currencies
        except sqlite3.Error as e:
            self.logger.error(f"Error extracting unique currencies: {e}")
            return set()

    def populate_currencies_table(self, currencies: List[str]):
        try:
            with self.conn:
                for currency in currencies:
                    self.cursor.execute('SELECT id FROM currencies WHERE name = ?', (currency,))
                    if not self.cursor.fetchone():
                        self.cursor.execute('INSERT INTO currencies (name) VALUES (?)', (currency,))
            self.conn.commit()
            self.logger.info(f"Inserted {len(currencies)} unique currencies into the table")
        except sqlite3.Error as e:
            self.logger.error(f"Error inserting currencies: {e}")

    def close(self):
        self.conn.close()
        self.logger.info("Database connection closed")

    def get_trading_tables(self):
        self.cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE '%_trading_pairs'")
        tables = [row[0] for row in self.cursor.fetchall()]
        self.logger.info(f"Found trading pair tables: {tables}")
        return tables