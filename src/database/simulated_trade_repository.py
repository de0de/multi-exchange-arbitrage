"""
Репозиторий симулированных сделок (Paper Trading).

Таблица: simulated_trades — FK на arbitrage_opportunities.id.
Поля пары/бирж/цен обнаружения не дублируются: для дедупликации
и закрытия позиций используется JOIN с arbitrage_opportunities.

Использует единое соединение (psycopg), переданное из main.py.
"""
import logging
from typing import List, Optional

import psycopg

from src.core.models.simulated_trade import SimulatedTrade, STATUS_OPEN, STATUS_CLOSED


class SimulatedTradeRepository:
    """Сохранение, поиск открытых и закрытие симулированных сделок."""

    def __init__(self, conn: psycopg.Connection):
        self.conn = conn
        self.cursor = conn.cursor()
        self.logger = logging.getLogger(__name__)
        self._create_table()

    def _create_table(self):
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS simulated_trades (
                id                        BIGSERIAL PRIMARY KEY,
                opportunity_id            BIGINT NOT NULL REFERENCES arbitrage_opportunities(id),
                status                    TEXT NOT NULL DEFAULT 'open',
                entry_detected_at         DOUBLE PRECISION NOT NULL,
                entry_readable_time       TEXT,
                requested_volume_usdt     DOUBLE PRECISION NOT NULL,
                executed_volume_usdt      DOUBLE PRECISION NOT NULL,
                partial_fill              INTEGER DEFAULT 0,
                entry_buy_price_effective DOUBLE PRECISION,
                base_amount               DOUBLE PRECISION,
                transfer_network          TEXT,
                expected_transfer_seconds DOUBLE PRECISION,
                hypothetical_close_at     DOUBLE PRECISION NOT NULL,
                withdrawal_fee_coin       DOUBLE PRECISION,
                withdrawal_fee_usdt       DOUBLE PRECISION,
                fee_unknown               INTEGER DEFAULT 0,
                volume_curve              TEXT,
                closed_at                 DOUBLE PRECISION,
                close_readable_time       TEXT,
                close_price_buy           DOUBLE PRECISION,
                close_price_sell          DOUBLE PRECISION,
                realized_profit_usdt      DOUBLE PRECISION,
                realized_profit_percent   DOUBLE PRECISION,
                outcome                   TEXT
            )
        """)
        self.cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_simulated_trades_status
            ON simulated_trades(status)
        """)
        self.conn.commit()

    def save_trade(self, trade: SimulatedTrade) -> int:
        """INSERT новой (открытой) сделки. Возвращает id записи."""
        self.cursor.execute("""
            INSERT INTO simulated_trades (
                opportunity_id, status,
                entry_detected_at, entry_readable_time,
                requested_volume_usdt, executed_volume_usdt, partial_fill,
                entry_buy_price_effective, base_amount,
                transfer_network, expected_transfer_seconds, hypothetical_close_at,
                withdrawal_fee_coin, withdrawal_fee_usdt, fee_unknown,
                volume_curve
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
        """, (
            trade.opportunity_id,
            trade.status,
            trade.entry_detected_at,
            trade.entry_readable_time,
            trade.requested_volume_usdt,
            trade.executed_volume_usdt,
            1 if trade.partial_fill else 0,
            trade.entry_buy_price_effective,
            trade.base_amount,
            trade.transfer_network,
            trade.expected_transfer_seconds,
            trade.hypothetical_close_at,
            trade.withdrawal_fee_coin,
            trade.withdrawal_fee_usdt,
            1 if trade.fee_unknown else 0,
            trade.volume_curve,
        ))
        trade.id = self.cursor.fetchone()[0]
        self.conn.commit()
        return trade.id

    def has_open_trade(self, standardized_pair: str, exchange_buy: str, exchange_sell: str) -> bool:
        """
        Дедупликация: есть ли уже ОТКРЫТАЯ сделка по этой связке
        (пара + направление buy→sell)? Пока есть — новая не открывается.
        """
        self.cursor.execute("""
            SELECT 1
            FROM simulated_trades st
            JOIN arbitrage_opportunities ao ON ao.id = st.opportunity_id
            WHERE st.status = %s
              AND ao.standardized_pair = %s
              AND ao.exchange_buy = %s
              AND ao.exchange_sell = %s
            LIMIT 1
        """, (STATUS_OPEN, standardized_pair, exchange_buy, exchange_sell))
        return self.cursor.fetchone() is not None

    def get_last_close_for_route(
        self, standardized_pair: str, exchange_buy: str, exchange_sell: str
    ) -> Optional[tuple]:
        """
        Последнее закрытие по связке: (closed_at, realized_profit_percent).

        Нужно для кулдауна переоткрытия: токсичные связки (перманентный
        разрыв цен между биржами — миграции токенов, замороженные рынки,
        коллизии ниже порога) без кулдауна переоткрываются сразу после
        каждого убыточного закрытия и накручивают фиктивные убытки.
        """
        self.cursor.execute("""
            SELECT st.closed_at, st.realized_profit_percent
            FROM simulated_trades st
            JOIN arbitrage_opportunities ao ON ao.id = st.opportunity_id
            WHERE st.status = %s
              AND ao.standardized_pair = %s
              AND ao.exchange_buy = %s
              AND ao.exchange_sell = %s
            ORDER BY st.closed_at DESC
            LIMIT 1
        """, (STATUS_CLOSED, standardized_pair, exchange_buy, exchange_sell))
        return self.cursor.fetchone()

    def get_open_trades_ready_to_close(self, now: float) -> List[dict]:
        """
        Открытые сделки, у которых hypothetical_close_at уже наступил.

        Возвращает словари с полями сделки + контекстом возможности
        (пара, биржи, комиссии) из JOIN с arbitrage_opportunities.
        """
        self.cursor.execute("""
            SELECT st.id, st.opportunity_id,
                   st.entry_detected_at, st.hypothetical_close_at,
                   st.requested_volume_usdt, st.executed_volume_usdt,
                   st.partial_fill, st.entry_buy_price_effective, st.base_amount,
                   st.withdrawal_fee_coin, st.withdrawal_fee_usdt, st.fee_unknown,
                   ao.standardized_pair, ao.base_currency,
                   ao.exchange_buy, ao.exchange_sell,
                   ao.buy_exchange_fee_percent, ao.sell_exchange_fee_percent
            FROM simulated_trades st
            JOIN arbitrage_opportunities ao ON ao.id = st.opportunity_id
            WHERE st.status = %s AND st.hypothetical_close_at <= %s
        """, (STATUS_OPEN, now))
        columns = [desc[0] for desc in self.cursor.description]
        return [dict(zip(columns, row)) for row in self.cursor.fetchall()]

    def close_trade(
        self,
        trade_id: int,
        closed_at: float,
        close_readable_time: str,
        outcome: str,
        close_price_buy: Optional[float] = None,
        close_price_sell: Optional[float] = None,
        realized_profit_usdt: Optional[float] = None,
        realized_profit_percent: Optional[float] = None,
    ):
        """Проставляет результат закрытия и переводит сделку в status='closed'."""
        self.cursor.execute("""
            UPDATE simulated_trades
            SET status = %s,
                closed_at = %s,
                close_readable_time = %s,
                outcome = %s,
                close_price_buy = %s,
                close_price_sell = %s,
                realized_profit_usdt = %s,
                realized_profit_percent = %s
            WHERE id = %s
        """, (
            STATUS_CLOSED,
            closed_at,
            close_readable_time,
            outcome,
            close_price_buy,
            close_price_sell,
            realized_profit_usdt,
            realized_profit_percent,
            trade_id,
        ))
        self.conn.commit()
