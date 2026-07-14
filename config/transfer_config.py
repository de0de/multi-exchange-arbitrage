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

    # --- Data-driven пополнение (2026-07-14): монеты из реальных находок
    # arbitrage_opportunities, комиссии — KuCoin public API
    # (/api/v3/currencies/{coin}, сеть с минимальной комиссией вывода).
    # Приближение: комиссия вывода у каждой биржи своя, значения KuCoin
    # используются как представительные для всех бирж. Время перевода —
    # эвристика по типу сети.
    "VANRY": TransferInfo("VANRY", "ERC20", 167.0, 300.0),
    "ROUTE": TransferInfo("ROUTE", "ERC20", 2200.0, 300.0),
    "KARRAT": TransferInfo("KARRAT", "ERC20", 200.0, 300.0),
    "WBAI": TransferInfo("WBAI", "BEP20", 400.0, 120.0),
    "STREAM": TransferInfo("STREAM", "SOL", 70.0, 90.0),
    "RWA": TransferInfo("RWA", "Base", 450.0, 300.0),
    "REACT": TransferInfo("REACT", "react", 40.0, 900.0),
    "NIM": TransferInfo("NIM", "NIM", 572.61, 900.0),
    "LINGO": TransferInfo("LINGO", "Base", 80.0, 300.0),
    "DMTR": TransferInfo("DMTR", "ERC20", 170.0, 300.0),
    "CHEQ": TransferInfo("CHEQ", "Cheqd", 240.0, 900.0),
    "ALEX": TransferInfo("ALEX", "STX", 400.0, 900.0),
    "XTER": TransferInfo("XTER", "BEP20", 30.0, 120.0),
    "PZP": TransferInfo("PZP", "BEP20", 200.0, 120.0),
    "NUM": TransferInfo("NUM", "BEP20", 200.0, 120.0),
    "TOWER": TransferInfo("TOWER", "ERC20", 3500.0, 300.0),
    "RMV": TransferInfo("RMV", "Polygon POS", 164.07, 300.0),
    "PURR": TransferInfo("PURR", "hype", 10.0, 900.0),
    "POKT": TransferInfo("POKT", "Pokt Shannon", 50.0, 900.0),
    "OBI": TransferInfo("OBI", "BEP20", 3600.0, 120.0),
    "MYRIA": TransferInfo("MYRIA", "ERC20", 10000.0, 300.0),
    "KONET": TransferInfo("KONET", "Konet", 10.0, 900.0),
    "HPP": TransferInfo("HPP", "HPP Mainet", 20.0, 900.0),
    "GTAI": TransferInfo("GTAI", "BEP20", 35.0, 120.0),
    "FORTH": TransferInfo("FORTH", "ERC20", 1.7, 300.0),
    "ESPORTS": TransferInfo("ESPORTS", "BEP20", 18.0, 120.0),
    "CREDI": TransferInfo("CREDI", "ERC20", 980.0, 300.0),
    "CHO": TransferInfo("CHO", "ERC20", 400.0, 300.0),
    "BULLA": TransferInfo("BULLA", "BEP20", 60.0, 120.0),
    "BAX": TransferInfo("BAX", "ERC20", 120000.0, 300.0),
    "PUBLIC": TransferInfo("PUBLIC", "NEAR", 90.0, 120.0),
    "MOVA": TransferInfo("MOVA", "Mova Mainnet", 0.5, 900.0),
    "GHX": TransferInfo("GHX", "ERC20", 178.0, 300.0),
    "DIN": TransferInfo("DIN", "BEP20", 80.0, 120.0),
    "ANKR": TransferInfo("ANKR", "ERC20", 330.0, 300.0),
    "LVVA": TransferInfo("LVVA", "ERC20", 1400.0, 300.0),
    "ATWO": TransferInfo("ATWO", "Base", 280.0, 300.0),
    "RIZ": TransferInfo("RIZ", "Base", 9000.0, 300.0),
    "PIPE": TransferInfo("PIPE", "SOL", 45.0, 90.0),
    "KAIO": TransferInfo("KAIO", "ERC20", 30.0, 300.0),
    "UNION": TransferInfo("UNION", "ERC20", 1800.0, 300.0),
    "U2U": TransferInfo("U2U", "U2U", 2400.0, 900.0),
    "MANTA": TransferInfo("MANTA", "Manta", 12.0, 900.0),
    "MAN": TransferInfo("MAN", "MAN", 200.0, 900.0),
    "LAB": TransferInfo("LAB", "BEP20", 0.9, 120.0),
    "ELIZAOS": TransferInfo("ELIZAOS", "SOL", 900.0, 90.0),
    "BTT": TransferInfo("BTT", "TRC20", 3200000.0, 180.0),
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
