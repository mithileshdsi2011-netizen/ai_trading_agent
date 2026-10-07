"""
Angel One WebSocket Module

Handles Angel One SmartAPI WebSocket 2.0 (SmartWebSocketV2) for real-time
market data. Completely separate from Kite WebSocket.

Degrades safely: if the smartapi-python SDK is not installed or Angel
credentials are missing, every method fails closed and status reports
DISCONNECTED — Swing Trading and IPO Intelligence are unaffected.
"""
import logging
import threading
import time
from typing import Optional, Callable, Dict, Any, List
from datetime import datetime

logger = logging.getLogger(__name__)

# Angel exchange type codes for SmartWebSocketV2 subscriptions
EXCHANGE_TYPE_NSE_CM = 1        # NSE cash market
MODE_LTP = 1                    # LTP-only subscription mode
MODE_QUOTE = 2                  # 5-level quote
MODE_SNAP_QUOTE = 3             # full snap quote

MAX_RECONNECT_ATTEMPTS = 5
BACKOFF_BASE_SECONDS = 5


class AngelWebSocket:
    """Angel One SmartAPI WebSocket 2.0 handler."""

    def __init__(self, auth, exchange_type: int = EXCHANGE_TYPE_NSE_CM):
        """
        Initialize WebSocket handler.

        Args:
            auth: AngelAuth instance
            exchange_type: Angel exchange type code (default NSE cash)
        """
        self.auth = auth
        self.exchange_type = exchange_type
        self._ws = None
        self._connected = False
        self._subscribed_tokens = set()
        self._callbacks = {}
        self._thread = None
        self._running = False
        self._reconnect_attempts = 0
        self._last_heartbeat: Optional[datetime] = None
        self._last_error: Optional[str] = None
        self._lock = threading.Lock()

    # ── SDK wiring ──────────────────────────────────────────────────
    def _build_ws(self):
        """Instantiate SmartWebSocketV2 (lazy import)."""
        try:
            from SmartApi.smartWebSocketV2 import SmartWebSocketV2
        except ImportError:
            try:
                from smartapi.smartWebSocketV2 import SmartWebSocketV2
            except ImportError:
                raise ImportError("smartapi-python package not installed")

        ws = SmartWebSocketV2(
            self.auth.jwt_token,
            self.auth.api_key,
            self.auth.client_code,
            self.auth.feed_token,
        )

        ws.on_open = self._on_open
        ws.on_data = self._on_data
        ws.on_error = self._on_error
        ws.on_close = self._on_close
        return ws

    # ── callbacks ───────────────────────────────────────────────────
    def _on_open(self, wsapp):
        with self._lock:
            self._connected = True
            self._reconnect_attempts = 0
            self._last_heartbeat = datetime.now()
        logger.info("[ANGEL WS] Connected")
        # Resubscribe after (re)connect
        tokens = list(self._subscribed_tokens)
        if tokens:
            self._send_subscribe(tokens, MODE_LTP)

    def _on_data(self, wsapp, data):
        self._last_heartbeat = datetime.now()
        for cb in list(self._callbacks.values()):
            try:
                cb(data)
            except Exception as e:
                logger.error(f"[ANGEL WS] Callback error: {e}")

    def _on_error(self, wsapp, error):
        self._last_error = str(error)
        logger.error(f"[ANGEL WS] Error: {error}")

    def _on_close(self, wsapp=None, *args):
        was_running = self._running
        with self._lock:
            self._connected = False
        logger.warning("[ANGEL WS] Connection closed")
        if was_running:
            self._schedule_reconnect()

    # ── lifecycle ───────────────────────────────────────────────────
    def connect(self) -> bool:
        """
        Connect to Angel WebSocket.

        Returns:
            True if connection initiated successfully
        """
        with self._lock:
            if self._running or self._connected:
                logger.info("[ANGEL WS] Already connected/connecting — ignoring duplicate connect")
                return True

            if not self.auth.is_authenticated():
                logger.error("[ANGEL WS] Not authenticated, cannot connect WebSocket")
                return False

            try:
                self._ws = self._build_ws()
            except ImportError as e:
                logger.warning(f"[ANGEL WS] {e}")
                self._last_error = str(e)
                return False
            except Exception as e:
                logger.error(f"[ANGEL WS] Build error: {e}")
                self._last_error = str(e)
                return False

            self._running = True
            self._thread = threading.Thread(
                target=self._run, daemon=True, name="angel-ws"
            )
            self._thread.start()
            return True

    def _run(self):
        """Blocking SDK connect() call, run on a daemon thread."""
        try:
            self._ws.connect()
        except Exception as e:
            logger.error(f"[ANGEL WS] Connect error: {e}")
            self._last_error = str(e)
            self._on_close()

    def _schedule_reconnect(self):
        """Reconnect with exponential backoff, bounded attempts."""
        def _reconnect():
            while self._running and self._reconnect_attempts < MAX_RECONNECT_ATTEMPTS:
                self._reconnect_attempts += 1
                delay = BACKOFF_BASE_SECONDS * (2 ** (self._reconnect_attempts - 1))
                logger.info(f"[ANGEL WS] Reconnect attempt {self._reconnect_attempts}/{MAX_RECONNECT_ATTEMPTS} in {delay}s")
                time.sleep(delay)
                if not self._running:
                    return
                try:
                    self._ws = self._build_ws()
                    self._ws.connect()
                    return
                except Exception as e:
                    logger.error(f"[ANGEL WS] Reconnect failed: {e}")
                    self._last_error = str(e)
            if self._reconnect_attempts >= MAX_RECONNECT_ATTEMPTS:
                logger.error("[ANGEL WS] Max reconnect attempts reached — staying DISCONNECTED")
                self._running = False

        threading.Thread(target=_reconnect, daemon=True, name="angel-ws-reconnect").start()

    def _send_subscribe(self, tokens: List[str], mode: int) -> bool:
        try:
            correlation_id = f"intraday_{int(time.time())}"
            token_list = [{"exchangeType": self.exchange_type, "tokens": tokens}]
            self._ws.subscribe(correlation_id, mode, token_list)
            return True
        except Exception as e:
            logger.error(f"[ANGEL WS] Subscribe send error: {e}")
            return False

    def subscribe(self, tokens: List[str], callback: Optional[Callable] = None,
                  mode: int = MODE_LTP) -> bool:
        """
        Subscribe to symbol tokens for real-time data.

        Args:
            tokens: List of Angel symbol tokens
            callback: Optional callback for tick updates
            mode: Subscription mode (LTP / QUOTE / SNAP_QUOTE)

        Returns:
            True if subscription request accepted
        """
        try:
            if not self._connected or self._ws is None:
                logger.warning("[ANGEL WS] Not connected, cannot subscribe")
                return False

            if callback:
                self._callbacks['default'] = callback

            if not tokens:
                return True

            if not self._send_subscribe(tokens, mode):
                return False

            self._subscribed_tokens.update(tokens)
            logger.info(f"[ANGEL WS] Subscribed to {len(tokens)} tokens")
            return True

        except Exception as e:
            logger.error(f"[ANGEL WS] Subscription error: {e}")
            return False

    def unsubscribe(self, tokens: List[str]) -> bool:
        """
        Unsubscribe from symbol tokens.

        Args:
            tokens: List of symbol tokens

        Returns:
            True if unsubscription successful
        """
        try:
            if self._connected and self._ws and tokens:
                token_list = [{"exchangeType": self.exchange_type, "tokens": tokens}]
                self._ws.unsubscribe(f"intraday_{int(time.time())}", MODE_LTP, token_list)

            self._subscribed_tokens.difference_update(tokens)
            logger.info(f"[ANGEL WS] Unsubscribed {len(tokens)} tokens")
            return True

        except Exception as e:
            logger.error(f"[ANGEL WS] Unsubscription error: {e}")
            return False

    def disconnect(self) -> bool:
        """
        Clean shutdown of the WebSocket.

        Returns:
            True if disconnection clean
        """
        try:
            self._running = False
            self._subscribed_tokens.clear()
            self._connected = False

            ws = self._ws
            self._ws = None
            if ws is not None:
                try:
                    ws.close_connection()
                except Exception as e:
                    logger.warning(f"[ANGEL WS] close_connection error: {e}")

            logger.info("[ANGEL WS] Disconnected")
            return True

        except Exception as e:
            logger.error(f"[ANGEL WS] Disconnection error: {e}")
            return False

    # ── status ──────────────────────────────────────────────────────
    def is_connected(self) -> bool:
        """Check if WebSocket is connected."""
        return self._connected

    def is_data_stale(self, max_age_seconds: int = 30) -> bool:
        """True when connected but no tick seen within max_age_seconds."""
        if self._last_heartbeat is None:
            return True
        return (datetime.now() - self._last_heartbeat).total_seconds() > max_age_seconds

    def get_subscribed_symbols(self) -> List[str]:
        """Get list of subscribed tokens."""
        return list(self._subscribed_tokens)

    def get_status(self) -> Dict[str, Any]:
        """Get WebSocket status."""
        if not self._connected:
            state = "DISCONNECTED"
        elif self.is_data_stale():
            state = "STALE"
        else:
            state = "LIVE"
        return {
            'connected': self._connected,
            'status': state,
            'subscribed_count': len(self._subscribed_tokens),
            'last_heartbeat': self._last_heartbeat.isoformat() if self._last_heartbeat else None,
            'last_error': self._last_error,
            'reconnect_attempts': self._reconnect_attempts,
            'running': self._running
        }
