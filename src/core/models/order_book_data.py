from dataclasses import dataclass, field
from typing import List, Optional, Tuple


@dataclass
class OrderBookLevel:
    """Один уровень стакана: цена → объём."""
    price: float
    volume: float


@dataclass
class OrderBookData:
    """Модель данных стакана заявок (order book depth).

    Хранит 20 лучших bid/ask уровней для пары на конкретной бирже.
    """
    exchange: str
    original_pair: str
    standardized_pair: str
    bids: List[OrderBookLevel] = field(default_factory=list)   # отсортированы по убыванию цены
    asks: List[OrderBookLevel] = field(default_factory=list)   # отсортированы по возрастанию цены
    timestamp: Optional[float] = None
    readable_time: Optional[str] = None