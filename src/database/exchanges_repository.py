import logging

import psycopg

from src.core.models.exchanges import Exchange

class ExchangesRepository:
    def __init__(self, conn: psycopg.Connection):
        self.conn = conn
        self.cursor = conn.cursor()
        self.logger = logging.getLogger(__name__)
        self.create_table()

    def create_table(self):
        try:
            self.cursor.execute('''
                CREATE TABLE IF NOT EXISTS exchanges (
                    id BIGSERIAL PRIMARY KEY,
                    name TEXT UNIQUE NOT NULL,
                    maker_fee DOUBLE PRECISION DEFAULT 0.001,
                    taker_fee DOUBLE PRECISION DEFAULT 0.001
                )
            ''')
            self.conn.commit()
            self.logger.info("Exchanges table created successfully")
        except psycopg.Error as e:
            self.conn.rollback()
            self.logger.error(f"Error creating exchanges table: {e}")

    def get_or_create_exchange_id(self, exchange_name: str, maker_fee: float = 0.001, taker_fee: float = 0.001) -> int:
        self.cursor.execute('SELECT id FROM exchanges WHERE name = %s', (exchange_name,))
        result = self.cursor.fetchone()
        if result:
            return result[0]
        else:
            self.cursor.execute(
                'INSERT INTO exchanges (name, maker_fee, taker_fee) VALUES (%s, %s, %s) RETURNING id',
                (exchange_name, maker_fee, taker_fee)
            )
            exchange_id = self.cursor.fetchone()[0]
            self.conn.commit()
            return exchange_id

    def save_or_update_exchange(self, exchange: Exchange):
        try:
            self.logger.debug(f"Attempting to save or update exchange: {exchange}")

            # Обновляем существующую запись
            self.cursor.execute('''
                UPDATE exchanges
                SET maker_fee = %s, taker_fee = %s
                WHERE name = %s
            ''', (exchange.maker_fee, exchange.taker_fee, exchange.name))

            # Если ни одна строка не была обновлена, вставляем новую запись
            if self.cursor.rowcount == 0:
                self.cursor.execute('''
                    INSERT INTO exchanges (name, maker_fee, taker_fee)
                    VALUES (%s, %s, %s)
                ''', (exchange.name, exchange.maker_fee, exchange.taker_fee))

            self.conn.commit()
            self.logger.info(f"Saved or updated exchange data for {exchange.name}")
        except psycopg.Error as e:
            self.conn.rollback()
            self.logger.error(f"Error saving or updating exchange data for {exchange.name}: {e}")

    def update_balances(self, exchange_name: str, usdt_balance: float, spot_balance_usdt: float):
        try:
            self.logger.debug(f"Updating balances for {exchange_name}")
            self.cursor.execute('''
                UPDATE exchanges
                SET usdt_balance = %s, spot_balance_usdt = %s
                WHERE name = %s
            ''', (usdt_balance, spot_balance_usdt, exchange_name))

            self.conn.commit()
            self.logger.info(f"Updated balances for {exchange_name}")
        except psycopg.Error as e:
            self.conn.rollback()
            self.logger.error(f"Error updating balances for {exchange_name}: {e}")
