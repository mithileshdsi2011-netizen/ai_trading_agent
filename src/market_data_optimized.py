"""
Optimized Market Data Fetcher with centralized two-stage cache, batch quote APIs,
global rate limiting, and per-cycle metrics.

Trading consumers (SignalGenerator, AIResearchAgent, RiskManager, MultiTimeframe,
Dashboard, etc.) continue to call the same public methods; the cache and batching
happen transparently underneath.
"""
import json
import logging
import os
import sys
import time
import threading
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import numpy as np
import pytz

# Add parent directory to path for imports
current_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, current_dir)

from token_manager import TokenManager
from api_usage_monitor import api_monitor

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class RateLimiter:
    """
    Thread-safe token-bucket style rate limiter that keeps each Kite endpoint
    inside its per-minute limit.  One token per API call, regardless of batch size.
    Offline analysis can use the AI_TRADING_ANALYSIS env flag for a faster profile.
    """

    # Production per-minute limits -> minimum seconds between calls
    _MIN_INTERVALS = {
        'quote': 1.0,              # 60/min -> 1/s
        'ltp': 1.0,                # 60/min
        'historical_data': 1.0,    # 60/min
        'instruments': 6.0,        # 10/min
        'margins': 1.0,
        'holdings': 1.0,
        'positions': 1.0,
        'orders': 1.0,
        'profile': 1.0,
        'default': 1.0,
    }

    # Analysis mode: shorter gaps. Kite may still throttle; use with care.
    _MIN_INTERVALS_ANALYSIS = {
        'quote': 1.0,
        'ltp': 1.0,
        'historical_data': 0.2,
        'instruments': 6.0,
        'margins': 1.0,
        'holdings': 1.0,
        'positions': 1.0,
        'orders': 1.0,
        'profile': 1.0,
        'default': 0.2,
    }

    _ANALYSIS_MODE = os.environ.get('AI_TRADING_ANALYSIS', '0') == '1'

    _lock = threading.Lock()
    _last_call: Dict[str, float] = {}

    @classmethod
    def _intervals(cls):
        if cls._ANALYSIS_MODE:
            return cls._MIN_INTERVALS_ANALYSIS
        return cls._MIN_INTERVALS

    @classmethod
    def acquire(cls, endpoint: str):
        """Block until it is safe to make another call to `endpoint`."""
        t_start = time.perf_counter()
        mode = 'ANALYSIS' if cls._ANALYSIS_MODE else 'PRODUCTION'
        print(f"DEBUG RateLimiter.acquire({endpoint}) mode={mode}", flush=True)
        intervals = cls._intervals()
        min_interval = intervals.get(endpoint, intervals['default'])
        with cls._lock:
            last = cls._last_call.get(endpoint, 0.0)
            elapsed = time.time() - last
            wait = max(0.0, min_interval - elapsed)
            cls._last_call[endpoint] = time.time() + wait

        print(f"DEBUG RateLimiter.acquire({endpoint}) wait={wait:.3f}s", flush=True)
        if wait > 0:
            time.sleep(wait)
        print(f"DEBUG RateLimiter.acquire({endpoint}) done in {time.perf_counter()-t_start:.3f}s", flush=True)


class MarketDataFetcher:
    """Centralized, cached, batched market data provider."""

    # ── Instrument cache ────────────────────────────────────────────────────
    _instruments_cache: Optional[List[Dict]] = None
    _instruments_cache_time: Optional[datetime] = None
    _symbol_to_token: Dict[str, int] = {}
    _INSTRUMENTS_TTL = timedelta(hours=4)

    # ── Quote / LTP cache ───────────────────────────────────────────────────
    _quote_cache: Dict[str, Dict] = {}
    _quote_cache_time: Dict[str, datetime] = {}
    _QUOTE_TTL = timedelta(seconds=30)

    _ltp_cache: Dict[str, float] = {}
    _ltp_cache_time: Dict[str, datetime] = {}
    _LTP_TTL = timedelta(seconds=30)

    # ── Historical data cache ───────────────────────────────────────────────
    _hist_cache: Dict[str, pd.DataFrame] = {}
    _hist_cache_time: Dict[str, datetime] = {}
    _HIST_TTL = {
        '1d': timedelta(hours=6),
        '1h': timedelta(hours=1),
        '15m': timedelta(minutes=15),
        '5m': timedelta(minutes=5),
        '1m': timedelta(minutes=1),
        'default': timedelta(minutes=15),
    }

    # ── Circuit breaker (shared globally) ───────────────────────────────────
    _cb_failures: int = 0
    _cb_open_until: float = 0.0
    _CB_MAX_FAILURES: int = 5
    _CB_RESET_SECONDS: float = 30.0
    _cb_lock = threading.Lock()

    # ── Kite singleton and cycle metrics ────────────────────────────────────
    _kite_lock = threading.Lock()
    _kite: Any = None
    _cycle_metrics: Dict[str, Any] = {}
    _metrics_lock = threading.Lock()

    # NSE market holidays — update annually
    _NSE_HOLIDAYS = {
        "2025-01-26", "2025-02-26", "2025-03-14", "2025-03-31",
        "2025-04-10", "2025-04-14", "2025-04-18", "2025-05-01",
        "2025-08-15", "2025-08-27", "2025-10-02", "2025-10-02",
        "2025-10-20", "2025-10-24", "2025-11-05", "2025-12-25",
        "2026-01-26", "2026-03-03", "2026-03-20", "2026-04-02",
        "2026-04-03", "2026-04-14", "2026-05-01", "2026-08-15",
        "2026-10-02", "2026-10-29", "2026-11-13", "2026-12-25",
    }

    _period_days = {
        '1d': 1,
        '5d': 5,
        '1mo': 30,
        '3mo': 90,
        '6mo': 180,
        '1y': 365,
        '2y': 730,
        '5y': 1825,
    }

    _interval_map = {
        '1m': 'minute',
        '5m': '5minute',
        '15m': '15minute',
        '1h': 'hour',
        '1d': 'day',
    }

    def __init__(self, kite=None):
        self.kite = kite or MarketDataFetcher._get_kite()

    @classmethod
    def _get_kite(cls):
        with cls._kite_lock:
            if cls._kite is None:
                try:
                    token_manager = TokenManager()
                    cls._kite = token_manager.initialize_kite()
                    logger.info("Kite Connect initialized for market data")
                except Exception as e:
                    logger.error(f"Failed to initialize Kite Connect: {e}")
                    cls._kite = None
            return cls._kite

    def _kite_call_with_retry(self, func, endpoint: str, *args, **kwargs):
        """Call Kite API with rate limiting, retry, and circuit breaker."""
        with MarketDataFetcher._cb_lock:
            if time.time() < MarketDataFetcher._cb_open_until:
                remaining = MarketDataFetcher._cb_open_until - time.time()
                raise RuntimeError(f"Kite circuit open — retry after {remaining:.0f}s")
            MarketDataFetcher._cb_open_until = 0.0

        last_exc = None
        t_retry = time.perf_counter()
        print(f"DEBUG _kite_call_with_retry({endpoint}) enter", flush=True)
        for attempt in range(3):
            try:
                t_attempt = time.perf_counter()
                print(f"DEBUG _kite_call_with_retry({endpoint}) attempt {attempt+1}", flush=True)
                RateLimiter.acquire(endpoint)
                print(f"DEBUG _kite_call_with_retry({endpoint}) calling func", flush=True)
                t_func = time.perf_counter()
                result = func(*args, **kwargs)
                print(f"DEBUG _kite_call_with_retry({endpoint}) func returned in {time.perf_counter()-t_func:.3f}s", flush=True)
                with MarketDataFetcher._cb_lock:
                    MarketDataFetcher._cb_failures = 0
                self._record_metric('api_calls', 1)
                api_monitor.record_api_call(endpoint, batch_size=1)
                return result
            except Exception as exc:
                last_exc = exc
                exc_str = str(exc).lower()
                print(f"DEBUG _kite_call_with_retry({endpoint}) attempt {attempt+1} error: {exc} after {time.perf_counter()-t_attempt:.3f}s", flush=True)
                if 'access_token' in exc_str or 'api_key' in exc_str or 'invalid token' in exc_str:
                    logger.error("Kite auth error — token invalid. Run: python get_kite_token.py")
                    raise exc
                with MarketDataFetcher._cb_lock:
                    MarketDataFetcher._cb_failures += 1
                    if MarketDataFetcher._cb_failures >= MarketDataFetcher._CB_MAX_FAILURES:
                        MarketDataFetcher._cb_open_until = time.time() + MarketDataFetcher._CB_RESET_SECONDS
                        logger.warning(
                            f"Kite circuit OPEN after {MarketDataFetcher._cb_failures} failures — "
                            f"pausing API calls for {MarketDataFetcher._CB_RESET_SECONDS:.0f}s"
                        )
                        self._record_metric('circuit_breakers', 1)
                        api_monitor.record_circuit_breaker(endpoint)
                sleep_time = min(2 ** attempt, 4)
                logger.warning(f"Kite API error (attempt {attempt+1}/3): {exc} — retry in {sleep_time}s")
                time.sleep(sleep_time)
        print(f"DEBUG _kite_call_with_retry({endpoint}) raising after {time.perf_counter()-t_retry:.3f}s", flush=True)
        raise last_exc

    @classmethod
    def _get_instruments(cls) -> List[Dict]:
        now = datetime.now()
        t_inst = time.perf_counter()
        print("DEBUG _get_instruments enter", flush=True)
        if (cls._instruments_cache is not None and cls._instruments_cache_time is not None
                and (now - cls._instruments_cache_time) < cls._INSTRUMENTS_TTL):
            print(f"DEBUG _get_instruments cache hit in {time.perf_counter()-t_inst:.3f}s", flush=True)
            return cls._instruments_cache

        kite = cls._get_kite()
        if not kite:
            return []
        try:
            print("DEBUG _get_instruments calling kite.instruments", flush=True)
            t_ki = time.perf_counter()
            instruments = kite.instruments("NSE")
            print(f"DEBUG _get_instruments kite.instruments returned in {time.perf_counter()-t_ki:.3f}s", flush=True)
            cls._instruments_cache = instruments
            cls._instruments_cache_time = now
            cls._symbol_to_token = {i['tradingsymbol']: i['instrument_token'] for i in instruments}
            print(f"DEBUG _get_instruments loaded in {time.perf_counter()-t_inst:.3f}s", flush=True)
            return instruments
        except Exception as e:
            print(f"DEBUG _get_instruments error after {time.perf_counter()-t_inst:.3f}s: {e}", flush=True)
            logger.error(f"Error loading instruments: {e}")
            return cls._instruments_cache or []

    def _get_instrument_token(self, symbol: str) -> Optional[int]:
        print(f"DEBUG _get_instrument_token({symbol})", flush=True)
        if not MarketDataFetcher._symbol_to_token:
            self._get_instruments()
        token = MarketDataFetcher._symbol_to_token.get(symbol)
        print(f"DEBUG _get_instrument_token({symbol}) token={token}", flush=True)
        return token

    # ── Batch quote / LTP ───────────────────────────────────────────────────

    def get_batch_realtime_prices(self, symbols: List[str]) -> Dict[str, float]:
        if not self.kite or not symbols:
            return {}

        now = datetime.now()
        result = {}
        missing = []
        for s in symbols:
            key = f"NSE:{s}"
            if key in MarketDataFetcher._ltp_cache and (now - MarketDataFetcher._ltp_cache_time.get(key, now)) < MarketDataFetcher._LTP_TTL:
                result[s] = MarketDataFetcher._ltp_cache[key]
                self._record_metric('cache_hits', 1)
                api_monitor.record_api_call('ltp', batch_size=1, cache_hit=True)
            else:
                missing.append(s)

        if not missing:
            return result

        batch_size = 100
        for i in range(0, len(missing), batch_size):
            batch = missing[i:i + batch_size]
            keys = [f"NSE:{s}" for s in batch]
            try:
                data = self._kite_call_with_retry(self.kite.ltp, 'ltp', *keys)
                if data:
                    for key, val in data.items():
                        s = key.replace('NSE:', '')
                        price = val.get('last_price', 0)
                        if price > 0:
                            result[s] = price
                            MarketDataFetcher._ltp_cache[key] = price
                            MarketDataFetcher._ltp_cache_time[key] = now
            except Exception as e:
                logger.warning(f"Batch LTP failed for {len(batch)} symbols: {e}")

        return result

    def get_batch_stock_info(self, symbols: List[str]) -> Dict[str, Dict]:
        if not self.kite or not symbols:
            return {}

        now = datetime.now()
        result = {}
        missing = []
        for s in symbols:
            if s in MarketDataFetcher._quote_cache and (now - MarketDataFetcher._quote_cache_time.get(s, now)) < MarketDataFetcher._QUOTE_TTL:
                result[s] = MarketDataFetcher._quote_cache[s]
                self._record_metric('cache_hits', 1)
                api_monitor.record_api_call('quote', batch_size=1, cache_hit=True)
            else:
                missing.append(s)

        if not missing:
            return result

        batch_size = 100
        for i in range(0, len(missing), batch_size):
            batch = missing[i:i + batch_size]
            keys = [f"NSE:{s}" for s in batch]
            try:
                data = self._kite_call_with_retry(self.kite.quote, 'quote', *keys)
                if data:
                    for key, val in data.items():
                        s = key.replace('NSE:', '')
                        token = self._get_instrument_token(s)
                        info = {
                            'symbol': s,
                            'current_price': val.get('last_price', 0),
                            'day_open': val.get('ohlc', {}).get('open', 0),
                            'day_high': val.get('ohlc', {}).get('high', 0),
                            'day_low': val.get('ohlc', {}).get('low', 0),
                            'day_close': val.get('ohlc', {}).get('close', 0),
                            'volume': val.get('volume', 0),
                            'change': val.get('net_change', 0),
                            'change_percent': val.get('ohlc', {}).get('change', 0),
                            'instrument_token': token,
                            'exchange': 'NSE',
                        }
                        result[s] = info
                        MarketDataFetcher._quote_cache[s] = info
                        MarketDataFetcher._quote_cache_time[s] = now
            except Exception as e:
                logger.warning(f"Batch quote failed for {len(batch)} symbols: {e}")

        return result

    # ── Historical data (single-symbol per Kite call) with shared cache ───────

    def _hist_key(self, symbol: str, period: str, interval: str) -> str:
        return f"{symbol}|{period}|{interval}"

    def _hist_ttl(self, interval: str) -> timedelta:
        return MarketDataFetcher._HIST_TTL.get(interval, MarketDataFetcher._HIST_TTL['default'])

    def _validate_ohlcv(self, df: pd.DataFrame, symbol: str) -> pd.DataFrame:
        if df.empty:
            return df
        orig_len = len(df)
        price_cols = [c for c in ['Open', 'High', 'Low', 'Close'] if c in df.columns]
        df = df.dropna(subset=price_cols)
        for col in price_cols:
            df = df[df[col] > 0]
        if 'Volume' in df.columns:
            df = df[df['Volume'] >= 0]
        if 'Close' in df.columns and len(df) > 1:
            pct_chg = df['Close'].pct_change().abs()
            spike_mask = pct_chg > 0.50
            if spike_mask.any():
                logger.warning(f"{symbol}: dropped {spike_mask.sum()} spike candle(s)")
                df = df[~spike_mask]
        if df.index.duplicated().any():
            df = df[~df.index.duplicated(keep='last')]
        if len(df) < orig_len:
            logger.debug(f"{symbol}: validation removed {orig_len - len(df)}/{orig_len} bad rows")
        return df

    def get_batch_stock_data(self, symbols: List[str], period: str = "1mo", interval: str = "1d") -> Dict[str, pd.DataFrame]:
        t_batch = time.perf_counter()
        print(f"DEBUG get_batch_stock_data(symbols={symbols}, period={period}, interval={interval}) enter", flush=True)
        if not self.kite or not symbols:
            print(f"DEBUG get_batch_stock_data early return in {time.perf_counter()-t_batch:.3f}s", flush=True)
            return {}

        days = MarketDataFetcher._period_days.get(period, 30)
        kite_interval = MarketDataFetcher._interval_map.get(interval, 'day')
        to_date = datetime.now()
        from_date = to_date - timedelta(days=days)

        results = {}
        for symbol in symbols:
            t_sym = time.perf_counter()
            print(f"DEBUG get_batch_stock_data symbol={symbol} start", flush=True)
            key = self._hist_key(symbol, period, interval)
            now = datetime.now()
            if key in MarketDataFetcher._hist_cache and (now - MarketDataFetcher._hist_cache_time.get(key, now)) < self._hist_ttl(interval):
                results[symbol] = MarketDataFetcher._hist_cache[key]
                print(f"DEBUG get_batch_stock_data symbol={symbol} cache hit in {time.perf_counter()-t_sym:.3f}s", flush=True)
                self._record_metric('cache_hits', 1)
                api_monitor.record_api_call('historical_data', batch_size=1, cache_hit=True)
                continue

            token = self._get_instrument_token(symbol)
            if not token:
                logger.warning(f"Instrument not found for {symbol}")
                continue

            try:
                data = self._kite_call_with_retry(
                    self.kite.historical_data,
                    'historical_data',
                    instrument_token=token,
                    from_date=from_date,
                    to_date=to_date,
                    interval=kite_interval,
                )
                print(f"DEBUG get_batch_stock_data symbol={symbol} got data in {time.perf_counter()-t_sym:.3f}s", flush=True)
                if not data:
                    logger.warning(f"No historical data for {symbol}")
                    continue

                df = pd.DataFrame(data)
                df.columns = [c.capitalize() for c in df.columns]
                df = self._validate_ohlcv(df, symbol)
                print(f"DEBUG get_batch_stock_data symbol={symbol} validated in {time.perf_counter()-t_sym:.3f}s", flush=True)

                if df.empty:
                    continue

                MarketDataFetcher._hist_cache[key] = df
                MarketDataFetcher._hist_cache_time[key] = now
                results[symbol] = df
                print(f"DEBUG get_batch_stock_data symbol={symbol} cached in {time.perf_counter()-t_sym:.3f}s", flush=True)

            except Exception as e:
                print(f"DEBUG get_batch_stock_data symbol={symbol} error {e} at {time.perf_counter()-t_sym:.3f}s", flush=True)
                self._record_metric('api_errors', 1)
                logger.error(f"Error fetching historical data for {symbol}: {e}")

        # Evict oldest if cache is too large
        while len(MarketDataFetcher._hist_cache) > 500:
            oldest = min(MarketDataFetcher._hist_cache_time, key=MarketDataFetcher._hist_cache_time.get)
            MarketDataFetcher._hist_cache.pop(oldest, None)
            MarketDataFetcher._hist_cache_time.pop(oldest, None)

        print(f"DEBUG get_batch_stock_data completed in {time.perf_counter()-t_batch:.3f}s", flush=True)
        return results

    def get_stock_data(self, symbol: str, period: str = "1mo", interval: str = "1d") -> pd.DataFrame:
        t0 = time.perf_counter()
        print(f"DEBUG get_stock_data({symbol},{period},{interval}) enter", flush=True)
        key = self._hist_key(symbol, period, interval)
        now = datetime.now()
        if key in MarketDataFetcher._hist_cache and (now - MarketDataFetcher._hist_cache_time.get(key, now)) < self._hist_ttl(interval):
            print(f"DEBUG get_stock_data({symbol}) cache hit in {time.perf_counter()-t0:.3f}s", flush=True)
            self._record_metric('cache_hits', 1)
            api_monitor.record_api_call('historical_data', batch_size=1, cache_hit=True)
            return MarketDataFetcher._hist_cache[key]

        print(f"DEBUG get_stock_data({symbol}) cache miss", flush=True)
        self._record_metric('cache_misses', 1)
        t1 = time.perf_counter()
        data = self.get_batch_stock_data([symbol], period, interval)
        print(f"DEBUG get_stock_data({symbol}) batch took {time.perf_counter()-t1:.3f}s", flush=True)
        result = data.get(symbol, pd.DataFrame())
        print(f"DEBUG get_stock_data({symbol}) returning in {time.perf_counter()-t0:.3f}s, rows={len(result)}", flush=True)
        return result

    def get_multiple_stocks_data(self, symbols: List[str], period: str = "1mo") -> Dict[str, pd.DataFrame]:
        # Default to daily candles for the requested period
        return self.get_batch_stock_data(symbols, period=period, interval="1d")

    def get_intraday_data(self, symbol: str, days: int = 5) -> pd.DataFrame:
        return self.get_stock_data(symbol, period=f"{days}d", interval="15m")

    # ── Single-symbol wrappers (used by many consumers) ─────────────────────

    def get_realtime_price(self, symbol: str) -> Optional[float]:
        prices = self.get_batch_realtime_prices([symbol])
        price = prices.get(symbol)
        if price is None and self.kite:
            info = self.get_batch_stock_info([symbol])
            price = info.get(symbol, {}).get('current_price')
        return price

    def get_stock_info(self, symbol: str) -> Dict:
        info = self.get_batch_stock_info([symbol])
        return info.get(symbol, {})

    # ── Explicit prefetch hooks for the trading cycle ───────────────────────

    def prefetch_quotes(self, symbols: List[str]) -> Dict[str, Dict]:
        """Stage 1: one batch quote call for all symbols; warms LTP cache too."""
        if not symbols:
            return {}
        ltp = self.get_batch_realtime_prices(symbols)
        quotes = self.get_batch_stock_info(symbols)
        # Also warm LTP cache from quote last_price if LTP call failed
        for s, q in quotes.items():
            if s not in ltp and q.get('current_price'):
                key = f"NSE:{s}"
                MarketDataFetcher._ltp_cache[key] = q['current_price']
                MarketDataFetcher._ltp_cache_time[key] = datetime.now()
        return quotes

    def prefetch_historical(self, symbols: List[str], period: str, interval: str) -> Dict[str, pd.DataFrame]:
        """Stage 2: fetch candles for a shortlist; subsequent calls return cached."""
        return self.get_batch_stock_data(symbols, period=period, interval=interval)

    # ── Cycle metrics ───────────────────────────────────────────────────────

    @classmethod
    def new_cycle(cls):
        with cls._metrics_lock:
            cls._cycle_metrics = {
                'cycle_start': time.time(),
                'api_calls': 0,
                'cache_hits': 0,
                'cache_misses': 0,
                'api_errors': 0,
                'circuit_breakers': 0,
            }

    @classmethod
    def _record_metric(cls, key: str, value: int = 1):
        with cls._metrics_lock:
            if key not in cls._cycle_metrics:
                cls._cycle_metrics[key] = 0
            cls._cycle_metrics[key] += value

    @classmethod
    def get_cycle_metrics(cls) -> Dict[str, Any]:
        with cls._metrics_lock:
            m = cls._cycle_metrics.copy()
        elapsed = time.time() - m.get('cycle_start', time.time())
        total = m.get('api_calls', 0) + m.get('cache_hits', 0) + m.get('cache_misses', 0)
        cache_lookups = m.get('cache_hits', 0) + m.get('cache_misses', 0)
        m['cycle_elapsed_seconds'] = round(elapsed, 2)
        m['cache_hit_ratio'] = round(m.get('cache_hits', 0) / cache_lookups, 4) if cache_lookups else 0.0
        m['cache_miss_ratio'] = round(m.get('cache_misses', 0) / cache_lookups, 4) if cache_lookups else 0.0
        m['total_lookups'] = total
        cls._persist_cycle_metrics(m)
        return m

    @classmethod
    def _persist_cycle_metrics(cls, metrics: Dict[str, Any]):
        try:
            from config import config as _cfg
            log_dir = getattr(_cfg, 'LOGS_DIR', 'logs')
        except Exception:
            log_dir = 'logs'
        try:
            os.makedirs(log_dir, exist_ok=True)
            path = os.path.join(log_dir, 'market_data_metrics.json')
            with open(path, 'w') as f:
                json.dump(metrics, f)
        except Exception:
            pass

    @classmethod
    def load_cycle_metrics(cls) -> Dict[str, Any]:
        try:
            from config import config as _cfg
            log_dir = getattr(_cfg, 'LOGS_DIR', 'logs')
        except Exception:
            log_dir = 'logs'
        try:
            path = os.path.join(log_dir, 'market_data_metrics.json')
            with open(path) as f:
                return json.load(f)
        except Exception:
            return {}

    # ── Market hours helpers ────────────────────────────────────────────────

    @classmethod
    def is_market_open(cls) -> bool:
        ist = pytz.timezone('Asia/Kolkata')
        now = datetime.now(ist)
        if now.weekday() >= 5:
            return False
        today_str = now.strftime("%Y-%m-%d")
        if today_str in cls._NSE_HOLIDAYS:
            logger.info(f"Market holiday today ({today_str}) — trading paused")
            return False
        market_open = datetime.strptime("09:15", "%H:%M").time()
        market_close = datetime.strptime("15:30", "%H:%M").time()
        return market_open <= now.time() <= market_close

    @classmethod
    def is_market_holiday(cls) -> bool:
        ist = pytz.timezone('Asia/Kolkata')
        today_str = datetime.now(ist).strftime("%Y-%m-%d")
        return today_str in cls._NSE_HOLIDAYS
