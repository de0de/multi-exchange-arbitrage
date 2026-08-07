# Multi-Exchange Arbitrage — Architecture & Development Plan

## 1. Обзор проекта

**Назначение:** Асинхронный сбор рыночных данных с криптовалютных бирж для поиска арбитражных возможностей.

**Стек:** Python 3.10+, asyncio, aiohttp (HTTP-клиент), SQLite (хранение данных), python-dotenv (конфигурация)

**Запуск:**
```
D:\multi-exchange-arbitrage\venv\Scripts\python.exe main.py
```

**Важно:** Всегда использовать python из venv (`venv\Scripts\python.exe`). Не использовать `python -c "..."` для многострочного кода с кириллицей (баг PSReadLine в PowerShell). Для тестов создавать временный `.py` файл.

**Инструменты разработки:** проект разрабатывается с помощью AI-ассистентов. 
Cline — правила в `.clinerules`. Claude Code — правила в `CLAUDE.md` (если 
создан). Разные инструменты могут иметь разные ограничения (например, 
запрет git-команд в `.clinerules` специфичен для бага именно Cline — не 
переносить на другие инструменты без проверки).

---

## 2. Архитектура (текущее состояние)

### 2.1. Структура папок

```
multi-exchange-arbitrage/
├── main.py                          # Точка входа, оркестратор
├── config/
│   ├── settings.py                  # DATABASE_URL, ключи из .env
│   └── transfer_config.py           # Словарь переводов монет: сеть, withdrawal fee, время (paper trading)
├── src/
│   ├── api/exchanges/cex/           # API-клиенты централизованных бирж
│   │   ├── base_cex_exchange.py     # Базовый класс: aiohttp-сессия, _make_request, retry, hmac-подпись
│   │   ├── binance/
│   │   │   └── binance_spot_api.py  # Binance Spot (публичный, 1366 пар)
│   │   ├── kucoin/
│   │   │   ├── kucoin_spot_api.py   # KuCoin Spot (публичный, 1037 пар)
│   │   │   └── kucoin_futures_api.py # KuCoin Futures (публичный, контракты + allTickers)
│   │   ├── gate/
│   │   │   └── gate_spot_api.py     # Gate.io Spot (публичный, ~2200 пар)
│   │   └── mexc/
│   │       └── mexc_spot_api.py     # MEXC Spot (публичный, ~2100 пар, Binance-совместимый)
│   ├── core/
│   │   ├── spread_monitor.py           # Мониторинг спредов (spot-only, INSERT)
│   │   ├── paper_trading/
│   │   │   ├── base_strategy.py         # BasePaperTradingStrategy — общий интерфейс стратегий
│   │   │   └── spot_spot_strategy.py    # SpotSpotStrategy — Realistic spot-spot симуляция
│   │   └── models/
│   │       ├── pair_data.py             # PairData: цена, объём, bid/ask, метка времени
│   │       ├── order_book_data.py       # OrderBookData, OrderBookLevel — depth стакана
│   │       ├── arbitrage_opportunity.py # ArbitrageOpportunity, SlippageInfo
│   │       ├── simulated_trade.py       # SimulatedTrade — гипотетическая сделка paper trading
│   │       ├── currencies.py            # Currency
│   │       └── exchanges.py             # Exchange (name, maker_fee, taker_fee)
│   ├── data/collectors/cex/          # Сборщики данных (API → БД)
│   │   ├── base_collector.py        # Абстрактный базовый класс
│   │   ├── binance_collector.py     # Binance: fetch → save_trading_pairs
│   │   ├── binance_futures_collector.py # Binance Futures
│   │   ├── kucoin_collector.py      # KuCoin Spot
│   │   ├── kucoin_futures_collector.py # KuCoin Futures
│   │   ├── gate_collector.py        # Gate.io Spot
│   │   ├── mexc_collector.py        # MEXC Spot
│   │   └── order_book_collector.py  # Order Book depth (универсальный, duck-typing)
│   ├── database/
│   │   ├── base_repository.py       # Абстрактный репозиторий
│   │   ├── market_repository.py     # {exchange}_trading_pairs (UPSERT)
│   │   ├── order_book_repository.py # {exchange}_order_book (top-20 уровней, UPSERT)
│   │   ├── funding_rate_repository.py # {exchange}_funding_rates
│   │   ├── arbitrage_opportunity_repository.py # arbitrage_opportunities (INSERT)
│   │   ├── simulated_trade_repository.py # simulated_trades (paper trading, INSERT + UPDATE при закрытии)
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

-- Симулированные сделки Paper Trading (Фаза 1: spot-spot)
CREATE TABLE simulated_trades (
    id                        INTEGER PRIMARY KEY AUTOINCREMENT,
    opportunity_id            INTEGER NOT NULL REFERENCES arbitrage_opportunities(id),
    status                    TEXT NOT NULL DEFAULT 'open',  -- open / closed
    entry_detected_at         REAL NOT NULL,
    entry_readable_time       TEXT,
    requested_volume_usdt     REAL NOT NULL,
    executed_volume_usdt      REAL NOT NULL,
    partial_fill              INTEGER DEFAULT 0,  -- стакан не вместил объём → 2-я withdrawal fee за остаток
    entry_buy_price_effective REAL,               -- цена покупки с учётом slippage
    base_amount               REAL,               -- куплено base currency (после торговой комиссии)
    transfer_network          TEXT,
    expected_transfer_seconds REAL,
    hypothetical_close_at     REAL NOT NULL,      -- entry_detected_at + время перевода
    withdrawal_fee_coin       REAL,
    withdrawal_fee_usdt       REAL,
    fee_unknown               INTEGER DEFAULT 0,  -- монета вне словаря переводов
    volume_curve              TEXT,               -- JSON: net_profit_percent по точкам объёма
    closed_at                 REAL,               -- фактическое закрытие (может быть позже плана)
    close_readable_time       TEXT,
    close_price_buy           REAL,               -- актуальный ask биржи покупки (справочно)
    close_price_sell          REAL,               -- актуальный bid биржи продажи (цена исполнения)
    realized_profit_usdt      REAL,
    realized_profit_percent   REAL,
    outcome                   TEXT                -- profitable / unprofitable / opportunity_vanished / fee_unknown
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

- [ ] **Исторические данные** — сейчас БД хранит только последнее значение (UPSERT). 
  Дизайн решён в `DATA_SPECIFICATION.md` (2026-07-14): не сырой `price_history` 
  (140 млн строк/сутки — исключено замером), а агрегаты спреда по паре 
  (`spread_history`, `futures_spread_history`, `funding_rate_history`) + retention. 
  Реализация — см. пункт "Подготовка к первому VPS-прогону" ниже.

- [x] **Очистка протухших записей в `{exchange}_trading_pairs`** — выполнено 2026-07-14 
  одним проходом по всем 6 таблицам: удалено 582 (binance) + 601 (kucoin) записей 
  старше 7 дней (фактический возраст 499–554 дня — делистнутые/переименованные пары; 
  промежуточных случаев не было, разрыв между 7 и 499 днями пуст). Перед DELETE — 
  WAL checkpoint + бэкап `data/arbitrage_data.bak-cleanup-2026-07-14.db`. На 
  futures/gate/mexc протухших записей не оказалось. **Остаётся открытым:** 
  периодическая очистка (retention job) или ручной скрипт перед релизом — проблема 
  будет возвращаться по мере делистингов на любой бирже.

- [x] **Мониторинг спредов (SpreadMonitor)**:
  - Spot-only сравнение (`binance_trading_pairs` ↔ `kucoin_trading_pairs`), фьючерсы исключены
  - JOIN по `standardized_pair`, сравнение best bid/ask с учётом комиссий бирж
  - [COLLISION?]-защита: спред ≥20% (параметр `suspected_collision_threshold_percent`) → разные токены с одинаковым тикером, помечается `suspected_collision`
  - Расчёт slippage через Order Book (TTL-кеш 5 сек) для топ-кандидатов
  - Сохранение в `arbitrage_opportunities` через INSERT (накопление истории, не перезапись)

- [x] **Paper Trading симуляция — Фаза 1 (spot-spot) — реализовано:**
  - Realistic-модель (не Instant) — между открытием и закрытием позиции проходит 
    реальное время перевода средств между биржами, к моменту закрытия цены 
    сверяются заново, не экстраполируются с момента обнаружения
  - Таблица `simulated_trades`, FK на `arbitrage_opportunities.id`
  - Механизм "открытых позиций, ожидающих закрытия" — проверяется на каждом 
    цикле `SpreadMonitor.scan()`, не разовый расчёт
  - Переиспользовать существующий `_calc_slippage()` из SpreadMonitor
  - **Учесть частичное исполнение ордера** — при низкой ликвидности реально 
    исполнимый объём может быть меньше запрошенного, остаток требует отдельного 
    перевода и второй withdrawal fee — сравнивать запрошенный объём с реально 
    исполнимым по order book
  - Withdrawal fee: ручной словарь для топ монет/сетей + явный `fee_unknown: True` 
    для остальных (не подставлять 0)
  - Начинать на 2 существующих биржах (Binance, KuCoin), не сразу на 4+
  - Архитектура: заложить общий интерфейс/базовый класс уже сейчас (например 
    `BasePaperTradingStrategy` → `SpotSpotStrategy`), даже с одним наследником — 
    чтобы Фаза 2 не потребовала болезненного рефакторинга
  - **Итог реализации:** `BasePaperTradingStrategy` → `SpotSpotStrategy` 
    (`src/core/paper_trading/`), таблица `simulated_trades` 
    (`SimulatedTradeRepository`, FK на `arbitrage_opportunities.id`), словарь 
    переводов `config/transfer_config.py` (18 монет: сеть, withdrawal fee, 
    время перевода), размер сделки $1000 (рабочий депозит — на малых объёмах 
    фиксированные издержки перевода искажают результат), кривая `volume_curve` 
    по точкам $100–$5000 с логом рекомендации по объёму (зависимость 
    немонотонна: снизу давит withdrawal fee, сверху slippage — подтверждено 
    на реальных данных).
  - **Ограничения:** slippage продажи при закрытии не пересчитывается по 
    стакану (используется best bid из `{exchange}_trading_pairs`); при 
    частичном исполнении остаток считается купленным по той же эффективной 
    цене (вторая withdrawal fee учтена); монеты вне словаря переводов дают 
    `outcome=fee_unknown` и исключаются из агрегатов прибыльности.
  - **Расширение на 4 биржи (решение от 2026-07-14):** сканер спредов и paper 
    trading включены сразу на Binance/KuCoin/Gate.io/MEXC — осознанное 
    отступление от исходной спецификации "начинать на 2 биржах". Обоснование: 
    истории цен нет (только UPSERT-снэпшот), поэтому "понаблюдать за новыми 
    биржами пару дней" ничего не проверяет; вместо этого детектор коллизий и 
    диагностические сигналы paper trading (fee_unknown, opportunity_vanished, 
    partial_fill) используются как QA-процесс для новых бирж. **Shakeout:** 
    данные arbitrage_opportunities/simulated_trades переходного периода могут 
    содержать необнаруженные баги данных новых бирж — не доверять агрегатной 
    статистике по gate/mexc до первой проверки на вменяемость.
  - **Пополнение словаря переводов — выполнено 2026-07-14 (data-driven):** 
    47 монет из реальных находок добавлены с живыми комиссиями (публичный 
    KuCoin API `/api/v3/currencies/{coin}`, сеть с минимальной комиссией 
    вывода); 20 монет на KuCoin отсутствуют (торгуются только на Gate/MEXC) — 
    остаются `fee_unknown`. **Задача повторяющаяся:** состав монет в находках 
    плывёт, свежие монеты снова появляются как fee_unknown — при повторном 
    пополнении приоритет тот же: НЕ произвольный топ монет, а те, что реально 
    фигурируют в `arbitrage_opportunities` за последние N дней:
    ```sql
    SELECT base_currency, COUNT(*) AS cnt
    FROM arbitrage_opportunities
    WHERE suspected_collision = 0
      AND timestamp > strftime('%s', 'now', '-14 days')
    GROUP BY base_currency
    ORDER BY cnt DESC;
    ```
    Комиссии и сети — публичный KuCoin API (см. выше); для монет вне KuCoin — 
    страницы вывода Gate.io/MEXC вручную, пока нет источника по этим биржам 
    (см. Withdrawal fee parser ниже).
  - **Второе пополнение — 2026-07-23 (data-driven, тот же метод):** топ-60 монет
    по частоте `fee_unknown` в `simulated_trades` за 14 суток (запрос — JOIN
    с `arbitrage_opportunities` по `opportunity_id`, `suspected_collision = 0`
    и исключены 8 заблокированных тикеров из `collision_blocklist.py` — их
    `fee_unknown` был артефактом ложных сравнений, не реальной торговлей).
    48 монет добавлены с живыми комиссиями (тот же публичный KuCoin API,
    минимальная комиссия среди сетей с `isWithdrawEnabled=true`); 4 монеты
    (BANK, NIGHT, ACE, NFP) на KuCoin есть, но withdrawal отключён на всех
    сетях прямо сейчас — реальной комиссии нет, остаются `fee_unknown`
    осознанно; 8 монет (RSC, AO, TX, QBX, WKC, TBC, ARG, BTS) на KuCoin не
    найдены вовсе. Проверено количественно: 48 добавленных монет покрывают
    9219 из 30304 (30.4%) сделок с `fee_unknown` за 14 суток — эффект будет
    виден только на новых сделках после деплоя, уже закрытые в БД сделки
    останутся `fee_unknown` как есть. Пересечений с предыдущей партией (65
    монет от 07-14 и раньше) нет — итого в словаре 113 монет.
    **Задеплоено 2026-07-22** (коммит `6221997`, рестарт прод-сервиса в
    22:33:33 UTC): проверено функционально через `get_transfer_info()` на
    сервере после рестарта — NKN/ERA/BLUAI/NPC возвращают известную
    комиссию (`fee_unknown=False`), BANK/RSC по-прежнему `fee_unknown=True`
    (withdrawal отключён / монеты нет на KuCoin — как и задумано).

- [ ] **Paper Trading симуляция — Фаза 2 (spot-futures / futures-futures, после Фазы 1):**
  - Перевод между биржами не нужен только если обе ноги на одной бирже — 
    начинать с этого допущения
  - Funding rate как discrete-event state machine (не непрерывная 
    интерполяция) — hold/close решение пересматривается каждый цикл по 
    `next_funding_time` каждой ноги
  - Hold/close-логика — настраиваемый параметр/подключаемая функция, не хардкод
  - Если разные биржи — межбиржевое позиционирование капитала через USDT 
    TRC-20 (допущение: фиксированная $1 комиссия, но всё равно прогоняется 
    через расчёт, не игнорируется при малых суммах)
  - Возможно понадобится `strategy_type` поле или отдельные таблицы для 
    разнородных моделей закрытия (transfer-delay vs funding-driven)

- [x] **Подготовка к первому VPS-прогону — выполнена 2026-07-14, проверена локальными прогонами.**
  Дизайн и объёмы — см. `QUESTIONS.md` и `DATA_SPECIFICATION.md`. Каждый шаг 
  тестировался отдельно, замеры сошлись с расчётами спецификации:
  1. [x] `spread_history` — история спредов (агрегат по паре, порог 0.2% + 5-мин 
     снэпшоты всех многобиржевых пар) + retention 14 дней. Замер: ~195 строк/цикл 
     + 1468 строк/снэпшот, время скана не изменилось
  2. [x] `FuturesSpreadMonitor` — запись спот-фьюч/фьюч-фьюч спредов с embedded 
     funding-снимком и детекцией коллизий. Только детекция и запись, БЕЗ симуляции — 
     Фаза 2 paper trading проектируется позже на этих данных. Вызов в main loop 
     после сохранения funding rate (снимок текущего цикла)
  3. [x] `funding_rate_history` — INSERT при изменении ставки с фильтром дрейфа 
     (прогнозные ставки бирж дрейфуют каждый цикл — см. уточнение в 
     DATA_SPECIFICATION.md п.5); существующие UPSERT-таблицы funding не изменялись

- [x] **Архив истории (data lake) — экспорт перед retention-удалением — реализовано 2026-07-15:**
  - `HistoryArchiver` (`src/data/history_archiver.py`): раз в сутки строки старше 
    14 дней выгружаются в `data/archive/{table}_{дата}.csv.gz` (переносимый формат, 
    читается pandas/PostgreSQL без восстановления), затем удаляются; при ошибке 
    экспорта удаление не происходит
  - Покрывает `spread_history`, `futures_spread_history` и `arbitrage_opportunities` — 
    у последней retention отсутствовал вовсе при росте ~1.7 млн строк/сутки (пробел 
    исходной спецификации, закрыт); строки под `simulated_trades` не удаляются
  - Архивы ~200–380 МБ/сутки (замер сжатия в DATA_SPECIFICATION.md п.6): забирать 
    вручную (scp/WinSCP) каждые 2–3 недели с очисткой папки
  - [ ] Опционально: автоматизация rclone → Backblaze B2 (10 ГБ бесплатно ≈ 4–6 
    недель архивов; аккаунт/бакет создаёт пользователь; Duplicati рассмотрен и 
    отклонён — проприетарный формат требует восстановления перед анализом)
  - **Gotcha (2026-07-30): `_last_run` живёт только в памяти процесса, не
    персистится.** Любой рестарт `main.py` сбрасывает внутренний суточный
    таймер `run_if_due()` на 0 — следующая проверка привязывается к моменту
    рестарта, а не к абсолютному расписанию. Обнаружено при разборе рестарта
    2026-07-28 06:35 UTC (needrestart после libc6-патча unattended-upgrades):
    первый реальный прогон архиватора сдвинулся на ~06:35 UTC 07-31 вместо
    момента, который посчитали бы от старта деплоя (07-17 04:26 UTC).
    **Непреднамеренная связь с п.5 (graceful degradation, коммит `1b8d27d`,
    задеплоен 2026-07-25, см. раздел 5.5):** тот фикс специально делает
    рестарты ЧАЩЕ при сбоях БД (exit code 1 вместо 0 → `systemd
    Restart=on-failure` реально срабатывает, это его цель и это хорошо для
    надёжности) — побочный эффект: каждый такой полезный рестарт заново
    сдвигает расписание архивации. Два компонента, спроектированных
    независимо, оказались связаны способом, который не планировался.
    **РЕШЕНО И ЗАДЕПЛОЕНО 2026-08-06 (коммит `5fdcc44`): персистентный
    `_last_run`.** Время прошлого прогона лежит в таблице `archiver_state`
    (`CREATE TABLE IF NOT EXISTS`, аддитивно, миграции не потребовалось) и
    подхватывается в `__init__`. Семантика прежняя: отметка ставится ДО
    работы — упавший прогон не повторяется в цикле рестартов, retention
    ждёт следующих суток. Ошибки чтения/записи состояния логируются, а не
    проглатываются: тихо съехавшее расписание не заметили бы.
    **`pg_cron` ПРОВЕРЕН И ОТКЛОНЁН ПО ФАКТУ, а не по вкусу:** `SELECT *
    FROM pg_available_extensions WHERE name = 'pg_cron'` на проде
    возвращает ПУСТО — в образе `timescale/timescaledb:latest-pg17`
    расширения нет вообще (доступны только `timescaledb` и
    `pg_stat_statements`, `shared_preload_libraries = timescaledb`).
    Внедрение потребовало бы сменить Docker-образ БД и перезапустить сам
    PostgreSQL — неприемлемый риск перед месяцем без присмотра. Плюс
    ранее отмеченное ограничение подтвердилось: `pg_cron` не пишет
    `.csv.gz`, логика разъехалась бы между Python и SQL («два источника
    правды», уже отклонено ниже в записи про PL/pgSQL).
    **Проверено на проде, а не только локально** (локальные 6/6 проверок
    ничего не говорят о проде). Рестарт 2026-08-06 20:51:32 UTC; прогон
    отработал штатно (1 562 965 + 5 835 869 + 637 314 строк, экспортировано
    == удалено по всем трём, пауза главного цикла ~7 мин, зомби нет).
    Само свойство персистентности проверено БЕЗ второго рестарта бота:
    одноразовый скрипт создал новый экземпляр `HistoryArchiver` на отдельном
    соединении — ровно то, что делает рестарт процесса; `run_if_due()` не
    вызывался, решение посчитано вручную по той же формуле. Результат:
    `_last_run` подхвачен из БД (`2026-08-06 20:52:01 UTC`),
    `would_run_now = False`, следующий прогон через 23.86 ч. До фикса тот же
    экземпляр стартовал бы с `0.0` и запустил архивацию немедленно.
    **Третий случай сброса, 2026-08-03/04:** OOM-kill (20:31:32 UTC 03-08)
    и рестарт от `unattended-upgrades` (06:35:41 UTC 04-08) друг за другом
    снова сдвинули суточное расписание, на этот раз с ~20:27 на ~06:36 UTC
    — подробности OOM-инцидента см. ниже, отдельной записью.
  - **Отклонено (2026-08-04): перенос всей торговой логики/калькулятора в
    PostgreSQL (PL/pgSQL + pg_cron вместо Python).** Технически возможно, но
    не оправдано: PL/pgSQL заметно менее удобен для сложной логики, чем
    Python (тестирование, читаемость diff'ов, экосистема); реальные узкие
    места (round-trip'ы, блокировка event loop) уже закрыты точечно (индекс
    на FK, `to_thread()`, техника границы по `id`) без переписывания языка
    вычислений; разделение бизнес-логики между Python и PL/pgSQL создало бы
    проблему "два источника правды" в самом коде, не только в документации.
    Push-down вычислений в БД, где это оправдано, уже применяется точечно —
    агрегация в QUESTIONS.md через оконные функции SQL, без необходимости
    городить pg_cron-планирование поверх основной торговой логики.
  - **Частично смягчено 2026-08-01 (`to_thread()`), НЕ полное решение —
    читать оговорку в конце перед тем, как считать вопрос закрытым:**
    `run_if_due()` — синхронная
    `def`, вызывается без `await` внутри `async def main()` (main.py:346) —
    блокирует ВЕСЬ event loop на время архивации, не только текущую
    корутину. Обнаружено при разборе Q-002 (35+ мин бакет, 96 сделок
    закрылись одной минутой с outcome=vanished) — сверка `Scan complete` в
    логе показала разрыв **06:36:12 → 07:49:11 UTC 2026-07-31 (4379с, ~73
    минуты)**, начавшийся сразу после первого реального запуска
    `HistoryArchiver` (файлы архива датированы 06:37-06:48) и закончившийся
    ровно в минуту батч-закрытия тех 96 сделок и суточного отчёта. Всё это
    время цикл `scan()` не выполнялся вообще — не только пропущенные
    закрытия сделок с протухшей ценой, но и ноль новых находок/записей в
    `spread_history`/`arbitrage_opportunities` за 73 минуты.
    **Та же категория проблемы, что и п.1 внешнего ревью (раздел 5.5,
    "Синхронный psycopg блокирует event loop"), но на порядки серьёзнее:**
    там измерили ~2.15с блокировки на цикл (~24% времени, деградация
    производительности) — здесь тот же класс бага (синхронный вызов внутри
    `async def` без `await`/`to_thread`) дал не деградацию, а полную
    остановку: 73 минуты без единого цикла — ни сбора данных ни с одной
    биржи, ни проверки открытых позиций, ничего.
    **Проверено, что это разовая проблема первого запуска, не ежедневная:**
    на 08-01 (второй прогон архиватора) провалов >30с в `Scan complete` нет
    вообще — вероятно, первый прогон обрабатывал весь накопленный бэклог
    (retention ни разу не срабатывал с 07-17), последующие — только один
    день новых строк, на порядки меньше и быстрее.
    **ОТКАЧЕНО 2026-08-01 (позже в тот же день) — вывод выше был
    методологически ошибочным.** Проверка "провалов нет" смотрела только
    узкое окно 06:00-08:00 UTC. При деплое сегодняшнего `to_thread()`-фикса
    обнаружен разрыв в `Scan complete` **06:36:19 → 14:42:47 UTC (8+ часов)**
    на том самом "чистом" втором прогоне — вышел за пределы проверявшегося
    окна, поэтому остался незамеченным. `arbitrage_opportunities`
    (14 088 712 строк, ~805К подлежат архивации за цикл) не завершила
    архивацию ни разу за эти 8+ часов на старом синхронном коде. `EXPLAIN`
    (без `ANALYZE`, чтобы не провоцировать повтор на проде) показал разумный
    план (parallel seq scan + hash anti-join) — не патологический запрос как
    таковой; точная причина (совокупная нагрузка на диск/WAL/autovacuum от
    трёх последовательных больших архиваций подряд? что-то системное?) —
    **не установлена, требует расследования**. **Практическое следствие:**
    решение "отложить fire-and-forget, риск редкий" — тоже под вопросом,
    основывалось на неверной предпосылке. Не выбрасывать `to_thread()`-фикс
    (он всё ещё лучше синхронного блокирования), но считать вопрос
    производительности архивации **открытым и приоритетным**, не отложенным.
    **Известный риск на ближайшие сутки:** таймер `_last_run` сбросился при
    сегодняшнем рестарте — следующая проверка через ~24ч, к тому моменту
    `arbitrage_opportunities` пополнится ещё на ~900К-1М строк, тот же
    паттерн может повториться поверх уже задеплоенного `to_thread()`.
    **Сделано и протестировано (коммит `fc43d6f`, задеплоено 2026-08-01
    14:43:17 UTC):**
    `run_if_due()` обёрнут в `await asyncio.to_thread(...)`. Проверено
    реальным сценарием (не рассуждением) — локально, искусственный бэклог
    (весь 14-дневный объём разом, ~3.3М строк, `retention_days` временно
    занижен для теста): архивация заняла ~5.5 минут. **Результат теста
    честно смешанный, не "починено":** независимая фоновая задача
    (`health_monitor`, не трогает БД) отработала на расписании прямо во
    время архивации — event loop не заморожен технически, сигналы и прочие
    независимые корутины живы. НО главный цикл (`scan()`/сбор данных) всё
    равно не выполнялся все 5.5 минуты — `await` внутри той же
    последовательной корутины продолжает ждать архивацию, просто "вежливо"
    (прерываемо), не мёртвой хваткой на весь процесс.
    **Осознанно НЕ доведено до полного решения.** Настоящее устранение
    пропуска циклов требует fire-and-forget (`asyncio.create_task()` без
    `await` на месте вызова) — но это требует **отдельного, выделенного
    psycopg-соединения** для архиватора (конкурентный доступ к общему `conn`
    из двух потоков одновременно небезопасен — проверено анализом кода
    перед реализацией, не постфактум). Отдельное постоянное соединение
    нарушает принцип "единый писатель на процесс", осознанно заложенный в
    архитектуру с самого начала — такое изменение заслуживает отдельного
    архитектурного дизайна (жизненный цикл соединения при shutdown,
    обработка ошибок архивации без `await`-результата), не хирургической
    правки в конце и так насыщенной сессии. **Решение отложить:**
    риск сейчас низкий и редкий (второй прогон архиватора, 08-01, — без
    единого провала; многоминутная архивация возникает только при большом
    накопленном бэклоге — редкий сценарий: retention отключат/включат
    заново, или бот будет недоступен много дней подряд). Текущее состояние
    (управляемая пауза в несколько минут раз в сутки в обычном режиме,
    вместо полного паралича) — приемлемый компромисс, не финальное решение.
    Если частота/длительность архивации вырастет в будущем — вернуться к
    fire-and-forget с отдельным соединением как к спланированной задаче.
    **АВТОТРИГГЕР ОТКЛЮЧЁН 2026-08-01 15:20:56 UTC (коммит `519d2f5`) —
    расследование выявило корневую причину серьёзнее гипотезы про WAL/
    autovacuum.** Живая проверка (`pg_stat_activity`) во время третьего
    подряд инцидента показала не деградацию запроса, а **zombie-транзакцию**:
    `DELETE FROM arbitrage_opportunities` пережил `systemctl restart` бота
    и продолжал выполняться в Postgres 8.5+ часов после того, как Python-
    процесс, его запустивший, был убит — `asyncio.to_thread()` не отменяет
    фоновый поток при shutdown, соединение архиватора остаётся зависшим на
    стороне БД. Следующая попытка архивации при новом старте упиралась в
    lock от этого зомби, создавая видимость "тот же паттерн повторяется".
    Убито через `pg_terminate_backend()` дважды (второй раз — новый зомби,
    возникший сразу после первого рестарта, тем же путём) — оба раза с
    явного разрешения пользователя, безопасно (`DELETE` не был закоммичен).
    `EXPLAIN` был в порядке — значит проблема не в плане запроса.
    **Рабочая гипотеза для следующей сессии (не подтверждена, это её
    отправная точка):** `_archive_table()` делает `SELECT *` через обычный
    (не именованный/server-side) psycopg-курсор — по умолчанию это
    буферизует весь результат (~14М строк для `arbitrage_opportunities`)
    в памяти/на стороне клиента ДО первого `fetchmany()`, а не отдаёт
    потоково. Для таблицы такого размера это может объяснять и саму
    аномальную длительность, и распухание памяти процесса (наблюдали рост
    до 1.6 ГБ). Задача уже, чем казалось изначально — не "спроектировать
    fire-and-forget архитектуру", а сначала попробовать именованный курсор
    (`connection.cursor(name=...)`) с батчевой выборкой в `_archive_table()`.
    **Текущее состояние — retention временно НЕ работает** (вызов
    `run_if_due()` закомментирован в `main.py`, не удалён — тривиально
    включить обратно одной строкой после фикса и теста). Диск на момент
    отключения — 71% (51G/75G, 22 ГБ свободно), рост без компенсации
    retention ~2.6-3 ГБ/сутки по прежним замерам → **ориентировочно ~7-8
    суток до необходимости вмешаться**, не бесконечный запас времени.
    **Перед повторным включением автотриггера — обязательно:** протестировать
    именно гипотезу про server-side курсор реальным сценарием (большой
    бэклог, как тестировали `to_thread()`), не полагаться на рассуждение;
    проверить `pg_stat_activity` на отсутствие зомби после теста; явно
    решить, что делать при следующем перезапуске сервиса, чтобы бэклог не
    накопился настолько, что первое включение снова упрётся в те же часы.
  - **КОРНЕВАЯ ПРИЧИНА НАЙДЕНА И УСТРАНЕНА 2026-08-01 — неиндексированный
    внешний ключ, не курсор и не план запроса.** `simulated_trades
    .opportunity_id` (FK на `arbitrage_opportunities.id`) не имел индекса:
    на `simulated_trades` были только `pkey` и `idx_simulated_trades_status`.
    `DELETE` из родительской таблицы запускает RI-проверку `SELECT 1 FROM
    ONLY simulated_trades WHERE $1 = opportunity_id FOR KEY SHARE` на
    КАЖДУЮ удаляемую строку — без индекса это полный seq scan 122К строк /
    199 МБ на строку. При ~826К удаляемых строк за суточный прогон это
    ~10^11 чтений. Доказательства с прода: `simulated_trades.seq_scan =
    9 417 726`, `seq_tup_read = 270 159 521 669` (270 млрд строк из
    таблицы на 122К строк); `arbitrage_opportunities.n_tup_del = 2 734 393`
    при `min(id) = 10` — три инцидента × ~826К попыток, ни одна не
    закоммичена. Скорость 826К/8.5ч = 27 строк/сек = 37 мс на строку =
    один скан 199 МБ из shared_buffers.
    **Почему два разбора через `EXPLAIN` этого не увидели:** RI-триггеры
    внешнего ключа не входят в план запроса вообще. Они видны ТОЛЬКО в
    `EXPLAIN ANALYZE` отдельной строкой `Trigger for constraint ...` после
    дерева плана. План был в порядке — он просто не про то место, где
    уходило время. Правило на будущее: при разборе медленного `DELETE`
    из таблицы, на которую есть FK, `EXPLAIN` без `ANALYZE` бесполезен.
    **Гипотеза про server-side курсор проверена и разделена надвое:**
    как факт кода подтверждена (обычный курсор, psycopg3 буферизует весь
    результат в libpq до первого `fetchmany()`; в тесте видно, что backend
    уходит в `idle in transaction`, пока Python минутами пишет gzip). Как
    причина зависания — ОПРОВЕРГНУТА: по таймингам файлов архива и лога
    прода экспорт `arbitrage_opportunities` оба раза укладывался в минуту.
    Сверка объёмов: у `spread_history` и `futures_spread_history` второй
    прогон дал ровно 0.33 первого и по строкам, и по байтам, у
    `arbitrage_opportunities` — 1.38, потому что `DELETE` не закоммитился
    и во втором прогоне выгружались те же строки плюс 8 часов дозревших.
    **Замер на локальной копии сопоставимого объёма** (`arch_test`,
    14 100 000 строк / 5509 МБ против прода 14 096 227 / 4973 МБ;
    `simulated_trades` 122 000 / 106 МБ против 122 048 / 199 МБ — вдвое
    меньше, то есть замер занижает стоимость, не завышает; shared_buffers
    1.19 ГБ против 1.94 ГБ, work_mem 14.9 против 15.5 МБ), батч 2000 строк:
    план 147 мс, `Trigger for constraint simulated_trades_opportunity_id_fkey:
    time=33348.084 calls=2000` — 16.7 мс на строку, 99.5% времени запроса.
    После индекса тот же замер: 106 мс, 0.053 мс на строку — **в 314 раз**.
    Экстраполяция без индекса: 4.3 ч локально, ~7.6 ч на проде при
    наблюдавшихся 8.5 ч.
    **Сделано:**
    1. Индекс `idx_simulated_trades_opportunity_id` — в
       `SimulatedTradeRepository._create_table()` и на проде через
       `CREATE INDEX CONCURRENTLY` (локально 209 мс, на проде 147 мс,
       2736 кБ). Аудит всех FK прода: это был единственный
       неиндексированный FK в прикладной схеме (остальные без индекса —
       внутренние каталоги TimescaleDB).
    2. Предохранители от зомби-транзакций в `HistoryArchiver`:
       `SET LOCAL statement_timeout` (15 мин) внутри транзакции архивации
       через `set_config(..., is_local=true)` — на проде глобальный
       `statement_timeout = 0`, останавливать зомби было нечем в принципе;
       `cancel_running()` → `conn.cancel()` из обработчика SIGTERM/SIGINT
       (`main.py`); флаг `_cancelled`, который не даёт начать архивацию
       следующей таблицы и запустить `DELETE` после запроса на остановку;
       удаление незавершённого `.csv.gz` при прерывании.
    **Проверено реальными прогонами (не рассуждением):** полный
    end-to-end прогон настоящего `HistoryArchiver` через тот же путь, что
    в `main.py` — 940 027 строк за 158 с, экспортировано = удалено.
    Сценарии остановки:
    - жёсткое убийство процесса (аналог SIGKILL по `TimeoutStopSec`) в
      момент `DELETE`: зомби ВОЗНИКАЕТ — это уровень ОС/БД, кодом не
      предотвращается — но живёт 87 с вместо 8.5 ч, потому что сам запрос
      стал коротким; `DELETE` откатился, данные целы;
    - SIGTERM в момент `DELETE`: отмена → откат → выход за 41 мс,
      `pg_stat_activity` чист. PostgreSQL в тексте ошибки подтвердил
      механизм: `CONTEXT: SQL statement "SELECT 1 FROM ONLY
      "public"."simulated_trades" x WHERE $1 OPERATOR(pg_catalog.=)
      "opportunity_id" FOR KEY SHARE OF x"`;
    - SIGTERM в фазу записи gzip (на стороне БД ничего не выполняется,
      отменять нечего) — этот тест вскрыл дыру: без явной проверки флага
      архиватор дошёл бы до `DELETE` уже ПОСЛЕ запроса на остановку.
      Закрыто; после фикса реакция 1.27 с (граница чанка), `DELETE` не
      запускается вообще.
    **Остаточные ограничения, зафиксировать честно:** SIGKILL всё равно
    оставляет выполняющийся запрос (минуты, не часы) и незавершённый
    `.csv.gz` на диске — после SIGKILL Python-код уже не исполняется.
    **Отложено осознанно, отдельными задачами:**
    - server-side курсор (`connection.cursor(name=...)`) — экспорт 8М
      строк `futures_spread_history` буферизует ~1.4 ГБ в libpq на машине
      с 7.7 ГБ и БЕЗ swap (это и есть наблюдавшиеся 1.6 ГБ). Проблема
      реальная и самостоятельная, но к зависанию отношения не имеет;
    - три полных seq scan на таблицу за прогон (`COUNT`, `SELECT`,
      `DELETE`) — ни у одной из трёх таблиц нет индекса с `timestamp` в
      ведущей позиции; плюс `DELETE` повторно вычисляет тот же `NOT IN`,
      из-за чего возможно расхождение «экспортировано ≠ удалено».
      Чинится удалением по собранным при экспорте `id` батчами.
    **Гигиена data lake:** архивы `arbitrage_opportunities_2026-08-01
    .csv.gz` и `..._145103.csv.gz` на проде содержат пересекающиеся
    строки (`DELETE` не прошёл ни разу) — при чтении дедуплицировать
    по `id`.

- [x] **`DailyReport` — суточная сводка блокировала event loop, исправлено
  2026-08-01 (коммит `4321d4f`):**
  - **Что было:** `daily_report.log_if_due()` вызывался в `main.py`
    СИНХРОННО, без `await`/`to_thread` — то есть блокировал event loop
    целиком, хуже архиватора. Внутри 5 запросов вида `SELECT COUNT(*) ...
    WHERE timestamp > %s` по растущим таблицам истории; индекса с
    `timestamp` в ведущей позиции нет ни у одной, поэтому каждый давал
    полный seq scan: 5 ГБ + 4 ГБ + 19 ГБ за одну сводку. Запускается при
    КАЖДОМ старте процесса (`_last_run = 0`). Замеры с прода: **4 мин 53 с
    (2026-07-31)** и **4 мин 45 с (2026-08-01)**.
  - **Историческая атрибуция проверена по логам и ОПРОВЕРГАЕТ
    первоначальное предположение** (оно звучало как «часть 73-минутного
    разрыва 31.07 принадлежит сводке»). Точный разбор:
    `06:36:12` последний скан → `06:38:00` архиватор `spread_history`
    (265 236) → `06:47:55` архиватор `futures_spread_history` (746 232) →
    `07:44:08` архиватор `arbitrage_opportunities` (105 354) → `07:49:01`
    сводка → `07:49:11` скан возобновился. Сводке принадлежит **4 мин
    53 с, то есть 7%**; 68 из 73 минут — архиватор. **Побочно это дало
    независимое подтверждение диагноза про неиндексированный FK на данных
    другого дня:** 56 мин 13 с на 105 354 строки = **32 мс на строку**,
    ровно расчётная стоимость RI-проверки на проде.
  - **Индексы по `timestamp` рассмотрены и отклонены по фактам:**
    btree на три таблицы = ~4.4 ГБ при 21 ГБ свободных (диск 72%) плюс
    `CREATE INDEX CONCURRENTLY` по 19-ГБ таблице под живой записью. BRIN
    был бы ~1 МБ (`pg_stats.correlation` = 1.0 у всех трёх таблиц), но
    опирается на физический порядок строк, а он обречён расползтись:
    у `futures_spread_history` 13.2М мёртвых строк, после autovacuum
    ведущие страницы освободятся и вставки начнут их переиспользовать.
    Для скользящего окна с retention это ненадёжный фундамент.
  - **Сделано — счёт по границе `id` через существующий PK.** Таблицы
    append-only, `id` (`BIGSERIAL`) монотонен по времени вставки, PK-индекс
    уже есть: граница суток ищется двоичным поиском за ~20 точечных чтений
    (`_id_boundary()`), счёт сводится к диапазону по PK (`_count_since()`).
    Надёжность опирается на монотонность последовательности, гарантированную
    конструкцией, а не на текущую раскладку данных на диске.
    Двоичный поиск устойчив к дырам: пробуется не сам `mid`, а первая
    существующая строка в `[mid, hi]` — retention оставляет пропуски
    (старые строки удаляются, кроме тех, на которые ссылаются
    `simulated_trades`).
    `funding_rate_history` (101 МБ) и `simulated_trades` (199 МБ) оставлены
    на прямом фильтре — seq scan такого размера это доли секунды.
    Плюс `to_thread` и путь отмены (`cancel_running()` → `conn.cancel()`)
    по образцу `HistoryArchiver`.
  - **Замеры на проде (read-only, до деплоя):** поиск границ трёх таблиц
    1.47 с; `COUNT` по границе — futures 1.41 с, spread 0.45 с,
    arbitrage_opportunities 0.62 с. Итого **~3.9 с против 4 мин 45 с**.
  - **Смена семантики, названа явно:** граница по `id` точна с точностью
    до одной пачки вставки (внутри цикла строки делят общий `timestamp` и
    получают подряд идущие `id`). Для суточной сводки в лог несущественно.
  - **Проверено локально на сопоставимом профиле** (БД `dr_test`: futures
    12М строк, spread 6М, `arbitrage_opportunities` 2.8М — специально
    дырявая, **6.6% пропусков `id`** после имитации retention):
    счёт по границе `id` совпал со счётом по `timestamp` **строка в
    строку** по всем четырём запросам (199 803 / 66 601 / 399 330 /
    797 796); краевые случаи (пустая таблица, все строки свежее, все
    старее) — тоже. Сценарий прерывания: SIGTERM в момент построения
    сводки → отмена за 8 мс → исключение поймано, процесс не упал → выход
    за 17 мс → `pg_stat_activity` чист.
    Локальное ускорение 11.67 с → 1.38 с (8.5x) меньше продового 73x
    ПОТОМУ ЧТО локальная таблица в 9 раз меньше: старый путь
    масштабируется с полным размером таблицы, новый — только с объёмом
    суточного среза. Это ожидаемое поведение, не расхождение.
  - **Результат на проде (рестарт 20:26:13 UTC):** сводка **5 секунд**
    вместо 4 мин 45 с; пауза главного цикла 5 мин 57 с против 12 мин 51 с
    при прошлом рестарте; зомби нет, ноль ERROR в логе.
  - **Следствие — теперь пауза это почти целиком собственные `COUNT(*)`
    архиватора:** в том же прогоне он потратил 5 мин 43 с на 387 252
    строки суммарно, из них ~3 мин 20 с — `COUNT` по 19-ГБ
    `futures_spread_history`. Отложенный «фикс 4» (три seq scan на таблицу
    за прогон) стал главным оставшимся источником пауз, и техника границы
    по `id` переносится туда напрямую, без индексов.
  - **Не сделано, отдельным пунктом:** `arbitrage_opportunities` в сводке
    считается двумя запросами (возможности и коллизии) — сводится в один
    через `FILTER`.

- [ ] **ПРИОРИТЕТ: OOM-kill процесса бота, 2026-08-03 20:31:32 UTC —
  инцидент расследован, но причина потребления ~3.6 ГБ памяти НЕ
  установлена. Выяснить в ближайшие дни, не откладывать неопределённо.**
  - **Что произошло:** ядро убило главный процесс (`status=9/KILL`) через
    3 мин 31 с после того, как архиватор успешно завершил `spread_history`
    (20:28:01) — то есть в момент, когда должен был идти `COUNT(*)` по
    `futures_spread_history` (следующая таблица в очереди). systemd поднял
    процесс заново через 10 секунд (`Restart=on-failure` сработал штатно).
  - **Данные не пострадали:** файл `futures_spread_history_2026-08-03.csv.gz`
    на Volume — единственный, полный (229 МБ), без огрызков/дублей.
  - **ИСПРАВЛЕНО 2026-08-05: вывод, сделанный при первом разборе
    ("крах на фазе `COUNT(*)`, это ослабляет гипотезу про Фикс 2"), был
    НЕВЕРЕН.** Ошибка рассуждения: `_target_path()` (строка 159) файл не
    создаёт, только вычисляет имя; файл появляется на `gzip.open` (строка
    167), то есть ПОСЛЕ `execute("SELECT *")` (строка 164). Значит
    отсутствие обрезанного файла не локализует фазу — окно между концом
    `COUNT` и созданием файла целиком занято буферизацией. Решающий довод:
    `COUNT(*)` возвращает одну строку и клиентскую память израсходовать не
    может в принципе, а OOM — событие памяти.
  - **Улики указывают НА "Фикс 2" (буферизация `SELECT *` без
    server-side курсора), количественно:** замер `pg_column_size` на проде
    2026-08-05 — `futures_spread_history` 161 Б/строка данных при 16
    колонках; libpq держит в `PGresult` ещё и массив дескрипторов полей
    (~16 Б × 16 колонок = 256 Б/строку). При суточном объёме 7 623 050
    строк это **~3.2 ГБ разовой аллокации** поверх базовых 1.4 ГБ RSS,
    при `VmHWM` 3.85 ГБ и 7.7 ГБ на всю машину (плюс PostgreSQL и
    Metabase). Порядок величины сходится с наблюдаемым OOM.
  - **Не утечка, а всплеск — проверено:** у живого процесса 2026-08-05
    (аптайм 1 сут 13 ч) `VmRSS` 1.4 ГБ при `VmHWM` 3.85 ГБ. При утечке
    текущее значение было бы близко к пику. `health_monitor` как источник
    исключён: списки `latencies`/`errors` ограничены (100/20 элементов,
    обрезаются в `record_request`).
  - **Восстановление — чистое:** `spread_history` доархивировал небольшой
    остаток (9 695 строк) сразу после рестарта, `futures_spread_history`
    и `arbitrage_opportunities` прошли штатно в тот же день. Зомби-транзакций
    не осталось (`pg_stat_activity` чист при проверке двумя днями позже).
  - **Почему это приоритет, а не фоновое наблюдение:** память системы
    работает у края. У убитого инстанса пик потребления был 3.6 ГБ за
    ~2 суток аптайма. У СЛЕДУЮЩЕГО инстанса (работает с 04-08, ни разу не
    падал на момент проверки 05-08) пик — **тоже 3.6 ГБ**. Swap на VPS
    отсутствует (0/0/0), свободно ~2.3 ГБ из 7.7 ГБ. Это похоже на
    системную характеристику процесса, не разовую случайность — при чуть
    менее удачном стечении обстоятельств повторится, и OOM-kill — не
    мягкая деградация, а внезапная остановка без предупреждения в
    непредсказуемый момент.
  - **ЗАКРЫТО 2026-08-06 деплоем server-side курсора (коммит `a43f8ca`).**
    Гипотеза «улики указывают НА буферизацию» подтвердилась практикой:
    после перевода `_archive_table()` на именованный курсор
    (`conn.cursor(name=...)` + `itersize`) пик памяти на проде упал с
    3.85 ГБ до 315 МБ при 18.5М строк за прогон (рестарт 00:23:30 UTC).
    Контрольный замер живого процесса 2026-08-06 21:10 UTC: `VmHWM`
    **384 МБ**, `VmRSS` 293 МБ — и это пик процесса, который к тому моменту
    уже отработал ПОЛНЫЙ цикл архивации (20:52→20:59, 8.0М строк), то есть
    измерение покрывает тяжёлую фазу, а не только холостой ход.
    OOM-событий в kernel-логе после деплоя нет (единственное за август —
    то самое 03-08 20:31:32).
    **Профилирование памяти (`tracemalloc`/снимки RSS по компонентам),
    записанное здесь как следующий шаг, БОЛЬШЕ НЕ НУЖНО** — причина
    установлена и устранена, запускать его заново незачем. Ранее
    отмеченное «не утечка, а всплеск» этим и объясняется: всплеском была
    разовая аллокация `PGresult` на суточный объём выгрузки.
    **Оговорка, чтобы не переоценить:** «не падал больше» опирается на
    ~21 час наблюдения после деплоя, не на месяц. Запас памяти по-прежнему
    не бесконечен (swap отсутствует, свободно ~3.4 ГБ из 7.7), поэтому
    при следующем OOM-kill — если он всё же случится — начинать надо не с
    профилирования заново, а со сверки, какой именно фазе он соответствует.
  - **Побочный эффект:** сбросил `_last_run` архиватора (см. gotcha выше,
    третий подобный случай).

- [x] **`logs/systemd-stdout.log` рос без ротации — закрыто 2026-08-06.**
  - Найдено при подготовке бота к автономной работе: файл 211 МБ и растёт
    ~10 МБ/сутки, единственный на сервере файл с неограниченным ростом.
  - **Это не служебный шум systemd, а полная копия прикладного лога** —
    1 382 189 строк, те же записи, что в `logs/arbitrage_*.log` (логгер
    пишет и в файл, и в stdout). То есть существовал второй экземпляр всего
    лога, растущий вечно, при том что у оригинала ротация есть.
  - Решение: `/etc/logrotate.d/multi-exchange-arbitrage` — только
    `systemd-stdout.log` и `systemd-stderr.log` (`daily`, `maxsize 50M`,
    `rotate 7`, `compress`, `copytruncate`). `logs/arbitrage_*.log` в конфиг
    НЕ включены намеренно: у них своя ротация через `RotatingFileHandler`
    в `src/utils/logger.py`. Dry-run прогнан до применения; результат —
    211 МБ → 18 МБ `.gz`, диск 79% → 78%.
  - **Gotcha при правке этого конфига:** `copytruncate` обязателен — юнит
    открывает файлы через `StandardOutput=append:` и держит дескриптор до
    перезапуска сервиса, обычную ротацию (rename+create) он бы не заметил и
    продолжил писать в переименованный файл; `append:` = `O_APPEND`, поэтому
    усечение на месте безопасно. Проверено: после ротации файл начал расти
    заново, сервис не перезапускался. `delaycompress` сознательно НЕ
    используется — он нужен, когда процесс дописывает в переименованный
    файл, а с `copytruncate` копия `.1` сразу никем не используется; с ним
    200+ МБ пролежали бы несжатыми лишние сутки.

- [x] **Рост БД замедлился до ~195 МБ/сутки — закрыто наблюдение от
  2026-08-03 (было ~2.6 ГБ/сутки, тот же темп, что и ДО фикса retention).**
  - Точный байтовый снимок по трём таблицам, тот же приём, что и раньше
    (не оценка по row-count): baseline `2026-08-03 19:23:36 UTC` →
    контрольный снимок `2026-08-05 19:03:01 UTC` (47.7 часа, три полных
    цикла архивации внутри окна — 03-08, 04-08, 05-08).
  - `futures_spread_history` (самая крупная таблица, ~70% БД): размер НЕ
    изменился ни на байт (34 502 328 320 в обоих замерах). Вся БД:
    48.79 ГБ → 49.20 ГБ, **+388 МБ / 47.7ч ≈ 195 МБ/сутки** — падение
    примерно в 13 раз от исходного темпа.
  - **Интерпретация:** похоже, система вышла на устойчивый режим — окно
    retention (14 дней) наконец догнало приток данных. При 17.2 ГБ
    свободных это ~68 суток запаса вместо тревожных ~6.
  - Не проверено окончательно, что это устоявшийся темп, а не разовое
    удачное окно (три цикла — лучше, чем один, но не месяц наблюдений) —
    если рост снова ускорится, вернуться к этой записи, не считать вопрос
    закрытым навсегда.

- [ ] **Withdrawal fee parser (комиссии на перевод монет):**
  - Двухуровневый кеш — тот же паттерн, что уже есть для Order Book 
    (`get_order_book_cached()`): широкий TTL-кеш (раз в сутки, все уникальные 
    монеты) + точечный refresh для конкретной монеты при найденном спреде
  - **Источник данных частично найден (2026-07-14), задача реализована не на 100%:** 
    публичный KuCoin API `/api/v3/currencies/{coin}` (сети, комиссии вывода, статус — 
    без авторизации) проверен при ручном пополнении словаря переводов. Покрывает 
    только монеты, листингованные на KuCoin: связки между Gate.io/MEXC по монетам, 
    которых на KuCoin нет, остаются без комиссий — нужны дополнительные источники 
    по остальным биржам (аналогичные эндпоинты Gate.io/MEXC требуют приватных 
    ключей). Остаётся: источники для остальных бирж + обернуть в двухуровневый 
    кеш по паттерну Order Book (см. выше). Проверено 2026-07-15: CoinGecko как 
    источник withdrawal fee ОТПАЛ — публичный API (coins/{id}, exchanges/{id}) 
    не содержит полей комиссий вывода; комиссия — свойство «биржа+монета+сеть», 
    агрегатор монет её не отдаёт.
  - **Найден более сильный источник (2026-07-16, разбор стороннего проекта):** 
    `ccxt.fetch_currencies()` даёт per-network статус (deposit/withdraw открыт, 
    комиссия, **адрес контракта токена**) напрямую с бирж. У части бирж (в чужом 
    проекте — kucoin/gate/htx/bitget) вызов **публичный, без ключей**. Это не 
    просто источник комиссий — сверка адреса контракта на общей сети между двумя 
    биржами ловит коллизию тикеров **детерминированно** (не по цене/порогу, как 
    наш текущий детектор — разные контракты = гарантированно разные токены), 
    и дополняет, а не заменяет ценовой детектор. **Открытый вопрос закрыт
    (2026-07-23), проверено напрямую:** `fetch_currencies()` без ключей
    публично работает у **KuCoin** (2220 валют, контракт токена в ответе)
    и **Gate.io** (5334 валюты, контракт в поле `addr` сырого `info`); у
    **Binance и MEXC требует ключей** — см. отдельный gotcha в разделе 6
    про тихий пустой словарь вместо исключения. Практически ценно: KuCoin
    и Gate.io — ровно те биржи, где 2026-07-22 найдены реальные коллизии
    (`collision_blocklist.py`, 8 тикеров) — детерминированная проверка по
    контракту токена усилила бы именно этот механизм на будущее.
    **Реализация — отдельная, спланированная задача** (архитектура кеша
    по паттерну Order Book, решение по Binance/MEXC — жить на ценовом
    детекторе или вернуться к вопросу read-only ключей), не начата;
    `ccxt` не добавлен в `requirements.txt` — проверка была разовым
    тестом в venv, пакет удалён после неё.

### 5.2. Новые платформы (среднесрочный)
- [ ] **DEX (децентрализованные биржи):**
  - Uniswap V2/V3 (Ethereum)
  - PancakeSwap (BSC)
  - TraderJoe (Avalanche)
  - Требуется: интеграция с web3.py, чтение пулов ликвидности через RPC
- [x] **CEX биржи: Gate.io и MEXC — добавлены** (API + Collector по шаблону раздела 4, 
  slug'и `gate`/`mexc`, сбор в основном цикле; включены в EXCHANGE_TABLES сканера 
  спредов и, как следствие, в paper trading — см. пометку про shakeout в 5.1).
  - **Уточнение по MEXC:** API (exchangeInfo) сообщает per-symbol комиссии 
    maker 0% / taker 0.05% — исходное наблюдение про "постоянный 0% taker" 
    (внешнее, не проверка собственного аккаунта) публичным API не подтверждается. 
    Зарегистрировано консервативное 0.05%; ручной override при подтверждённой 
    скидке — задача 5.3.
  - **Кандидаты на будущее:** OKX, Bybit, Kraken (5-я биржа — после миграции БД, 
    см. 5.2.1); дальний кандидат — Bitunix (Trust Score 9/10 на CoinGecko, топ-10 
    по надёжности, но ликвидность тоньше топовых — только после OKX/Bybit/Kraken)
  - **Источник для выбора следующих бирж — CoinGecko** (проверено 2026-07-15): 
    бесплатный эндпоинт `/api/v3/exchanges` отдаёт Trust Score (ликвидность, объём, 
    регуляторный статус) и объёмы без API-ключа, включая DEX — использовать 
    топ-10/20 при выборе кандидатов по мере роста проекта
- [x] **Фьючерсы Gate.io и MEXC — выполнено 2026-07-16** (коммиты fb9d4b2, cd17542,
  1ccb948, 0c27a29, 839f7ea): `GateFuturesAPI`/`MexcFuturesAPI` + коллекторы + funding,
  интегрированы в main.py (итого 8 потоков сбора), участвуют в кросс-биржевой
  фьючерсной детекции (`FuturesSpreadMonitor`, 22 сравнения). Работают в проде.

### 5.2.1. План масштабирования и переход на удалённый сервер

- [x] **Довести количество поддерживаемых CEX-бирж до 4** — выполнено (Binance, KuCoin, 
  Gate.io, MEXC). Осознанно НЕ 5 — порог 5+ бирж уже зафиксирован ниже как триггер 
  для миграции на PostgreSQL/TimescaleDB, инфраструктура под которую ещё не готова. 
  5-я биржа (из кандидатов OKX/Bybit/Kraken) — после того, как миграция БД будет 
  готова принять возросшую нагрузку.
- [x] **Перенос проекта на удалённый сервер (VPS) — ВЫПОЛНЕН 2026-07-17, бот работает автономно.** 
  Выполняется после добавления Gate.io и MEXC (4 биржи) — следующий шаг после проверки 
  стабильности на новом масштабе в локальном режиме. VPS позволит работать 24/7 
  независимо от локального ноутбука. Целевой срок — 2026-07-18, ПОСЛЕ выполнения 
  пункта "Подготовка к первому VPS-прогону" (5.1). В чек-лист настройки VPS включить 
  `lnav` (POSIX-инструмент чтения логов: мерж файлов по времени, SQL по логам, 
  headless-режим — на Windows недоступен, ставится сразу на сервере).
  **Решения (2026-07-15):** старт с ЧИСТОЙ БД — локальная история содержит 
  shakeout-артефакты трёх тестовых конфигураций, временные ряды для Q-анализов 
  должны начинаться со стабильной конфигурации (локальная БД остаётся у 
  пользователя). В чек-лист VPS: Docker + `docker compose up -d` (БД — часть 
  развёртывания), rclone (опционально), lnav. **Диск (замер длинного прогона 
  2026-07-16):** фактический прирост БД в PostgreSQL ~1.8 ГБ/сутки (уже с 
  PG-оверхедом) — ниже верхней теоретической оценки. **Решение пользователя: 
  retention_days=14 (дефолт кода не меняется)** → БД за 14 дней ≈ 25–30 ГБ; 
  на диске 80 ГБ работает с запасом ~2× (БД + csv.gz-архивы + ОС/Docker ≈ 
  40–45 ГБ). Перепроверить по суточной сводке в логе после запуска на VPS.
  - **Инфраструктура (Hetzner CX33 + Volume 80 ГБ, Ubuntu 26.04):** Docker
    Compose (`timescale/timescaledb:latest-pg17`), venv + `requirements.txt`,
    systemd-юнит `multi-exchange-arbitrage.service` (`enabled` + `active`,
    `Restart=on-failure`, `After=docker.service` — переживает и падение
    процесса, и ребут сервера), `ufw` (default-deny, разрешён только 22/tcp),
    `lnav` установлен. БД — на корневом диске (68 ГБ свободно, решение
    2026-07-17: разделять диски не стали, см. ниже); Volume 80 ГБ смонтирован
    в `/mnt/HC_Volume_106390176` и используется ТОЛЬКО под архивы
    `HistoryArchiver` — `data/archive` на VPS это симлинк на Volume (не файл
    в git, настраивается вручную при каждом передеплое).
  - **Gotcha (зафиксировать для будущих передеплоев):** Ubuntu 26.04 несёт
    только Python 3.14, а `requirements.txt` пока не имеет закреплённых
    wheel-совместимых версий под 3.14 (`aiohttp==3.10.10` собирается из
    исходников — работает, но требует `build-essential` + `python3.14-dev`
    на сервере). При следующем передеплое/апгрейде ОС эта же проблема может
    повториться — либо доустанавливать компилятор заново, либо обновить
    версии в `requirements.txt` под актуальный Python отдельной задачей.
  - **Проверено функционально, не только по статусам:** ручной прогон 5+ мин
    без ошибок → systemd-запуск подтверждён записью в БД (не просто "active")
    → firewall проверен НОВЫМ SSH-подключением (не только текущей сессией) —
    ни один шаг не считался пройденным по одному лишь "команда не упала".
  - **Побочное архитектурное подтверждение:** во время развёртывания бот
    поймал случайный SIGTERM (внешняя причина — фоновая SSH-сессия
    инструмента, не баг проекта) и после паузы ~2ч корректно закрыл
    зависшие paper trading позиции по АКТУАЛЬНЫМ ценам на момент
    фактического закрытия (не по предположению на момент открытия) —
    живое подтверждение Realistic-модели Фазы 1 на реальной, незапланированной
    ситуации, не в тесте.
- [x] Подготовка инфраструктуры миграции SQLite → PostgreSQL/TimescaleDB — выполнено, см. 5.5.
- [x] Уточнить раздел 5.5 — РАЗРЕШЕНО 2026-07-15: миграция на PostgreSQL выполнена 
  ДО переноса на VPS (см. 5.5); VPS-перенос идёт сразу на PostgreSQL в Docker, 
  промежуточный SQLite-этап на VPS отменён.

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
- [ ] **Analysis Module — аналитический слой поверх собранных данных:**
  - Цель: раз в сутки (позже — раз в неделю) находить пропущенные калькулятором 
    спреды, оценивать длительность жизни спреда, искать неочевидные паттерны
  - **На старте — только 2 документа**, остальные сущности вводятся по факту 
    накопления данных, не заранее:
    - `DATA_SPECIFICATION.md` — техническое задание, какие агрегаты/метрики 
      нужны модулю (писать после того, как сформулированы первые вопросы)
    - `QUESTIONS.md` — список конкретных исследовательских вопросов (Q-001, 
      Q-002...) со статусами Confirmed/Rejected, не готовых гипотез
  - **Критерии подтверждения гипотезы** — явно прописать порог перехода в 
    Confirmed (например: не менее 100 случаев, не менее 14 дней наблюдений, 
    проверено на разных биржах, пережила попытку опровержения)
  - **Архитектура данных:** SQLite → Python/SQL агрегация → готовые метрики → 
    LLM интерпретирует уже посчитанное, НЕ читает сырую БД целиком (риск 
    нахождения ложных паттернов в шуме при прямом чтении сырых данных)
  - Не антропоморфизировать модуль ("AI хочет/думает") — называть 
    "Analysis Module"/"Research Module", это компонент системы, не личность
  - Модель-исполнитель — любая LLM с доступом к code execution: метрики 
    считаются реальным кодом (Python/SQL), не восстанавливаются моделью по памяти
  - Остальные сущности (Observation, Hypothesis, Experiment, Finding, 
    Recommendation) — вводить по факту накопления данных, вероятно как 
    таблицы БД, не markdown-файлы
  - Зависит от пункта "Исторические данные" (`price_history`) выше — часть 
    метрик (длительность жизни спреда) требует непрерывной истории, не 
    только последнего среза
- [ ] **Уведомления:**
  - Telegram-бот при найденном арбитраже
  - Оповещения при падении/восстановлении бирж

### 5.4. Инфраструктура (долгосрочный)
- [ ] **REST API** (FastAPI) для внешнего доступа к данным
- [x] **Веб-интерфейс — дашборд с графиками и метриками — реализовано 2026-07-21
  через Metabase** (не собственная разработка, готовый open-source инструмент):
  - Отдельный Docker-стек `/root/metabase/docker-compose.yml` на VPS,
    **вне** проектного `docker-compose.yml` и вне `git` — намеренно
    развязан от жизненного цикла бота (передеплой/рестарт бота его не
    касается, и наоборот)
  - Подключается к БД через новую read-only роль PostgreSQL
    `arbitrage_readonly` (`GRANT SELECT` на все текущие и будущие таблицы
    через `ALTER DEFAULT PRIVILEGES`; INSERT/UPDATE/DELETE — `permission
    denied`, проверено функционально, не только по гранту). Даже полная
    компрометация Metabase не даёt возможности писать в прод-БД
  - Порт 3000 открыт в `ufw` (`0.0.0.0:3000`, доступ по паролю самого
    Metabase — задаётся пользователем при первом входе в веб-мастере,
    не хранится в коде/конфигах проекта). Единственный порт кроме SSH,
    открытый наружу на этом сервере
  - Пароль read-only роли — НЕ в git (репозиторий публичный); выдан
    пользователю напрямую, инструкция по использованию — `CHEATSHEET.md`
  - Мотивация: пользователь не работает с терминалом напрямую и до этого
    видел сервер только через отчёты Claude Code — дашборд даёт
    самостоятельную визуальную проверку данных (например, сверка
    withdrawal fee в `transfer_config.py` с реальными комиссиями на своих
    аккаунтах), не заменяя, а дополняя текстовые отчёты
- [x] **Очистить git от `data/arbitrage_data.db`** — правило `data/*.db` уже в `.gitignore`, выполнено: `git rm --cached` + коммит, файл больше не отслеживается.
- [x] **Uptime Kuma — проактивный мониторинг главного цикла. СДЕЛАНО И
  ПРОВЕРЕНО ВЖИВУЮ 2026-08-06** (найдено 2026-08-04). Push-based
  уведомления в Telegram при сбое —
  закрывает реальный пробел: текущие инструменты (Metabase, lnav, ручные
  чек-апы) требуют, чтобы кто-то сам зашёл проверить; ни один не толкает
  проактивно. Особенно применимо к уже случавшимся инцидентам (73-минутный
  паралич главного цикла, 8.5-часовая zombie-транзакция) — во всех случаях
  порты/процесс формально были живы, обычная TCP/HTTP-проверка доступности
  их бы не поймала.

  **Ключевое архитектурное решение (уже продумано, не в момент реализации
  придумывать заново):** использовать Push-монитор Kuma (процесс сам
  периодически стучится на URL, алерт — при отсутствии пинга дольше
  ожидаемого интервала), не обычную проверку доступности порта — только
  так ловится "процесс жив, но завис внутри", а не только полное падение.

  **Как реализовано (коммит `72a5188`):**
  - Отдельный стек `/root/uptime-kuma/docker-compose.yml`, образ
    `louislam/uptime-kuma:2`, вне проектного compose и вне git — тот же
    принцип развязки, что у Metabase.
  - **Порт НЕ открывался вовсе.** Вместо ограничения 3001 по IP выбран
    SSH-туннель (`ssh -L 3001:localhost:3001`): Kuma слушает только
    `127.0.0.1:3001`, в `ufw` не добавлено ни одного правила. Не требует
    статического IP — а домашний IP динамический, и правило пришлось бы
    править при каждой смене, что перед месяцем без присмотра неприемлемо.
    Telegram-алерты исходящие, порт им не нужен.
  - Push-URL в `.env` (содержит секретный токен, репозиторий публичный),
    `UPTIME_KUMA_PUSH_URL` в `config/settings.py`; пустое значение
    полностью выключает функцию — локальная разработка не требует Kuma.
  - В URL используется `127.0.0.1`, НЕ `localhost`: Kuma слушает только
    IPv4, а `localhost` может отрезолвиться в `::1` и дать
    `Connection refused` — монитор сбоев сам стал бы источником ложных
    алертов.

  **ИСПРАВЛЕНИЕ СОБСТВЕННОГО ЧЕК-ЛИСТА — пункт «push-запрос добавить в
  `health_monitor.py`» БЫЛ НЕВЕРЕН, не возвращать его.**
  `ExchangeHealthMonitor.monitoring_loop()` — независимая фоновая корутина
  со своим `asyncio.sleep`, она продолжает работать, когда главный цикл
  стоит. Это уже зафиксировано в 5.1 как результат теста `to_thread()`:
  health_monitor отработал на расписании прямо во время архивации, пока
  `scan()` не выполнялся. Пинг оттуда горел бы зелёным во время
  73-минутного паралича — ровно того сценария, ради которого монитор и
  ставится. Реализовано иначе: `src/utils/uptime_push.py`, вызов
  `await uptime_push.ping()` в КОНЦЕ итерации `main()`, после
  `daily_report` — пинг доказывает, что итерация ДОШЛА ДО КОНЦА, а не что
  процесс существует.

  **Heartbeat 900 с, retries 0 — не произвольные числа.** Обычный цикл
  идёт ~8-9 с, но раз в сутки архивация занимает 7-10 минут и пингов в
  это время нет. Меньший интервал давал бы ложный алерт каждые сутки, а
  монитор, который врёт по расписанию, перестают читать. Retries 0, иначе
  алерт пришёл бы через 2×900 = 30 минут вместо 15.

  **Алерт проверен вживую, на боевом мониторе** (пункт «обязательно
  протестировать» выполнен, а не отмечен): временно `interval=60`, бот
  остановлен на 82 с. `22:43:37` последний heartbeat → `22:44:38` Kuma
  `status=0, important=1` → реальное сообщение в Telegram → `22:45:16`
  `status=1` и уведомление о восстановлении. Интервал возвращён на 900.
  Отдельно проверена привязка уведомления к монитору
  (`monitor_notification`) — настроенное, но не прицепленное уведомление
  это самая частая причина, по которой Kuma молчит в нужный момент.

  **ДОПОЛНИТЕЛЬНО 2026-08-06 (запрос пользователя, сверх исходного
  плана): алерт по свободному месту на диске.** Второй push-монитор,
  `disk-space`, коммиты `4f8df1b` + `e4db8f1`.
  **Отдельным systemd-таймером, НЕ проверкой внутри бота — тот же урок,
  что с `health_monitor`:** если бот упадёт или зависнет, мониторинг диска
  не должен умереть вместе с ним — место при этом продолжит кончаться
  (архиватор не отработает, PostgreSQL продолжит писать WAL). Сломается
  сам таймер — Kuma заметит по пропаже heartbeat.
  Состав: `scripts/disk_alert.sh` (в репозитории), `disk-alert.timer`
  каждые 10 минут (тройной запас к heartbeat 900 с), конфиг в `.env` —
  `DISK_ALERT_PUSH_URL`, `DISK_ALERT_THRESHOLD_GB=10`.
  Скрипт сам решает `status=up/down`, в Telegram приходит не «No
  heartbeat», а строка с цифрами. В `ping=` — свободное место в
  МЕГАБАЙТАХ: Kuma рисует ping как «response time», то есть в UI
  появляется график свободного места во времени (читать как мегабайты, не
  как миллисекунды).
  Проверено вживую без реального заполнения диска: порог временно поднят
  до 900 ГБ → `status=0, important=1`, сообщение с реальными цифрами
  дошло; порог возвращён на 10 → восстановление зафиксировано.
  **Про сам порог 10 ГБ:** органический рост по замеру 03-08→06-08
  (за вычетом 2.48 ГБ образа Kuma) ≈ 450 МБ/сутки, при 13.5 ГБ свободных
  это ~30 дней — порог МОЖЕТ сработать штатно в середине месяца, и это не
  ложная тревога, а сигнал «плато не наступило» с запасом ~22 дня на
  реакцию. Оценка снята с переходного периода (retention=7 доработал
  только 06-08 03:04) и является ВЕРХНЕЙ — на плато рост будет ближе к
  нулю.
- [ ] **Порт 3000 (Metabase) всё ещё открыт всему интернету** — этот
  подпункт ехал прицепом к задаче Uptime Kuma («заодно вспомнить») и
  вместе с ней НЕ закрылся: для Kuma порт вообще не открывали, а 3000 как
  был `ALLOW Anywhere`, так и остался. Вынесено отдельным пунктом, чтобы
  не потерялось вместе с закрытой задачей. Тот же SSH-туннель решил бы и
  это, но Metabase нужен пользователю с телефона — решать отдельно.
- [ ] **Docker-контейнеризация**
- [ ] **Тесты** — unit-тесты (pytest) на API-клиенты и репозитории
- [ ] **CI/CD** — GitHub Actions для линтинга и тестов

### 5.5. Миграция БД (зависимая задача)

> **Решение по окружению (2026-07-15):** локально и на VPS PostgreSQL/TimescaleDB
> запускается в Docker (образ `timescale/timescaledb`, `docker-compose.yml` в
> корне репозитория) — одно окружение на обеих машинах, данные в именованном
> volume. Нативная установка PostgreSQL на Windows НЕ планируется: локальная
> разработка после переноса на VPS использует тот же контейнер; возвращаться
> к идее нативной установки только если Docker будет мешать (ресурсы/WSL2).
> Миграция выполняется ДО переноса на VPS (решение пользователя, 2026-07-15).

> **Обновление 2026-07-14:** третье условие из списка ниже (потребность в 
> аналитических запросах) наступило — история спредов по `DATA_SPECIFICATION.md` 
> даёт ~3.7 млн строк/сутки, что для SQLite выполнимо только с retention 14 дней. 
> Миграция на обычный PostgreSQL + TimescaleDB (НЕ экспериментальные реализации) — 
> актуальная задача, выполняется отдельным спокойным заходом после первого 
> VPS-прогона, не под дедлайн.

- [x] **Миграция на PostgreSQL — выполнена 2026-07-15** (коммит 654c3f9):
  Docker-контейнер `timescale/timescaledb`, единое psycopg-соединение на процесс,
  пакетные UPSERT (`INSERT ... ON CONFLICT`, урок: построчный перенос SQLite-паттерна
  давал 25 тыс. round-trip/цикл). Перенос данных из SQLite не выполнялся — чистый
  старт (локальная история — shakeout); файл `data/arbitrage_data.db` остался на
  диске как референс. **ВАЖНО: выполнена миграция на обычный PostgreSQL —
  TimescaleDB-специфика НЕ сделана (пункт ниже), выигрыша в дисковом профиле
  от сжатия пока НЕТ.**
- [ ] **TimescaleDB-специфика: hypertables + сжатие старых чанков (10–20×)** —
  требует перевода `timestamp` (сейчас epoch DOUBLE PRECISION во всём коде) на
  TIMESTAMPTZ: dimension-колонка Timescale не может быть double. До этого
  retention — через HistoryArchiver (экспорт в csv.gz + DELETE).
- [ ] **РЕЗЕРВ 11 ГБ на почти неиспользуемых индексах — найдено и посчитано
  2026-08-06, СОЗНАТЕЛЬНО НЕ ТРОГАЕМ. Читать вместе с 5.3 (Analysis Module)
  и QUESTIONS.md.**
  - Замер `pg_stat_user_indexes` на проде 2026-08-06 21:40 UTC:
    ```
    futures_spread_history | idx_futures_spread_pair_ts  | 8245 МБ |          54 скана
    spread_history         | idx_spread_history_pair_ts  | 2559 МБ |          54 скана
    funding_rate_history   | idx_funding_history_pair_ts |  142 МБ |           1 скан
    arbitrage_opportunities| arbitrage_opportunities_pkey|  379 МБ | 18 772 487 354 скана
    ```
    Разница между рабочим индексом и этими тремя — девять порядков.
  - Масштаб: индексы занимают **15 ГБ из 46 ГБ БД**; у `spread_history` они
    весят 82% от собственных данных (3550 МБ индексов при 4326 МБ heap).
  - **Почему они почти не читаются, хотя не мусор:** построены под выборки
    «пара + интервал времени» для аналитики QUESTIONS.md (Q-001…Q-007),
    которая сознательно отложена. Пишущий путь бота ими не пользуется —
    он адресует строки по PK. То есть это не ошибка проектирования, а
    инфраструктура, оплаченная авансом под ещё не начатую работу.
  - **Почему это единственный быстрый рычаг по месту:** `DELETE` не
    возвращает место ОС (страницы помечаются свободными и переиспользуются
    — поэтому БД встала в плато 46 ГБ, хотя удаляется больше, чем
    вставляется). `VACUUM FULL` по `futures_spread_history` невозможен
    физически: нужен свободный объём размером с таблицу (21 ГБ heap) при
    16 ГБ свободных, плюс `ACCESS EXCLUSIVE` на всё время. `pg_repack` в
    образе `timescale/timescaledb:latest-pg17` отсутствует
    (`pg_available_extensions` = 0). `DROP INDEX` же удаляет файл сразу.
  - **Решение 2026-08-06: НЕ дропать.** Единственный сценарий, который это
    оправдывал бы — добавление новых бирж в ближайший месяц, — сам оценён
    как маловероятный (приоритет смещён на копитрейдинг-бот). Освобождать
    место под работу, которая не начнётся, чтобы потом платить
    `CREATE INDEX CONCURRENTLY` по 21-ГБ таблице (два прохода под живой
    записью, ощутимая нагрузка) — лишнее движение. Места и так хватает:
    16 ГБ свободных против прогнозируемых +6 ГБ за месяц.
  - **Когда доставать эту запись:** (1) при добавлении новых бирж —
    освобождать место вместе с задачей, а не заранее; (2) перед началом
    аналитики QUESTIONS.md — тогда индексы, наоборот, понадобятся, и
    трогать их нельзя; (3) если диск неожиданно пойдёт к 90% — это первый
    и самый дешёвый рычаг, обратимый и не меняющий схему данных.
  - Связанное, тоже не приоритет: у `simulated_trades` нет собственного
    retention, из-за чего 111 507 строк `arbitrage_opportunities` старше
    7 суток защищены от удаления ссылкой FK и держат возраст таблицы на
    20.8 суток вместо 7 (~100 МБ, прирост ~8 тыс. строк/сутки). Механизм
    структурно неограничен во времени, но в масштабе месяца это шум.
- [ ] **Синхронный psycopg блокирует event loop (внешнее ревью, 2026-07-26)** —
  измерено, не предположение: py-spy-профиль живого прод-процесса (20 с,
  329 сэмплов) показал ~40% времени внутри psycopg; Postgres-логи (25 с,
  `log_min_duration_statement=0`) — 89 301 отдельный SQL-запрос за 3 цикла
  (~30 тыс./цикл), суммарно 6.44 с серверного времени исполнения. Проверено
  по коду: репозитории вызывают настоящий `cursor.executemany()` (не скрытый
  цикл `execute()`), и psycopg3 3.1+ (у нас 3.3.4) сам включает pipeline-режим
  внутри `executemany()` — но pipelining скрывает только сетевую задержку
  round-trip'а, не уменьшает число выполнений запроса на сервере и не
  сокращает суммарное время исполнения (Postgres логирует каждый bind/execute
  одинаково что с pipelining, что без). Рекомендация "мигрировать на asyncpg"
  отпадает — psycopg3 уже имеет встроенный `AsyncConnection`. Настоящий фикс —
  сократить ЧИСЛО запросов: подлинный multi-row `VALUES(...)` вместо
  N-строчного `executemany()` (или `COPY` для больших батчей), а не просто
  включить async/pipeline. Не начато.
- [x] **Нет graceful degradation при падении БД — исправлено и протестировано
  (внешнее ревью, 2026-07-26, коммит 1b8d27d)** — эмпирически было
  подтверждено: необработанное исключение в главном цикле ловилось верхним
  `except`, `finally` закрывал сессии, `main()` возвращался нормально →
  `asyncio.run()` завершался без ошибки → exit code 0. `systemd
  Restart=on-failure` перезапускает только при ненулевом коде/сигнале — при
  падении БД бот тихо останавливался и НЕ перезапускался сам. Исправлено:
  `main()` возвращает 1 при срабатывании `except Exception` (сбой), 0 — при
  штатной остановке через `shutdown_event` (SIGTERM/SIGINT), `sys.exit(...)`
  в точке входа. Проверено реальным падением БД (`docker stop` на локальный
  Postgres во время работающего бота) — поймано `psycopg.errors.AdminShutdown`,
  корректно закрыты все 8 биржевых сессий, exit code 1 (было бы 0). Не
  задеплоено на VPS — только локально, ждёт отдельного явного запроса.
  **Проверено перед деплоем (2026-07-25), эмпирически, не по документации
  systemd:** опасение, что `Restart=on-failure` может упереться в дефолтный
  `StartLimitBurst=5`/`StartLimitIntervalSec=10s` при длительном (не
  мгновенном) падении БД — не подтвердилось. Тестовый одноразовый юнит
  (`/bin/false`, те же `Restart=on-failure`/`RestartSec=10`, дефолтные
  лимиты) прогнан вживую на VPS (не на проде): счётчик рестартов дошёл до
  9 за 90+ секунд, юнит оставался в `activating (auto-restart)`, ни разу не
  свалился в `failed` — потому что `RestartSec` (10с) численно совпадает с
  шириной окна подсчёта (`StartLimitIntervalSec=10s`), и в скользящее окно
  почти никогда не попадает больше одного рестарта. **Задеплоено 2026-07-25**
  (рестарт прод-сервиса в 21:54:22 UTC вместе с пунктами 2 и 6 ниже) —
  синтаксис/импорт проверены до рестарта, живые логи и БД (funding rate 4
  бирж, разброс timestamp 0.26с) подтвердили штатную работу после рестарта.
- [x] **Параллельный funding rate fetch + параметры стратегии в `.env`
  (внешнее ревью, 2026-07-26, коммит 30e6936) — применено и протестировано.**
  П.2: 4 последовательных `await` для funding rate (Binance/KuCoin/Gate.io/
  MEXC Futures) заменены на `asyncio.gather()` — тот же паттерн, что уже
  используется для основного сбора данных. Проверено по коду: реальный
  HTTP-запрос делает только Binance Futures, остальные три читают из
  in-memory кеша — экономия в основном за счёт Binance. П.6:
  `min_spread_percent`, `min_volume_usdt`, `max_staleness_seconds`,
  `ob_ttl_seconds`, `trade_size_usdt` вынесены в `config/settings.py` с
  `.env`-переопределением; дефолты не изменены (поведение то же, если
  `.env` их не задаёт). Уточнение по факту кода: `max_leg_skew_seconds` и
  `suspected_collision_threshold_percent` остаются Python-дефолтами
  конструктора, не `.env` — другой, тоже валидный паттерн, не тот же, что
  описан выше. **Задеплоено 2026-07-25** (рестарт 21:54:22 UTC вместе с
  п.5 выше): проверено живыми логами и разбросом timestamp funding rate
  между 4 биржами — 0.26с (было бы растянуто последовательно).
- [x] **FuturesSpreadMonitor.scan() — опровергнуто измерением (внешнее
  ревью, 2026-07-26).** Рекомендация предполагала, что это заметная доля
  времени цикла — измерение показало ~3.3%, фикс не нужен.
- [ ] **Развязка цикла от самой медленной биржи** — при таймаутах одной биржи
  (наблюдалось с Gate.io 2026-07-15) `gather` цикла растягивается >15 с, данные
  быстрых бирж протухают для фильтра свежести → пустые сканы. Идеи: per-exchange
  бюджет времени сбора / динамический `max_staleness_seconds` от длительности
  gather. Существовало и до миграции, теперь заметнее из-за более длинного цикла.
  Подтверждено повторно внешним ревью 2026-07-26 — не начато.

---

## 6. Известные ограничения и gotchas

- **Проверка «сколько экземпляров бота запущено» через `ps aux | grep -c
  "[m]ain.py"` даёт ЛОЖНЫЙ ПЛЮС.** 2026-08-01 перед деплоем она показала
  2 процесса вместо 1: вторым совпадением оказалась сама ssh-сессия, в
  командной строке которой присутствовал текст `main.py` (от `py_compile
  main.py`). Настоящий процесс был один. Правило «проверять процессы перед
  рестартом» (инцидент 2026-07-17, два процесса → DeadlockDetected)
  остаётся в силе — ненадёжен именно инструмент. Надёжная замена:
  `systemctl show multi-exchange-arbitrage.service -p MainPID --value`
  плюс `pgrep -c -f "^/root/multi-exchange-arbitrage/venv/bin/python"`.

- **Доля `opportunity_vanished` в paper trading сильно чувствительна к сетевой
  стабильности конкретно к Gate.io Futures**, а не является только функцией
  качества спредов на рынке. Проверено количественно (2026-07-20): на
  домашнем интернете (локальный shakeout-прогон 15–16.07) Gate.io Futures
  давал ~35 таймаутов/час, доля `opportunity_vanished` — 45%; на Hetzner DE
  (прод-VPS, 17–20.07) — ~0.02 таймаута/час, доля упала до 0.2%. Механизм:
  `max_close_staleness_seconds` (15 с) сравнивается с возрастом строки
  *конкретной биржи* на момент закрытия сделки, не с длительностью всего
  цикла `scan()` — серия таймаутов подряд у одной биржи оставляет её
  котировки протухшими намного дольше порога, даже если сам цикл укладывается
  в лимит. **Если эта метрика резко изменится в будущем (миграция VPS на
  другого провайдера/регион, деградация сети к конкретной бирже) — сначала
  проверить частоту таймаутов по биржам (`grep 'Попытка' logs/... | grep -oP
  'для \K.+?(?=\. Попытка)' | sort | uniq -c`), не сразу искать баг в коде.**
  **Новый всплеск, крупнее всех прежних (2026-07-23, 18:00–20:00 UTC):**
  495 из 496 WARNING-строк retry ("Попытка") в этом окне атрибутированы к
  Gate.io Futures (методология — тот же grep, что и выше), плюс 156
  полностью исчерпанных операций ("все 3 попытки не удались"), тоже 100%
  Gate.io Futures. За весь день 07-23 — 510 Gate.io Futures против всего
  4 MEXC + 2 Binance + 1 Binance Futures (проверено отдельно, не
  предположено по остатку) — остальные три биржи не всплёскивали. Темп
  в пике ~495 попыток / 2 часа ≈ 247/ч — выше домашнего бейзлайна (~35/ч)
  и на порядки выше прод-бейзлайна на Hetzner (~0.02/ч), крупнейший
  зафиксированный всплеск на сегодня. Самостоятельно прошёл (частота
  упала к 20:00 UTC), сервис не падал и не перезапускался — retry/
  health-monitor отработали штатно, без вмешательства. Похоже на
  временную деградацию/частичную недоступность Gate.io Futures на их
  стороне, не баг в коде. Для сравнения на следующий день (07-24) —
  другой паттерн, HTTP 503 от KuCoin (~245 ошибок), тоже внешняя причина,
  тоже самостоятельно прошло. Если оба паттерна начнут повторяться
  регулярно (не разово) — см. выше, сначала смотреть частоту по биржам,
  не искать баг.
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
- **`ccxt.fetch_currencies()` — тихий пустой словарь без ключей у Binance/MEXC, не исключение.** Проверено 2026-07-23 (разбор исходника ccxt): у Binance и MEXC этот метод требует приватных ключей, но при их отсутствии не бросает ошибку, а молча возвращает `{}` (в коде ccxt буквально `if not self.check_required_credentials(False): return {}`). Вызов без проверки длины результата выглядит рабочим, но ничего не делает — легко принять за временный сбой API биржи, а не ожидаемое поведение по дизайну. У KuCoin и Gate.io тот же метод публичен и возвращает реальные данные (2220 и 5334 валюты, включая адрес контракта токена).
- **РАЗРЕШЕНО 2026-08-01 (при анализе Q-003):** `spread_history` — строки с `best_bid_exchange = best_ask_exchange` (одна и та же биржа на обеих сторонах). Гипотеза из предыдущей записи подтвердилась и оказалась мундианной, не багом: если у пары есть 2+ котирующих её биржи, и ОДНА из них квотирует более узкий рынок, чем остальные (её лучший bid выше чужого лучшего bid, И её лучший ask ниже чужого лучшего ask одновременно) — она законно занимает обе роли (`max(bid)` и `min(ask)`) в независимом расчёте. Означает "межбиржевого расхождения нет прямо сейчас", не аномалию. В безусловных 5-минутных снэпшотах (`is_snapshot=1`, без порога) таких строк оказалось много — за 14 суток: Binance→Binance 888 545, Gate.io→Gate.io 616 716, KuCoin→KuCoin 209 009, MEXC→MEXC 180 872 (не 3, как показалось в Q-001, где смотрели только пороговые записи `is_snapshot=0`) — но `raw_spread_percent` для них всегда ≤0 (bid биржи не может быть выше её же ask), поэтому в пороговых записях это почти никогда не проходит фильтр ≥0.2% (отсюда и всего 3 случая в выборке Q-001). Наблюдение (не расследовано глубже): Binance доминирует как "самая узкая биржа" чаще всех остальных вместе взятых — согласуется с её репутацией самой ликвидной площадки из четырёх. Действий не требует — self-routes исключены из анализов маршрутов как нерелевантные (уже сделано в Q-003).
- **Протухшие записи в `{exchange}_trading_pairs`:** UPSERT обновляет только пары, которые биржа реально возвращает в ответе; делистнутые/переименованные пары остаются в таблице с устаревшим timestamp навсегда. Актуально для каждой новой биржи — требует мониторинга/очистки, не одноразовая проблема конкретной биржи.
- **Единый писатель в БД — не обеспечивается на уровне приложения** (нет file-lock/pid-check при старте `main.py`). Проверено эмпирически 2026-07-17 (случайный запуск второго процесса поверх работающего): при двух одновременных процессах PostgreSQL детектирует deadlock (`DeadlockDetected`) и корректно завершает один процесс через штатный `finally` в `main.py` — данные не портятся (проверено: атомарность транзакции подтверждена, прерванный батч `UPSERT` не оставляет смешанных/частично применённых строк, просто не применяется целиком). Graceful degradation подтверждён, но полагаться на это как на защиту не стоит — перед запуском `main.py` всегда проверять `Get-Process`/`ps aux` на дубликаты, особенно на VPS после restart/redeploy.
- **Коллизии в `arbitrage_opportunities` концентрированы в узком списке тикеров, не размазаны по хвосту.** Проверено 2026-07-22 по данным за 5 суток (17–22.07): доля `suspected_collision` стабильна 29–32% по дням (не растёт), но 65.7% всех коллизий (1 062 603 из 1 616 251) дают всего 10 тикеров — VANRYUSDT, TROLLUSDT, ELONUSDT, EDGEUSDT, RWAUSDT, SIRENUSDT, ESPORTSUSDT, UPUSDT, AIUSDT, VONUSDT (последние два — уже известные случаи из истории с 316% спредом). Один тикер (VANRYUSDT) даёт 89.7% всех коллизий на маршруте Gate.io→Binance (58260 из 64946 записей). Детектор работает корректно (эти находки исключены из paper trading), но каждый цикл заново пишет в `spread_history`/`arbitrage_opportunities` заведомо известный мусор. **Рекомендация (не срочно):** явный blocklist (тикер + пара бирж, где коллизия подтверждена), чтобы `SpreadMonitor` не тратил на них цикл и не засорял историю повторно — прямая, измеримая экономия объёма БД. **Реализовано и задеплоено 2026-07-22** (коммит `34113c9`, рестарт прод-сервиса в 22:18:38 UTC): `config/collision_blocklist.py`, 8 тикеров (VANRYUSDT/EDGEUSDT/ELONUSDT/RWAUSDT/SIRENUSDT/TROLLUSDT/UPUSDT/VONUSDT). **Уточнение модели (важно, не совпадает с формулировкой выше):** фильтр в `SpreadMonitor.scan()` исключает НЕ конкретную пару бирж, а ВСЮ ногу заданной биржи для тикера — из всех сравнений сразу (Gate.io для VANRYUSDT убирается и против Binance, и против KuCoin, и против MEXC одновременно). Это осознанный, проверенный выбор, не расширение вслепую: подтверждено 2026-07-23 (полная история, без ограничения окном) — для всех 8 записей коллизия 100% против КАЖДОЙ биржи, с которой тикер вообще сравнивается (VANRYUSDT: Binance/KuCoin/MEXC — 100/100/100%; остальные 7 — по обеим биржам, с которыми торгуются, тоже 100/100%). Модель "блок по бирже" оправдана данными, не только удобством реализации; тесты в `tests/test_collision_blocklist.py` фиксируют именно эту модель (не по-маршрутную). AIUSDT/ESPORTSUSDT намеренно не включены (неровная доля коллизий 33–77%, похоже на реальную волатильность, не на константно разный актив). **Граница для анализа истории:** данные `spread_history`/`arbitrage_opportunities` до 2026-07-22 22:18 UTC содержат полный объём коллизий по этим 8 тикерам, после — нет; при разборе Q-001…Q-008 и любых объёмных сравнений до/после — учитывать этот разрыв, не путать с изменением рыночных условий.
- **429 rate-limit всплески KuCoin — разово наблюдались 2026-07-22, не системная проблема (пока).** Окно 15:41–15:52 (12 минут): 155 из 173 ошибок за сутки, каскад от `429 Too many requests` (`System-level rate limit exceeded`) → вторичные ошибки парсинга ответа. Для сравнения — предыдущие 5 дней: 36/10/3/2/20 ошибок за весь день. Самостоятельно восстановился, вмешательства не потребовалось. **Если такие всплески участятся** — `async_retry` (3 попытки, exponential backoff, `src/utils/retry.py`) может быть недостаточен именно для KuCoin, стоит рассмотреть отдельный, более консервативный rate-limiting для этой биржи; не задача сейчас, на основе единичного инцидента.
  **Второй такой всплеск, 2026-08-03, другой код ошибки — паттерн повторяется, не единичный случай.** Окно 14:00–14:59 (1 час): 997 из 1730 ошибок за весь период наблюдения (57%), но теперь `503 Service Unavailable`, не `429`. Тот же класс инцидента — концентрированный всплеск, самостоятельное восстановление в пределах часа, `health_monitor` сразу после показал 8/8 бирж доступно. **Уточнение для будущего:** всплеск бывает не только с кодом `429` — при мониторинге ошибок KuCoin ориентироваться на концентрацию во времени (много ошибок в узком окне, самостоятельное восстановление), а не на конкретный HTTP-код.


## 6.1. Workflow и правила для Cline

> Все инструкции по git-workflow, формату задач и правилам работы с этим файлом — см. `.clinerules` в корне проекта.

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