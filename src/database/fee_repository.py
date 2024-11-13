import sqlite3
import logging
from typing import List
from src.core.models.exchange_fee import ExchangeFee
from datetime import datetime

class FeeRepository:
    def __init__(self, db_path: str):
        self.conn = sqlite3.connect(db_path)
        self.cursor = self.conn.cursor()
        self.logger = logging.getLogger(__name__)
        self.create_table()

    def create_table(self):
        try:
            self.cursor.execute('''
                CREATE TABLE IF NOT EXISTS exchange_fees (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    exchange_id INTEGER NOT NULL,
                    original_pair TEXT,
                    standardized_pair TEXT,
                    base_currency TEXT,
                    quote_currency TEXT,
                    maker_fee REAL,
                    taker_fee REAL,
                    timestamp DATETIME,
                    readable_time TEXT,
                    FOREIGN KEY (exchange_id) REFERENCES exchanges(id),
                    UNIQUE(exchange_id, original_pair)
                )
            ''')
            self.conn.commit()
            self.logger.info("Exchange fees table created successfully")
        except sqlite3.Error as e:
            self.logger.error(f"Error creating exchange fees table: {e}")

    def save_fees(self, fees: List[ExchangeFee]):
        try:
            with self.conn:
                for fee in fees:
                    self.cursor.execute('''
                        UPDATE exchange_fees
                        SET maker_fee = ?, taker_fee = ?, timestamp = ?, readable_time = ?
                        WHERE exchange_id = ? AND original_pair = ?
                    ''', (
                        fee.maker_fee, fee.taker_fee, fee.timestamp, 
                        datetime.fromtimestamp(fee.timestamp).strftime('%Y-%m-%d %H:%M:%S'),
                        fee.exchange_id, fee.original_pair
                    ))
                    if self.cursor.rowcount == 0:
                        self.cursor.execute('''
                            INSERT INTO exchange_fees (exchange_id, original_pair, standardized_pair, base_currency, quote_currency, maker_fee, taker_fee, timestamp, readable_time)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ''', (
                            fee.exchange_id, fee.original_pair, fee.standardized_pair, fee.base_currency, fee.quote_currency,
                            fee.maker_fee, fee.taker_fee, fee.timestamp,
                            datetime.fromtimestamp(fee.timestamp).strftime('%Y-%m-%d %H:%M:%S')
                        ))
            self.logger.info(f"Saved {len(fees)} exchange fees")
        except sqlite3.Error as e:
            self.logger.error(f"Error saving exchange fees: {e}")

    def close(self):
        self.conn.close()
        self.logger.info("Database connection closed")

    def get_original_pairs(self) -> List[str]:
        self.cursor.execute('SELECT original_pair FROM exchange_fees')
        return [row[0] for row in self.cursor.fetchall()] 