from dataclasses import dataclass
from typing import Optional


@dataclass
class FundingRateData:
    """Модель данных funding rate для фьючерсных пар.

    Все временные метки приводятся к единому формату Unix timestamp (float, секунды).
    """
    exchange: str
    original_pair: str
    standardized_pair: str
    funding_rate: float
    funding_interval_hours: float
    mark_price: Optional[float] = None
    next_funding_time: Optional[float] = None  # Unix timestamp (секунды)
    timestamp: Optional[float] = None
    readable_time: Optional[str] = None