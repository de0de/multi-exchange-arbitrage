"""
Мониторинг спредов между биржами.

Собирает торговые пары из БД, находит пары, присутствующие на >=2 биржах,
рассчитывает спред с учётом комиссий, фильтрует по объёму и свежести данных,
опционально загружает Order Book depth для учёта проскальзывания.

Использует единое соединение sqlite3 из main.py.
"""
import logging
import sqlite3
import time
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from src.core.models.arbitrage_opportunity import (
    ArbitrageOpportunity,
    SlippageInfo,
)
from src.core.models.order_book_data import OrderBookData, OrderBookLevel
from src.database.arbitrage_opportunity_repository import ArbitrageOpportunityRepository
from src.database.order_book_repository import OrderBookRepository
from src.data.collectors.cex.order_book_collector import OrderBookCollector


class SpreadMonitor:
    """
    Мониторинг спредов цен между биржами.

    Поток работы в main loop:
        1. scan() — читает все trading_pairs из БД, находит расхождения.
        2. Сохраняет результаты через ArbitrageOpportunityRepository.

    Пример использования:
        monitor = SpreadMonitor(conn, apis, order_book_repos, collector, ...)
        opportunities = await monitor.scan()
        monitor.save_results(opportunities)
    """

    # Список биржевых таблиц trading_pairs для сравнения.
    # Включены только spot-биржи (Binance Spot, KuCoin Spot).
    # Futures исключены, так как spot↔futures сравнение даёт ложные
    # "арбитражные возможности" из-за разной природы цен (фьючерсные
    # премии/дисконты, funding rate) — такие пары не являются реальными
    # арбитражными возможностями в простом ценовом смысле.
    EXCHANGE_TABLES: List[str] = [
        "binance_trading_pairs",
        "kucoin_trading_pairs",
    ]

    # Маппинг: exchange_name (из БД) → exchange_display_name (из API)
    EXCHANGE_DISPLAY: Dict[str, str] = {
        "binance": "Binance",
        "binance_futures": "Binance Futures",
        "kucoin": "KuCoin",
        "kucoin_futures": "KuCoin Futures",
    }

    def __init__(
        self,
        conn: sqlite3.Connection,
        apis: Dict[str, object],
        order_book_repos: Dict[str, OrderBookRepository],
        order_book_collector: OrderBookCollector,
        min_spread_percent: float = 0.5,
        min_volume_usdt: float = 1000.0,
        max_staleness_seconds: float = 15.0,
        allowed_quote_currencies: Optional[List[str]] = None,
        ob_ttl_seconds: float = 5.0,
        suspected_collision_threshold_percent: float = 20.0,
        max_opportunities: int = 100,
    ):
        self.conn = conn
        self.cursor = conn.cursor()
        self.logger = logging.getLogger(__name__)

        # API-клиенты для Order Book
        self.apis = apis
        self.order_book_repos = order_book_repos
        self.order_book_collector = order_book_collector
        self.ob_ttl_seconds = ob_ttl_seconds

        # Параметры фильтрации
        self.min_spread_percent = min_spread_percent
        self.min_volume_usdt = min_volume_usdt
        self.max_staleness_seconds = max_staleness_seconds
        self.allowed_quote_currencies = allowed_quote_currencies or ["USDT", "USDC", "BTC", "ETH"]
        self.suspected_collision_threshold_percent = suspected_collision_threshold_percent
        self.max_opportunities = max_opportunities

        # Кеш комиссий бирж {exchange_display_name: taker_fee}
        self._exchange_fees: Dict[str, float] = {}
        self._load_exchange_fees()

        # Репозиторий для сохранения результатов
        self.opportunity_repo = ArbitrageOpportunityRepository(conn)

    def _load_exchange_fees(self):
        """Загружает taker_fee из таблицы exchanges в кеш на время жизни объекта."""
        try:
            self.cursor.execute("SELECT name, taker_fee FROM exchanges")
            rows = self.cursor.fetchall()
            for name, fee in rows:
                self._exchange_fees[name] = fee if fee is not None else 0.001
            self.logger.info(
                f"Loaded exchange fees: {self._exchange_fees}"
            )
        except sqlite3.Error as e:
            self.logger.warning(f"Could not load exchange fees from DB: {e}. Using defaults.")
            self._exchange_fees = {
                "Binance": 0.001,
                "Binance Futures": 0.001,
                "KuCoin": 0.001,
                "KuCoin Futures": 0.001,
            }

    def _get_exchange_name(self, table_name: str) -> str:
        """Из 'binance_trading_pairs' → 'binance'."""
        return table_name.replace("_trading_pairs", "")

    def _get_exchange_display(self, exchange_key: str) -> str:
        """Из 'binance' → 'Binance'."""
        return self.EXCHANGE_DISPLAY.get(exchange_key, exchange_key.capitalize())

    def _read_trading_table(self, table: str) -> List[dict]:
        """Читает все строки из одной trading_pairs таблицы."""
        try:
            self.cursor.execute(f"""
                SELECT original_pair, standardized_pair,
                       base_currency, quote_currency,
                       price, volume, bid, ask,
                       bid_volume, ask_volume,
                       timestamp
                FROM {table}
            """)
            columns = [desc[0] for desc in self.cursor.description]
            return [dict(zip(columns, row)) for row in self.cursor.fetchall()]
        except sqlite3.OperationalError as e:
            self.logger.warning(f"Cannot read table {table}: {e}")
            return []

    def _build_pairs_map(
        self,
    ) -> Dict[str, Dict[str, dict]]:
        """
        Строит карту {standardized_pair: {exchange_key: row_dict}}.

        exchange_key — короткое имя ('binance', 'kucoin', ...).
        """
        pairs_map: Dict[str, Dict[str, dict]] = {}

        for table in self.EXCHANGE_TABLES:
            exchange_key = self._get_exchange_name(table)
            rows = self._read_trading_table(table)
            for row in rows:
                std_pair = row["standardized_pair"]
                if std_pair is None:
                    continue
                if std_pair not in pairs_map:
                    pairs_map[std_pair] = {}
                pairs_map[std_pair][exchange_key] = row

        return pairs_map

    def _calc_slippage(
        self,
        order_book: OrderBookData,
        is_buy_side: bool,
        target_volume: float,
    ) -> SlippageInfo:
        """
        Рассчитывает проскальзывание для указанного объёма.

        Для покупки (is_buy_side=True) — смотрим asks (покупаем по ask).
        Для продажи (is_buy_side=False) — смотрим bids (продаём по bid).

        Алгоритм: идём по уровням от лучшей цены, "съедая" объём,
        пока не наберём target_volume или не кончатся уровни.
        Средневзвешенная цена покупки/продажи сравнивается с лучшей ценой.
        """
        levels = order_book.asks if is_buy_side else order_book.bids
        if not levels:
            return SlippageInfo(price_impact_percent=0.0, filled_volume=0.0, levels_consumed=0)

        best_price = levels[0].price
        remaining = target_volume
        total_cost = 0.0
        filled = 0.0
        levels_used = 0

        for level in levels:
            if remaining <= 0:
                break
            take = min(remaining, level.volume)
            total_cost += take * level.price
            filled += take
            remaining -= take
            levels_used += 1

        if filled == 0:
            return SlippageInfo(price_impact_percent=0.0, filled_volume=0.0, levels_consumed=0)

        avg_price = total_cost / filled
        price_impact = abs(avg_price - best_price) / best_price * 100.0

        return SlippageInfo(
            price_impact_percent=price_impact,
            filled_volume=filled,
            levels_consumed=levels_used,
        )

    async def _fetch_order_book_with_cache(
        self,
        exchange_key: str,
        symbol: str,
    ) -> Optional[OrderBookData]:
        """Загружает Order Book через TTL-кеш."""
        exchange_display = self._get_exchange_display(exchange_key)
        api = self.apis.get(exchange_display)
        repo = self.order_book_repos.get(exchange_key)

        if api is None or repo is None:
            self.logger.debug(f"No API/repo for {exchange_display}, skipping Order Book")
            return None

        return await self.order_book_collector.get_order_book_cached(
            api=api,
            repo=repo,
            symbol=symbol,
            ttl_seconds=self.ob_ttl_seconds,
        )

    async def scan(self) -> List[ArbitrageOpportunity]:
        """
        Основной метод: сканирует trading_pairs, находит арбитражные возможности.

        Возвращает отсортированный список ArbitrageOpportunity.
        """
        self.logger.info("Starting spread scan...")
        start_ts = time.time()

        # 1. Строим карту пар
        pairs_map = self._build_pairs_map()
        self.logger.info(f"Found {len(pairs_map)} unique standardized pairs across all exchanges")

        # 2. Собираем предварительные кандидаты (без Order Book)
        candidates: List[ArbitrageOpportunity] = []
        now = time.time()

        for std_pair, exchange_data in pairs_map.items():
            if len(exchange_data) < 2:
                continue  # пара есть только на одной бирже

            # Определяем quote_currency из первой записи
            first_exchange = next(iter(exchange_data.values()))
            base_currency = first_exchange.get("base_currency", "")
            quote_currency = first_exchange.get("quote_currency", "")

            # Фильтр по quote_currency
            if quote_currency not in self.allowed_quote_currencies:
                continue

            # Собираем цены bid/ask по каждой бирже
            exchange_prices: List[Tuple[str, float, float, float, float, float, float]] = []
            for exch_key, row in exchange_data.items():
                bid = row.get("bid")
                ask = row.get("ask")
                price = row.get("price")
                volume = row.get("volume", 0) or 0

                if bid is None or ask is None or price is None:
                    continue

                # Проверка свежести данных
                ts = row.get("timestamp")
                if ts is not None:
                    age = now - ts
                    if age > self.max_staleness_seconds:
                        continue

                bid_vol = row.get("bid_volume", 0) or 0
                ask_vol = row.get("ask_volume", 0) or 0

                exchange_prices.append((exch_key, bid, ask, price, volume, bid_vol, ask_vol))

            if len(exchange_prices) < 2:
                continue

            # 3. Сравниваем все пары бирж
            for i in range(len(exchange_prices)):
                for j in range(len(exchange_prices)):
                    if i == j:
                        continue

                    buy_exch_key, buy_bid, buy_ask, buy_price, buy_vol, buy_bid_vol, buy_ask_vol = exchange_prices[i]
                    sell_exch_key, sell_bid, sell_ask, sell_price, sell_vol, sell_bid_vol, sell_ask_vol = exchange_prices[j]

                    # Покупаем по ask (самая низкая цена продавца), продаём по bid (самая высокая цена покупателя)
                    buy_price_effective = buy_ask
                    sell_price_effective = sell_bid

                    if buy_price_effective <= 0 or sell_price_effective <= 0:
                        continue

                    # Вычисляем спред
                    raw_spread_percent = (sell_price_effective - buy_price_effective) / buy_price_effective * 100.0

                    # Фильтр по минимальному спреду
                    if raw_spread_percent < self.min_spread_percent:
                        continue

                    buy_exchange_display = self._get_exchange_display(buy_exch_key)
                    sell_exchange_display = self._get_exchange_display(sell_exch_key)

                    # Комиссии
                    buy_fee = self._exchange_fees.get(buy_exchange_display, 0.001)
                    sell_fee = self._exchange_fees.get(sell_exchange_display, 0.001)
                    total_fee_percent = buy_fee * 100 + sell_fee * 100  # конвертация в проценты (0.001 = 0.1%)
                    net_spread_percent = raw_spread_percent - total_fee_percent

                    if net_spread_percent < self.min_spread_percent:
                        continue

                    # Объём
                    buy_volume_original = buy_vol
                    sell_volume_original = sell_vol
                    max_buy_volume_usdt = buy_vol * buy_price_effective
                    max_sell_volume_usdt = sell_vol * sell_price_effective
                    trade_volume_usdt = min(max_buy_volume_usdt, max_sell_volume_usdt)

                    # Фильтр по минимальному объёму (обе стороны >= min_volume_usdt)
                    if max_buy_volume_usdt < self.min_volume_usdt or max_sell_volume_usdt < self.min_volume_usdt:
                        continue

                    # Проверка на коллизию тикеров (аномально большой спред)
                    suspected_collision = raw_spread_percent >= self.suspected_collision_threshold_percent
                    if suspected_collision:
                        self.logger.warning(
                            f"SUSPECTED_TICKER_COLLISION: {std_pair} "
                            f"spread={raw_spread_percent:.2f}% "
                            f"(threshold={self.suspected_collision_threshold_percent}%) "
                            f"between {buy_exchange_display} (ask={buy_price_effective}) "
                            f"and {sell_exchange_display} (bid={sell_price_effective})"
                        )

                    candidates.append(ArbitrageOpportunity(
                        standardized_pair=std_pair,
                        base_currency=base_currency,
                        quote_currency=quote_currency,
                        exchange_buy=buy_exchange_display,
                        exchange_sell=sell_exchange_display,
                        buy_price=buy_price_effective,
                        sell_price=sell_price_effective,
                        raw_spread_percent=raw_spread_percent,
                        buy_exchange_fee_percent=buy_fee * 100,
                        sell_exchange_fee_percent=sell_fee * 100,
                        net_spread_percent=net_spread_percent,
                        max_buy_volume_usdt=max_buy_volume_usdt,
                        max_sell_volume_usdt=max_sell_volume_usdt,
                        trade_volume_usdt=trade_volume_usdt,
                        buy_volume_original=buy_volume_original,
                        sell_volume_original=sell_volume_original,
                        suspected_collision=suspected_collision,
                    ))

        if not candidates:
            self.logger.info("No candidates found after filtering")
            return []

        # 4. Сортируем по net_spread убыванию и берём топ-N * 3 для проверки Order Book
        top_n_candidates = min(len(candidates), self.max_opportunities * 3)
        candidates.sort(key=lambda opp: opp.net_spread_percent, reverse=True)
        top_candidates = candidates[:top_n_candidates]

        self.logger.info(
            f"Found {len(candidates)} candidates, "
            f"checking Order Book for top {len(top_candidates)}"
        )

        # 5. Для топ-кандидатов загружаем Order Book и рассчитываем slippage
        for opp in top_candidates:
            # Определяем exchange_key и original_pair для запроса Order Book
            buy_exch_key = self._find_exchange_key_by_display(opp.exchange_buy)
            sell_exch_key = self._find_exchange_key_by_display(opp.exchange_sell)

            if buy_exch_key is None or sell_exch_key is None:
                continue

            # Определяем original_pair для каждой биржи (может отличаться)
            buy_symbol = self._get_original_symbol(buy_exch_key, opp.standardized_pair)
            sell_symbol = self._get_original_symbol(sell_exch_key, opp.standardized_pair)

            if buy_symbol is None or sell_symbol is None:
                self.logger.debug(
                    f"Cannot resolve symbol for {opp.standardized_pair} "
                    f"on {buy_exch_key}/{sell_exch_key}, skipping slippage"
                )
                continue

            # Загружаем Order Book для buy и sell бирж
            buy_ob = await self._fetch_order_book_with_cache(buy_exch_key, buy_symbol)
            sell_ob = await self._fetch_order_book_with_cache(sell_exch_key, sell_symbol)

            if buy_ob is None or sell_ob is None:
                self.logger.debug(
                    f"No Order Book data for {opp.standardized_pair}, "
                    f"keeping without slippage adjustment"
                )
                continue

            # Рассчитываем slippage для buy (покупаем — смотрим asks) и sell (продаём — смотрим bids)
            buy_slippage = self._calc_slippage(buy_ob, is_buy_side=True, target_volume=opp.buy_volume_original)
            sell_slippage = self._calc_slippage(sell_ob, is_buy_side=False, target_volume=opp.sell_volume_original)

            # Общий slippage = сумма impact с обеих сторон
            total_slippage_percent = buy_slippage.price_impact_percent + sell_slippage.price_impact_percent

            # Объём, доступный с учётом slippage (минимальный filled объём с обеих сторон)
            slippage_limited_volume_usdt = min(
                buy_slippage.filled_volume * opp.buy_price,
                sell_slippage.filled_volume * opp.sell_price,
            )

            opp.buy_slippage = buy_slippage
            opp.sell_slippage = sell_slippage
            opp.slippage_available = True
            opp.net_spread_with_slippage_percent = opp.net_spread_percent - total_slippage_percent
            opp.slippage_limited_volume_usdt = slippage_limited_volume_usdt

        # 6. Финальная сортировка:
        #    - сначала проверенные (slippage=True) по net_profit убыванию
        #    - затем непроверенные по net_spread убыванию
        verified = [opp for opp in top_candidates if opp.slippage_available]
        unverified = [opp for opp in candidates if not opp.slippage_available]

        verified.sort(key=lambda opp: opp.net_profit_percent(), reverse=True)
        unverified.sort(key=lambda opp: opp.net_profit_percent(), reverse=True)

        final = (verified + unverified)[:self.max_opportunities]

        elapsed = time.time() - start_ts
        self.logger.info(
            f"Scan complete in {elapsed:.2f}s, "
            f"found {len(final)} opportunities "
            f"(verified={len(verified)}, unverified={len(unverified)})"
        )

        return final

    async def fetch_order_books_for_opportunity(
        self,
        opp: ArbitrageOpportunity,
    ) -> Tuple[Optional[OrderBookData], Optional[OrderBookData]]:
        """
        Загружает Order Book обеих сторон возможности через TTL-кеш.

        Публичная точка доступа для Paper Trading: возвращает
        (order_book биржи покупки, order_book биржи продажи),
        любой из них может быть None при ошибке загрузки.
        """
        buy_key = self._find_exchange_key_by_display(opp.exchange_buy)
        sell_key = self._find_exchange_key_by_display(opp.exchange_sell)
        if buy_key is None or sell_key is None:
            return None, None

        buy_symbol = self._get_original_symbol(buy_key, opp.standardized_pair)
        sell_symbol = self._get_original_symbol(sell_key, opp.standardized_pair)
        if buy_symbol is None or sell_symbol is None:
            return None, None

        buy_ob = await self._fetch_order_book_with_cache(buy_key, buy_symbol)
        sell_ob = await self._fetch_order_book_with_cache(sell_key, sell_symbol)
        return buy_ob, sell_ob

    def _find_exchange_key_by_display(self, display_name: str) -> Optional[str]:
        """Ищет exchange_key по display_name."""
        for key, display in self.EXCHANGE_DISPLAY.items():
            if display == display_name:
                return key
        return None

    def _get_original_symbol(self, exchange_key: str, standardized_pair: str) -> Optional[str]:
        """
        Возвращает original_pair для указанной биржи и standardized_pair.

        Читает из БД таблицы {exchange_key}_trading_pairs
        для получения оригинального символа биржи.
        """
        table = f"{exchange_key}_trading_pairs"
        try:
            self.cursor.execute(
                f"SELECT original_pair FROM {table} WHERE standardized_pair = ? LIMIT 1",
                (standardized_pair,)
            )
            row = self.cursor.fetchone()
            return row[0] if row else None
        except sqlite3.OperationalError as e:
            self.logger.debug(f"Cannot read {table}: {e}")
            return None

    def save_results(self, opportunities: List[ArbitrageOpportunity]) -> List[int]:
        """Сохраняет результаты в БД через репозиторий. Возвращает список id."""
        return self.opportunity_repo.save_opportunities(opportunities)

    def log_top_opportunities(self, opportunities: List[ArbitrageOpportunity], top_n: int = 10):
        """Логирует топ-N арбитражных возможностей."""
        if not opportunities:
            self.logger.info("No arbitrage opportunities found")
            return

        self.logger.info(f"{'='*80}")
        self.logger.info(f"TOP {min(top_n, len(opportunities))} ARBITRAGE OPPORTUNITIES:")
        self.logger.info(f"{'='*80}")

        for i, opp in enumerate(opportunities[:top_n]):
            verified_tag = " [OB]" if opp.slippage_available else ""
            coll_tag = " [COLLISION?]" if opp.suspected_collision else ""
            profit_tag = f" | est_profit=${opp.estimated_profit_usdt():.2f}"
            vol_info = f" | vol={opp.trade_volume_usdt:.0f} USDT"
            slippage_info = ""
            if opp.slippage_available and opp.buy_slippage and opp.sell_slippage:
                slippage_info = (
                    f" | slippage_in={opp.buy_slippage.price_impact_percent:.3f}% "
                    f"out={opp.sell_slippage.price_impact_percent:.3f}%"
                )

            self.logger.info(
                f"{i+1:2d}. {opp.standardized_pair}{verified_tag}{coll_tag}\n"
                f"    {opp.exchange_buy} buy @ {opp.buy_price:.4f} "
                f"→ {opp.exchange_sell} sell @ {opp.sell_price:.4f}\n"
                f"    raw_spread={opp.raw_spread_percent:.3f}% "
                f"net_spread={opp.net_spread_percent:.3f}%"
                f"{slippage_info}"
                f"{vol_info}{profit_tag}"
            )

        self.logger.info(f"{'='*80}")