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