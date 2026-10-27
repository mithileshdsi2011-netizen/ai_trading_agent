"""
Angel One LIVE Order Gateway

The ONLY path from this codebase to real SmartAPI order endpoints.
Every mutating call (place/modify/cancel) is double-gated:

  1. `enabled` — constructed False; the engine wires it to the armed
     state, so a disarmed process physically cannot submit.
  2. Per-call `armed()` re-check — the flag is consulted at call time,
     not cached, so a disarm takes effect immediately.

Read-only calls (order book, trade book, positions, funds) are allowed
in any arm state — reconciliation and monitoring need them.

The existing client.place_order/modify_order/cancel_order stubs stay
disabled — nothing here weakens them; this gateway talks to the SDK
directly through the client's throttle/retry wrapper.
"""
import logging
import threading
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

DISARMED_RESULT = {
    'success': False,
    'error': 'LIVE DISARMED — order not submitted',
    'disarmed': True,
}


class AngelOrderGateway:
    """Rate-limited, arm-gated wrapper over SmartAPI order endpoints."""

    def __init__(self, client, armed: Optional[Callable[[], bool]] = None):
        self._client = client
        self._armed = armed or (lambda: False)
        self._order_lock = threading.Lock()

    def armed(self) -> bool:
        try:
            return bool(self._armed())
        except Exception:
            return False

    # ── read-only (any arm state) ───────────────────────────────────
    def order_book(self) -> Dict[str, Any]:
        """Raw order book. {'success': bool, 'orders': [...]}"""
        try:
            result = self._client.get_order_book()
            if not result.get('success'):
                return {'success': False,
                        'error': result.get('error', 'order book failed'),
                        'orders': []}
            data = result.get('data')
            rows = data.get('data') if isinstance(data, dict) else data
            return {'success': True, 'orders': rows if isinstance(rows, list) else []}
        except Exception as e:
            logger.error(f"[ANGEL ORDERS] order_book error: {e}")
            return {'success': False, 'error': str(e), 'orders': []}

    def trade_book(self) -> Dict[str, Any]:
        try:
            result = self._client.get_trade_book()
            if not result.get('success'):
                return {'success': False,
                        'error': result.get('error', 'trade book failed'),
                        'trades': []}
            data = result.get('data')
            rows = data.get('data') if isinstance(data, dict) else data
            return {'success': True, 'trades': rows if isinstance(rows, list) else []}
        except Exception as e:
            logger.error(f"[ANGEL ORDERS] trade_book error: {e}")
            return {'success': False, 'error': str(e), 'trades': []}

    def positions(self) -> Dict[str, Any]:
        try:
            result = self._client.get_positions()
            if not result.get('success'):
                return {'success': False,
                        'error': result.get('error', 'positions failed'),
                        'positions': []}
            data = result.get('data')
            rows = (data.get('data') if isinstance(data, dict) else data) or []
            return {'success': True,
                    'positions': rows if isinstance(rows, list) else []}
        except Exception as e:
            logger.error(f"[ANGEL ORDERS] positions error: {e}")
            return {'success': False, 'error': str(e), 'positions': []}

    def funds(self) -> Dict[str, Any]:
        """Available margin/cash via rmsLimit (read-only)."""
        try:
            result = self._client.get_rms_limits()
            if not result.get('success'):
                return {'success': False,
                        'error': result.get('error', 'rmsLimit failed')}
            data = result.get('data') or {}
            available = data.get('availablecash')
            try:
                available = float(available) if available is not None else None
            except (TypeError, ValueError):
                available = None
            return {'success': True, 'available_cash': available, 'raw': data}
        except Exception as e:
            logger.error(f"[ANGEL ORDERS] funds error: {e}")
            return {'success': False, 'error': str(e)}

    # ── mutating (armed only) ───────────────────────────────────────
    def place(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Submit a real order. Refuses unless armed at call time."""
        with self._order_lock:
            if not self.armed():
                logger.error(
                    "[ANGEL ORDERS] place() refused — LIVE DISARMED "
                    f"(symbol={params.get('tradingsymbol')})")
                return dict(DISARMED_RESULT)
            try:
                result = self._client._call(
                    'account', self._client._get_smartapi().placeOrder, params)
                return self._normalize_submit(result)
            except Exception as e:
                # Exception AFTER submission may still mean the broker
                # received the order — the adapter treats this as UNKNOWN,
                # never as safe-to-retry.
                logger.error(f"[ANGEL ORDERS] place() raised: {e}")
                return {'success': False, 'error': str(e), 'unknown': True}

    def modify(self, params: Dict[str, Any]) -> Dict[str, Any]:
        with self._order_lock:
            if not self.armed():
                return dict(DISARMED_RESULT)
            try:
                result = self._client._call(
                    'account', self._client._get_smartapi().modifyOrder, params)
                return self._normalize_submit(result)
            except Exception as e:
                logger.error(f"[ANGEL ORDERS] modify() raised: {e}")
                return {'success': False, 'error': str(e), 'unknown': True}

    def cancel(self, order_id: str, variety: str = "NORMAL") -> Dict[str, Any]:
        with self._order_lock:
            if not self.armed():
                return dict(DISARMED_RESULT)
            try:
                result = self._client._call(
                    'account', self._client._get_smartapi().cancelOrder,
                    order_id, variety)
                if isinstance(result, dict) and result.get('status') is False:
                    return {'success': False,
                            'error': result.get('message', 'cancel failed')}
                return {'success': True, 'raw': result}
            except Exception as e:
                logger.error(f"[ANGEL ORDERS] cancel() raised: {e}")
                return {'success': False, 'error': str(e), 'unknown': True}

    @staticmethod
    def _normalize_submit(result: Any) -> Dict[str, Any]:
        """Normalize a SmartAPI place/modify response."""
        if not isinstance(result, dict):
            # Some SDK builds return a bare order-id string on success.
            if isinstance(result, str) and result:
                return {'success': True, 'broker_order_id': result, 'raw': result}
            return {'success': False, 'error': f'unrecognized response: {result!r}',
                    'unknown': True}
        if result.get('status') is False:
            return {'success': False,
                    'error': result.get('message', 'order rejected'),
                    'errorcode': result.get('errorcode'),
                    'rejected': True}
        data = result.get('data') or {}
        order_id = data.get('orderid') or result.get('orderid')
        return {'success': True, 'broker_order_id': order_id, 'raw': result}


def classify_broker_status(raw_status: Optional[str],
                           filled_qty: int, total_qty: int) -> str:
    """Map a broker order status string to the adapter's coarse classes:
    FILLED / PARTIAL / OPEN / REJECTED / CANCELLED / UNKNOWN."""
    s = (raw_status or '').strip().lower()
    if s in ('complete', 'completed', 'traded', 'filled'):
        return 'FILLED'
    if s in ('rejected', 'rejected by broker'):
        return 'REJECTED'
    if s in ('cancelled', 'canceled'):
        return 'CANCELLED'
    if s in ('pending', 'open', 'trigger pending', 'put order req received',
             'validation pending', 'modified', 'after market order req received'):
        if 0 < filled_qty < total_qty:
            return 'PARTIAL'
        return 'OPEN'
    if filled_qty >= total_qty > 0:
        return 'FILLED'
    if 0 < filled_qty < total_qty:
        return 'PARTIAL'
    return 'UNKNOWN'
