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
import threading

# Add parent directory to path for imports
current_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, current_dir)

from token_manager import TokenManager
from api_usage_monitor import api_monitor, monitored_api_call

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class MarketDataFetcher:
    """Fetches market data using Kite Connect"""

    _instruments_cache: Optional[List[Dict]] = None
    _instruments_cache_time: Optional[datetime] = None
    _last_historical_request: float = 0.0
    _min_historical_interval: float = 0.35  # seconds between historical API calls

    # ── Circuit Breaker (class-level, shared across instances) ────────────────
    _cb_failures: int = 0
    _cb_open_until: float = 0.0          # epoch time when circuit re-closes
    _CB_MAX_FAILURES: int = 5
    _CB_RESET_SECONDS: float = 30.0
    _cb_lock = threading.Lock()

    _CACHE_MAX_ENTRIES: int = 500   # evict oldest when exceeded

    def __init__(self, kite=None):
        self.cache = {}
        self.cache_duration = timedelta(minutes=5)   # default TTL; overridden per call
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
    
    # ── Retry helper ──────────────────────────────────────────────────────────
    def _kite_call_with_retry(self, fn, *args, max_retries: int = 3, **kwargs):
        """
        Call a Kite API function with exponential backoff retry and circuit breaker.
        Raises the last exception if all retries fail.
        """
        with MarketDataFetcher._cb_lock:
            remaining = MarketDataFetcher._cb_open_until - time.time()
            if remaining > 0:
                raise RuntimeError(
                    f"Kite circuit open — retry after {remaining:.0f}s"
                )
            elif MarketDataFetcher._cb_open_until > 0:
                # Circuit just elapsed — reset failure count so it can reopen fresh
                MarketDataFetcher._cb_failures = 0
                MarketDataFetcher._cb_open_until = 0.0

        last_exc = None
        for attempt in range(max_retries):
            try:
                result = fn(*args, **kwargs)
                # Successful call — reset failure counter and circuit breaker
                with MarketDataFetcher._cb_lock:
                    MarketDataFetcher._cb_failures = 0
                    MarketDataFetcher._cb_open_until = 0.0
                return result
            except Exception as exc:
                last_exc = exc
                exc_str = str(exc).lower()
                # Auth errors (bad token) — no point retrying, raise immediately
                if 'access_token' in exc_str or 'api_key' in exc_str or 'invalid token' in exc_str:
                    logger.error(f"Kite auth error — token invalid. Run: python get_kite_token.py")
                    raise exc
                with MarketDataFetcher._cb_lock:
                    MarketDataFetcher._cb_failures += 1
                    if MarketDataFetcher._cb_failures >= MarketDataFetcher._CB_MAX_FAILURES:
                        MarketDataFetcher._cb_open_until = time.time() + MarketDataFetcher._CB_RESET_SECONDS
                        logger.warning(
                            f"Kite circuit OPEN after {MarketDataFetcher._cb_failures} failures — "
                            f"pausing API calls for {MarketDataFetcher._CB_RESET_SECONDS:.0f}s"
                        )
                sleep_time = 2 ** attempt          # 1s, 2s, 4s
                logger.warning(f"Kite API error (attempt {attempt+1}/{max_retries}): {exc} — retry in {sleep_time}s")
                time.sleep(sleep_time)
        raise last_exc

    # ── Data validation ───────────────────────────────────────────────────────
    @staticmethod
    def _validate_ohlcv(df: pd.DataFrame, symbol: str) -> pd.DataFrame:
        """
        Drop rows with: negative/zero price, NaN OHLC, volume=0,
        or a price spike >50% from previous close (bad tick).
        """
        if df.empty:
            return df
        orig_len = len(df)
        # Drop NaN in price columns
        price_cols = [c for c in ['Open', 'High', 'Low', 'Close'] if c in df.columns]
        df = df.dropna(subset=price_cols)
        # Drop non-positive prices
        for col in price_cols:
            df = df[df[col] > 0]
        # Drop zero volume rows (if Volume exists)
        if 'Volume' in df.columns:
            df = df[df['Volume'] >= 0]   # 0 is okay for index/pre-market
        # Detect price spikes: close changes >50% in one candle
        if 'Close' in df.columns and len(df) > 1:
            pct_chg = df['Close'].pct_change().abs()
            spike_mask = pct_chg > 0.50
            if spike_mask.any():
                logger.warning(f"{symbol}: dropped {spike_mask.sum()} spike candle(s)")
                df = df[~spike_mask]
        # Remove duplicates on index (date)
        if df.index.duplicated().any():
            df = df[~df.index.duplicated(keep='last')]
        dropped = orig_len - len(df)
        if dropped:
            logger.debug(f"{symbol}: validation removed {dropped}/{orig_len} bad rows")
        return df

    def get_stock_data(self, symbol: str, period: str = "1mo", interval: str = "1d") -> pd.DataFrame:
        """
        Fetch historical stock data using Kite Connect.
        TTL-based cache: daily candles cached 60s; intraday 30s.

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

        cache_key = f"{symbol}_{period}_{interval}"
        ttl = timedelta(seconds=30 if interval != '1d' else 60)
        cached = self.cache.get(cache_key)
        if cached and (datetime.now() - cached['timestamp']) < ttl:
            return cached['data']

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

            # Get historical data (with retry + circuit breaker)
            to_date = datetime.now()
            from_date = to_date - timedelta(days=days)

            data = self._kite_call_with_retry(
                self.kite.historical_data,
                instrument_token=instrument['instrument_token'],
                from_date=from_date,
                to_date=to_date,
                interval=kite_interval,
            )
            MarketDataFetcher._last_historical_request = time.time()

            if not data:
                logger.warning(f"No historical data for {symbol}")
                return pd.DataFrame()

            # Convert to DataFrame
            df = pd.DataFrame(data)
            df.columns = [col.capitalize() for col in df.columns]

            # Validate and clean
            df = self._validate_ohlcv(df, symbol)

            # Cache the data (evict oldest entries if over limit)
            if len(self.cache) >= MarketDataFetcher._CACHE_MAX_ENTRIES:
                oldest_keys = sorted(
                    self.cache, key=lambda k: self.cache[k]['timestamp']
                )[:100]
                for _k in oldest_keys:
                    del self.cache[_k]
            self.cache[cache_key] = {
                'data': df,
                'timestamp': datetime.now()
            }

            logger.debug(f"Fetched {len(df)} candles for {symbol}")
            return df

        except RuntimeError as re:
            logger.warning(f"Circuit breaker blocked {symbol}: {re}")
            return pd.DataFrame()
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
            quote = self._kite_call_with_retry(self.kite.ltp, [key])
            if not quote or key not in quote:
                logger.warning(f"No quote data for {symbol}")
                return None
            price = quote[key]['last_price']
            if not price or price <= 0:
                logger.warning(f"Invalid price for {symbol}: {price}")
                return None
            return price
        except RuntimeError:
            return None
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
            quote = self._kite_call_with_retry(self.kite.quote, [key])
            if not quote or key not in quote:
                logger.warning(f"No quote data for {symbol}")
                return {}

            q = quote[key]
            token = self._get_instrument_token(symbol)
            lp = q.get('last_price', 0)
            if not lp or lp <= 0:
                logger.warning(f"Invalid price in stock info for {symbol}: {lp}")
                return {}
            return {
                'symbol': symbol,
                'current_price': lp,
                'day_open': q.get('ohlc', {}).get('open', 0),
                'day_high': q.get('ohlc', {}).get('high', 0),
                'day_low': q.get('ohlc', {}).get('low', 0),
                'day_close': q.get('ohlc', {}).get('close', 0),
                'volume': q.get('volume', 0),
                'change': q.get('net_change', 0),
                'change_percent': q.get('ohlc', {}).get('change', 0),
                'instrument_token': token,
                'exchange': 'NSE'
            }
        except RuntimeError:
            return {}
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

            data = self._kite_call_with_retry(
                self.kite.historical_data,
                instrument_token=instrument['instrument_token'],
                from_date=from_date,
                to_date=to_date,
                interval="15minute",
            )
            MarketDataFetcher._last_historical_request = time.time()

            if not data:
                logger.warning(f"No intraday data found for {symbol}")
                return pd.DataFrame()

            # Convert to DataFrame and validate
            df = pd.DataFrame(data)
            df.columns = [col.capitalize() for col in df.columns]
            df = self._validate_ohlcv(df, symbol)

            logger.debug(f"Fetched intraday data for {symbol}: {len(df)} records")
            return df

        except RuntimeError as re:
            logger.warning(f"Circuit breaker blocked intraday {symbol}: {re}")
            return pd.DataFrame()
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

# Shadow the legacy MarketDataFetcher with the optimized, cached, batched implementation
from market_data_optimized import MarketDataFetcher
