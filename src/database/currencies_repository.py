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

    def extract_unique_currencies(self) -> List[str]:
        # Извлечение уникальных валют из таблицы networks
        self.cursor.execute('SELECT DISTINCT currency FROM networks')
        network_currencies = {row[0] for row in self.cursor.fetchall()}
        self.logger.info(f"Network currencies: {network_currencies}")

        # Извлечение уникальных валют из таблицы trading_pairs
        self.cursor.execute('SELECT DISTINCT base_currency FROM trading_pairs')
        base_currencies = {row[0] for row in self.cursor.fetchall()}
        self.logger.info(f"Base currencies: {base_currencies}")

        self.cursor.execute('SELECT DISTINCT quote_currency FROM trading_pairs')
        quote_currencies = {row[0] for row in self.cursor.fetchall()}
        self.logger.info(f"Quote currencies: {quote_currencies}")

        # Объединение всех уникальных валют
        all_currencies = network_currencies.union(base_currencies, quote_currencies)
        self.logger.info(f"All unique currencies: {all_currencies}")
        return list(all_currencies)

    def populate_currencies_table(self, currencies: List[str]):
        try:
            with self.conn:
                for currency in currencies:
                    self.cursor.execute('INSERT OR IGNORE INTO currencies (name) VALUES (?)', (currency,))
            self.conn.commit()
            self.logger.info(f"Inserted {len(currencies)} unique currencies into the table")
        except sqlite3.Error as e:
            self.logger.error(f"Error inserting currencies: {e}")

    def close(self):
        self.conn.close()
        self.logger.info("Database connection closed") 