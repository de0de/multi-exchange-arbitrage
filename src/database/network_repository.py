import sqlite3
import logging
from typing import List
from src.core.models.network import Network

class NetworkRepository:
    def __init__(self, db_path: str):
        self.conn = sqlite3.connect(db_path)
        self.cursor = self.conn.cursor()
        self.logger = logging.getLogger(__name__)
        self.create_table()

    def create_table(self):
        try:
            self.cursor.execute('''
                CREATE TABLE IF NOT EXISTS networks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    currency TEXT NOT NULL,
                    network TEXT NOT NULL,
                    name TEXT,
                    withdraw_fee REAL,
                    min_withdraw REAL,
                    deposit_enabled BOOLEAN,
                    withdraw_enabled BOOLEAN,
                    timestamp REAL,
                    readable_time TEXT,
                    UNIQUE(currency, network)
                )
            ''')
            self.conn.commit()
            self.logger.info("Networks table created successfully")
        except sqlite3.Error as e:
            self.logger.error(f"Error creating networks table: {e}")

    def save_networks(self, networks: List[Network]):
        try:
            with self.conn:
                for network in networks:
                    self.cursor.execute('''
                        UPDATE networks
                        SET name = ?, withdraw_fee = ?, min_withdraw = ?,
                            deposit_enabled = ?, withdraw_enabled = ?,
                            timestamp = ?, readable_time = ?
                        WHERE currency = ? AND network = ?
                    ''', (
                        network.name,
                        network.withdraw_fee,
                        network.min_withdraw,
                        network.deposit_enabled,
                        network.withdraw_enabled,
                        network.timestamp,
                        network.readable_time,
                        network.currency,
                        network.network
                    ))
                    if self.cursor.rowcount == 0:
                        self.cursor.execute('''
                            INSERT INTO networks (
                                currency, network, name, withdraw_fee,
                                min_withdraw, deposit_enabled, withdraw_enabled,
                                timestamp, readable_time
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ''', (
                            network.currency,
                            network.network,
                            network.name,
                            network.withdraw_fee,
                            network.min_withdraw,
                            network.deposit_enabled,
                            network.withdraw_enabled,
                            network.timestamp,
                            network.readable_time
                        ))
            self.logger.info(f"Saved {len(networks)} networks")
        except sqlite3.Error as e:
            self.logger.error(f"Error saving networks: {e}")

    def close(self):
        self.conn.close()
        self.logger.info("Database connection closed") 