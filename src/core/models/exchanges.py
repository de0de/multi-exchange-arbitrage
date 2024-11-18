from dataclasses import dataclass

@dataclass
class Exchange:
    id: int
    name: str
    usdt_balance: float = 0.0
    total_balance_usdt: float = 0.0
    spot_balance_usdt: float = 0.0
    futures_balance_usdt: float = 0.0
    additional_info: str = "" 