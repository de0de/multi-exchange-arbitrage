from dataclasses import dataclass


@dataclass
class PairData:
    exchange: str
    original_pair: str
    standardized_pair: str
    base_currency: str
    quote_currency: str
    price: float
    volume: float
    bid: float
    ask: float
    bid_volume: float
    ask_volume: float
    timestamp: float
    readable_time: str
    multiplier: float = 1.0       # контрактный множитель (для фьючерсов), для спота = 1.0
    lot_size: float | None = None  # минимальный размер позиции в контрактах (для фьючерсов)