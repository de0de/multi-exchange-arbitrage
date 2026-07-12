"""
Репозиторий для хранения арбитражных возможностей.

Таблица: arbitrage_opportunities
Хранит результаты расчёта спредов между биржами.
"""
import json
import logging
import sqlite3
from datetime import datetime
from typing import List, Optional

from src.core.models.arbitrage_opportunity import ArbitrageOpportunity, SlippageInfo


class ArbitrageOpportunityRepository:
    """
    Сохраняет и загружает арбитражные возможности в/из БД.

    Использует единое соединение sqlite3, переданное из main.py.
    """

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self.cursor = conn.cursor()
        self.logger = logging.getLogger(__name__)
        self._create_table()

    def _create_table(self):
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS arbitrage_opportunities (
                id                     INTEGER PRIMARY KEY AUTOINCREMENT,
                standardized_pair      TEXT NOT NULL,
                base_currency          TEXT,
                quote_currency         TEXT,
                exchange_buy           TEXT NOT NULL,
                exchange_sell          TEXT NOT NULL,
                buy_price              REAL,
                sell_price             REAL,
                raw_spread_percent     REAL,
                buy_exchange_fee_percent  REAL,
                sell_exchange_fee_percent REAL,
                net_spread_percent     REAL,
                max_buy_volume_usdt    REAL,
                max_sell_volume_usdt   REAL,
                trade_volume_usdt      REAL,
                buy_volume_original    REAL,
                sell_volume_original   REAL,
                slippage_available     INTEGER DEFAULT 0,
                buy_slippage           TEXT,
                sell_slippage          TEXT,
                net_spread_with_slippage_percent REAL,
                slippage_limited_volume_usdt REAL,
                timestamp              REAL,
                readable_time          TEXT,
                suspected_collision    INTEGER DEFAULT 0
            )
        """)
        self.conn.commit()

    def save_opportunity(self, opp: ArbitrageOpportunity):
        """INSERT: сохраняет одну арбитражную возможность (история событий)."""
        self.cursor.execute("""
            INSERT INTO arbitrage_opportunities (
                standardized_pair, base_currency, quote_currency,
                exchange_buy, exchange_sell,
                buy_price, sell_price, raw_spread_percent,
                buy_exchange_fee_percent, sell_exchange_fee_percent,
                net_spread_percent,
                max_buy_volume_usdt, max_sell_volume_usdt,
                trade_volume_usdt, buy_volume_original, sell_volume_original,
                slippage_available,
                buy_slippage, sell_slippage,
                net_spread_with_slippage_percent,
                slippage_limited_volume_usdt,
                timestamp, readable_time,
                suspected_collision
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            opp.standardized_pair,
            opp.base_currency,
            opp.quote_currency,
            opp.exchange_buy,
            opp.exchange_sell,
            opp.buy_price,
            opp.sell_price,
            opp.raw_spread_percent,
            opp.buy_exchange_fee_percent,
            opp.sell_exchange_fee_percent,
            opp.net_spread_percent,
            opp.max_buy_volume_usdt,
            opp.max_sell_volume_usdt,
            opp.trade_volume_usdt,
            opp.buy_volume_original,
            opp.sell_volume_original,
            1 if opp.slippage_available else 0,
            json.dumps({
                'price_impact_percent': opp.buy_slippage.price_impact_percent,
                'filled_volume': opp.buy_slippage.filled_volume,
                'levels_consumed': opp.buy_slippage.levels_consumed,
            }) if opp.buy_slippage else None,
            json.dumps({
                'price_impact_percent': opp.sell_slippage.price_impact_percent,
                'filled_volume': opp.sell_slippage.filled_volume,
                'levels_consumed': opp.sell_slippage.levels_consumed,
            }) if opp.sell_slippage else None,
            opp.net_spread_with_slippage_percent,
            opp.slippage_limited_volume_usdt,
            opp.timestamp,
            opp.readable_time,
            1 if opp.suspected_collision else 0,
        ))
        self.conn.commit()

    def save_opportunities(self, opportunities: List[ArbitrageOpportunity]):
        """Сохраняет список арбитражных возможностей."""
        for opp in opportunities:
            self.save_opportunity(opp)
        self.logger.info(f"Saved {len(opportunities)} arbitrage opportunities to database")