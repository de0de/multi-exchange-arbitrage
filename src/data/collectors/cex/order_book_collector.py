"""
Коллектор Order Book depth.

Собирает стакан (bid/ask уровни) с указанных бирж для заданного списка пар
и сохраняет через OrderBookRepository.

Использует duck-typing: любой объект, имеющий метод
    async fetch_order_book(symbol: str, limit: int = 20) -> OrderBookData
считается "биржевым API".
"""
import logging
from typing import List, Optional
from src.core.models.order_book_data import OrderBookData
from src.database.order_book_repository import OrderBookRepository


class OrderBookCollector:
    """
    Собирает Order Book depth с одной или нескольких бирж.

    Пример использования:
        collector = OrderBookCollector()
        collector.add_source(binance_api, OrderBookRepository(db_path, "binance"))
        collector.add_source(kucoin_futures_api, OrderBookRepository(db_path, "kucoin_futures"))
        await collector.collect_order_books(["BTCUSDT", "ETHUSDT"])
    """

    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self._sources: List[dict] = []  # [{ 'api': ..., 'repo': OrderBookRepository }, ...]

    def add_source(self, api, repo: OrderBookRepository):
        """
        Добавляет источник данных.
        api — любой объект с async def fetch_order_book(symbol, limit) -> OrderBookData.
        repo — OrderBookRepository для сохранения.
        """
        if not hasattr(api, 'fetch_order_book'):
            raise ValueError("api должен иметь метод fetch_order_book(symbol, limit)")
        self._sources.append({'api': api, 'repo': repo})

    async def collect_order_books(self, symbols: List[str], limit: int = 20) -> int:
        """
        Собирает Order Book depth для каждого символа со всех зарегистрированных бирж.

        Args:
            symbols: список торговых пар (в формате каждой биржи).
            limit: количество уровней (по умолчанию 20).

        Returns:
            количество успешно сохранённых записей.
        """
        total_saved = 0
        for source in self._sources:
            api = source['api']
            repo = source['repo']
            for symbol in symbols:
                try:
                    order_book = await api.fetch_order_book(symbol, limit=limit)
                    if order_book and order_book.bids:
                        repo.save_order_book(order_book)
                        total_saved += 1
                    else:
                        self.logger.warning(
                            f"Пустой order book для {symbol} от {getattr(api, 'EXCHANGE_NAME', 'unknown')}"
                        )
                except Exception as e:
                    exchange_name = getattr(api, 'EXCHANGE_NAME', type(api).__name__)
                    self.logger.error(f"Ошибка при сборе order book {symbol} с {exchange_name}: {e}")
        return total_saved

    async def collect_top_pairs(
        self,
        symbols_by_exchange: dict,
        limit: int = 20
    ) -> int:
        """
        Собирает Order Book depth для указанных пар по каждой бирже.

        WARNING: Это не полноценный сбор топ-N пар по объёму.
        Требует ручного указания, какие пары для какой биржи собирать.

        Args:
            symbols_by_exchange: словарь {api_key: [symbols]},
                где api_key — ключ, под которым api был добавлен через add_source.
                Если ключ не передан, можно использовать словарь {0: [...], 1: [...]}
                по порядку добавления источников.
            limit: количество уровней.

        Returns:
            количество успешно сохранённых записей.
        """
        total_saved = 0
        for idx, source in enumerate(self._sources):
            api = source['api']
            repo = source['repo']
            exchange_name = getattr(api, 'EXCHANGE_NAME', f"source_{idx}")

            # Определяем, какие символы собирать для этой биржи
            symbols = []
            if exchange_name in symbols_by_exchange:
                symbols = symbols_by_exchange[exchange_name]
            elif idx in symbols_by_exchange:
                symbols = symbols_by_exchange[idx]
            else:
                self.logger.debug(f"Нет символов для {exchange_name}, пропускаем")
                continue

            for symbol in symbols:
                try:
                    order_book = await api.fetch_order_book(symbol, limit=limit)
                    if order_book and order_book.bids:
                        repo.save_order_book(order_book)
                        total_saved += 1
                    else:
                        self.logger.warning(
                            f"Пустой order book для {symbol} от {exchange_name}"
                        )
                except Exception as e:
                    self.logger.error(
                        f"Ошибка при сборе order book {symbol} с {exchange_name}: {e}"
                    )
        return total_saved