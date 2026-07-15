"""
Подключение к PostgreSQL/TimescaleDB (docker-compose.yml).

Принцип проекта сохраняется: ОДНО соединение на процесс, создаётся в
main.py через connect() и передаётся во все репозитории. Репозитории
сами вызывают conn.commit() после записи (как и раньше с sqlite3).

Гипертаблицы TimescaleDB на этом этапе не используются (timestamp хранится
как epoch float, а dimension-колонка TimescaleDB требует INT/TIMESTAMPTZ) —
перевод на гипертаблицы + сжатие вынесен в отдельную задачу (PLAN.md 5.5).
"""
import psycopg

from config.settings import PG_HOST, PG_PORT, PG_DB, PG_USER, PG_PASSWORD


def connect() -> psycopg.Connection:
    """Создаёт соединение с PostgreSQL из настроек (.env / дефолты compose)."""
    return psycopg.connect(
        host=PG_HOST,
        port=PG_PORT,
        dbname=PG_DB,
        user=PG_USER,
        password=PG_PASSWORD,
    )
