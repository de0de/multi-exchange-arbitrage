import os
import sqlite3
import logging
from datetime import datetime
from typing import List

from src.core.models.order_book_data import OrderBookData, OrderBookLevel


class OrderBookRepository:
    """Репозиторий для хранения Order Book depth (верхние 20 уровней).

    Таблица: {exchange}_order_book
    Хранит bid/ask уровни как JSON-строку в одной строке на пару (upsert).
    """

    def __init__(self, db_path: str, exchange_name: str):
        self.db_path = db_path
        self.exchange_name = exchange_name.lower()
        self.conn: sqlite3.Connection
        self.logger = logging.getLogger(__name__)
        self._connect()
        self._create_table()

    def _connect(self):
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.execute("PRAGMA journal_mode=WAL;")
        self.cursor = self.conn.cursor()

    def _create_table(self):
        table_name = f"{self.exchange_name}_order_book"
        self.cursor.execute(f'''
            CREATE TABLE IF NOT EXISTS {table_name} (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                exchange_id INTEGER,
                original_pair TEXT,
                standardized_pair TEXT,
                bids TEXT,
                asks TEXT,
                timestamp REAL,
                readable_time TEXT,
                FOREIGN KEY (exchange_id) REFERENCES exchanges(id),
                UNIQUE(exchange_id, original_pair)
            )
        ''')
        self.conn.commit()

    def save_order_book(self, order_book: OrderBookData):
        """UPSERT: сохраняет/обновляет order book для пары."""
        table_name = f"{self.exchange_name}_order_book"

        # Получаем exchange_id
        self.cursor.execute(
            'SELECT id FROM exchanges WHERE name = ?', (order_book.exchange,)
        )
        row = self.cursor.fetchone()
        if row:
            exchange_id = row[0]
        else:
            self.logger.warning(f"Exchange '{order_book.exchange}' not found in database, skipping")
            return

        # Сериализуем уровни в JSON (список словарей)
        import json
        bids_json = json.dumps([
            {'price': lvl.price, 'volume': lvl.volume} for lvl in order_book.bids
        ])
        asks_json = json.dumps([
            {'price': lvl.price, 'volume': lvl.volume} for lvl in order_book.asks
        ])

        now = datetime.now().timestamp()
        readable = datetime.fromtimestamp(now).strftime('%Y-%m-%d %H:%M:%S')
        ts = order_book.timestamp if order_book.timestamp is not None else now
        rt = order_book.readable_time if order_book.readable_time is not None else readable

        self.cursor.execute(f'''
            UPDATE {table_name}
            SET bids = ?, asks = ?, timestamp = ?, readable_time = ?
            WHERE exchange_id = ? AND original_pair = ?
        ''', (
            bids_json, asks_json, ts, rt,
            exchange_id, order_book.original_pair
        ))
        if self.cursor.rowcount == 0:
            self.cursor.execute(f'''
                INSERT INTO {table_name} (
                    exchange_id, original_pair, standardized_pair,
                    bids, asks, timestamp, readable_time
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (
                exchange_id, order_book.original_pair, order_book.standardized_pair,
                bids_json, asks_json, ts, rt
            ))
        self.conn.commit()

    def get_order_book_with_age(self, original_pair: str):
        """
        Возвращает (OrderBookData | None, age_seconds | None).
        age_seconds = time.time() - timestamp записи.
        Если записи нет — (None, None).
        """
        import json
        import time

        table_name = f"{self.exchange_name}_order_book"
        self.cursor.execute(
            f'''SELECT exchange_id, original_pair, standardized_pair, bids, asks, timestamp, readable_time
                FROM {table_name} WHERE original_pair = ?''',
            (original_pair,)
        )
        row = self.cursor.fetchone()
        if not row:
            return None, None

        bids = [OrderBookLevel(**lvl) for lvl in json.loads(row[3])]
        asks = [OrderBookLevel(**lvl) for lvl in json.loads(row[4])]

        ob = OrderBookData(
            exchange=self.exchange_name,
            original_pair=row[1],
            standardized_pair=row[2],
            bids=bids,
            asks=asks,
            timestamp=row[5],
            readable_time=row[6]
        )
        age = time.time() - row[5]
        return ob, age

    def save_order_books(self, order_books: List[OrderBookData]):
        """Сохраняет список order books."""
        for ob in order_books:
            self.save_order_book(ob)
        self.logger.info(f"Saved {len(order_books)} order books in the database")

    def close(self):
        self.conn.close()
        self.logger.info("OrderBookRepository connection closed")
