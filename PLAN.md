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
- [ ] **Фьючерсы Gate.io и MEXC** — по аналогии с Binance/KuCoin Futures, отдельная 
  задача после стабилизации spot-данных этих бирж. Раздел 4 PLAN.md описывает только 
  spot-шаблон — фьючерсы для новых бирж всегда отдельный последующий шаг, не часть 
  базового добавления биржи.

### 5.2.1. План масштабирования и переход на удалённый сервер

- [x] **Довести количество поддерживаемых CEX-бирж до 4** — выполнено (Binance, KuCoin, 
  Gate.io, MEXC). Осознанно НЕ 5 — порог 5+ бирж уже зафиксирован ниже как триггер 
  для миграции на PostgreSQL/TimescaleDB, инфраструктура под которую ещё не готова. 
  5-я биржа (из кандидатов OKX/Bybit/Kraken) — после того, как миграция БД будет 
  готова принять возросшую нагрузку.
- [ ] **Перенос проекта на удалённый сервер (VPS)** для автономной круглосуточной работы. 
  Выполняется после добавления Gate.io и MEXC (4 биржи) — следующий шаг после проверки 
  стабильности на новом масштабе в локальном режиме. VPS позволит работать 24/7 
  независимо от локального ноутбука. Целевой срок — 2026-07-18, ПОСЛЕ выполнения 
  пункта "Подготовка к первому VPS-прогону" (5.1). В чек-лист настройки VPS включить 
  `lnav` (POSIX-инструмент чтения логов: мерж файлов по времени, SQL по логам, 
  headless-режим — на Windows недоступен, ставится сразу на сервере).
  **Решения (2026-07-15):** старт с ЧИСТОЙ БД — локальная история содержит 
  shakeout-артефакты трёх тестовых конфигураций, временные ряды для Q-анализов 
  должны начинаться со стабильной конфигурации (локальная БД остаётся у 
  пользователя). Диск: прирост БД ~1.2–1.5 ГБ/сутки (DATA_SPECIFICATION.md п.2, 6) → 
  от 60 ГБ, либо 40 ГБ с retention_days=7–10.
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
- [ ] **Веб-интерфейс** — дашборд с графиками и метриками
- [x] **Очистить git от `data/arbitrage_data.db`** — правило `data/*.db` уже в `.gitignore`, выполнено: `git rm --cached` + коммит, файл больше не отслеживается.
- [ ] **Docker-контейнеризация**
- [ ] **Тесты** — unit-тесты (pytest) на API-клиенты и репозитории
- [ ] **CI/CD** — GitHub Actions для линтинга и тестов

### 5.5. Миграция БД (зависимая задача)

> **Обновление 2026-07-14:** третье условие из списка ниже (потребность в 
> аналитических запросах) наступило — история спредов по `DATA_SPECIFICATION.md` 
> даёт ~3.7 млн строк/сутки, что для SQLite выполнимо только с retention 14 дней. 
> Миграция на обычный PostgreSQL + TimescaleDB (НЕ экспериментальные реализации) — 
> актуальная задача, выполняется отдельным спокойным заходом после первого 
> VPS-прогона, не под дедлайн.

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