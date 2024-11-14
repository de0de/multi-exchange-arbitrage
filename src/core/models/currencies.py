from dataclasses import dataclass

@dataclass
class Currency:
    id: int
    exchange_id: int
    name: str
