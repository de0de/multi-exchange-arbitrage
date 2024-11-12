import os
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = 'sqlite:///data/arbitrage_data.db'
BINANCE_API_KEY = os.getenv('BINANCE_API_KEY')
BINANCE_API_SECRET = os.getenv('BINANCE_API_SECRET') 