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
import time
from datetime import datetime
from typing import Tuple

import psycopg


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
    ):
        self.conn = conn
        self.cursor = conn.cursor()
        self.logger = logging.getLogger(__name__)
        self.archive_dir = archive_dir
        self.retention_days = retention_days
        self.check_interval = check_interval
        self.chunk_rows = chunk_rows
        self._last_run = 0.0

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
        for table, where in self.TABLES:
            try:
                self._archive_table(table, where, (cutoff,))
            except (psycopg.Error, OSError) as e:
                self.conn.rollback()
                self.logger.error(
                    f"Архивация {table} не удалась, строки НЕ удалены: {e}"
                )
        return True

    def _archive_table(self, table: str, where: str, params: tuple):
        """Экспортирует строки по условию в .csv.gz, затем удаляет их же."""
        self.cursor.execute(f"SELECT COUNT(*) FROM {table} WHERE {where}", params)
        expected = self.cursor.fetchone()[0]
        if expected == 0:
            return

        os.makedirs(self.archive_dir, exist_ok=True)
        path = self._target_path(table)

        # Потоковый экспорт чанками — суточный объём arbitrage_opportunities
        # может достигать миллионов строк, fetchall() съел бы память
        self.cursor.execute(f"SELECT * FROM {table} WHERE {where}", params)
        columns = [d[0] for d in self.cursor.description]
        written = 0
        with gzip.open(path, "wt", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(columns)
            while True:
                chunk = self.cursor.fetchmany(self.chunk_rows)
                if not chunk:
                    break
                writer.writerows(chunk)
                written += len(chunk)

        # Удаление — только после успешно записанного файла
        self.cursor.execute(f"DELETE FROM {table} WHERE {where}", params)
        deleted = self.cursor.rowcount
        self.conn.commit()

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

    def _target_path(self, table: str) -> str:
        """data/archive/{table}_{YYYY-MM-DD}.csv.gz; при повторе за день — с временем."""
        date_str = datetime.now().strftime("%Y-%m-%d")
        path = os.path.join(self.archive_dir, f"{table}_{date_str}.csv.gz")
        if os.path.exists(path):
            time_str = datetime.now().strftime("%H%M%S")
            path = os.path.join(self.archive_dir, f"{table}_{date_str}_{time_str}.csv.gz")
        return path
