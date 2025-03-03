import sqlite3
import logging
from typing import List

class TradingPairsRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self.cursor = self.conn.cursor()
        self.logger = logging.getLogger(__name__)
        self.create_table()

    def create_table(self):
        try:
            with self.conn:
                self.cursor.execute('''
                    CREATE TABLE IF NOT EXISTS unique_pairs (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        standardized_pair TEXT UNIQUE NOT NULL
                    )
                ''')
            self.logger.info("Unique pairs table created successfully")
        except sqlite3.Error as e:
            self.logger.error(f"Error creating unique pairs table: {e}")

    def extract_unique_trading_pairs(self, trading_tables: List[str]) -> List[str]:
        """Извлекает уникальные торговые пары из всех таблиц _trading_pairs."""
        unique_pairs = set()
        for table in trading_tables:
            self.cursor.execute(f'SELECT DISTINCT standardized_pair FROM {table}')
            pairs = {row[0] for row in self.cursor.fetchall()}
            self.logger.info(f"Standardized pairs from {table}: {pairs}")
            unique_pairs.update(pairs)
        self.logger.info(f"All unique trading pairs: {unique_pairs}")
        return list(unique_pairs)

    def populate_unique_trading_pairs_table(self, pairs: List[str]):
        try:
            with self.conn:
                for pair in pairs:
                    self.cursor.execute('SELECT id FROM unique_pairs WHERE standardized_pair = ?', (pair,))
                    if not self.cursor.fetchone():
                        self.cursor.execute('INSERT INTO unique_pairs (standardized_pair) VALUES (?)', (pair,))
            self.conn.commit()
            self.logger.info(f"Inserted {len(pairs)} unique trading pairs into the table")
        except sqlite3.Error as e:
            self.logger.error(f"Error inserting trading pairs: {e}")

    def close(self):
        self.conn.close()
        self.logger.info("Database connection closed") 