from src.api.exchanges.cex.base_cex_exchange import BaseExchangeAPI
from src.core.models.pair_data import PairData
from typing import List
from datetime import datetime
from src.api.exchanges.cex.kucoin.kucoin_constants import BASE_URL, EXCHANGE_NAME, ENDPOINTS

class KuCoinSpotAPI(BaseExchangeAPI):
    BASE_URL = BASE_URL
    EXCHANGE_NAME = EXCHANGE_NAME

    def __init__(self):
        super().__init__(None, None)  # Публичные данные не требуют ключей

    async def fetch_trading_pairs(self) -> List[PairData]:
        await self.init_session()
        try:
            exchange_info = await self._make_request('GET', ENDPOINTS['exchange_info'])
            self.logger.debug(f"Exchange info response: {exchange_info}")

            if 'data' not in exchange_info:
                self.logger.error("Key 'data' not found in exchange info response")
                return []

            symbols = {s['symbol']: s for s in exchange_info['data'] if s['enableTrading']}
            ticker_data = await self._make_request('GET', ENDPOINTS['ticker'])
            pairs = []
            for pair_data in ticker_data['data']['ticker']:
                if pair_data['symbol'] in symbols:
                    timestamp = datetime.now().timestamp()
                    readable_time = datetime.fromtimestamp(timestamp).strftime('%Y-%m-%d %H:%M:%S')
                    pairs.append(PairData(
                        exchange=self.EXCHANGE_NAME,
                        original_pair=pair_data['symbol'],
                        standardized_pair=pair_data['symbol'].replace('-', ''),
                        base_currency=symbols[pair_data['symbol']]['baseCurrency'],
                        quote_currency=symbols[pair_data['symbol']]['quoteCurrency'],
                        price=float(pair_data['last']),
                        volume=float(pair_data['vol']),
                        bid=float(pair_data['buy']),
                        ask=float(pair_data['sell']),
                        bid_volume=float(pair_data['vol']),
                        ask_volume=float(pair_data['vol']),
                        timestamp=timestamp,
                        readable_time=readable_time
                    ))
            self.logger.info(f"Successfully fetched {len(pairs)} trading pairs")
            return pairs
        except Exception as e:
            self.logger.error(f"Error fetching trading pairs: {e}")
            return []
