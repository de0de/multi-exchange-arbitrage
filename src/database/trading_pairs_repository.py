import logging
from typing import List

import psycopg

class TradingPairsRepository:
    def __init__(self, conn: psycopg.Connection):
        self.conn = conn
        self.cursor = self.conn.cursor()
        self.logger = logging.getLogger(__name__)
        self.create_table()

    def create_table(self):
        try:
            self.cursor.execute('''
                CREATE TABLE IF NOT EXISTS unique_pairs (
                    id BIGSERIAL PRIMARY KEY,
                    standardized_pair TEXT UNIQUE NOT NULL
                )
            ''')
            self.conn.commit()
            self.logger.info("Unique pairs table created successfully")
        except psycopg.Error as e:
            self.conn.rollback()
            self.logger.error(f"Error creating unique pairs table: {e}")

    def extract_unique_trading_pairs(self, trading_tables: List[str]) -> List[str]:
        """Извлекает уникальные торговые пары из всех таблиц _trading_pairs."""
        unique_pairs = set()
        for table in trading_tables:
            self.cursor.execute(f'SELECT DISTINCT standardized_pair FROM {table}')
            pairs = {row[0] for row in self.cursor.fetchall()}
            self.logger.debug(f"Standardized pairs from {table}: {len(pairs)}")
            unique_pairs.update(pairs)
        self.logger.debug(f"All unique trading pairs: {len(unique_pairs)}")
        return list(unique_pairs)

    def populate_unique_trading_pairs_table(self, pairs: List[str]):
        try:
            # Пакетно: построчный SELECT+INSERT давал тысячи round-trip'ов
            self.cursor.executemany(
                'INSERT INTO unique_pairs (standardized_pair) VALUES (%s) ON CONFLICT (standardized_pair) DO NOTHING',
                [(p,) for p in pairs if p is not None]
            )
            self.conn.commit()
            self.logger.info(f"Inserted {len(pairs)} unique trading pairs into the table")
        except psycopg.Error as e:
            self.conn.rollback()
            self.logger.error(f"Error inserting trading pairs: {e}")
