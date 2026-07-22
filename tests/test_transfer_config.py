"""
Тесты get_transfer_info() / TRANSFER_TABLE — точечно на изменения от
2026-07-22/23 (две партии data-driven пополнения словаря переводов).
Без сетевых вызовов: модуль работает только со статическим словарём.
"""
from config.transfer_config import get_transfer_info, TRANSFER_TABLE


def test_known_coin_from_batch_2026_07_14():
    info = get_transfer_info("REACT")
    assert info.fee_unknown is False
    assert info.network == "react"
    assert info.withdrawal_fee == 40.0


def test_known_coin_from_batch_2026_07_23():
    info = get_transfer_info("NKN")
    assert info.fee_unknown is False
    assert info.network == "ERC20"
    assert info.withdrawal_fee == 111.744

    info_era = get_transfer_info("ERA")
    assert info_era.fee_unknown is False
    assert info_era.network == "ERC20"
    assert info_era.withdrawal_fee == 9.0


def test_coin_with_withdrawal_disabled_everywhere_is_not_in_table():
    """
    BANK/NIGHT/ACE/NFP найдены на KuCoin при пополнении 2026-07-23, но
    withdrawal был отключён на всех сетях на момент проверки — реальной
    комиссии нет, поэтому они НЕ были добавлены в TRANSFER_TABLE (см.
    PLAN.md 5.1, "Второе пополнение — 2026-07-23"). Это отличается от
    формулировки "присутствуют в словаре, но помечены fee_unknown=True" —
    в коде для них нет отдельной записи, они проходят тот же fallback,
    что и любая совсем неизвестная монета (см. test ниже). Тест фиксирует
    оба факта: их нет в TRANSFER_TABLE, и на выходе они всё равно и
    корректно дают fee_unknown=True.
    """
    for coin in ("BANK", "NIGHT", "ACE", "NFP"):
        assert coin not in TRANSFER_TABLE, (
            f"{coin} неожиданно оказался в TRANSFER_TABLE — "
            f"если это осознанное добавление, обновить и этот тест, и комментарий в PLAN.md"
        )
        info = get_transfer_info(coin)
        assert info.fee_unknown is True
        assert info.withdrawal_fee is None


def test_coin_not_in_dictionary_at_all():
    info = get_transfer_info("RSC")
    assert info.fee_unknown is True
    assert info.withdrawal_fee is None
    assert info.network == "unknown"


def test_control_case_btc_unaffected():
    """BTC не менялся сегодняшними правками — поведение должно остаться прежним."""
    info = get_transfer_info("BTC")
    assert info.fee_unknown is False
    assert info.network == "BTC"
    assert info.withdrawal_fee == 0.0002
    assert info.transfer_seconds == 2400.0


def test_lookup_is_case_insensitive():
    assert get_transfer_info("btc").fee_unknown is False
    assert get_transfer_info("bTc").withdrawal_fee == get_transfer_info("BTC").withdrawal_fee
