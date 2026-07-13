"""
Справочник межбиржевых переводов монет: сеть, withdrawal fee, ожидаемое время.

Используется Paper Trading симуляцией (Фаза 1, spot-spot): между "покупкой"
и "продажей" проходит реальное время перевода base currency между биржами,
а withdrawal fee вычитается из переводимого объёма.

Значения заполнены вручную (ориентир — середина 2026, типичные комиссии
Binance/KuCoin). Это осознанно ручной словарь (см. PLAN.md 5.1):
- withdrawal_fee указана в единицах САМОЙ монеты (не USDT)
- transfer_seconds — полное время перевода между биржами:
  вывод с биржи + подтверждения сети + зачисление на второй бирже

Для монет вне словаря get_transfer_info() возвращает запись с
fee_unknown=True и консервативным временем по умолчанию — комиссия
НИКОГДА не подставляется нулём (сделки с fee_unknown исключаются из
агрегированной прибыльности).
"""
from dataclasses import dataclass
from typing import Dict, Optional

# Консервативный дефолт для монет вне словаря: 30 минут
DEFAULT_TRANSFER_SECONDS = 1800.0


@dataclass(frozen=True)
class TransferInfo:
    """Параметры перевода одной монеты между биржами."""
    coin: str
    network: str
    withdrawal_fee: Optional[float]   # в единицах монеты; None — неизвестна
    transfer_seconds: float
    fee_unknown: bool = False


# Ручной словарь: монета → параметры перевода по самой ходовой/дешёвой сети
TRANSFER_TABLE: Dict[str, TransferInfo] = {
    "BTC":  TransferInfo("BTC",  "BTC",       0.0002,  2400.0),  # ~2 подтверждения
    "ETH":  TransferInfo("ETH",  "ERC20",     0.002,   300.0),
    "USDT": TransferInfo("USDT", "TRC20",     1.0,     180.0),
    "USDC": TransferInfo("USDC", "Solana",    1.0,     120.0),
    "SOL":  TransferInfo("SOL",  "Solana",    0.01,    90.0),
    "XRP":  TransferInfo("XRP",  "XRP",       0.25,    60.0),
    "TRX":  TransferInfo("TRX",  "TRC20",     1.0,     180.0),
    "LTC":  TransferInfo("LTC",  "LTC",       0.001,   900.0),
    "DOGE": TransferInfo("DOGE", "DOGE",      4.0,     600.0),
    "ADA":  TransferInfo("ADA",  "Cardano",   0.8,     600.0),
    "DOT":  TransferInfo("DOT",  "Polkadot",  0.08,    300.0),
    "AVAX": TransferInfo("AVAX", "AVAX C-Chain", 0.008, 120.0),
    "BNB":  TransferInfo("BNB",  "BEP20",     0.0005,  120.0),
    "ATOM": TransferInfo("ATOM", "Cosmos",    0.005,   120.0),
    "NEAR": TransferInfo("NEAR", "NEAR",      0.01,    120.0),
    "POL":  TransferInfo("POL",  "Polygon",   0.1,     300.0),
    "XLM":  TransferInfo("XLM",  "Stellar",   0.02,    60.0),
    "ETC":  TransferInfo("ETC",  "ETC",       0.01,    900.0),
}


def get_transfer_info(coin: str) -> TransferInfo:
    """
    Возвращает параметры перевода для монеты.

    Монета вне словаря → fee_unknown=True, withdrawal_fee=None,
    время перевода — DEFAULT_TRANSFER_SECONDS. Сделка всё равно
    симулируется (для статистики по времени жизни спреда), но
    помечается и исключается из агрегатов прибыльности.
    """
    info = TRANSFER_TABLE.get(coin.upper() if coin else "")
    if info is not None:
        return info
    return TransferInfo(
        coin=coin or "?",
        network="unknown",
        withdrawal_fee=None,
        transfer_seconds=DEFAULT_TRANSFER_SECONDS,
        fee_unknown=True,
    )
