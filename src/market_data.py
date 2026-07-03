"""
Market Data Fetcher Module
Fetches real-time and historical market data using Kite Connect
"""
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import logging
import os
import sys
import time

# Add parent directory to path for imports
current_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, current_dir)

from token_manager import TokenManager

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class MarketDataFetcher:
    """Fetches market data using Kite Connect"""

    _instruments_cache: Optional[List[Dict]] = None
    _instruments_cache_time: Optional[datetime] = None
    _last_historical_request: float = 0.0
    _min_historical_interval: float = 0.35  # seconds between historical API calls

    def __init__(self, kite=None):
        self.cache = {}
        self.cache_duration = timedelta(minutes=5)
        self.kite = kite
        if not self.kite:
            try:
                token_manager = TokenManager()
                self.kite = token_manager.initialize_kite()
                logger.info("Kite Connect initialized for market data")
            except Exception as e:
                logger.error(f"Failed to initialize Kite Connect: {e}")
                self.kite = None

    def _get_instruments(self) -> List[Dict]:
        """Return NSE instruments, cached for 4 hours to avoid repeated API calls."""
        now = datetime.now()
        if (
            MarketDataFetcher._instruments_cache is not None
            and MarketDataFetcher._instruments_cache_time is not None
            and (now - MarketDataFetcher._instruments_cache_time) < timedelta(hours=4)
        ):
            return MarketDataFetcher._instruments_cache
        if not self.kite:
            return []
        try:
            instruments = self.kite.instruments("NSE")
            MarketDataFetcher._instruments_cache = instruments
            MarketDataFetcher._instruments_cache_time = now
            return instruments
        except Exception as e:
            logger.error(f"Error loading instruments: {e}")
            return MarketDataFetcher._instruments_cache or []

    def _get_instrument_token(self, symbol: str) -> Optional[int]:
        """Lookup instrument token for a symbol."""
        for inst in self._get_instruments():
            if inst['tradingsymbol'] == symbol:
                return inst['instrument_token']
        return None
    
    def get_stock_data(self, symbol: str, period: str = "1mo", interval: str = "1d") -> pd.DataFrame:
        """
        Fetch historical stock data using Kite Connect

        Args:
            symbol: Stock symbol (e.g., "RELIANCE")
            period: Time period (1d, 5d, 1mo, 3mo, 6mo, 1y, 2y, 5y, 10y, ytd, max)
            interval: Data interval (1m, 2m, 5m, 15m, 30m, 60m, 90m, 1h, 1d, 5d, 1wk, 1mo, 3mo)

        Returns:
            DataFrame with OHLCV data
        """
        if not self.kite:
            logger.error("Kite Connect not initialized")
            return pd.DataFrame()

        try:
            # Get instrument token for the symbol
            instruments = self._get_instruments()
            instrument = None
            for inst in instruments:
                if inst['tradingsymbol'] == symbol:
                    instrument = inst
                    break

            if not instrument:
                logger.warning(f"Instrument not found for {symbol}")
                return pd.DataFrame()

            # Map period to days
            period_map = {
                "1d": 1,
                "5d": 5,
                "1mo": 30,
                "3mo": 90,
                "6mo": 180,
                "1y": 365,
                "2y": 730,
                "5y": 1825
            }
            days = period_map.get(period, 30)

            # Map interval to Kite interval
            interval_map = {
                "1d": "day",
                "1h": "hour",
                "15m": "15minute",
                "5m": "5minute",
                "1m": "minute"
            }
            kite_interval = interval_map.get(interval, "day")

            # Rate limit historical API calls
            elapsed = time.time() - MarketDataFetcher._last_historical_request
            if elapsed < MarketDataFetcher._min_historical_interval:
                time.sleep(MarketDataFetcher._min_historical_interval - elapsed)

            # Get historical data
            to_date = datetime.now()
            from_date = to_date - timedelta(days=days)

            data = self.kite.historical_data(
                instrument_token=instrument['instrument_token'],
                from_date=from_date,
                to_date=to_date,
                interval=kite_interval
            )
            MarketDataFetcher._last_historical_request = time.time()

            if not data:
                logger.warning(f"No historical data for {symbol}")
                return pd.DataFrame()

            # Convert to DataFrame
            df = pd.DataFrame(data)
            df.columns = [col.capitalize() for col in df.columns]

            # Cache the data
            cache_key = f"{symbol}_{period}_{interval}"
            self.cache[cache_key] = {
                'data': df,
                'timestamp': datetime.now()
            }

            logger.info(f"Fetched {len(df)} candles for {symbol}")
            return df

        except Exception as e:
            logger.error(f"Error fetching data for {symbol}: {e}")
            return pd.DataFrame()

    def get_realtime_price(self, symbol: str) -> Optional[float]:
        """
        Get real-time price for a stock using Kite Connect

        Args:
            symbol: Stock symbol

        Returns:
            Current price or None
        """
        if not self.kite:
            logger.error("Kite Connect not initialized")
            return None

        try:
            key = f"NSE:{symbol}"
            quote = self.kite.ltp([key])
            if not quote or key not in quote:
                logger.warning(f"No quote data for {symbol}")
                return None
            return quote[key]['last_price']
        except Exception as e:
            logger.error(f"Error getting real-time price for {symbol}: {e}")
            return None
    
    def get_stock_info(self, symbol: str) -> Dict:
        """
        Get comprehensive stock information using Kite Connect

        Args:
            symbol: Stock symbol

        Returns:
            Dictionary with stock information
        """
        if not self.kite:
            logger.error("Kite Connect not initialized")
            return {}

        try:
            key = f"NSE:{symbol}"
            quote = self.kite.quote([key])
            if not quote or key not in quote:
                logger.warning(f"No quote data for {symbol}")
                return {}

            q = quote[key]
            token = self._get_instrument_token(symbol)
            return {
                'symbol': symbol,
                'current_price': q['last_price'],
                'day_open': q['ohlc']['open'],
                'day_high': q['ohlc']['high'],
                'day_low': q['ohlc']['low'],
                'day_close': q['ohlc']['close'],
                'volume': q['volume'],
                'change': q.get('net_change', 0),
                'change_percent': q.get('ohlc', {}).get('change', 0),
                'instrument_token': token,
                'exchange': 'NSE'
            }
        except Exception as e:
            logger.error(f"Error getting stock info for {symbol}: {e}")
            return {}

    def get_multiple_stocks_data(self, symbols: List[str], period: str = "1mo") -> Dict[str, pd.DataFrame]:
        """
        Fetch data for multiple stocks
        
        Args:
            symbols: List of stock symbols
            period: Time period
        
        Returns:
            Dictionary mapping symbols to DataFrames
        """
        results = {}
        for symbol in symbols:
            data = self.get_stock_data(symbol, period=period)
            if not data.empty:
                results[symbol] = data
        return results
    
    def get_intraday_data(self, symbol: str, days: int = 5) -> pd.DataFrame:
        """
        Get intraday data for recent days using Kite Connect

        Args:
            symbol: Stock symbol
            days: Number of days of intraday data

        Returns:
            DataFrame with intraday data
        """
        if not self.kite:
            logger.error("Kite Connect not initialized")
            return pd.DataFrame()

        try:
            # Get instrument token for the symbol
            instruments = self._get_instruments()
            instrument = None
            for inst in instruments:
                if inst['tradingsymbol'] == symbol:
                    instrument = inst
                    break

            if not instrument:
                logger.warning(f"Instrument not found for {symbol}")
                return pd.DataFrame()

            # Rate limit historical API calls
            elapsed = time.time() - MarketDataFetcher._last_historical_request
            if elapsed < MarketDataFetcher._min_historical_interval:
                time.sleep(MarketDataFetcher._min_historical_interval - elapsed)

            # Get intraday data
            to_date = datetime.now()
            from_date = to_date - timedelta(days=days)

            data = self.kite.historical_data(
                instrument_token=instrument['instrument_token'],
                from_date=from_date,
                to_date=to_date,
                interval="15minute"
            )
            MarketDataFetcher._last_historical_request = time.time()

            if not data:
                logger.warning(f"No intraday data found for {symbol}")
                return pd.DataFrame()

            # Convert to DataFrame
            df = pd.DataFrame(data)
            df.columns = [col.capitalize() for col in df.columns]

            logger.info(f"Fetched intraday data for {symbol}: {len(df)} records")
            return df

        except Exception as e:
            logger.error(f"Error fetching intraday data for {symbol}: {e}")
            return pd.DataFrame()
    
    # NSE market holidays — update annually
    # Source: https://www.nseindia.com/resources/exchange-communication-holidays
    _NSE_HOLIDAYS = {
        # 2025
        "2025-01-26", "2025-02-26", "2025-03-14", "2025-03-31",
        "2025-04-10", "2025-04-14", "2025-04-18", "2025-05-01",
        "2025-08-15", "2025-08-27", "2025-10-02", "2025-10-02",
        "2025-10-20", "2025-10-24", "2025-11-05", "2025-12-25",
        # 2026
        "2026-01-26", "2026-03-03", "2026-03-20", "2026-04-02",
        "2026-04-03", "2026-04-14", "2026-05-01", "2026-08-15",
        "2026-10-02", "2026-10-29", "2026-11-13", "2026-12-25",
    }

    def is_market_open(self) -> bool:
        """
        Check if NSE market is currently open.
        Returns False on weekends and NSE public holidays.
        """
        from datetime import datetime
        import pytz

        ist = pytz.timezone('Asia/Kolkata')
        now = datetime.now(ist)
        current_time = now.time()
        today_str = now.strftime("%Y-%m-%d")

        # Weekend
        if now.weekday() >= 5:
            return False

        # NSE holiday
        if today_str in MarketDataFetcher._NSE_HOLIDAYS:
            logger.info(f"Market holiday today ({today_str}) — trading paused")
            return False

        market_open  = datetime.strptime("09:15", "%H:%M").time()
        market_close = datetime.strptime("15:30", "%H:%M").time()
        return market_open <= current_time <= market_close

    def is_market_holiday(self) -> bool:
        """Return True if today is a declared NSE holiday."""
        import pytz
        from datetime import datetime
        ist = pytz.timezone('Asia/Kolkata')
        today_str = datetime.now(ist).strftime("%Y-%m-%d")
        return today_str in MarketDataFetcher._NSE_HOLIDAYS
