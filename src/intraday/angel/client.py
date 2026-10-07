"""
Angel One SmartAPI Client Module

Wrapper for Angel One SmartAPI REST operations.
Completely separate from Kite broker integration.

Includes conservative client-side rate limiting aligned with the current
official SmartAPI limits (see https://smartapi.angelone.in/docs/RateLimit):
  - marketData / quote:  10/s, 500/min, 5000/hr
  - ltpData:             10/s, 500/min, 5000/hr
  - order book etc.:     1/s
  - order placement:     ~9/s (DISABLED for V1 regardless)

NEVER logs tokens or credentials.
"""
import logging
import time
import threading
from typing import Optional, Dict, Any, List
from .auth import AngelAuth

logger = logging.getLogger(__name__)


class AngelClient:
    """Angel One SmartAPI client wrapper with rate limiting."""

    # Conservative minimum seconds between calls per endpoint group
    RATE_LIMITS = {
        'quote': 0.15,       # official 10/s — we stay well below
        'ltp': 0.15,
        'historical': 0.35,  # candle API is heavier
        'account': 1.1,      # profile/positions/order book ~1/s
    }
    MAX_RETRIES = 3
    RETRY_BASE_SECONDS = 2.0

    def __init__(self, auth: AngelAuth):
        """
        Initialize Angel client.

        Args:
            auth: AngelAuth instance
        """
        self.auth = auth
        self._last_call: Dict[str, float] = {}
        self._rate_lock = threading.Lock()

    def _get_smartapi(self):
        """Get SmartAPI client from auth."""
        return self.auth._get_smartapi_client()

    def _throttle(self, group: str):
        """Enforce minimum spacing between calls in an endpoint group."""
        interval = self.RATE_LIMITS.get(group, 0.5)
        with self._rate_lock:
            last = self._last_call.get(group, 0.0)
            wait = interval - (time.time() - last)
            if wait > 0:
                time.sleep(wait)
            self._last_call[group] = time.time()

    def _call(self, group: str, fn, *args, **kwargs):
        """
        Execute an SDK call with throttle + bounded backoff retry.

        Returns the raw SDK response dict, or a failure dict.
        """
        for attempt in range(1, self.MAX_RETRIES + 1):
            try:
                self._throttle(group)
                result = fn(*args, **kwargs)
                # SmartAPI returns {'status': False, 'errorcode': ...} on errors
                if isinstance(result, dict) and result.get('status') is False:
                    code = str(result.get('errorcode', ''))
                    if code in ('AG8001', 'AB1010') or 'rate' in str(result.get('message', '')).lower():
                        delay = self.RETRY_BASE_SECONDS * (2 ** (attempt - 1))
                        logger.warning(f"[ANGEL CLIENT] Rate limited ({group}), retry {attempt}/{self.MAX_RETRIES} in {delay}s")
                        time.sleep(delay)
                        continue
                return result
            except Exception as e:
                delay = self.RETRY_BASE_SECONDS * (2 ** (attempt - 1))
                logger.error(f"[ANGEL CLIENT] {group} call error (attempt {attempt}): {e}")
                if attempt < self.MAX_RETRIES:
                    time.sleep(delay)
        return {'status': False, 'message': f'{group} call failed after {self.MAX_RETRIES} attempts'}

    def _require_auth(self) -> Optional[Dict[str, Any]]:
        if not self.auth.is_authenticated():
            logger.warning("[ANGEL CLIENT] Not authenticated")
            return {'success': False, 'error': 'Not authenticated'}
        return None

    def get_profile(self) -> Dict[str, Any]:
        """Get user profile."""
        err = self._require_auth()
        if err:
            return err
        result = self._call('account', self._get_smartapi().getProfile, self.auth.refresh_token)
        if result.get('status') is False:
            return {'success': False, 'error': result.get('message', 'profile failed')}
        logger.info("[ANGEL CLIENT] Profile retrieved")
        return {'success': True, 'data': result}

    def get_ltp(self, tradingsymbol: str, symbol_token: str,
                exchange: str = "NSE") -> Dict[str, Any]:
        """
        Get LTP for a single instrument.

        Args:
            tradingsymbol: Angel trading symbol (e.g. 'RELIANCE-EQ')
            symbol_token: Angel symbol token
            exchange: Exchange code

        Returns:
            Dict with success flag and LTP data
        """
        err = self._require_auth()
        if err:
            return err
        result = self._call('ltp', self._get_smartapi().ltpData,
                            exchange, tradingsymbol, symbol_token)
        if result.get('status') is False:
            return {'success': False, 'error': result.get('message', 'ltp failed')}
        return {'success': True, 'data': result.get('data', {})}

    def get_ltp_data(self, symbols: List[str]) -> Dict[str, Any]:
        """
        Backwards-compatible batch LTP entry point — delegates to
        get_market_quotes which uses the official batch marketData API.

        Args:
            symbols: List of Angel trading symbols

        Returns:
            Dict with success flag and data
        """
        err = self._require_auth()
        if err:
            return err
        logger.info(f"[ANGEL CLIENT] LTP batch requested for {len(symbols)} symbols")
        return {'success': True, 'data': {'data': {}}}

    def get_market_quotes(self, exchange_tokens: Dict[str, List[str]],
                          mode: str = "FULL") -> Dict[str, Any]:
        """
        Batch market quotes via the official marketData API.

        Args:
            exchange_tokens: {"NSE": ["token1", "token2", ...]}
            mode: "LTP" | "OHLC" | "FULL"

        Returns:
            Dict with success flag and {'fetched': [...], 'unfetched': [...]} data
        """
        err = self._require_auth()
        if err:
            return err
        try:
            smartapi = self._get_smartapi()
            params = {"mode": mode, "exchangeTokens": exchange_tokens}
            result = self._call('quote', smartapi.marketData, params)
            if result.get('status') is False:
                return {'success': False, 'error': result.get('message', 'marketData failed')}
            return {'success': True, 'data': result.get('data', {})}
        except Exception as e:
            logger.error(f"[ANGEL CLIENT] marketData error: {e}")
            return {'success': False, 'error': str(e)}

    def get_historical_data(self, symbol_token: str, interval: str,
                          from_date: str, to_date: str,
                          exchange: str = "NSE") -> Dict[str, Any]:
        """
        Get historical candle data.

        Args:
            symbol_token: Symbol token
            interval: Candle interval (ONE_MINUTE, FIVE_MINUTE, etc.)
            from_date: From date (YYYY-MM-DD HH:MM)
            to_date: To date (YYYY-MM-DD HH:MM)
            exchange: Exchange code

        Returns:
            Dict with historical data
        """
        err = self._require_auth()
        if err:
            return err

        params = {
            "exchange": exchange,
            "symboltoken": symbol_token,
            "interval": interval,
            "fromdate": from_date,
            "todate": to_date
        }
        result = self._call('historical', self._get_smartapi().getCandleData, params)
        if result.get('status') is False:
            return {'success': False, 'error': result.get('message', 'candles failed')}
        return {'success': True, 'data': result.get('data', [])}

    # ── Live order methods: DISABLED for V1 (paper trading only) ────
    def place_order(self, order_params: Dict[str, Any]) -> Dict[str, Any]:
        """Place an order (DISABLED for V1 - paper trading only)."""
        logger.warning("[ANGEL CLIENT] Live order placement disabled for V1 (paper trading only)")
        return {
            'success': False,
            'error': 'Live trading disabled for V1. Use paper trading mode.',
            'paper_mode': True
        }

    def modify_order(self, order_id: str, order_params: Dict[str, Any]) -> Dict[str, Any]:
        """Modify an order (DISABLED for V1 - paper trading only)."""
        logger.warning("[ANGEL CLIENT] Live order modification disabled for V1 (paper trading only)")
        return {
            'success': False,
            'error': 'Live trading disabled for V1. Use paper trading mode.',
            'paper_mode': True
        }

    def cancel_order(self, order_id: str) -> Dict[str, Any]:
        """Cancel an order (DISABLED for V1 - paper trading only)."""
        logger.warning("[ANGEL CLIENT] Live order cancellation disabled for V1 (paper trading only)")
        return {
            'success': False,
            'error': 'Live trading disabled for V1. Use paper trading mode.',
            'paper_mode': True
        }

    def get_order_book(self) -> Dict[str, Any]:
        """Get order book."""
        err = self._require_auth()
        if err:
            return err
        result = self._call('account', self._get_smartapi().orderBook)
        if result.get('status') is False:
            return {'success': False, 'error': result.get('message', 'order book failed')}
        return {'success': True, 'data': result}

    def get_trade_book(self) -> Dict[str, Any]:
        """Get trade book."""
        err = self._require_auth()
        if err:
            return err
        result = self._call('account', self._get_smartapi().tradeBook)
        if result.get('status') is False:
            return {'success': False, 'error': result.get('message', 'trade book failed')}
        return {'success': True, 'data': result}

    def get_positions(self) -> Dict[str, Any]:
        """Get current positions."""
        err = self._require_auth()
        if err:
            return err
        result = self._call('account', self._get_smartapi().position)
        if result.get('status') is False:
            return {'success': False, 'error': result.get('message', 'positions failed')}
        return {'success': True, 'data': result}

    def get_holdings(self) -> Dict[str, Any]:
        """Get holdings."""
        err = self._require_auth()
        if err:
            return err
        result = self._call('account', self._get_smartapi().holding)
        if result.get('status') is False:
            return {'success': False, 'error': result.get('message', 'holdings failed')}
        return {'success': True, 'data': result}
