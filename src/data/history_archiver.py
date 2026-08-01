"""
Архиватор истории: экспорт устаревших строк в .csv.gz ПЕРЕД retention-удалением.

Заменяет "слепое" retention-удаление в мониторах (DATA_SPECIFICATION.md, раздел 6):
всё, что выходит за горизонт retention_days, сначала выгружается в переносимый
.csv.gz (data/archive/), затем удаляется из БД. Строки не удаляются, если
экспорт не удался.

Файлы из data/archive/ скачиваются вручную (scp/WinSCP) или отправляются в
облако (rclone → Backblaze B2) — на код архиватора это не влияет, облачная
отправка добавляется позже как отдельная cron-команда.

Таблицы:
- spread_history, futures_spread_history — все строки старше cutoff;
- arbitrage_opportunities — старше cutoff, КРОМЕ строк, на которые ссылаются
  simulated_trades (сделки не должны терять контекст пары/бирж; таких строк
  мало благодаря дедупликации открытий).
"""
import csv
import gzip
import logging
import os
import threading
import time
from datetime import datetime
from typing import Tuple

import psycopg


class ArchiveCancelled(Exception):
    """Архивация прервана остановкой сервиса (не ошибка БД/диска)."""


class HistoryArchiver:
    """Ежесуточный экспорт+retention растущих таблиц истории."""

    TABLES: Tuple[Tuple[str, str], ...] = (
        ("spread_history", "timestamp < %s"),
        ("futures_spread_history", "timestamp < %s"),
        ("arbitrage_opportunities",
         "timestamp < %s AND id NOT IN (SELECT opportunity_id FROM simulated_trades)"),
    )

    def __init__(
        self,
        conn: psycopg.Connection,
        archive_dir: str = "data/archive",
        retention_days: float = 14.0,
        check_interval: float = 86400.0,
        chunk_rows: int = 50000,
        statement_timeout_ms: int = 900000,
    ):
        self.conn = conn
        self.cursor = conn.cursor()
        self.logger = logging.getLogger(__name__)
        self.archive_dir = archive_dir
        self.retention_days = retention_days
        self.check_interval = check_interval
        self.chunk_rows = chunk_rows
        # Предохранитель от зомби-транзакций: любой запрос архивации, который
        # выходит за этот лимит, снимается самим PostgreSQL. Инциденты
        # 2026-07-31/08-01 (PLAN.md 5.5) висели 8.5 часов именно потому, что
        # на сервере statement_timeout = 0 и остановить их было нечем.
        self.statement_timeout_ms = statement_timeout_ms
        self._last_run = 0.0
        # Взводится на время архивации: главный поток по нему понимает, есть
        # ли что отменять при получении сигнала остановки.
        self._active = threading.Event()
        # Взводится при отмене: без него цикл по таблицам после снятого
        # запроса пошёл бы архивировать следующую таблицу и завис бы уже
        # на ней — при остановке сервиса нужно выйти из прогона целиком.
        self._cancelled = threading.Event()

    def run_if_due(self, now: float = None) -> bool:
        """
        Запускает архивацию, если с прошлого запуска прошло check_interval.
        Вызывается из main loop каждый цикл — дешёвая проверка по времени.
        """
        now = now if now is not None else time.time()
        if now - self._last_run < self.check_interval:
            return False
        self._last_run = now

        cutoff = now - self.retention_days * 86400
        self._cancelled.clear()
        self._active.set()
        try:
            for table, where in self.TABLES:
                if self._cancelled.is_set():
                    self.logger.info(
                        f"Архивация прервана до обработки {table} — остановка сервиса"
                    )
                    break
                try:
                    self._archive_table(table, where, (cutoff,))
                except ArchiveCancelled:
                    self.conn.rollback()
                    self.logger.info(
                        f"Архивация {table} прервана остановкой сервиса, "
                        f"строки НЕ удалены"
                    )
                except (psycopg.Error, OSError) as e:
                    self.conn.rollback()
                    self.logger.error(
                        f"Архивация {table} не удалась, строки НЕ удалены: {e}"
                    )
        finally:
            self._active.clear()
        return True

    def cancel_running(self) -> bool:
        """
        Прерывает выполняющийся сейчас запрос архивации на стороне PostgreSQL.

        Вызывается из главного потока (обработчик SIGTERM/SIGINT), пока
        архивация идёт в фоновом потоке asyncio.to_thread(). Без этого при
        остановке сервиса получается зомби-транзакция: Python-процесс убивают
        по TimeoutStopSec, а backend PostgreSQL продолжает выполнять DELETE
        часами, потому что в середине запроса он сокет не читает и о разрыве
        соединения не узнаёт (PLAN.md 5.5, три инцидента 2026-08-01).

        Используется conn.cancel(), а не cancel_safe(): cancel() отправляет
        запрос отмены через отдельный объект libpq (PQcancel) и не блокирует
        вызывающий поток — это то, что можно звать из обработчика сигнала.
        cancel_safe() ждёт подтверждения с таймаутом, что для сигнального
        пути не подходит.

        Возвращает True, если отмена была отправлена.
        """
        if not self._active.is_set():
            return False
        self._cancelled.set()
        try:
            self.conn.cancel()
        except Exception as e:  # отмена не должна ломать путь остановки
            self.logger.warning(f"Не удалось отменить запрос архивации: {e}")
            return False
        self.logger.info("Архивация прервана: отправлен отмена-запрос в PostgreSQL")
        return True

    def _archive_table(self, table: str, where: str, params: tuple):
        """Экспортирует строки по условию в .csv.gz, затем удаляет их же."""
        # SET LOCAL действует до конца текущей транзакции и сбрасывается на
        # commit/rollback — главный цикл, работающий через ЭТО ЖЕ соединение,
        # чужой таймаут себе не заберёт. SET не принимает параметры запроса,
        # поэтому функциональная форма set_config(..., is_local=true).
        self.cursor.execute(
            "SELECT set_config('statement_timeout', %s, true)",
            (str(self.statement_timeout_ms),),
        )

        self.cursor.execute(f"SELECT COUNT(*) FROM {table} WHERE {where}", params)
        expected = self.cursor.fetchone()[0]
        if expected == 0:
            return

        os.makedirs(self.archive_dir, exist_ok=True)
        path = self._target_path(table)

        try:
            # Потоковый экспорт чанками — суточный объём arbitrage_opportunities
            # может достигать миллионов строк, fetchall() съел бы память
            self.cursor.execute(f"SELECT * FROM {table} WHERE {where}", params)
            columns = [d[0] for d in self.cursor.description]
            written = 0
            with gzip.open(path, "wt", encoding="utf-8", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(columns)
                while True:
                    # Запись gzip идёт минутами, и всё это время на стороне БД
                    # ничего не выполняется — conn.cancel() тут отменять
                    # нечего. Без явной проверки флага остановка сервиса,
                    # пришедшая в эту фазу, была бы замечена только ПОСЛЕ
                    # того, как архиватор успел бы запустить DELETE.
                    if self._cancelled.is_set():
                        raise ArchiveCancelled(table)
                    chunk = self.cursor.fetchmany(self.chunk_rows)
                    if not chunk:
                        break
                    writer.writerows(chunk)
                    written += len(chunk)

            if self._cancelled.is_set():
                raise ArchiveCancelled(table)

            # Удаление — только после успешно записанного файла
            self.cursor.execute(f"DELETE FROM {table} WHERE {where}", params)
            deleted = self.cursor.rowcount
            self.conn.commit()
        except BaseException:
            # Оборванный .csv.gz бесполезен: строки остались в БД и будут
            # выгружены заново следующим прогоном, а файл занял бы место на
            # архивном Volume и попал бы в data lake обрезком/дублем. После
            # инцидентов 2026-08-01 на проде осталось два таких файла.
            self._discard_partial(path)
            raise

        size_mb = os.path.getsize(path) / (1024 * 1024)
        self.logger.info(
            f"Архивация {table}: {written} строк -> {path} ({size_mb:.1f} МБ), "
            f"удалено {deleted}"
        )
        if deleted != written:
            self.logger.warning(
                f"Архивация {table}: экспортировано {written}, удалено {deleted} — "
                f"расхождение, проверить вручную"
            )

    def _discard_partial(self, path: str):
        """Удаляет незавершённый файл архива после прерванной архивации."""
        try:
            if os.path.exists(path):
                os.remove(path)
                self.logger.warning(f"Удалён незавершённый архив {path}")
        except OSError as e:
            self.logger.warning(f"Не удалось удалить незавершённый архив {path}: {e}")

    def _target_path(self, table: str) -> str:
        """data/archive/{table}_{YYYY-MM-DD}.csv.gz; при повторе за день — с временем."""
        date_str = datetime.now().strftime("%Y-%m-%d")
        path = os.path.join(self.archive_dir, f"{table}_{date_str}.csv.gz")
        if os.path.exists(path):
            time_str = datetime.now().strftime("%H%M%S")
            path = os.path.join(self.archive_dir, f"{table}_{date_str}_{time_str}.csv.gz")
        return path
