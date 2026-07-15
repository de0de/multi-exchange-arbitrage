"""
Репозиторий для хранения арбитражных возможностей.

Таблица: arbitrage_opportunities
Хранит результаты расчёта спредов между биржами.
"""
import json
import logging
from datetime import datetime
from typing import List, Optional

import psycopg

from src.core.models.arbitrage_opportunity import ArbitrageOpportunity, SlippageInfo


class ArbitrageOpportunityRepository:
    """
    Сохраняет и загружает арбитражные возможности в/из БД.

    Использует единое соединение (psycopg), переданное из main.py.
    """

    def __init__(self, conn: psycopg.Connection):
        self.conn = conn
        self.cursor = conn.cursor()
        self.logger = logging.getLogger(__name__)
        self._create_table()

    def _create_table(self):
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS arbitrage_opportunities (
                id                     BIGSERIAL PRIMARY KEY,
                standardized_pair      TEXT NOT NULL,
                base_currency          TEXT,
                quote_currency         TEXT,
                exchange_buy           TEXT NOT NULL,
                exchange_sell          TEXT NOT NULL,
                buy_price              DOUBLE PRECISION,
                sell_price             DOUBLE PRECISION,
                raw_spread_percent     DOUBLE PRECISION,
                buy_exchange_fee_percent  DOUBLE PRECISION,
                sell_exchange_fee_percent DOUBLE PRECISION,
                net_spread_percent     DOUBLE PRECISION,
                max_buy_volume_usdt    DOUBLE PRECISION,
                max_sell_volume_usdt   DOUBLE PRECISION,
                trade_volume_usdt      DOUBLE PRECISION,
                buy_volume_original    DOUBLE PRECISION,
                sell_volume_original   DOUBLE PRECISION,
                slippage_available     INTEGER DEFAULT 0,
                buy_slippage           TEXT,
                sell_slippage          TEXT,
                net_spread_with_slippage_percent DOUBLE PRECISION,
                slippage_limited_volume_usdt DOUBLE PRECISION,
                timestamp              DOUBLE PRECISION,
                readable_time          TEXT,
                suspected_collision    INTEGER DEFAULT 0
            )
        """)
        self.conn.commit()

    def save_opportunity(self, opp: ArbitrageOpportunity) -> int:
        """
        INSERT: сохраняет одну арбитражную возможность (история событий).

        Возвращает id вставленной записи (нужен Paper Trading для FK
        simulated_trades.opportunity_id).
        """
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
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
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
        opportunity_id = self.cursor.fetchone()[0]
        self.conn.commit()
        return opportunity_id

    def save_opportunities(self, opportunities: List[ArbitrageOpportunity]) -> List[int]:
        """Сохраняет список арбитражных возможностей. Возвращает список id."""
        ids = [self.save_opportunity(opp) for opp in opportunities]
        self.logger.debug(f"Saved {len(opportunities)} arbitrage opportunities to database")
        return ids
