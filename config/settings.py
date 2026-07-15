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