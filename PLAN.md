# Multi-Exchange Arbitrage — Architecture & Development Plan

## 1. Обзор проекта

**Назначение:** Асинхронный сбор рыночных данных с криптовалютных бирж для поиска арбитражных возможностей.

**Стек:** Python 3.10+, asyncio, aiohttp (HTTP-клиент), SQLite (хранение данных), python-dotenv (конфигурация)

**Запуск:**
```
D:\multi-exchange-arbitrage\venv\Scripts\python.exe main.py
```

**Важно:** Всегда использовать python из venv (`venv\Scripts\python.exe`). Не использовать `python -c "..."` для многострочного кода с кириллицей (баг PSReadLine в PowerShell). Для тестов создавать временный `.py` файл.

---

## 2. Архитектура (текущее состояние)

### 2.1. Структура папок

```
multi-exchange-arbitrage/
├── main.py                          # Точка входа, оркестратор
├── config/
│   └── settings.py                  # DATABASE_URL, ключи из .env
├── src/
│   ├── api/exchanges/cex/           # API-клиенты централизованных бирж
│   │   ├── base_cex_exchange.py     # Базовый класс: aiohttp-сессия, _make_request, retry, hmac-подпись
│   │   ├── binance/
│   │   │   └── binance_spot_api.py  # Binance Spot (публичный, 1366 пар)
│   │   └── kucoin/
│   │       └── kucoin_spot_api.py   # KuCoin Spot (публичный, 1037 пар)
│   ├── core/models/
│   │   ├── pair_data.py             # PairData: цена, объём, bid/ask, метка времени
│   │   ├── currencies.py            # Currency
│   │   └── exchanges.py             # Exchange (name, maker_fee, taker_fee)
│   ├── data/collectors/cex/          # Сборщики данных (API → БД)
│   │   ├── base_collector.py        # Абстрактный базовый класс
│   │   ├── binance_collector.py     # Binance: fetch → save_trading_pairs
│   │   └── kucoin_collector.py      # KuCoin: fetch → save_trading_pairs
│   ├── database/
│   │   ├── base_repository.py       # Абстрактный репозиторий
│   │   ├── market_repository.py     # {exchange}_trading_pairs (UPSERT)
│   │   ├── currencies_repository.py # Справочник валют
│   │   ├── exchanges_repository.py  # Справочник бирж (с комиссиями)
│   │   └── trading_pairs_repository.py # unique_pairs (дедупликация)
│   └── utils/
│       ├── logger.py                # RotatingFileHandler + stdout, UTF-8
│       ├── health_monitor.py        # Статусы бирж, latency, error rate
│       └── retry.py                 # Декоратор async_retry (3 попытки, exponential backoff)
├── data/
│   └── arbitrage_data.db            # SQLite-база
├── logs/
│   └── arbitrage_YYYY-MM-DD.log     # Логи с ротацией (10 МБ × 10 файлов)
├── .env                             # BINANCE_API_KEY, BINANCE_API_SECRET (не в git)
└── requirements.txt
```

### 2.2. Основные потоки вызовов

```
main()
├── setup_logging()
├── sqlite3.connect() → conn
├── BinanceSpotAPI(), KuCoinSpotAPI()
├── MarketRepository(db_path, "binance"), MarketRepository(db_path, "kucoin")
├── CurrenciesRepository(conn)
├── ExchangesRepository(db_path)
├── TradingPairsRepository(conn)
├── health_monitor.start_monitoring(report_interval=300)
│
├── [initial fill]
│   ├── binance_collector.collect_data()
│   │   ├── exchanges_repo.get_or_create_exchange_id("Binance", fees)
│   │   ├── binance_api.fetch_trading_pairs()
│   │   │   └── _make_request("GET", "/api/v3/exchangeInfo")
│   │   │   └── _make_request("GET", "/api/v3/ticker/bookTicker")
│   │   │   └── _make_request("GET", "/api/v3/ticker/24hr")
│   │   └── market_repo.save_trading_pairs(pairs)  # UPSERT
│   ├── kucoin_collector.collect_data()          # аналогично
│   ├── currencies_repo.extract_unique_currencies()
│   ├── currencies_repo.populate_currencies_table()
│   ├── trading_pairs_repo.extract_unique_trading_pairs()
│   ├── trading_pairs_repo.populate_unique_trading_pairs_table()
│   ├── market_repo.update_currency_ids()       # обновление FK
│   ├── market_repo.update_pair_ids()            # обновление FK
│   └── [инициализация завершена за ~3 сек]
│
├── [main loop — каждые 5 секунд]
│   ├── binance_collector.collect_data()
│   ├── health_monitor.record_request("Binance", ...)
│   ├── kucoin_collector.collect_data()
│   ├── health_monitor.record_request("KuCoin", ...)
│   └── sleep до следующего цикла
│
└── [shutdown]
    ├── health_monitor.stop_monitoring()
    ├── binance_api.close_session()
    ├── kucoin_api.close_session()
    └── conn.close()
```

### 2.3. Схема базы данных

```sql
-- Биржи (реестр)
CREATE TABLE exchanges (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT UNIQUE NOT NULL,
    maker_fee     REAL DEFAULT 0.001,
    taker_fee     REAL DEFAULT 0.001,
    -- usdt_balance, spot_balance_usdt (опционально, для приватных ключей)
);

-- Валюты (справочник)
CREATE TABLE currencies (
    id   INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL
);

-- Уникальные торговые пары (дедупликация по standardized_pair)
CREATE TABLE unique_pairs (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    standardized_pair TEXT UNIQUE NOT NULL
);

--Торговые пары для каждой биржи (динамическое имя таблицы)
CREATE TABLE {exchange}_trading_pairs (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    exchange_id        INTEGER,
    original_pair      TEXT,
    standardized_pair  TEXT,
    pair_id            INTEGER,
    base_currency      TEXT,
    base_currency_id   INTEGER,
    quote_currency     TEXT,
    quote_currency_id  INTEGER,
    price              REAL,
    volume             REAL,
    bid                REAL,
    ask                REAL,
    bid_volume         REAL,
    ask_volume         REAL,
    timestamp          REAL,
    readable_time      TEXT,
    FOREIGN KEY (exchange_id) REFERENCES exchanges(id),
    FOREIGN KEY (pair_id) REFERENCES unique_pairs(id),
    UNIQUE(exchange_id, original_pair)
);
```

---

## 3. Рабочий процесс инициализации

1. **Подключение к БД** — единое соединение `sqlite3.connect()`
2. **Создание API-клиентов** — по одному на биржу
3. **Инициализация репозиториев** — каждая биржа получает свой `MarketRepository`
4. **Health-мониторинг** — регистрация бирж, старт фонового отчёта
5. **Сбор данных (первичный)** — `collect_data()` для каждой биржи
6. **Извлечение уникальных валют** — из всех `*_trading_pairs` таблиц → `currencies`
7. **Извлечение уникальных пар** — из всех `*_trading_pairs` таблиц → `unique_pairs`
8. **Обновление FK** — `currency_id` и `pair_id` в таблицах торговых пар
9. **Циклический сбор** — каждые 5 секунд новый раунд `collect_data()` для каждой биржи

---

## 4. Добавление новой биржи (инструкция)

### Шаги:

1. **Создать API-класс** — `src/api/exchanges/cex/{exchange}/{exchange}_spot_api.py`
   - Наследоваться от `BaseExchangeAPI`
   - Указать `BASE_URL` и `EXCHANGE_NAME`
   - Реализовать `async def fetch_trading_pairs() -> List[PairData]`

2. **Создать Collector** — `src/data/collectors/cex/{exchange}_collector.py`
   - Наследоваться от `BaseDataCollector`
   - Конструктор принимает API, `MarketRepository`, `ExchangesRepository`
   - Реализовать `async def collect_data()`

3. **Добавить в `main.py`**:
   ```python
   exchange_api = ExchangeSpotAPI()
   market_repo_exchange = MarketRepository(db_path, "exchange_name")
   exchange_collector = ExchangeCollector(exchange_api, market_repo_exchange, exchanges_repo)
   health_monitor.register_exchange("ExchangeName")
   ```
   - В секции первичного сбора: `await exchange_collector.collect_data()`
   - В цикле: try/except с `health_monitor.record_request()`

4. **Обновить список `trading_tables`** в main.py (строка 81): добавить `"{exchange}_trading_pairs"`

### Что происходит автоматически:
- Таблица `{exchange}_trading_pairs` создаётся при первом вызове `MarketRepository`
- Биржа регистрируется в `exchanges` таблице через `get_or_create_exchange_id`
- Валюты и пары дедуплицируются глобально
- FK обновляются автоматически

---

## 5. План развития

### 5.1. Приоритет (краткосрочный)
- [x] **Параллельный сбор данных через `asyncio.gather()`** — Binance и KuCoin опрашиваются одновременно, временной сдвиг между ценами устранён.
  ```python
  await asyncio.gather(
      binance_collector.collect_data(),
      kucoin_collector.collect_data()
  )
  ```
  **Приоритет: наивысший**, реализуется быстро.

- [ ] **Фьючерсные данные** — добавить сбор фьючерсных котировок с Binance Futures API и KuCoin Futures API. Каждая новая биржа также должна получать futures-эндпоинт по тому же шаблону, что и spot (см. раздел 4).

- [ ] **Копитрейдинг из Telegram/Discord** — модуль распознавания торговых сигналов из каналов. Сигналы приходят в двух форматах:
  - **Текстовые сообщения** (например: "LONG BTC entry 65000 SL 64000 TP 67000") — обрабатываются обычной text-моделью (DeepSeek V4 Flash), без vision.
  - **Скриншоты с бирж** — обрабатываются vision-capable LLM API (DeepSeek Vision / Qwen3-VL / GLM-4.6V).
  - Модуль сам определяет тип входящего сообщения (наличие изображения vs только текст) и направляет в соответствующий обработчик. Промпт в обоих случаях возвращает строгий JSON единого формата (`symbol`, `side`, `entry`, `SL`, `TP`), чтобы дальнейшая логика бота не зависела от источника сигнала.
  - Не требует локальной модели — объём сообщений низкий, экономия на облачном API незначительна.

- [ ] **WebSocket** — замена REST polling (5 сек) на real-time стримы (Binance WebSocket, KuCoin WebSocket).

- [ ] **Исторические данные** — сейчас БД хранит только последнее значение. Нужна таблица `price_history` с временными рядами.

- [ ] **Мониторинг спредов** — расчёт разницы цен на `unique_pairs` между биржами.

### 5.2. Новые платформы (среднесрочный)
- [ ] **DEX (децентрализованные биржи):**
  - Uniswap V2/V3 (Ethereum)
  - PancakeSwap (BSC)
  - TraderJoe (Avalanche)
  - Требуется: интеграция с web3.py, чтение пулов ликвидности через RPC
- [ ] **CEX биржи (централизованные):**
  - OKX
  - Bybit
  - Kraken
  - Gate.io
  - Требуется: создать API-клиент + Collector по шаблону

### 5.2.1. План масштабирования и переход на удалённый сервер

- [ ] **Довести количество поддерживаемых CEX-бирж до 5** (текущие Binance, KuCoin + 3 новые из списка выше — OKX, Bybit, Kraken или Gate.io на выбор)
- [ ] **После достижения 5 бирж — перенос проекта на удалённый сервер (VPS)** для автономной круглосуточной работы, независимо от локального ноутбука пользователя
- [ ] **Параллельно с масштабированием до 5 бирж — подготовка инфраструктуры миграции SQLite → PostgreSQL/TimescaleDB** (см. раздел 5.5): продумать схему БД под PostgreSQL, план миграции данных, тестовое окружение — чтобы сама миграция на удалённом сервере прошла без простоя
- [ ] Уточнить раздел 5.5 — миграция БД должна быть завершена до или сразу после переноса на VPS, так как именно на этом масштабе (5+ бирж, круглосуточная работа) SQLite перестаёт справляться

### 5.3. Функциональность (среднесрочный)
- [ ] **Арбитражный движок:**
  - Поиск расхождений цен (с учётом комиссий)
  - Расчёт потенциальной прибыли
  - Фильтрация по минимальному объёму
- [ ] **Управление балансами:**
  - Поддержка приватных API-ключей для торговли
  - Отслеживание балансов на биржах
- [ ] **Уведомления:**
  - Telegram-бот при найденном арбитраже
  - Оповещения при падении/восстановлении бирж

### 5.4. Инфраструктура (долгосрочный)
- [ ] **REST API** (FastAPI) для внешнего доступа к данным
- [ ] **Веб-интерфейс** — дашборд с графиками и метриками
- [ ] **Docker-контейнеризация**
- [ ] **Тесты** — unit-тесты (pytest) на API-клиенты и репозитории
- [ ] **CI/CD** — GitHub Actions для линтинга и тестов

### 5.5. Миграция БД (зависимая задача)
- [ ] **PostgreSQL / TimescaleDB** — это **НЕ первоочередная задача**. Текущая SQLite-схема с одним соединением справляется с нагрузкой на масштабе 2–3 бирж при обновлении раз в 5 секунд. Миграция на PostgreSQL/TimescaleDB имеет смысл только после:
  - Перехода с REST polling на WebSocket (частота записи вырастет на порядок)
  - Масштабирования до 5+ бирж
  - Появления потребности в сложных аналитических запросах (оконные функции, JOIN по времени)
  
  До наступления этих условий миграция добавит сложность (отдельный сервер БД, управление соединениями, миграции схемы) без выигрыша в производительности.

---

## 6. Известные ограничения и gotchas

- **PSReadLine (PowerShell):** баг с кириллицей при многострочном `python -c "..."`. Всегда используйте `.py` файлы или запускайте `main.py` напрямую.
- **Публичный API:** Binance и KuCoin работают без ключей. Для приватных эндпоинтов (торговля, балансы) нужны ключи в `.env`.
- **Таймауты:** aiohttp-таймаут 10 секунд в `_make_request`. Если биржа недоступна, запрос упадёт с `TimeoutError` (сработает retry — 3 попытки).
- **Одно соединение БД:** `sqlite3` не поддерживает конкурентные записи. Всё выполняется последовательно в одном `asyncio`-потоке.
- **Логи:** ротация 10 МБ, хранится 10 файлов. Логи пишутся в `logs/arbitrage_YYYY-MM-DD.log`.
- **Standardized pairs:** Binance использует `ETHBTC`, KuCoin может использовать `ETH-BTC`. Collector приводит к единому формату `ETHBTC`.

---

## 6.1. Git-workflow

- **Коммиты подготавливает Cline, но не пушит автоматически.** После завершения и успешного тестирования задачи — выполняй `git add` и `git commit` с осмысленным описательным commit message (что изменено и зачем), но не выполняй `git push` самостоятельно.
- **Push на GitHub делает пользователь вручную** — это финальная точка контроля перед тем, как изменения попадут в удалённый репозиторий.
- Если команда `git commit` требует подтверждения в терминале — дождись подтверждения от пользователя, не форсируй выполнение.
- Не коммить автоматически при обнаружении незавершённых/непротестированных изменений — сначала уведоми пользователя о статусе.

---

## 7. Команды для быстрого старта

```bash
# Запуск приложения
D:\multi-exchange-arbitrage\venv\Scripts\python.exe main.py

# Установка зависимостей
D:\multi-exchange-arbitrage\venv\Scripts\pip.exe install -r requirements.txt

# Быстрый тест API одной биржи (создать test.py и запустить)
D:\multi-exchange-arbitrage\venv\Scripts\python.exe test.py

# Просмотр лога
Get-Content D:\multi-exchange-arbitrage\logs\arbitrage_2026-07-09.log -Tail 50