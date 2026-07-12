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
│   │       ├── kucoin_spot_api.py   # KuCoin Spot (публичный, 1037 пар)
│   │       └── kucoin_futures_api.py # KuCoin Futures (публичный, контракты + allTickers)
│   ├── core/
│   │   ├── spread_monitor.py           # Мониторинг спредов (spot-only, INSERT)
│   │   └── models/
│   │       ├── pair_data.py             # PairData: цена, объём, bid/ask, метка времени
│   │       ├── order_book_data.py       # OrderBookData, OrderBookLevel — depth стакана
│   │       ├── arbitrage_opportunity.py # ArbitrageOpportunity, SlippageInfo
│   │       ├── currencies.py            # Currency
│   │       └── exchanges.py             # Exchange (name, maker_fee, taker_fee)
│   ├── data/collectors/cex/          # Сборщики данных (API → БД)
│   │   ├── base_collector.py        # Абстрактный базовый класс
│   │   ├── binance_collector.py     # Binance: fetch → save_trading_pairs
│   │   ├── binance_futures_collector.py # Binance Futures
│   │   ├── kucoin_collector.py      # KuCoin Spot
│   │   ├── kucoin_futures_collector.py # KuCoin Futures
│   │   └── order_book_collector.py  # Order Book depth (универсальный, duck-typing)
│   ├── database/
│   │   ├── base_repository.py       # Абстрактный репозиторий
│   │   ├── market_repository.py     # {exchange}_trading_pairs (UPSERT)
│   │   ├── order_book_repository.py # {exchange}_order_book (top-20 уровней, UPSERT)
│   │   ├── funding_rate_repository.py # {exchange}_funding_rates
│   │   ├── arbitrage_opportunity_repository.py # arbitrage_opportunities (INSERT)
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
├── BinanceSpotAPI(), BinanceFuturesAPI(), KuCoinSpotAPI(), KuCoinFuturesAPI()
├── MarketRepository(db_path, "binance"), MarketRepository(db_path, "binance_futures"), MarketRepository(db_path, "kucoin"), MarketRepository(db_path, "kucoin_futures")
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
│   ├── kucoin_futures_collector.collect_data()   # KuCoin Futures (contracts/active + allTickers)
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
│   │   └── health_monitor.record_request("Binance", ...)
│   ├── kucoin_collector.collect_data()
│   │   └── health_monitor.record_request("KuCoin", ...)
│   ├── kucoin_futures_collector.collect_data()
│   │   └── health_monitor.record_request("KuCoin Futures", ...)
│   ├── binance_futures_api.fetch_funding_rates() → funding_repo_binance_futures.save_funding_rates()
│   ├── kucoin_futures_api.fetch_funding_rates() → funding_repo_kucoin_futures.save_funding_rates()
│   ├── [spread monitor]
│   │   ├── spread_monitor.scan() → List[ArbitrageOpportunity]
│   │   │   ├── JOIN {exchange}_trading_pairs по standardized_pair
│   │   │   ├── сравнение bid/ask с учётом комиссий (spot-only)
│   │   │   ├── [COLLISION?]-проверка (спред ≥20% → разные токены)
│   │   │   └── топ-N по net_spread, min_volume_usdt
│   │   ├── для топ-кандидатов: _calc_slippage()
│   │   │   └── order_book_collector.get_order_book_cached() — TTL 5 сек
│   │   └── arbitrage_opportunity_repo.save_opportunities() — INSERT (накопление)
│   └── sleep до следующего цикла
│
└── [shutdown]
    ├── health_monitor.stop_monitoring()
    ├── binance_api.close_session()
    ├── binance_futures_api.close_session()
    ├── kucoin_api.close_session()
    ├── kucoin_futures_api.close_session()
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

-- Торговые пары для каждой биржи (динамическое имя таблицы, UPSERT)
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
    -- Фьючерсные поля (опционально, NULL для спотовых пар)
    multiplier         REAL,
    lot_size           REAL,
    FOREIGN KEY (exchange_id) REFERENCES exchanges(id),
    FOREIGN KEY (pair_id) REFERENCES unique_pairs(id),
    UNIQUE(exchange_id, original_pair)
);

-- Order Book depth (top-20 уровней, динамическое имя таблицы, UPSERT — TTL-кеш обновляет поверх)
CREATE TABLE {exchange}_order_book (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    exchange_id        INTEGER,
    original_pair      TEXT,
    standardized_pair  TEXT,
    bids               TEXT,   -- JSON: [{"price": ..., "volume": ...}, ...]
    asks               TEXT,   -- JSON: [{"price": ..., "volume": ...}, ...]
    timestamp          REAL,
    readable_time      TEXT,
    FOREIGN KEY (exchange_id) REFERENCES exchanges(id),
    UNIQUE(exchange_id, original_pair)
);

-- Арбитражные возможности (накопление, не UPSERT)
CREATE TABLE arbitrage_opportunities (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    standardized_pair      TEXT NOT NULL,
    base_currency          TEXT,
    quote_currency         TEXT,
    exchange_buy           TEXT NOT NULL,
    exchange_sell          TEXT NOT NULL,
    buy_price              REAL,
    sell_price             REAL,
    raw_spread_percent     REAL,
    buy_exchange_fee_percent  REAL,
    sell_exchange_fee_percent REAL,
    net_spread_percent     REAL,
    max_buy_volume_usdt    REAL,
    max_sell_volume_usdt   REAL,
    trade_volume_usdt      REAL,
    buy_volume_original    REAL,
    sell_volume_original   REAL,
    slippage_available     INTEGER DEFAULT 0,
    buy_slippage           TEXT,  -- JSON: SlippageInfo
    sell_slippage          TEXT,  -- JSON: SlippageInfo
    net_spread_with_slippage_percent REAL,
    slippage_limited_volume_usdt REAL,
    timestamp              REAL,
    readable_time          TEXT,
    suspected_collision    INTEGER DEFAULT 0
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

4. **Обновить список `trading_tables`** в main.py: добавить `"{exchange}_trading_pairs"`

### Что происходит автоматически:
- Таблица `{exchange}_trading_pairs` создаётся при первом вызове `MarketRepository`
- Биржа регистрируется в `exchanges` таблице через `get_or_create_exchange_id`
- Валюты и пары дедуплицируются глобально
- FK обновляются автоматически

---

## 5. План развития

### 5.1. Приоритет (краткосрочный)
- [x] **Параллельный сбор данных через `asyncio.gather()`** — Binance и KuCoin опрашиваются одновременно, временной сдвиг между ценами устранён.
- [x] **Фьючерсные данные (Binance Futures)** — добавлен сбор фьючерсных котировок (702 пары).
- [x] **Фьючерсные данные (KuCoin Futures)** — добавлен сбор фьючерсных котировок KuCoin (контракты + allTickers). Таблица `kucoin_futures_trading_pairs`, поля `multiplier`/`lot_size` в `PairData`.
- [x] **Funding Rate** — сбор funding rate для фьючерсных пар:
  - Binance Futures: `GET /fapi/v1/premiumIndex`
  - KuCoin Futures: данные из кеша `_contracts_cache` (поле `fundingFeeRate` из `/api/v1/contracts/active`), без отдельного эндпоинта
- [x] **Order Book depth (top-20)** — эндпоинты глубины стакана для расчёта проскальзывания. Реализовано:
  - **Модель** `OrderBookData` / `OrderBookLevel` (`src/core/models/order_book_data.py`)
  - **Репозиторий** `OrderBookRepository` (`src/database/order_book_repository.py`) — таблицы `{exchange}_order_book`, хранит top-20 уровней как JSON (UPSERT)
  - **fetch_order_book(symbol, limit)** — во всех 4 API (Binance Spot, Binance Futures, KuCoin Spot, KuCoin Futures)
  - **OrderBookCollector** (`src/data/collectors/cex/order_book_collector.py`) — универсальный сборщик с duck-typing, два метода: `collect_order_books()` (одинаковый список символов для всех бирж), `collect_top_pairs()` (разные списки символов для разных бирж)
  - **Проверено** на 4 биржах для BTCUSDT — depth собирается и сохраняется корректно
  - **KuCoin Spot** — поправлена структура ответа (`data['data']`)
  - **KuCoin Futures** — создан с нуля, работает, стандартизация XBTUSD → BTCUSD
  - **Интегрировано в main.py** — OrderBookCollector с TTL-кешем подключён через `SpreadMonitor`, загружается on-demand для топ-кандидатов.
  - **Ограничение:** `collect_top_pairs()` собирает только указанные пользователем пары, не топ-N по объёму. Для полноценного "топ-50 пар по объёму" нужен дополнительный анализ.

- [ ] **Копитрейдинг из Telegram/Discord — отдельный процесс, не часть текущей архитектуры:**
  - Решение: копитрейдинг реализуется как **отдельный, независимый бот/процесс**, 
    не встраивается в `main.py` арбитражного бота. Причина — разный фокус (сигналы 
    из чатов vs сбор рыночных данных), риск размытия основной цели проекта.
  - **Реализация начнётся после** переноса основного арбитражного бота на VPS — 
    оба процесса смогут работать параллельно на одном сервере (арбитражный бот 
    продолжает копить исторические данные, копитрейдинг разрабатывается отдельно, 
    не требует истории).
  - **Архитектурные границы (зафиксировать сейчас, чтобы не сломать основной проект 
    при будущей интеграции):**
    - Копитрейдинг НЕ модифицирует существующие таблицы (`{exchange}_trading_pairs`, 
      `arbitrage_opportunities`, `{exchange}_order_book` и т.д.)
    - Если нужна общая БД — только через новые, отдельные таблицы или отдельный 
      файл БД
    - Точка будущей интеграции — общий формат сигналов/событий (JSON: `symbol`, 
      `side`, `entry`, `SL`, `TP` — как уже намечено), не прямая связь кода
  - Детали реализации (LLM-парсинг сигналов, vision-модели для скриншотов) — 
    без изменений, актуальны на момент реализации.

- [x] **DB-backed TTL cache для OrderBookCollector** — `get_order_book_cached(api, repo, symbol, ttl_seconds=5.0)`: перед HTTP-запросом проверяет timestamp последней записи в `{exchange}_order_book` через `get_order_book_with_age()`. Если запись свежая (age < TTL) — возвращает из БД (cache hit), иначе — HTTP-запрос + save (cache miss). TTL по умолчанию 5 секунд.
- [x] **Интеграция Order Book depth в main.py** — Order Book подключён on-demand через `SpreadMonitor`: загружается для топ-кандидатов при обнаружении ценового расхождения, не по фиксированному списку.

- [ ] **WebSocket** — замена REST polling на real-time стримы. **Отложено осознанно**, 
  не следующий приоритет: WebSocket даёт реальную пользу (низкая задержка) только при 
  автоматическом исполнении стратегии (спот-фьюч или фьюч-фьюч арбитраж), которое 
  ещё не спроектировано и дополнительно зависит от ещё не реализованной задачи 
  "Управление балансами" (приватные API-ключи). Внедрять точечно, под конкретную 
  биржу и конкретную стратегию — не единым шаблоном на все биржи сразу (у каждой 
  биржи свой WebSocket-протокол/формат сообщений, в отличие от единообразного REST 
  через `BaseExchangeAPI`).

- [ ] **Исторические данные** — сейчас БД хранит только последнее значение. Нужна таблица `price_history` с временными рядами.

- [ ] **Очистка протухших записей в `{exchange}_trading_pairs`** — обнаружены записи 
  возрастом 500+ дней (делистнутые/переименованные пары, которые биржа больше не 
  возвращает, поэтому UPSERT их не обновляет — зависают навсегда). Решено сделать 
  ОДНИМ проходом для всех 4 бирж (после добавления Gate.io и MEXC), перед переносом 
  на VPS, а не чинить по одной бирже за раз. Это будет повторяющаяся проблема для 
  каждой новой биржи — стоит либо добавить периодическую очистку (retention job), 
  либо ручной скрипт, запускаемый перед каждым релизом.

- [x] **Мониторинг спредов (SpreadMonitor)**:
  - Spot-only сравнение (`binance_trading_pairs` ↔ `kucoin_trading_pairs`), фьючерсы исключены
  - JOIN по `standardized_pair`, сравнение best bid/ask с учётом комиссий бирж
  - [COLLISION?]-защита: спред ≥20% (параметр `suspected_collision_threshold_percent`) → разные токены с одинаковым тикером, помечается `suspected_collision`
  - Расчёт slippage через Order Book (TTL-кеш 5 сек) для топ-кандидатов
  - Сохранение в `arbitrage_opportunities` через INSERT (накопление истории, не перезапись)

### 5.2. Новые платформы (среднесрочный)
- [ ] **DEX (децентрализованные биржи):**
  - Uniswap V2/V3 (Ethereum)
  - PancakeSwap (BSC)
  - TraderJoe (Avalanche)
  - Требуется: интеграция с web3.py, чтение пулов ликвидности через RPC
- [ ] **CEX биржи (централизованные):**
  - **Следующие 2 (приоритет):** Gate.io, MEXC — выбраны по наблюдениям за реальными 
    арбитражными возможностями (Gate.io — чаще всего, MEXC — реже, но постоянно). 
    MEXC также интересен отдельно из-за постоянного 0% taker fee (см. пункт про 
    льготные комиссии в 5.3).
  - **Кандидаты на будущее:** OKX, Bybit, Kraken
  - Требуется: создать API-клиент + Collector по шаблону (раздел 4)

### 5.2.1. План масштабирования и переход на удалённый сервер

- [ ] **Довести количество поддерживаемых CEX-бирж до 4** (текущие Binance, KuCoin + 
  Gate.io + MEXC). Осознанно НЕ 5 — порог 5+ бирж уже зафиксирован ниже как триггер 
  для миграции на PostgreSQL/TimescaleDB, инфраструктура под которую ещё не готова. 
  5-я биржа (из кандидатов OKX/Bybit/Kraken) — после того, как миграция БД будет 
  готова принять возросшую нагрузку.
- [ ] **Перенос проекта на удалённый сервер (VPS)** для автономной круглосуточной работы. 
  Выполняется после добавления Gate.io и MEXC (4 биржи) — следующий шаг после проверки 
  стабильности на новом масштабе в локальном режиме. VPS позволит работать 24/7 
  независимо от локального ноутбука.
- [ ] **Параллельно с масштабированием до 4 бирж — подготовка инфраструктуры миграции SQLite → PostgreSQL/TimescaleDB** (см. раздел 5.5): продумать схему БД под PostgreSQL, план миграции данных, тестовое окружение — чтобы сама миграция на удалённом сервере прошла без простоя
- [ ] Уточнить раздел 5.5 — миграция БД должна быть завершена до или сразу после переноса на VPS, так как именно на этом масштабе (5+ бирж, круглосуточная работа) SQLite перестаёт справляться. При текущем плане (4 биржи до VPS, 5-я — уже после миграции) этот пункт эволюционирует: VPS-перенос с 4 биржами на SQLite (промежуточный этап), затем 5-я биржа + PostgreSQL/TimescaleDB (финальный этап архитектуры).

### 5.3. Функциональность (среднесрочный)
- [x] **Арбитражный движок (SpreadMonitor):**
  - [x] Поиск расхождений цен (с учётом комиссий) — `SpreadMonitor.scan()`
  - [x] Расчёт потенциальной прибыли — `ArbitrageOpportunity.estimated_profit_usdt()`
  - [x] Фильтрация по минимальному объёму — `min_volume_usdt`, `max_opportunities`
  - [x] Учёт проскальзывания на основе Order Book depth — `_calc_slippage()` через `OrderBookCollector`
  - [x] [COLLISION?]-защита от разных токенов с одинаковым тикером на разных биржах (порог 20%)
- [ ] **Управление балансами:**
  - Поддержка приватных API-ключей для торговли
  - Отслеживание балансов на биржах
- [ ] **Учёт нулевых/льготных торговых комиссий:**
  - Некоторые биржи предлагают 0% taker fee постоянно (MEXC, Bitfinex) — можно заложить 
    как статичное значение в `exchanges.taker_fee` при добавлении такой биржи.
  - Другие биржи (Binance, Bybit, OKX, KuCoin, Gate.io) периодически запускают временные 
    промо (0% на 1–4 недели для отдельных монет) — требует **живого** источника данных, 
    не статичного поля. Риск: если бот не отследит окончание промо, расчёт прибыли 
    окажется неверным на реальной сделке.
  - Скидка за холд локальной монеты биржи (BNB на Binance, MX на MEXC и т.д.) — требует 
    знания баланса пользователя на конкретной бирже → зависит от задачи "Управление 
    балансами" выше (приватные API-ключи), либо ручного подтверждения пользователем.
  - **Предлагаемый MVP** (до полной автоматизации через приватный API):
    - Расширить схему `exchanges` полями `has_zero_fee_promo BOOLEAN`, 
      `fee_discount_token TEXT`, `fee_discount_percent REAL`
    - Ручной override пользователем ("на MEXC taker=0", "у меня есть BNB для скидки на 
      Binance") — без автоматического мониторинга промо-акций на старте
    - Автоматизация (парсинг промо-страниц или API баланса) — отдельная, более сложная 
      подзадача на будущее
  - Цель фичи: позволяет закрывать арбитражные сделки market-ордерами (мгновенно) вместо 
    лимитных, не теряя маржу на комиссии — потенциально ускоряет исполнение арбитражных 
    возможностей.
- [ ] **Уведомления:**
  - Telegram-бот при найденном арбитраже
  - Оповещения при падении/восстановлении бирж

### 5.4. Инфраструктура (долгосрочный)
- [ ] **REST API** (FastAPI) для внешнего доступа к данным
- [ ] **Веб-интерфейс** — дашборд с графиками и метриками
- [ ] **Очистить git от `data/arbitrage_data.db`** — правило `data/*.db` уже в `.gitignore`, осталось `git rm --cached data/arbitrage_data.db` и закоммитить, чтобы файл перестал отслеживаться.
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
- **KuCoin Futures mark_price:** Источником цены для KuCoin Futures является эндпоинт `allTickers` (а не отдельный тикерный эндпоинт). `mark_price` из `allTickers` может отличаться от цен Binance Futures на ~0.5–1% из-за разных ставок финансирования и ликвидности на отдельных фьючерсных биржах. Это не баг, а суть арбитражной возможности.
- **KuCoin Futures symbol:** spot-формат `BTC-USDT` (с дефисом), futures-формат `XBTUSDTM` (XBT вместо BTC).
- **KuCoin Spot depth:** ответ от `/api/v1/market/orderbook/level2_20` приходит в `data['data']`, а не на корневом уровне.
- **OrderBookRepository:** интегрирован в `main.py` через `SpreadMonitor` с TTL-кешем (5 сек). Загружается on-demand для топ-кандидатов.
- **OrderBookRepository slug (рассинхрон):** Второй аргумент конструктора `OrderBookRepository(db_path, slug)` определяет имя таблицы `{slug}_order_book`. `main.py` использует slug `"binance"` / `"kucoin"` — таблицы `binance_order_book`, `kucoin_order_book`. Если в тестовом/временном скрипте указать другой slug (например `"binance_spot"`), создастся дублирующая таблица `binance_spot_order_book`, которая никогда не будет обновляться основным кодом. Обнаружить такие таблицы-дубликаты можно по тому, что `timestamp` в них перестаёт обновляться. Перед `DROP TABLE` — всегда проверять содержимое `SELECT *` для подтверждения, что это мусор.
- **Протухшие записи в `{exchange}_trading_pairs`:** UPSERT обновляет только пары, которые биржа реально возвращает в ответе; делистнутые/переименованные пары остаются в таблице с устаревшим timestamp навсегда. Актуально для каждой новой биржи — требует мониторинга/очистки, не одноразовая проблема конкретной биржи.

---

## 6.1. Git-workflow

- **Cline НЕ выполняет НИКАКИЕ git-команды самостоятельно через терминал** — ни commit, ни push, ни merge, ни даже git status/git log. Известный баг терминала (PSReadLine/shell integration) вызывает зависание на любой git-команде, не только на commit.
- Вместо выполнения git-команд, Cline:
  - Сообщает, когда задача завершена и протестирована, и что пора закоммитить
  - Предлагает готовый commit message
  - Указывает точную команду для ручного выполнения (см. CHEATSHEET.md)
  - Даёт совет по следующему шагу разработки, не дожидаясь выполнения git-команды от пользователя — пользователь сообщит о результате в следующем сообщении
  - НЕ пытается сам проверить git status/git log после — просто спрашивает пользователя "готово?" или ждёт следующего сообщения с результатом
- Все git-операции (commit, push, merge, status, log) пользователь выполняет вручную в своём терминале, используя CHEATSHEET.md как справочник.

- Коммитить нужно по завершении каждой логически законченной единицы работы (например, отдельно "добавлен Binance Futures API-клиент", отдельно "добавлен Futures Collector", отдельно "интеграция в main.py"), а не всей сессии скопом. Это позволяет откатить конкретный шаг при проблеме, не теряя прогресс сессии.

- При прерывании сессии на середине задачи — дописывать короткую пометку под соответствующим пунктом в разделе 5.1, например:

  > В процессе: API-клиент готов, Collector — нет, интеграция в main.py — нет

  PLAN.md является единственным источником правды о состоянии проекта.

### Редактирование PLAN.md — особые правила

- Для правок PLAN.md Cline ВСЕГДА использует `replace_in_file` (точечная замена 
  конкретного фрагмента), НИКОГДА `write_to_file` (полная перезапись файла). 
  Если нужно добавить большой новый раздел — делать это через `replace_in_file` 
  с точным указанием, после какого существующего текста вставить новый блок, 
  а не переписывать файл целиком.
- Если Cline не уверен в текущем полном содержимом PLAN.md (например, после 
  компакции контекста) — сначала явно прочитать файл (`read_file`), и только 
  затем предлагать правку.
- Перед применением любой правки PLAN.md — показать пользователю diff/превью 
  изменений и дождаться подтверждения, даже если задача сформулирована как 
  "точечная правка" из раздела 6.2.
- Это правило существует из-за инцидента, когда write_to_file полностью заменил 
  содержимое PLAN.md вместо добавления нового раздела — откачено через git.

### Проверка перед итоговым отчётом (attempt_completion)

- Перед формированием любого итогового отчёта о выполненной работе (включая 
  commit message) — Cline сверяет реальное состояние проекта через доступные 
  инструменты (`search_files`, `read_file`, `list_files`), а не полагается 
  на память о том, что делал в рамках задачи.
- Если в отчёте упоминается конкретный файл, метод или функция как «созданный» 
  или «изменённый» — перед тем как написать это в отчёт, подтвердить через 
  `search_files` (метод физически существует в указанном файле) или `read_file` 
  (просмотр содержимого файла для подтверждения изменений).
- **Важно:** `git diff --stat` и `git status` запрещены (см. раздел 6.1), 
  поэтому верификация делается только через `search_files` / `read_file`.
- Это правило существует из-за инцидента: в итоговом отчёте был указан метод 
  `cleanup_stale_records()` в `market_repository.py` как реализованный, хотя 
  метод не существовал — код не был написан, это была галлюцинация в тексте 
  отчёта. Диагностировано через `search_files` (0 результатов) и прямой просмотр 
  файла.
- Правило применимо не только к commit message, но и к любому описанию 
  «что сделано» — включая ответы в чате пользователю.

### 6.2. Формат задач для Cline

- **Точечные правки существующих файлов** (конфиги, PLAN.md, мелкие фиксы) — пользователь даёт точную спецификацию (что менять → куда вставить → точный текст), Cline выполняет буквально.
- **Разработка нового функционала с нуля** (новая биржа, новый модуль) — пользователь даёт краткое ТЗ (что нужно получить в результате), Cline сам предлагает реализацию по шаблону из раздела 4 PLAN.md, а пользователь ревьюит готовый результат, а не диктует код заранее.
- **Практика ревью** — при завершении задачи полезно кратко проверить:
  - Есть ли скрытые риски (обработка ошибок, edge cases), не упомянутые в задаче
  - Есть ли важные нюансы вне исходного ТЗ (разница комиссий, funding rate)
  - Стоит ли что-то зафиксировать в PLAN.md на будущее
  - Это не гарантированное поведение, а чек-лист для самопроверки — помогает не упустить неочевидное.

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