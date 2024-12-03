import sqlite3
import logging
from typing import List
from src.core.models.currencies import Currency

class CurrenciesRepository:
    def __init__(self, db_path: str):
        self.conn = sqlite3.connect(db_path)
        self.cursor = self.conn.cursor()
        self.logger = logging.getLogger(__name__)
        self.create_table()

    def create_table(self):
        try:
            self.cursor.execute('''
                CREATE TABLE IF NOT EXISTS currencies (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT UNIQUE NOT NULL
                )
            ''')
            self.conn.commit()
            self.logger.info("Currencies table created successfully")
        except sqlite3.Error as e:
            self.logger.error(f"Error creating currencies table: {e}")

    def get_trading_tables(self) -> List[str]:
        """Получает список всех таблиц _trading_pairs в базе данных."""
        self.cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE '%_trading_pairs'")
        tables = [row[0] for row in self.cursor.fetchall()]
        self.logger.info(f"Found trading pair tables: {tables}")
        return tables

    def extract_unique_currencies(self) -> List[str]:
        """Извлекает уникальные валюты из всех таблиц _trading_pairs."""
        all_currencies = set()
        tables = self.get_trading_tables()

        for table in tables:
            self.cursor.execute(f'SELECT DISTINCT base_currency FROM {table}')
            base_currencies = {row[0] for row in self.cursor.fetchall()}
            self.logger.info(f"Base currencies from {table}: {base_currencies}")

            self.cursor.execute(f'SELECT DISTINCT quote_currency FROM {table}')
            quote_currencies = {row[0] for row in self.cursor.fetchall()}
            self.logger.info(f"Quote currencies from {table}: {quote_currencies}")

            all_currencies.update(base_currencies.union(quote_currencies))

        self.logger.info(f"All unique currencies: {all_currencies}")
        return list(all_currencies)

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