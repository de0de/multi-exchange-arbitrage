import sqlite3
import logging
from src.core.models.exchanges import Exchange

class ExchangesRepository:
    def __init__(self, db_path: str):
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
                    usdt_balance REAL DEFAULT 0,
                    total_balance_usdt REAL DEFAULT 0,
                    spot_balance_usdt REAL DEFAULT 0,
                    futures_balance_usdt REAL DEFAULT 0,
                    additional_info TEXT
                )
            ''')
            self.conn.commit()
            self.logger.info("Exchanges table created successfully")
        except sqlite3.Error as e:
            self.logger.error(f"Error creating exchanges table: {e}")

    def get_or_create_exchange_id(self, exchange_name: str) -> int:
        self.cursor.execute('SELECT id FROM exchanges WHERE name = ?', (exchange_name,))
        result = self.cursor.fetchone()
        if result:
            return result[0]
        else:
            self.cursor.execute('INSERT INTO exchanges (name) VALUES (?)', (exchange_name,))
            self.conn.commit()
            return self.cursor.lastrowid

    def save_or_update_exchange(self, exchange: Exchange):
        try:
            self.logger.debug(f"Attempting to save or update exchange: {exchange}")
            
            # Сначала попробуем обновить существующую запись
            self.cursor.execute('''
                UPDATE exchanges
                SET usdt_balance = ?, total_balance_usdt = ?, spot_balance_usdt = ?, futures_balance_usdt = ?, additional_info = ?
                WHERE name = ?
            ''', (exchange.usdt_balance, exchange.total_balance_usdt, exchange.spot_balance_usdt, exchange.futures_balance_usdt, exchange.additional_info, exchange.name))
            
            # Если ни одна строка не была обновлена, вставляем новую запись
            if self.cursor.rowcount == 0:
                self.cursor.execute('''
                    INSERT INTO exchanges (name, usdt_balance, total_balance_usdt, spot_balance_usdt, futures_balance_usdt, additional_info)
                    VALUES (?, ?, ?, ?, ?, ?)
                ''', (exchange.name, exchange.usdt_balance, exchange.total_balance_usdt, exchange.spot_balance_usdt, exchange.futures_balance_usdt, exchange.additional_info))

            self.conn.commit()
            self.logger.info(f"Saved or updated exchange data for {exchange.name}")
        except sqlite3.Error as e:
            self.logger.error(f"Error saving or updating exchange data for {exchange.name}: {e}")

    def update_balances(self, exchange_name: str, usdt_balance: float, spot_balance_usdt: float, futures_balance_usdt: float):
        try:
            self.logger.debug(f"Updating balances for {exchange_name}")
            self.cursor.execute('''
                UPDATE exchanges
                SET usdt_balance = ?, spot_balance_usdt = ?, futures_balance_usdt = ?, total_balance_usdt = ?
                WHERE name = ?
            ''', (usdt_balance, spot_balance_usdt, futures_balance_usdt, usdt_balance + futures_balance_usdt, exchange_name))
            
            self.conn.commit()
            self.logger.info(f"Updated balances for {exchange_name}")
        except sqlite3.Error as e:
            self.logger.error(f"Error updating balances for {exchange_name}: {e}")

    def close(self):
        self.conn.close()
        self.logger.info("Database connection closed")