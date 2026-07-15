from src.api.exchanges.cex.base_cex_exchange import BaseExchangeAPI
from src.core.models.pair_data import PairData
from src.core.models.funding_rate import FundingRateData
from src.core.models.order_book_data import OrderBookData, OrderBookLevel
from typing import Dict, List
from datetime import datetime


class GateFuturesAPI(BaseExchangeAPI):
    BASE_URL = "https://api.gateio.ws"
    EXCHANGE_NAME = "Gate.io Futures"

    def __init__(self):
        super().__init__(None, None)  # Публичные данные не требуют ключей
        # Кеш контрактов: funding и quanto_multiplier приходят в составе
        # /contracts — отдельный funding-эндпоинт не нужен (паттерн KuCoin Futures)
        self._contracts_cache: Dict[str, dict] = {}

    async def fetch_trading_pairs(self) -> List[PairData]:
        """
        GET /api/v4/futures/usdt/contracts — контракты (funding, multiplier, статус)
        GET /api/v4/futures/usdt/tickers — котировки

        ВАЖНО: размеры на лучших bid/ask (highest_size/lowest_size) приходят
        В КОНТРАКТАХ — пересчитываются в базовую монету через quanto_multiplier.
        Суточный объём берётся из volume_24h_base (уже в базовой монете).
        """
        await self.init_session()
        try:
            contracts_data = await self._make_request('GET', '/api/v4/futures/usdt/contracts')
            contracts = {
                c['name']: c for c in contracts_data
                if not c.get('in_delisting', False) and c.get('status', 'trading') == 'trading'
            }

            tickers_data = await self._make_request('GET', '/api/v4/futures/usdt/tickers')
            tickers = {t['contract']: t for t in tickers_data}

            pairs = []
            for name, contract in contracts.items():
                ticker = tickers.get(name)
                if ticker is None:
                    continue
                try:
                    multiplier = float(contract.get('quanto_multiplier') or 1.0) or 1.0

                    self._contracts_cache[name] = {
                        'multiplier': multiplier,
                        'funding_rate': contract.get('funding_rate'),
                        'funding_interval': contract.get('funding_interval'),   # секунды
                        'funding_next_apply': contract.get('funding_next_apply'),  # unix сек
                        'mark_price': float(contract.get('mark_price', 0) or 0),
                    }

                    base, _, quote = name.partition('_')
                    timestamp = datetime.now().timestamp()
                    readable_time = datetime.fromtimestamp(timestamp).strftime('%Y-%m-%d %H:%M:%S')

                    pairs.append(PairData(
                        exchange=self.EXCHANGE_NAME,
                        original_pair=name,
                        standardized_pair=name.replace('_', ''),
                        base_currency=base,
                        quote_currency=quote,
                        price=float(ticker['last']),
                        volume=float(ticker.get('volume_24h_base', 0) or 0),
                        bid=float(ticker['highest_bid']),
                        ask=float(ticker['lowest_ask']),
                        bid_volume=float(ticker.get('highest_size', 0) or 0) * multiplier,
                        ask_volume=float(ticker.get('lowest_size', 0) or 0) * multiplier,
                        multiplier=multiplier,
                        lot_size=float(contract.get('order_size_min', 1) or 1),
                        timestamp=timestamp,
                        readable_time=readable_time
                    ))
                except (KeyError, ValueError, TypeError) as e:
                    self.logger.warning(f"Пропущена пара {name}: {e}")
                    continue

            self.logger.debug(f"Successfully fetched {len(pairs)} trading pairs from Gate.io Futures")
            return pairs
        except Exception as e:
            self.logger.error(f"Error fetching Gate.io Futures trading pairs: {e}")
            return []

    async def fetch_funding_rates(self) -> List[FundingRateData]:
        """
        Funding rate из кеша _contracts_cache (заполняется при fetch_trading_pairs).

        Gate.io отдаёт funding в составе /contracts: funding_rate,
        funding_interval (секунды), funding_next_apply (unix секунды).
        """
        now = datetime.now().timestamp()
        readable_time = datetime.fromtimestamp(now).strftime('%Y-%m-%d %H:%M:%S')

        rates = []
        for name, info in self._contracts_cache.items():
            funding_rate = info.get('funding_rate')
            if funding_rate is None:
                continue

            interval_sec = info.get('funding_interval')
            interval_hours = float(interval_sec) / 3600.0 if interval_sec else 8.0

            next_apply = info.get('funding_next_apply')
            next_funding_time = float(next_apply) if next_apply else None

            rates.append(FundingRateData(
                exchange=self.EXCHANGE_NAME,
                original_pair=name,
                standardized_pair=name.replace('_', ''),
                funding_rate=float(funding_rate),
                funding_interval_hours=interval_hours,
                mark_price=info.get('mark_price'),
                next_funding_time=next_funding_time,
                timestamp=now,
                readable_time=readable_time
            ))

        self.logger.debug(f"Fetched {len(rates)} funding rates from Gate.io Futures cache")
        return rates

    async def fetch_order_book(self, symbol: str, limit: int = 20) -> OrderBookData:
        """
        GET /api/v4/futures/usdt/order_book?contract={symbol}&limit={limit}

        Формат уровней: {"p": "цена", "s": размер В КОНТРАКТАХ} —
        объём пересчитывается в базовую монету через quanto_multiplier.
        """
        await self.init_session()
        multiplier = self._contracts_cache.get(symbol, {}).get('multiplier', 1.0)
        try:
            data = await self._make_request('GET', '/api/v4/futures/usdt/order_book', params={
                'contract': symbol,
                'limit': limit
            })

            now = datetime.now().timestamp()
            readable = datetime.fromtimestamp(now).strftime('%Y-%m-%d %H:%M:%S')

            bids = [OrderBookLevel(price=float(l['p']), volume=float(l['s']) * multiplier)
                    for l in data.get('bids', [])]
            asks = [OrderBookLevel(price=float(l['p']), volume=float(l['s']) * multiplier)
                    for l in data.get('asks', [])]

            return OrderBookData(
                exchange=self.EXCHANGE_NAME,
                original_pair=symbol,
                standardized_pair=symbol.replace('_', ''),
                bids=bids,
                asks=asks,
                timestamp=now,
                readable_time=readable
            )
        except Exception as e:
            self.logger.error(f"Error fetching order book for {symbol}: {e}")
            return OrderBookData(
                exchange=self.EXCHANGE_NAME,
                original_pair=symbol,
                standardized_pair=symbol.replace('_', ''),
                bids=[],
                asks=[]
            )
