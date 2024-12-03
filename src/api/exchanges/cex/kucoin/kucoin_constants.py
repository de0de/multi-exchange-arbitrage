# Константы для работы с KuCoin API

# Базовые URL
BASE_URL = "https://api.kucoin.com"

# Название биржи
EXCHANGE_NAME = "KuCoin"

# Endpoints
ENDPOINTS = {
    'exchange_info': '/api/v1/symbols',
    'ticker': '/api/v1/market/allTickers',
    'trade_fee': '/api/v1/trade-fees',
    'server_time': '/api/v1/timestamp'
}

# Временные интервалы (в секундах)
INTERVALS = {
    'fee_update': 86400,  # 24 часа
    'recv_window': 5000,  # 5 секунд
}

# Статусы
TRADING_STATUS = 'TRADE'
