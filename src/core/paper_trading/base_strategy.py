"""
Базовый интерфейс paper-trading стратегии.

Жизненный цикл, единый для всех стратегий (вызывается из main loop
после каждого SpreadMonitor.scan()):
    1. open_positions(...) — по свежим возможностям решает, какие
       гипотетические позиции открыть.
    2. close_ready_positions() — проверяет уже открытые позиции и
       закрывает те, для которых наступило условие закрытия.

Условие закрытия — деталь конкретной стратегии: у SpotSpotStrategy
это истечение времени перевода монеты между биржами (transfer-delay),
у будущей SpotFuturesStrategy (Фаза 2) — funding-driven логика.
"""
from abc import ABC, abstractmethod
from typing import List, Tuple

from src.core.models.arbitrage_opportunity import ArbitrageOpportunity


class BasePaperTradingStrategy(ABC):
    """Общий контракт для всех paper-trading стратегий."""

    @abstractmethod
    async def open_positions(
        self,
        opportunities: List[Tuple[int, ArbitrageOpportunity]],
    ) -> int:
        """
        Открывает новые гипотетические позиции по найденным возможностям.

        Args:
            opportunities: список (opportunity_id из БД, ArbitrageOpportunity).

        Returns:
            количество открытых позиций.
        """

    @abstractmethod
    async def close_ready_positions(self) -> int:
        """
        Закрывает открытые позиции, для которых наступило условие закрытия.

        Returns:
            количество закрытых позиций.
        """
