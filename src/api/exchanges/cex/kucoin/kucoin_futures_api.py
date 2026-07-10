from src.api.exchanges.cex.base_cex_exchange import BaseExchangeAPI
from src.core.models.pair_data import PairData
from typing import List, Dict, Tuple
from datetime import datetime


class KuCoinFuturesAPI(BaseExchangeAPI):
    BASE_URL = "https://api-futures.kucoin.com"
    EXCHANGE_NAME = "KuCoin Futures"

    def __init__(self):
        super().__init__(None, None)  # Публичные данные не требуют ключей
        self._contracts_cache: Dict[str, Tuple[float, float]] = {}  # symbol -> (multiplier, lot_size)

    async def fetch_trading_pairs(self) -> List[PairData]:
        await self.init_session()
        try:
            # 1. Получаем контракты (multiplier, lotSize, 24h volume)
            contracts_data = await self._make_request('GET', '/api/v1/contracts/active')
            self.logger.debug(f"Contracts response: {contracts_data}")

            if 'data' not in contracts_data:
                self.logger.error("Key 'data' not found in contracts response")
                return []

            # Строим кеш контрактов и список активных символов
            contracts = {}
            for c in contracts_data['data']:
                if c.get('status') == 'Open':
                    symbol = c['symbol']
                    contracts[symbol] = c

            # 2. Получаем allTickers (цены)
            ticker_data = await self._make_request('GET', '/api/v1/allTickers')
            self.logger.debug(f"AllTickers response: {ticker_data}")

            if 'data' not in ticker_data:
                self.logger.error("Key 'data' not found in allTickers response")
                return []

            tickers = {t['symbol']: t for t in ticker_data['data']}

            pairs = []
            for symbol, contract in contracts.items():
                if symbol not in tickers:
                    continue

                ticker = tickers[symbol]
                try:
                    multiplier = float(contract['multiplier'])
                    lot_size = float(contract['lotSize'])
                    self._contracts_cache[symbol] = (multiplier, lot_size)

                    # Стандартизация: XBT -> BTC, убрать суффикс M
                    standardized = self._standardize_symbol(symbol)

                    # 24h объём в контрактах из contracts/active
                    volume_contracts = float(contract.get('volumeOf24h', 0))
                    # real volume = contracts * multiplier (т.к. multiplier — стоимость одного контракта в quote)
                    volume = volume_contracts * multiplier

                    timestamp = datetime.now().timestamp()
                    readable_time = datetime.fromtimestamp(timestamp).strftime('%Y-%m-%d %H:%M:%S')

                    pairs.append(PairData(
                        exchange=self.EXCHANGE_NAME,
                        original_pair=symbol,
                        standardized_pair=standardized,
                        base_currency=contract.get('baseCurrency', standardized.split('USDT')[0]),
                        quote_currency=contract.get('quoteCurrency', 'USDT'),
                        price=float(ticker.get('price', 0)),
                        volume=volume,
                        bid=float(ticker['bestBidPrice']),
                        ask=float(ticker['bestAskPrice']),
                        bid_volume=float(ticker.get('bestBidSize', 0)),
                        ask_volume=float(ticker.get('bestAskSize', 0)),
                        multiplier=multiplier,
                        lot_size=lot_size,
                        timestamp=timestamp,
                        readable_time=readable_time
                    ))
                except (KeyError, ValueError, TypeError) as e:
                    self.logger.warning(f"Пропущена пара {symbol}: {e}")
                    continue

            self.logger.info(f"Successfully fetched {len(pairs)} trading pairs from KuCoin Futures")
            return pairs

        except Exception as e:
            self.logger.error(f"Error fetching KuCoin Futures trading pairs: {e}")
            return []

    def _standardize_symbol(self, symbol: str) -> str:
        """
        Стандартизация символа KuCoin Futures.
        - XBT -> BTC
        - Убираем суффикс M
        Пример: XBTUSDTM -> BTCUSDT
        """
        s = symbol.replace('XBT', 'BTC')
        if s.endswith('M'):
            s = s[:-1]
        return s

    def get_contract_info(self, symbol: str) -> Tuple[float, float | None]:
        """
        Возвращает (multiplier, lot_size) для символа из кеша.
        Если символ не найден — возвращает (1.0, None).
        """
        return self._contracts_cache.get(symbol, (1.0, None))