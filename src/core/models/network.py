from dataclasses import dataclass

@dataclass
class Network:
    currency: str
    network: str
    name: str
    withdraw_fee: float
    min_withdraw: float
    deposit_enabled: bool
    withdraw_enabled: bool
    timestamp: float
    readable_time: str
    exchange_id: int 