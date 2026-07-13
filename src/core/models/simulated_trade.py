"""
Модель симулированной сделки (Paper Trading, Фаза 1: spot-spot).

Одна запись — одно гипотетическое исполнение арбитражной возможности:
покупка base currency на exchange_buy по ценам момента обнаружения,
перевод между биржами (реалистичная задержка + withdrawal fee),
продажа на exchange_sell по АКТУАЛЬНЫМ ценам на момент закрытия.

Поля, уже существующие в arbitrage_opportunities (пара, биржи, цены
обнаружения, комиссии), не дублируются — доступны через opportunity_id.
"""
from dataclasses import dataclass
from typing import Optional

# Возможные значения поля outcome
OUTCOME_PROFITABLE = "profitable"                # realized_profit > 0
OUTCOME_UNPROFITABLE = "unprofitable"            # realized_profit <= 0
OUTCOME_OPPORTUNITY_VANISHED = "opportunity_vanished"  # на момент закрытия нет свежих цен
OUTCOME_FEE_UNKNOWN = "fee_unknown"              # прибыль посчитана без withdrawal fee — не доверять

STATUS_OPEN = "open"
STATUS_CLOSED = "closed"


@dataclass
class SimulatedTrade:
    """Гипотетическая spot-spot сделка, привязанная к arbitrage_opportunities.id."""

    # Связь и статус
    opportunity_id: int
    status: str = STATUS_OPEN

    # Открытие (момент обнаружения спреда)
    entry_detected_at: float = 0.0
    entry_readable_time: Optional[str] = None
    requested_volume_usdt: float = 0.0        # запрошенный размер сделки (рабочий депозит)
    executed_volume_usdt: float = 0.0         # фактически потраченный объём
    partial_fill: bool = False                # стакан не вместил запрошенный объём —
                                              # остаток переводится отдельно (вторая withdrawal fee)
    entry_buy_price_effective: Optional[float] = None  # средняя цена покупки с учётом slippage
    base_amount: Optional[float] = None       # куплено base currency (после торговой комиссии)

    # Перевод между биржами
    transfer_network: Optional[str] = None
    expected_transfer_seconds: float = 0.0
    hypothetical_close_at: float = 0.0        # entry_detected_at + expected_transfer_seconds
    withdrawal_fee_coin: Optional[float] = None   # суммарно, в base currency (с учётом второго перевода)
    withdrawal_fee_usdt: Optional[float] = None
    fee_unknown: bool = False

    # Рекомендация по объёму: JSON-кривая net_profit_percent(volume)
    volume_curve: Optional[str] = None

    # Закрытие (заполняется при close)
    closed_at: Optional[float] = None         # фактический момент закрытия (может быть позже плана)
    close_readable_time: Optional[str] = None
    close_price_buy: Optional[float] = None   # актуальный ask биржи покупки (справочно)
    close_price_sell: Optional[float] = None  # актуальный bid биржи продажи (цена исполнения)
    realized_profit_usdt: Optional[float] = None
    realized_profit_percent: Optional[float] = None
    outcome: Optional[str] = None

    # Заполняется репозиторием
    id: Optional[int] = None
