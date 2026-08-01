"""
Ежесуточная сводка работы бота в лог.

Одна INFO-сводка в сутки (первая — сразу при старте процесса): счётчики за
последние 24 часа по всем потокам данных и paper trading + размер БД.
Нужна для автономного прогона на VPS: состояние системы читается из лога
за секунды, без ручных SQL-запросов.
"""
import logging
import threading
import time
from typing import Optional

import psycopg


class DailyReport:
    """Сводка по БД раз в сутки; вызывается из main loop дешёвой проверкой."""

    def __init__(
        self,
        conn: psycopg.Connection,
        interval: float = 86400.0,
    ):
        self.conn = conn
        self.cursor = conn.cursor()
        self.interval = interval
        self.logger = logging.getLogger(__name__)
        self._last_run = 0.0
        # Взводится на время построения сводки — по нему обработчик сигнала
        # понимает, есть ли что отменять при остановке сервиса.
        self._active = threading.Event()

    def log_if_due(self, now: Optional[float] = None) -> bool:
        now = now if now is not None else time.time()
        if now - self._last_run < self.interval:
            return False
        self._last_run = now
        self._active.set()
        try:
            self._log_report(now - 86400)
        except psycopg.Error as e:
            self.conn.rollback()
            self.logger.error(f"DailyReport: ошибка построения сводки: {e}")
        finally:
            self._active.clear()
        return True

    def cancel_running(self) -> bool:
        """
        Снимает выполняющийся сейчас запрос сводки на стороне PostgreSQL.

        Тот же путь, что у HistoryArchiver.cancel_running(): сводка считается
        в фоновом потоке asyncio.to_thread(), и прервать её запрос можно
        только отменой на стороне БД — закрытие Python-процесса backend в
        середине запроса не останавливает (PLAN.md 5.5).
        """
        if not self._active.is_set():
            return False
        try:
            self.conn.cancel()
        except Exception as e:  # отмена не должна ломать путь остановки
            self.logger.warning(f"DailyReport: не удалось отменить запрос: {e}")
            return False
        self.logger.info("DailyReport: построение сводки прервано, запрос снят")
        return True

    def _count(self, sql: str, params: tuple = ()) -> int:
        self.cursor.execute(sql, params)
        row = self.cursor.fetchone()
        return row[0] if row and row[0] is not None else 0

    def _id_boundary(self, table: str, since: float) -> Optional[int]:
        """
        Наименьший существующий id, у которого timestamp > since.

        Зачем это вместо прямого `WHERE timestamp > since`: у растущих таблиц
        истории нет индекса с timestamp в ведущей позиции, поэтому такой
        фильтр даёт полный seq scan — по замерам на проде вся сводка стоила
        4 мин 45 с (2026-08-01) и 4 мин 53 с (2026-07-31), из них львиная
        доля — скан 19-гигабайтной futures_spread_history. Таблицы
        append-only, id (BIGSERIAL) монотонен по времени вставки, PK-индекс
        уже есть — значит границу суток можно найти двоичным поиском за
        ~20 точечных чтений, а счёт свести к диапазону по PK.

        Точность — одна пачка вставки: внутри цикла строки делят общий
        timestamp и получают подряд идущие id. Для суточной сводки в лог
        этого достаточно.

        Пробуем не сам mid, а первую существующую строку правее него в
        пределах [mid, hi] — таблицы дырявые (retention удаляет старые
        строки, но оставляет те, на которые ссылаются simulated_trades).

        Возвращает None, если свежее since строк нет (или таблица пуста).
        """
        self.cursor.execute(f"SELECT min(id), max(id) FROM {table}")
        lo, hi = self.cursor.fetchone()
        if lo is None:
            return None

        boundary = None
        while lo <= hi:
            mid = (lo + hi) // 2
            self.cursor.execute(
                f"SELECT id, timestamp FROM {table} "
                f"WHERE id >= %s AND id <= %s ORDER BY id LIMIT 1",
                (mid, hi),
            )
            row = self.cursor.fetchone()
            if row is None:          # в [mid, hi] строк нет
                hi = mid - 1
                continue
            rid, ts = row
            if ts is not None and ts > since:
                boundary = rid
                hi = rid - 1
            else:
                lo = rid + 1
        return boundary

    def _count_since(self, table: str, since: float, extra: str = "") -> int:
        """COUNT(*) за последние сутки через диапазон по PK (см. _id_boundary)."""
        boundary = self._id_boundary(table, since)
        if boundary is None:
            return 0
        where = f"id >= %s" + (f" AND {extra}" if extra else "")
        return self._count(f"SELECT COUNT(*) FROM {table} WHERE {where}", (boundary,))

    def _log_report(self, since: float):
        # Три растущие таблицы истории — через диапазон по PK (_id_boundary),
        # иначе каждая даёт полный seq scan: 5 ГБ + 4 ГБ + 19 ГБ за сводку.
        opps = self._count_since("arbitrage_opportunities", since)
        collisions = self._count_since(
            "arbitrage_opportunities", since, "suspected_collision = 1")
        spread_rows = self._count_since("spread_history", since)
        futures_rows = self._count_since("futures_spread_history", since)
        # funding_rate_history (101 МБ) и simulated_trades (199 МБ) остаются
        # на прямом фильтре: seq scan такого размера — доли секунды.
        funding_rows = self._count(
            "SELECT COUNT(*) FROM funding_rate_history WHERE timestamp > %s", (since,))
        opened = self._count(
            "SELECT COUNT(*) FROM simulated_trades WHERE entry_detected_at > %s", (since,))
        open_now = self._count(
            "SELECT COUNT(*) FROM simulated_trades WHERE status = 'open'")

        self.cursor.execute("""
            SELECT outcome, COUNT(*), ROUND(AVG(realized_profit_percent)::numeric, 3)
            FROM simulated_trades WHERE closed_at > %s GROUP BY outcome
        """, (since,))
        outcomes = self.cursor.fetchall()

        db_mb = self._count("SELECT pg_database_size(current_database())") / (1024 * 1024)

        if outcomes:
            closed_str = ", ".join(
                f"{outcome}: {n}" + (f" (avg {avg:+.3f}%)" if avg is not None else "")
                for outcome, n, avg in outcomes
            )
        else:
            closed_str = "0"

        self.logger.info(
            f"СУТОЧНАЯ СВОДКА (последние 24 ч):\n"
            f"    возможности: {opps} (из них коллизий: {collisions})\n"
            f"    история: spread={spread_rows}, futures={futures_rows}, "
            f"funding_changes={funding_rows}\n"
            f"    paper trading: открыто {opened}, сейчас открытых позиций {open_now}\n"
            f"    закрыто за сутки: {closed_str}\n"
            f"    размер БД: {db_mb:.0f} МБ"
        )
