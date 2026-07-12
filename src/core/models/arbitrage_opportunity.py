"""
Модель арбитражной возможности.

Хранит результат расчёта спреда между двумя биржами для одной пары
с учётом комиссий, объёмов и проскальзывания на основе Order Book.
"""
from dataclasses import dataclass, field
from typing import List, Optional
from datetime import datetime


@dataclass
class SlippageInfo:
    """Информация о проскальзывании для одной стороны сделки."""
    price_impact_percent: float       # проскальзывание в %
    filled_volume: float               # какой объём "вместился" в стакан
    levels_consumed: int               # сколько уровней Order Book ушло


@dataclass
class ArbitrageOpportunity:
    """
    Одна арбитражная возможность: купить дешевле на exchange_buy,
    продать дороже на exchange_sell.
    """
    # Идентификация
    standardized_pair: str                                  # например "BTCUSDT"
    base_currency: str
    quote_currency: str

    # Биржи
    exchange_buy: str                                       # где дешевле (bid)
    exchange_sell: str                                      # где дороже (ask)

    # Цены
    buy_price: float                                        # цена покупки
    sell_price: float                                       # цена продажи
    raw_spread_percent: float                                # (ask - bid) / bid * 100 без комиссий

    # Комиссии
    buy_exchange_fee_percent: float                         # taker_fee биржи покупки
    sell_exchange_fee_percent: float                        # taker_fee биржи продажи
    net_spread_percent: float                               # raw_spread - сумма комиссий

    # Объёмы
    max_buy_volume_usdt: float                              # максимальный объём покупки (лимит объёма пары * цена)
    max_sell_volume_usdt: float                             # максимальный объём продажи
    trade_volume_usdt: float                                # лимитирующий объём (min из двух)
    buy_volume_original: float                              # объём пары на бирже покупки
    sell_volume_original: float                             # объём пары на бирже продажи

    # Проскальзывание (Order Book depth)
    slippage_available: bool = False                        # True, если Order Book был загружен
    buy_slippage: Optional[SlippageInfo] = None              # проскальзывание на buy-стороне
    sell_slippage: Optional[SlippageInfo] = None             # проскальзывание на sell-стороне
    net_spread_with_slippage_percent: Optional[float] = None  # net_spread минус slippage с обеих сторон
    slippage_limited_volume_usdt: Optional[float] = None     # объём, доступный с учётом проскальзывания

    # Метаданные
    timestamp: float = field(default_factory=lambda: datetime.now().timestamp())
    readable_time: str = field(default_factory=lambda: datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
    suspected_collision: bool = False                       # подозрение на коллизию тикеров

    def net_profit_percent(self) -> float:
        """
        Финальная расчётная доходность.
        Если slippage доступен — net_spread_with_slippage_percent,
        иначе net_spread_percent (консервативная оценка).
        """
        if self.slippage_available and self.net_spread_with_slippage_percent is not None:
            return self.net_spread_with_slippage_percent
        return self.net_spread_percent

    def estimated_profit_usdt(self) -> float:
        """Оценочная прибыль в USDT с учётом лимитирующего объёма и комиссий."""
        return self.trade_volume_usdt * self.net_profit_percent() / 100.0