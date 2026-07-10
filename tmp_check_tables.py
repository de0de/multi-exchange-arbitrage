import sqlite3
from config.settings import DATABASE_URL

db_path = DATABASE_URL.replace('sqlite:///', '')
conn = sqlite3.connect(db_path)

tables = [t[0] for t in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE '%order_book'").fetchall()]
print("Tables with 'order_book':")
for t in tables:
    cnt = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
    print(f"  {t}: {cnt} записей")
    if cnt:
        row = conn.execute(f"SELECT original_pair, standardized_pair FROM {t} LIMIT 1").fetchone()
        print(f"    Пример: {row}")
conn.close()