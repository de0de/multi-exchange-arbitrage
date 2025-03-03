import os
import sqlite3
import logging
from src.core.models.exchanges import Exchange

class ExchangesRepository:
    def __init__(self, db_url: str):
        db_path = db_url.replace('sqlite:///', '', 1)
        db_dir = os.path.dirname(db_path)
        if db_dir and not os.path.exists(db_dir):
            os.makedirs(db_dir, exist_ok=True)

        self.conn = sqlite3.connect(db_path)
        self.cursor = self.conn.cursor()
        self.logger = logging.getLogger(__name__)
        self.create_table()

    def create_table(self):
        try:
            self.cursor.execute('''
                CREATE TABLE IF NOT EXISTS exchanges (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT UNIQUE NOT NULL,
                    maker_fee REAL DEFAULT 0.001,
                    taker_fee REAL DEFAULT 0.001
                )
            ''')
            self.conn.commit()
            self.logger.info("Exchanges table created successfully")
        except sqlite3.Error as e:
            self.logger.error(f"Error creating exchanges table: {e}")

    def get_or_create_exchange_id(self, exchange_name: str, maker_fee: float = 0.001, taker_fee: float = 0.001) -> int:
        self.cursor.execute('SELECT id FROM exchanges WHERE name = ?', (exchange_name,))
        result = self.cursor.fetchone()
        if result:
            return result[0]
        else:
            self.cursor.execute('INSERT INTO exchanges (name, maker_fee, taker_fee) VALUES (?, ?, ?)', (exchange_name, maker_fee, taker_fee))
            self.conn.commit()
            return self.cursor.lastrowid

    def save_or_update_exchange(self, exchange: Exchange):
        try:
            self.logger.debug(f"Attempting to save or update exchange: {exchange}")
            
            # Обновляем существующую запись
            self.cursor.execute('''
                UPDATE exchanges
                SET maker_fee = ?, taker_fee = ?
                WHERE name = ?
            ''', (exchange.maker_fee, exchange.taker_fee, exchange.name))
            
            # Если ни одна строка не была обновлена, вставляем новую запись
            if self.cursor.rowcount == 0:
                self.cursor.execute('''
                    INSERT INTO exchanges (name, maker_fee, taker_fee)
                    VALUES (?, ?, ?)
                ''', (exchange.name, exchange.maker_fee, exchange.taker_fee))

            self.conn.commit()
            self.logger.info(f"Saved or updated exchange data for {exchange.name}")
        except sqlite3.Error as e:
            self.logger.error(f"Error saving or updating exchange data for {exchange.name}: {e}")

    def update_balances(self, exchange_name: str, usdt_balance: float, spot_balance_usdt: float):
        try:
            self.logger.debug(f"Updating balances for {exchange_name}")
            self.cursor.execute('''
                UPDATE exchanges
                SET usdt_balance = ?, spot_balance_usdt = ?
                WHERE name = ?
            ''', (usdt_balance, spot_balance_usdt, exchange_name))
            
            self.conn.commit()
            self.logger.info(f"Updated balances for {exchange_name}")
        except sqlite3.Error as e:
            self.logger.error(f"Error updating balances for {exchange_name}: {e}")

    def close(self):
        self.conn.close()
        self.logger.info("Database connection closed")