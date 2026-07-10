from src.api.exchanges.cex.base_cex_exchange import BaseExchangeAPI
from src.core.models.pair_data import PairData
from src.core.models.funding_rate import FundingRateData
from src.core.models.order_book_data import OrderBookData, OrderBookLevel
from typing import List
from datetime import datetime

class BinanceFuturesAPI(BaseExchangeAPI):
    BASE_URL = "https://fapi.binance.com"
    EXCHANGE_NAME = "Binance Futures"

    def __init__(self):
        super().__init__(None, None)  # Публичные данные не требуют ключей

    async def fetch_trading_pairs(self) -> List[PairData]:
        await self.init_session()
        try:
            exchange_info = await self._make_request('GET', '/fapi/v1/exchangeInfo')
            self.logger.debug(f"Exchange info response: {exchange_info}")

            if 'symbols' not in exchange_info:
                self.logger.error("Key 'symbols' not found in exchange info response")
                return []

            symbols = {s['symbol']: s for s in exchange_info['symbols'] if s['status'] == 'TRADING'}
            book_tickers = await self._make_request('GET', '/fapi/v1/ticker/bookTicker')
            book_dict = {t['symbol']: t for t in book_tickers}
            price_data = await self._make_request('GET', '/fapi/v1/ticker/24hr')
            pairs = []
            for pair_data in price_data:
                symbol = pair_data['symbol']
                if symbol not in symbols:
                    continue
                try:
                    book = book_dict[symbol]
                    timestamp = datetime.now().timestamp()
                    readable_time = datetime.fromtimestamp(timestamp).strftime('%Y-%m-%d %H:%M:%S')
                    pairs.append(PairData(
                        exchange=self.EXCHANGE_NAME,
                        original_pair=symbol,
                        standardized_pair=symbol,
                        base_currency=symbols[symbol]['baseAsset'],
                        quote_currency=symbols[symbol]['quoteAsset'],
                        price=float(pair_data['lastPrice']),
                        volume=float(pair_data['volume']),
                        bid=float(book['bidPrice']),
                        ask=float(book['askPrice']),
                        bid_volume=float(book['bidQty']),
                        ask_volume=float(book['askQty']),
                        timestamp=timestamp,
                        readable_time=readable_time
                    ))
                except (KeyError, ValueError) as e:
                    self.logger.warning(f"Пропущена пара {symbol}: {e}")
                    continue
            self.logger.info(f"Successfully fetched {len(pairs)} trading pairs")
            return pairs
        except Exception as e:
            self.logger.error(f"Error fetching trading pairs: {e}")
            return []

    async def fetch_funding_rates(self) -> List[FundingRateData]:
        """
        Возвращает funding rate для всех USDⓈ-M фьючерсов Binance.

        Эндпоинт: GET /fapi/v1/premiumIndex
        Документация: https://binance-docs.github.io/apidocs/futures/en/#mark-price-kline-data-of-all-market-tickers

        Response включает:
          - symbol
          - markPrice
          - indexPrice
          - estimatedSettlePrice
          - lastFundingRate
          - nextFundingTime (milliseconds)
          - interestRate
        """
        await self.init_session()
        try:
            data = await self._make_request('GET', '/fapi/v1/premiumIndex')
            self.logger.debug(f"premiumIndex response count: {len(data)}")

            now = datetime.now().timestamp()
            readable_time = datetime.fromtimestamp(now).strftime('%Y-%m-%d %H:%M:%S')

            rates = []
            for item in data:
                symbol = item['symbol']
                last_funding_rate = item.get('lastFundingRate')
                if last_funding_rate is None:
                    continue

                mark_price = item.get('markPrice')
                if mark_price is not None:
                    mark_price = float(mark_price)

                next_funding_time_ms = item.get('nextFundingTime')
                if next_funding_time_ms is not None:
                    next_funding_time = float(next_funding_time_ms) / 1000.0
                else:
                    next_funding_time = None

                # Binance USDⓈ-M фьючерсы используют 8-часовой интервал
                funding_interval = 8.0

                rates.append(FundingRateData(
                    exchange=self.EXCHANGE_NAME,
                    original_pair=symbol,
                    standardized_pair=symbol,
                    funding_rate=float(last_funding_rate),
                    funding_interval_hours=funding_interval,
                    mark_price=mark_price,
                    next_funding_time=next_funding_time,
                    timestamp=now,
                    readable_time=readable_time
                ))

            self.logger.info(f"Fetched {len(rates)} funding rates from Binance Futures")
            return rates

        except Exception as e:
            self.logger.error(f"Error fetching funding rates from Binance Futures: {e}")
            return []

    async def fetch_order_book(self, symbol: str, limit: int = 20) -> OrderBookData:
        """
        Возвращает order book depth для указанного символа Binance Futures.

        GET /fapi/v1/depth?symbol={symbol}&limit={limit}
        Документация: https://binance-docs.github.io/apidocs/futures/en/#order-book
        """
        await self.init_session()
        try:
            data = await self._make_request('GET', '/fapi/v1/depth', params={
                'symbol': symbol,
                'limit': limit
            })

            now = datetime.now().timestamp()
            readable = datetime.fromtimestamp(now).strftime('%Y-%m-%d %H:%M:%S')

            bids = [OrderBookLevel(price=float(p[0]), volume=float(p[1])) for p in data.get('bids', [])]
            asks = [OrderBookLevel(price=float(p[0]), volume=float(p[1])) for p in data.get('asks', [])]

            return OrderBookData(
                exchange=self.EXCHANGE_NAME,
                original_pair=symbol,
                standardized_pair=symbol,
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
                standardized_pair=symbol,
                bids=[],
                asks=[]
            )
