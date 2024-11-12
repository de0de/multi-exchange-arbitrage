import os
import sqlite3
import logging
from src.database.base_repository import BaseRepository
from src.core.models.pair_data import PairData
from typing import List
from datetime import datetime

class MarketRepository(BaseRepository):
    def __init__(self, db_url: str):
        self.db_url = db_url
        self.conn = None
        self.logger = logging.getLogger(__name__)
        self._connect()
        self._create_tables()

    def _connect(self):
        # Извлекаем путь к базе данных из URL
        db_path = self.db_url.replace('sqlite:///', '')
        # Создаем директорию, если она не существует
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        # Подключаемся к базе данных
        self.conn = sqlite3.connect(db_path)
        self.cursor = self.conn.cursor()

    def _create_tables(self):
        # Создаем таблицу exchanges, если она не существует
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS exchanges (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL
            )
        ''')

        # Создаем таблицу trading_pairs, если она не существует
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS trading_pairs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                exchange_id INTEGER,
                original_pair TEXT,
                standardized_pair TEXT,
                base_currency TEXT,
                quote_currency TEXT,
                price REAL,
                volume REAL,
                bid REAL,
                ask REAL,
                bid_volume REAL,
                ask_volume REAL,
                timestamp REAL,
                readable_time TEXT,
                FOREIGN KEY (exchange_id) REFERENCES exchanges(id),
                UNIQUE(exchange_id, original_pair)
            )
        ''')
        self.conn.commit()

    def get_or_create_exchange_id(self, exchange_name: str) -> int:
        # Получаем или создаем идентификатор биржи
        self.cursor.execute('SELECT id FROM exchanges WHERE name = ?', (exchange_name,))
        result = self.cursor.fetchone()
        if result:
            return result[0]
        else:
            self.cursor.execute('INSERT INTO exchanges (name) VALUES (?)', (exchange_name,))
            self.conn.commit()
            return self.cursor.lastrowid

    def save_trading_pairs(self, pairs: List[PairData]):
        # Сохраняем торговые пары в базу данных
        for pair in pairs:
            exchange_id = self.get_or_create_exchange_id(pair.exchange)
            # Используем UPDATE для обновления существующих записей
            self.cursor.execute('''
                UPDATE trading_pairs
                SET standardized_pair = ?, base_currency = ?, quote_currency = ?,
                    price = ?, volume = ?, bid = ?, ask = ?, bid_volume = ?, ask_volume = ?,
                    timestamp = ?, readable_time = ?
                WHERE exchange_id = ? AND original_pair = ?
            ''', (
                pair.standardized_pair, pair.base_currency, pair.quote_currency,
                pair.price, pair.volume, pair.bid, pair.ask,
                pair.bid_volume, pair.ask_volume, pair.timestamp, pair.readable_time,
                exchange_id, pair.original_pair
            ))
            # Если запись не обновилась, вставляем новую
            if self.cursor.rowcount == 0:
                self.cursor.execute('''
                    INSERT INTO trading_pairs (
                        exchange_id, original_pair, standardized_pair, base_currency,
                        quote_currency, price, volume, bid, ask, bid_volume, ask_volume, timestamp, readable_time
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    exchange_id, pair.original_pair, pair.standardized_pair, pair.base_currency,
                    pair.quote_currency, pair.price, pair.volume, pair.bid, pair.ask,
                    pair.bid_volume, pair.ask_volume, pair.timestamp, pair.readable_time
                ))
        self.conn.commit()
        self.logger.info(f"Updated {len(pairs)} trading pairs in the database")