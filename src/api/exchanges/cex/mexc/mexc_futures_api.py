from src.api.exchanges.cex.base_cex_exchange import BaseExchangeAPI
from src.core.models.pair_data import PairData
from src.core.models.funding_rate import FundingRateData
from src.core.models.order_book_data import OrderBookData, OrderBookLevel
from typing import Dict, List
from datetime import datetime


class MexcFuturesAPI(BaseExchangeAPI):
    # У фьючерсов MEXC ОТДЕЛЬНЫЙ домен (не api.mexc.com, как у спота)
    BASE_URL = "https://contract.mexc.com"
    EXCHANGE_NAME = "MEXC Futures"

    def __init__(self):
        super().__init__(None, None)  # Публичные данные не требуют ключей
        # Кеш контрактов: contractSize и funding из detail/ticker
        # (паттерн KuCoin Futures — без отдельного funding-эндпоинта)
        self._contracts_cache: Dict[str, dict] = {}

    async def fetch_trading_pairs(self) -> List[PairData]:
        """
        GET /api/v1/contract/detail — контракты (contractSize, state 0=активен)
        GET /api/v1/contract/ticker — котировки (bid1/ask1, volume24, fundingRate)

        ВАЖНО: volume24 приходит В КОНТРАКТАХ — пересчитывается в базовую
        монету через contractSize. Размеров на лучших bid/ask тикер не отдаёт —
        в bid_volume/ask_volume подставляется суточный объём (паттерн KuCoin Spot).
        """
        await self.init_session()
        try:
            detail_data = await self._make_request('GET', '/api/v1/contract/detail')
            contracts = {
                c['symbol']: c for c in detail_data.get('data', [])
                if c.get('state') == 0
            }

            ticker_data = await self._make_request('GET', '/api/v1/contract/ticker')
            tickers = {t['symbol']: t for t in ticker_data.get('data', [])}

            pairs = []
            for symbol, contract in contracts.items():
                ticker = tickers.get(symbol)
                if ticker is None:
                    continue
                try:
                    contract_size = float(contract.get('contractSize', 1) or 1)
                    volume = float(ticker.get('volume24', 0) or 0) * contract_size

                    self._contracts_cache[symbol] = {
                        'contractSize': contract_size,
                        # fundingRate из ticker; next_funding_time публичный API
                        # MEXC Futures не отдаёт — остаётся None
                        'fundingRate': ticker.get('fundingRate'),
                        'mark_price': float(ticker.get('fairPrice', 0) or 0),
                    }

                    timestamp = datetime.now().timestamp()
                    readable_time = datetime.fromtimestamp(timestamp).strftime('%Y-%m-%d %H:%M:%S')

                    pairs.append(PairData(
                        exchange=self.EXCHANGE_NAME,
                        original_pair=symbol,
                        standardized_pair=symbol.replace('_', ''),
                        base_currency=contract.get('baseCoin', symbol.split('_')[0]),
                        quote_currency=contract.get('quoteCoin', 'USDT'),
                        price=float(ticker['lastPrice']),
                        volume=volume,
                        bid=float(ticker['bid1']),
                        ask=float(ticker['ask1']),
                        bid_volume=volume,
                        ask_volume=volume,
                        multiplier=contract_size,
                        lot_size=float(contract.get('minVol', 1) or 1),
                        timestamp=timestamp,
                        readable_time=readable_time
                    ))
                except (KeyError, ValueError, TypeError) as e:
                    self.logger.warning(f"Пропущена пара {symbol}: {e}")
                    continue

            self.logger.debug(f"Successfully fetched {len(pairs)} trading pairs from MEXC Futures")
            return pairs
        except Exception as e:
            self.logger.error(f"Error fetching MEXC Futures trading pairs: {e}")
            return []

    async def fetch_funding_rates(self) -> List[FundingRateData]:
        """
        Funding rate из кеша _contracts_cache (заполняется при fetch_trading_pairs).

        Ограничение MEXC: публичный API не отдаёт next_funding_time —
        поле остаётся None, funding_interval_hours принимается 8.0
        (стандарт MEXC). Пересмотреть при появлении приватных ключей.
        """
        now = datetime.now().timestamp()
        readable_time = datetime.fromtimestamp(now).strftime('%Y-%m-%d %H:%M:%S')

        rates = []
        for symbol, info in self._contracts_cache.items():
            funding_rate = info.get('fundingRate')
            if funding_rate is None:
                continue

            rates.append(FundingRateData(
                exchange=self.EXCHANGE_NAME,
                original_pair=symbol,
                standardized_pair=symbol.replace('_', ''),
                funding_rate=float(funding_rate),
                funding_interval_hours=8.0,
                mark_price=info.get('mark_price'),
                next_funding_time=None,
                timestamp=now,
                readable_time=readable_time
            ))

        self.logger.debug(f"Fetched {len(rates)} funding rates from MEXC Futures cache")
        return rates

    async def fetch_order_book(self, symbol: str, limit: int = 20) -> OrderBookData:
        """
        GET /api/v1/contract/depth/{symbol}?limit={limit}

        Формат уровней: [цена, размер В КОНТРАКТАХ, число ордеров] —
        объём пересчитывается в базовую монету через contractSize.
        """
        await self.init_session()
        contract_size = self._contracts_cache.get(symbol, {}).get('contractSize', 1.0)
        try:
            data = await self._make_request('GET', f'/api/v1/contract/depth/{symbol}', params={
                'limit': limit
            })

            now = datetime.now().timestamp()
            readable = datetime.fromtimestamp(now).strftime('%Y-%m-%d %H:%M:%S')

            inner = data.get('data', {})
            bids = [OrderBookLevel(price=float(l[0]), volume=float(l[1]) * contract_size)
                    for l in inner.get('bids', [])]
            asks = [OrderBookLevel(price=float(l[0]), volume=float(l[1]) * contract_size)
                    for l in inner.get('asks', [])]

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
