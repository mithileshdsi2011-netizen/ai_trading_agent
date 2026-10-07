"""
Angel One Market Data Module

Fetches market data from Angel One SmartAPI.
Completely separate from Kite market data.
"""
import logging
from typing import Optional, Dict, Any, List
from datetime import datetime, timedelta
import pandas as pd

logger = logging.getLogger(__name__)


class AngelMarketData:
    """Angel One market data fetcher."""

    def __init__(self, client, instruments=None):
        """
        Initialize market data fetcher.

        Args:
            client: AngelClient instance
            instruments: Optional AngelInstruments for symbol->token mapping
        """
        self.client = client
        self.instruments = instruments
        self._cache = {}
        self._cache_expiry = {}
        self._cache_ttl_seconds = 30
        self._last_update: Optional[datetime] = None

    @staticmethod
    def _map_quote(raw: Dict[str, Any]) -> Dict[str, Any]:
        """Map an Angel FULL-mode quote to our internal quote shape."""
        return {
            'ltp': raw.get('ltp', 0),
            'open': raw.get('open', 0),
            'high': raw.get('high', 0),
            'low': raw.get('low', 0),
            'close': raw.get('close', 0),
            'volume': raw.get('tradeVolume', raw.get('totalTradedVolume', 0)),
            'change': raw.get('netChange', 0),
            'change_percent': raw.get('percentChange', 0),
            'symbol_token': raw.get('symbolToken'),
            'trading_symbol': raw.get('tradingSymbol'),
            'exchange': raw.get('exchange'),
            'fetched_at': datetime.now(),
        }

    def get_quote(self, symbol: str) -> Optional[Dict[str, Any]]:
        """
        Get quote for a single base symbol (e.g. 'RELIANCE').

        Returns:
            Dict with quote data or None
        """
        try:
            cache_key = f"quote_{symbol}"
            if self._is_cache_valid(cache_key):
                return self._cache.get(cache_key)

            if not self.instruments:
                return None
            instrument = self.instruments.get_instrument(symbol)
            if not instrument:
                return None

            result = self.client.get_ltp(
                tradingsymbol=self.instruments.get_tradingsymbol(symbol),
                symbol_token=instrument.symbol_token
            )
            if result.get('success') and result.get('data'):
                quote = self._map_quote(result['data'])
                self._cache[cache_key] = quote
                self._cache_expiry[cache_key] = (
                    datetime.now() + timedelta(seconds=self._cache_ttl_seconds)
                )
                self._last_update = datetime.now()
                return quote
            return None

        except Exception as e:
            logger.error(f"[ANGEL MARKET DATA] Quote error for {symbol}: {e}")
            return None

    def get_quotes(self, symbols: List[str]) -> Dict[str, Any]:
        """
        Get quotes for multiple base symbols via the batch marketData API.

        Returns:
            Dict mapping base symbols to quote data
        """
        try:
            if not self.instruments or not symbols:
                return {}

            token_to_symbol = {}
            tokens = []
            for s in symbols:
                inst = self.instruments.get_instrument(s)
                if inst and inst.symbol_token:
                    token_to_symbol[inst.symbol_token] = s
                    tokens.append(inst.symbol_token)

            if not tokens:
                return {}

            result = self.client.get_market_quotes({"NSE": tokens}, mode="FULL")
            if not result.get('success'):
                return {}

            quotes = {}
            for item in (result['data'].get('fetched') or []):
                base = token_to_symbol.get(str(item.get('symbolToken')))
                if base:
                    quotes[base] = self._map_quote(item)

            if quotes:
                self._last_update = datetime.now()
            return quotes

        except Exception as e:
            logger.error(f"[ANGEL MARKET DATA] Quotes error: {e}")
            return {}

    def get_historical_candles(self, symbol_token: str, interval: str,
                               days: int = 5) -> Optional[pd.DataFrame]:
        """
        Get historical candle data.

        Returns:
            DataFrame with OHLCV data or None
        """
        try:
            to_date = datetime.now()
            from_date = to_date - timedelta(days=days)

            from_str = from_date.strftime("%Y-%m-%d %H:%M")
            to_str = to_date.strftime("%Y-%m-%d %H:%M")

            result = self.client.get_historical_data(
                symbol_token=symbol_token,
                interval=interval,
                from_date=from_str,
                to_date=to_str
            )

            if result.get('success') and result.get('data'):
                candles = result['data']
                df = pd.DataFrame(
                    candles,
                    columns=['datetime', 'open', 'high', 'low', 'close', 'volume']
                )
                df['datetime'] = pd.to_datetime(df['datetime'])
                df.set_index('datetime', inplace=True)
                logger.debug(f"[ANGEL MARKET DATA] Candles retrieved for {symbol_token}")
                return df

            return None

        except Exception as e:
            logger.error(f"[ANGEL MARKET DATA] Historical candles error: {e}")
            return None

    def last_update_time(self) -> Optional[datetime]:
        """Time of the last successful market-data fetch."""
        return self._last_update

    def _is_cache_valid(self, key: str) -> bool:
        """Check if cache entry is still valid."""
        if key not in self._cache or key not in self._cache_expiry:
            return False
        return datetime.now() < self._cache_expiry[key]

    def clear_cache(self):
        """Clear all cached data."""
        self._cache.clear()
        self._cache_expiry.clear()
        logger.debug("[ANGEL MARKET DATA] Cache cleared")
