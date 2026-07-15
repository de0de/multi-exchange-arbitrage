"""
Paper Trading, Фаза 1: spot-spot стратегия (Realistic-модель).

Симуляция гипотетического исполнения арбитражной возможности:
    1. "Покупка" base currency на exchange_buy по ценам момента
       обнаружения (с учётом slippage по реальному стакану).
    2. Перевод монеты между биржами — реалистичная задержка и
       withdrawal fee из ручного словаря config/transfer_config.py.
    3. "Продажа" на exchange_sell по АКТУАЛЬНОМУ bid на момент
       hypothetical_close_at (цены не экстраполируются с момента
       обнаружения, а заново читаются из {exchange}_trading_pairs).

Ограничение модели: slippage на стороне продажи при закрытии не
пересчитывается по стакану — используется best bid из trading_pairs.

Частичное исполнение: если реальная глубина стакана не вмещает
запрошенный объём, реально исполнимая часть меньше запрошенной —
остаток переводится отдельно, что даёт ВТОРУЮ withdrawal fee
(при малой ликвидности это ощутимая фиксированная издержка).

Рекомендация по объёму: зависимость прибыли от объёма не монотонна
(снизу её давит фиксированная withdrawal fee, сверху — slippage),
поэтому вместо одного "минимального объёма" строится кривая
net_profit_percent по нескольким точкам объёма (volume_curve).
"""
import json
import logging
import time
from datetime import datetime

import psycopg
from typing import List, Optional, Tuple

from config.transfer_config import TransferInfo, get_transfer_info
from src.core.models.arbitrage_opportunity import ArbitrageOpportunity
from src.core.models.simulated_trade import (
    SimulatedTrade,
    OUTCOME_PROFITABLE,
    OUTCOME_UNPROFITABLE,
    OUTCOME_OPPORTUNITY_VANISHED,
    OUTCOME_FEE_UNKNOWN,
)
from src.core.paper_trading.base_strategy import BasePaperTradingStrategy
from src.core.spread_monitor import SpreadMonitor
from src.database.simulated_trade_repository import SimulatedTradeRepository


class SpotSpotStrategy(BasePaperTradingStrategy):
    """
    Spot-spot симуляция с transfer-delay закрытием.

    Пример использования (main loop, после SpreadMonitor.scan()):
        strategy = SpotSpotStrategy(conn, spread_monitor, trade_repo)
        opened = await strategy.open_positions(list(zip(ids, opportunities)))
        closed = await strategy.close_ready_positions()
    """

    def __init__(
        self,
        conn: psycopg.Connection,
        spread_monitor: SpreadMonitor,
        trade_repo: SimulatedTradeRepository,
        trade_size_usdt: float = 1000.0,
        min_profit_threshold_percent: float = 0.1,
        curve_volumes: Tuple[float, ...] = (100.0, 250.0, 500.0, 1000.0, 2500.0, 5000.0),
        max_close_staleness_seconds: float = 15.0,
    ):
        """
        Args:
            trade_size_usdt: размер симулируемой сделки. Дефолт $1000 —
                привязан к реалистичному рабочему депозиту: фиксированные
                издержки перевода ($1-2) на этом объёме дают 0.1-0.2%,
                а на малых объёмах ($100) те же издержки съедают 1-2% и
                почти любая сделка выглядит убыточной независимо от
                качества самого спреда.
            min_profit_threshold_percent: порог для рекомендации по объёму.
            curve_volumes: точки объёма для кривой net_profit_percent(volume).
            max_close_staleness_seconds: максимальный возраст цены при
                закрытии; старше — outcome=opportunity_vanished.
        """
        self.conn = conn
        self.cursor = conn.cursor()
        self.logger = logging.getLogger(__name__)
        self.spread_monitor = spread_monitor
        self.trade_repo = trade_repo
        self.trade_size_usdt = trade_size_usdt
        self.min_profit_threshold_percent = min_profit_threshold_percent
        self.curve_volumes = curve_volumes
        self.max_close_staleness_seconds = max_close_staleness_seconds

    # ------------------------------------------------------------------
    # Открытие позиций
    # ------------------------------------------------------------------

    async def open_positions(
        self,
        opportunities: List[Tuple[int, ArbitrageOpportunity]],
    ) -> int:
        opened = 0
        for opp_id, opp in opportunities:
            try:
                if await self._try_open(opp_id, opp):
                    opened += 1
            except Exception as e:
                self.logger.error(
                    f"Paper trading: ошибка открытия позиции {opp.standardized_pair}: {e}"
                )
        if opened:
            self.logger.info(f"Paper trading: открыто {opened} симулированных позиций")
        return opened

    async def _try_open(self, opp_id: int, opp: ArbitrageOpportunity) -> bool:
        # Коллизии тикеров и возможности без проверенного стакана не симулируем
        if opp.suspected_collision or not opp.slippage_available:
            return False

        # Дедупликация: одна открытая позиция на связку (пара + направление)
        if self.trade_repo.has_open_trade(
            opp.standardized_pair, opp.exchange_buy, opp.exchange_sell
        ):
            return False

        transfer = get_transfer_info(opp.base_currency)

        buy_ob, sell_ob = await self.spread_monitor.fetch_order_books_for_opportunity(opp)
        if buy_ob is None or sell_ob is None or not buy_ob.asks or not sell_ob.bids:
            self.logger.debug(
                f"Paper trading: нет Order Book для {opp.standardized_pair}, позиция не открыта"
            )
            return False

        # Кривая прибыльности по объёмам + точка рабочего депозита
        curve = [
            self._evaluate_volume(opp, buy_ob, sell_ob, transfer, v)
            for v in self.curve_volumes
        ]
        entry = self._evaluate_volume(opp, buy_ob, sell_ob, transfer, self.trade_size_usdt)

        if entry["executable_usdt"] <= 0:
            self.logger.debug(
                f"Paper trading: пустой стакан для {opp.standardized_pair}, позиция не открыта"
            )
            return False

        partial_fill = entry["n_transfers"] > 1
        n_transfers = entry["n_transfers"]

        # Цена покупки с учётом slippage; весь запрошенный объём считается
        # исполненным, но при partial_fill остаток переводится отдельно
        # (вторая withdrawal fee уже учтена в n_transfers)
        effective_buy_price = opp.buy_price * (1 + entry["buy_slippage_percent"] / 100.0)
        executed_usdt = self.trade_size_usdt
        buy_fee = opp.buy_exchange_fee_percent / 100.0
        base_amount = executed_usdt * (1 - buy_fee) / effective_buy_price

        if transfer.fee_unknown or transfer.withdrawal_fee is None:
            fee_coin_total: Optional[float] = None
            fee_usdt_total: Optional[float] = None
        else:
            fee_coin_total = n_transfers * transfer.withdrawal_fee
            fee_usdt_total = fee_coin_total * opp.buy_price

        entry_ts = opp.timestamp
        trade = SimulatedTrade(
            opportunity_id=opp_id,
            entry_detected_at=entry_ts,
            entry_readable_time=datetime.fromtimestamp(entry_ts).strftime('%Y-%m-%d %H:%M:%S'),
            requested_volume_usdt=self.trade_size_usdt,
            executed_volume_usdt=executed_usdt,
            partial_fill=partial_fill,
            entry_buy_price_effective=effective_buy_price,
            base_amount=base_amount,
            transfer_network=transfer.network,
            expected_transfer_seconds=transfer.transfer_seconds,
            hypothetical_close_at=entry_ts + transfer.transfer_seconds,
            withdrawal_fee_coin=fee_coin_total,
            withdrawal_fee_usdt=fee_usdt_total,
            fee_unknown=transfer.fee_unknown,
            volume_curve=json.dumps(curve),
        )
        self.trade_repo.save_trade(trade)

        self.logger.info(
            f"Paper trading OPEN #{trade.id}: {opp.standardized_pair} "
            f"{opp.exchange_buy} → {opp.exchange_sell}, "
            f"${executed_usdt:.0f} @ {effective_buy_price:.6g} "
            f"(net_spread={opp.net_spread_percent:.3f}%), "
            f"перевод {opp.base_currency}/{transfer.network} "
            f"~{transfer.transfer_seconds/60:.0f} мин"
            f"{', fee_unknown' if transfer.fee_unknown else ''}"
            f"{', PARTIAL FILL (2-я withdrawal fee)' if partial_fill else ''}"
        )
        self._log_volume_recommendation(opp, curve)
        return True

    def _evaluate_volume(
        self,
        opp: ArbitrageOpportunity,
        buy_ob,
        sell_ob,
        transfer: TransferInfo,
        volume_usdt: float,
    ) -> dict:
        """
        Считает ожидаемый net_profit_percent для заданного объёма сделки:
        slippage обеих сторон по реальному стакану (переиспользует
        SpreadMonitor._calc_slippage) + торговые комиссии + withdrawal fee
        (вторая комиссия при частичном исполнении).
        """
        target_base = volume_usdt / opp.buy_price
        buy_slip = self.spread_monitor._calc_slippage(buy_ob, is_buy_side=True, target_volume=target_base)
        sell_slip = self.spread_monitor._calc_slippage(sell_ob, is_buy_side=False, target_volume=target_base)

        executable_usdt = min(
            buy_slip.filled_volume * opp.buy_price,
            sell_slip.filled_volume * opp.sell_price,
        )
        # Стакан не вместил объём (допуск 1%) → остаток отдельным переводом
        n_transfers = 1 if executable_usdt >= volume_usdt * 0.99 else 2

        fee_included = not (transfer.fee_unknown or transfer.withdrawal_fee is None)
        withdrawal_fee_percent = 0.0
        if fee_included:
            withdrawal_fee_percent = (
                n_transfers * transfer.withdrawal_fee * opp.buy_price / volume_usdt * 100.0
            )

        net_profit_percent = (
            opp.raw_spread_percent
            - opp.buy_exchange_fee_percent
            - opp.sell_exchange_fee_percent
            - buy_slip.price_impact_percent
            - sell_slip.price_impact_percent
            - withdrawal_fee_percent
        )

        return {
            "volume_usdt": volume_usdt,
            "net_profit_percent": round(net_profit_percent, 4),
            "buy_slippage_percent": round(buy_slip.price_impact_percent, 4),
            "sell_slippage_percent": round(sell_slip.price_impact_percent, 4),
            "withdrawal_fee_percent": round(withdrawal_fee_percent, 4),
            "executable_usdt": round(executable_usdt, 2),
            "n_transfers": n_transfers,
            "fee_included": fee_included,
        }

    def _log_volume_recommendation(self, opp: ArbitrageOpportunity, curve: List[dict]):
        """
        Логирует рекомендацию по объёму на основе кривой.

        Кривая обычно имеет форму "холма" (не монотонна), поэтому
        показывается диапазон прибыльных объёмов и точка максимума,
        а не одно "минимальное" число.
        """
        threshold = self.min_profit_threshold_percent
        above = [p for p in curve if p["net_profit_percent"] >= threshold]
        best = max(curve, key=lambda p: p["net_profit_percent"])

        if not above:
            self.logger.info(
                f"    Объём: ни одна точка ${curve[0]['volume_usdt']:.0f}-"
                f"${curve[-1]['volume_usdt']:.0f} не даёт >={threshold}% "
                f"(максимум {best['net_profit_percent']:.3f}% при ${best['volume_usdt']:.0f})"
            )
            return

        lo = min(p["volume_usdt"] for p in above)
        hi = max(p["volume_usdt"] for p in above)
        fee_note = "" if best["fee_included"] else " [без withdrawal fee — монета вне словаря]"
        self.logger.info(
            f"    Объём: прибыльно (>={threshold}%) в диапазоне ${lo:.0f}-${hi:.0f}, "
            f"максимум {best['net_profit_percent']:.3f}% при ${best['volume_usdt']:.0f}"
            f"{fee_note}"
        )

    # ------------------------------------------------------------------
    # Закрытие позиций
    # ------------------------------------------------------------------

    async def close_ready_positions(self) -> int:
        now = time.time()
        ready = self.trade_repo.get_open_trades_ready_to_close(now)
        closed = 0
        for row in ready:
            try:
                self._close_trade(row, now)
                closed += 1
            except Exception as e:
                self.logger.error(
                    f"Paper trading: ошибка закрытия сделки #{row['id']}: {e}"
                )
        return closed

    def _close_trade(self, row: dict, now: float):
        """
        Закрывает одну сделку по актуальным ценам из {exchange}_trading_pairs.

        Продажа исполняется по текущему bid биржи продажи; текущий ask
        биржи покупки сохраняется справочно (спред на момент закрытия).
        """
        pair = row["standardized_pair"]
        readable_now = datetime.fromtimestamp(now).strftime('%Y-%m-%d %H:%M:%S')

        sell_prices = self._get_current_prices(row["exchange_sell"], pair, now)
        buy_prices = self._get_current_prices(row["exchange_buy"], pair, now)
        close_price_buy = buy_prices["ask"] if buy_prices else None

        # Нет свежей цены продажи → честно посчитать закрытие нельзя
        if sell_prices is None or sell_prices["bid"] is None or sell_prices["bid"] <= 0:
            self.trade_repo.close_trade(
                trade_id=row["id"],
                closed_at=now,
                close_readable_time=readable_now,
                outcome=OUTCOME_OPPORTUNITY_VANISHED,
                close_price_buy=close_price_buy,
            )
            self.logger.info(
                f"Paper trading CLOSE #{row['id']}: {pair} — OPPORTUNITY_VANISHED "
                f"(нет свежей цены на {row['exchange_sell']})"
            )
            return

        close_price_sell = sell_prices["bid"]
        sell_fee = (row["sell_exchange_fee_percent"] or 0.0) / 100.0

        # Withdrawal fee вычитается из переводимого объёма монеты
        base_received = row["base_amount"] - (row["withdrawal_fee_coin"] or 0.0)
        proceeds_usdt = max(base_received, 0.0) * close_price_sell * (1 - sell_fee)

        executed = row["executed_volume_usdt"]
        realized_usdt = proceeds_usdt - executed
        realized_percent = realized_usdt / executed * 100.0 if executed else 0.0

        if row["fee_unknown"]:
            outcome = OUTCOME_FEE_UNKNOWN
        elif realized_usdt > 0:
            outcome = OUTCOME_PROFITABLE
        else:
            outcome = OUTCOME_UNPROFITABLE

        self.trade_repo.close_trade(
            trade_id=row["id"],
            closed_at=now,
            close_readable_time=readable_now,
            outcome=outcome,
            close_price_buy=close_price_buy,
            close_price_sell=close_price_sell,
            realized_profit_usdt=realized_usdt,
            realized_profit_percent=realized_percent,
        )

        planned_delay = row["hypothetical_close_at"] - row["entry_detected_at"]
        actual_delay = now - row["entry_detected_at"]
        self.logger.info(
            f"Paper trading CLOSE #{row['id']}: {pair} "
            f"{row['exchange_buy']} → {row['exchange_sell']}, "
            f"результат {realized_usdt:+.2f} USDT ({realized_percent:+.3f}%), "
            f"outcome={outcome}, "
            f"задержка план/факт {planned_delay/60:.0f}/{actual_delay/60:.0f} мин"
        )

    def _get_current_prices(
        self, exchange_display: str, standardized_pair: str, now: float
    ) -> Optional[dict]:
        """
        Актуальные bid/ask из {exchange}_trading_pairs.

        Возвращает None, если пары нет в таблице или данные протухли
        (старше max_close_staleness_seconds) — цены НЕ экстраполируются.
        """
        exchange_key = self.spread_monitor._find_exchange_key_by_display(exchange_display)
        if exchange_key is None:
            return None
        table = f"{exchange_key}_trading_pairs"
        try:
            self.cursor.execute(
                f"SELECT bid, ask, timestamp FROM {table} WHERE standardized_pair = %s LIMIT 1",
                (standardized_pair,),
            )
        except psycopg.Error as e:
            self.conn.rollback()
            self.logger.debug(f"Paper trading: не читается {table}: {e}")
            return None
        row = self.cursor.fetchone()
        if row is None:
            return None
        bid, ask, ts = row
        if ts is None or (now - ts) > self.max_close_staleness_seconds:
            return None
        return {"bid": bid, "ask": ask, "timestamp": ts}
