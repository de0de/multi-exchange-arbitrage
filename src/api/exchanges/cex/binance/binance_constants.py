# Константы для работы с Binance API

# Базовые URL
BASE_URL = "https://api.binance.com"
SPOT_BASE_URL = "https://api.binance.com"
SAPI_BASE_URL = "https://api.binance.com"

# Название биржи
EXCHANGE_NAME = "Binance"

# Endpoints
ENDPOINTS = {
    'exchange_info': '/api/v3/exchangeInfo',
    'book_ticker': '/api/v3/ticker/bookTicker',
    'ticker_24hr': '/api/v3/ticker/24hr',
    'trade_fee': '/sapi/v1/asset/tradeFee',
    'network_info': '/sapi/v1/capital/config/getall',
    'server_time': '/api/v3/time'
}

# Временные интервалы (в секундах)
INTERVALS = {
    'fee_update': 86400,  # 24 часа
    'network_update': 86400,  # 24 часа
    'recv_window': 5000,  # 5 секунд
}

# Статусы
TRADING_STATUS = 'TRADING'