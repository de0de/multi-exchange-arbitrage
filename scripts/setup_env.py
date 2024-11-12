import os

def setup_environment():
    os.environ['DATABASE_URL'] = 'sqlite:///arbitrage_data.db'
    os.environ['BINANCE_API_KEY'] = 'your_api_key'
    os.environ['BINANCE_API_SECRET'] = 'your_secret_key' 