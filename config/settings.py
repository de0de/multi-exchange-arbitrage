import os
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = 'sqlite:///data/arbitrage_data.db'  # legacy (SQLite до миграции)

# PostgreSQL/TimescaleDB (docker-compose.yml); пароль переопределяется в .env
PG_HOST = os.getenv('PG_HOST', '127.0.0.1')
PG_PORT = int(os.getenv('PG_PORT', '5432'))
PG_DB = os.getenv('PG_DB', 'arbitrage')
PG_USER = os.getenv('PG_USER', 'arbitrage')
PG_PASSWORD = os.getenv('PG_PASSWORD', 'arbitrage_dev')

BINANCE_API_KEY = os.getenv('BINANCE_API_KEY')
BINANCE_API_SECRET = os.getenv('BINANCE_API_SECRET')

# Параметры стратегии — переопределяются через .env, дефолты совпадают с
# ранее захардкоженными в main.py значениями (поведение не меняется, если
# .env их не переопределяет)
MIN_SPREAD_PERCENT = float(os.getenv('MIN_SPREAD_PERCENT', '0.5'))
MIN_VOLUME_USDT = float(os.getenv('MIN_VOLUME_USDT', '1000.0'))
MAX_STALENESS_SECONDS = float(os.getenv('MAX_STALENESS_SECONDS', '15.0'))
OB_TTL_SECONDS = float(os.getenv('OB_TTL_SECONDS', '5.0'))
TRADE_SIZE_USDT = float(os.getenv('TRADE_SIZE_USDT', '1000.0'))  # рабочий депозит paper trading

# Горизонт retention для HistoryArchiver: строки старше этого возраста
# выгружаются в data/archive/*.csv.gz и удаляются из живой БД (архив
# остаётся полным, данные не теряются). Уменьшение окна — это потолок на
# рост живой БД при непредвиденном скачке притока, а не реакция на текущий
# темп (см. PLAN.md 5.5).
RETENTION_DAYS = float(os.getenv('RETENTION_DAYS', '7.0'))

# Push-URL монитора Uptime Kuma (генерируется в его веб-интерфейсе при
# создании push-монитора). Пустая строка = мониторинг выключен, бот
# работает как раньше — так локальная разработка не требует поднятого
# Kuma. Сам URL содержит секретный токен монитора, поэтому живёт в .env,
# а не в коде (репозиторий публичный).
UPTIME_KUMA_PUSH_URL = os.getenv('UPTIME_KUMA_PUSH_URL', '')