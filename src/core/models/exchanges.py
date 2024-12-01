from dataclasses import dataclass

@dataclass
class Exchange:
    id: int
    name: str
    maker_fee: float = 0.001
    taker_fee: float = 0.001 