from dataclasses import dataclass

@dataclass
class ExchangeFee:
    id: int
    exchange_id: int
    original_pair: str
    standardized_pair: str
    base_currency: str
    quote_currency: str
    maker_fee: float
    taker_fee: float
    timestamp: float
    readable_time: str 