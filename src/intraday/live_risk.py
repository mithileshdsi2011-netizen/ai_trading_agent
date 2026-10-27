"""
Intraday LIVE Risk Controls

Independent, configurable risk controls for real-money Angel execution.
Completely separate from the PAPER risk manager — PAPER limits
(₹10,000 capital, 5 entries/day, 2 concurrent, ₹200 daily loss) live in
IntradayRiskManager and are untouched by anything here.

Every control fails CLOSED: a missing, zero or unreadable limit means
DENY, not allow. The adapter additionally requires an explicit arm state
before any order path is reachable at all.
"""
import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, time, date
from typing import Any, Dict, List, Optional, Tuple

import pytz

logger = logging.getLogger(__name__)

IST = pytz.timezone("Asia/Kolkata")


def _parse_hhmm(value: str, fallback: time) -> time:
    try:
        hh, mm = str(value).split(":")
        return time(int(hh), int(mm))
    except Exception:
        return fallback


@dataclass
class LiveRiskConfig:
    """Independent LIVE risk limits. Defaults deny everything."""
    enabled: bool = False
    capital: float = 0.0                # allocated LIVE capital
    max_order_value: float = 0.0        # per-order notional cap
    max_order_qty: int = 0              # per-order quantity cap
    max_per_trade_loss: float = 0.0     # qty * |entry - stop| cap
    max_daily_loss: float = 0.0         # realized + unrealized loss cap
    max_entries_per_day: int = 0
    max_concurrent_positions: int = 0
    max_gross_exposure: float = 0.0     # sum of open notionals
    allow_leverage: bool = False        # False => order value <= capital/funds
    max_slippage_pct: float = 0.0       # |signal px - quote px| / quote px cap
    entry_cutoff: str = "14:30"         # no new entries after this time
    force_exit_by: str = "15:05"        # mandatory flat-by deadline
    max_broker_errors: int = 3          # consecutive broker failures -> breaker
    order_timeout_s: float = 30.0       # ack poll timeout before UNKNOWN
    poll_interval_s: float = 5.0        # order-status poll spacing

    def to_dict(self) -> Dict[str, Any]:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}


class LiveRiskManager:
    """
    Pre-trade risk gate for LIVE orders plus durable daily counters.

    Daily counters persist under <state_dir>/live/daily_state.json so a
    restart cannot reset the entries/day or loss accounting. The file is
    written through the same tmp+fsync+replace path as PAPER state.
    """

    def __init__(self, config: LiveRiskConfig, state_dir: Optional[str] = None):
        self.config = config
        self._state_dir = state_dir
        self._lock = threading.RLock()
        self._consecutive_broker_errors = 0
        self._breaker_tripped_at: Optional[datetime] = None
        self._kill_switch = False
        self._daily = self._load_daily()

    # ── durable daily state ─────────────────────────────────────────
    def _daily_path(self) -> Optional[str]:
        if not self._state_dir:
            return None
        import os
        return os.path.join(self._state_dir, "live", "daily_state.json")

    def _load_daily(self) -> Dict[str, Any]:
        today = date.today().isoformat()
        default = {'date': today, 'entries': 0, 'realized_pnl': 0.0}
        path = self._daily_path()
        if not path:
            return default
        try:
            from .state import IntradayStateStore
            data = IntradayStateStore()._read_json(path)
            if isinstance(data, dict) and data.get('date') == today:
                return {
                    'date': today,
                    'entries': int(data.get('entries', 0)),
                    'realized_pnl': float(data.get('realized_pnl', 0.0)),
                }
        except Exception as e:
            logger.error(f"[LIVE RISK] Daily state load failed: {e}")
        return default

    def _save_daily(self):
        path = self._daily_path()
        if not path:
            return
        try:
            import os
            os.makedirs(os.path.dirname(path), exist_ok=True)
            from .state import IntradayStateStore
            IntradayStateStore()._write_json(path, dict(self._daily))
        except Exception as e:
            logger.error(f"[LIVE RISK] Daily state save failed: {e}")

    def record_entry(self):
        with self._lock:
            if self._daily['date'] != date.today().isoformat():
                self._daily = self._load_daily()
            self._daily['entries'] += 1
            self._save_daily()

    def record_realized(self, pnl: float):
        with self._lock:
            if self._daily['date'] != date.today().isoformat():
                self._daily = self._load_daily()
            self._daily['realized_pnl'] = round(
                self._daily['realized_pnl'] + float(pnl), 4)
            self._save_daily()

    # ── circuit breaker ─────────────────────────────────────────────
    def record_broker_error(self, what: str):
        with self._lock:
            self._consecutive_broker_errors += 1
            if (self._consecutive_broker_errors >=
                    max(1, self.config.max_broker_errors)
                    and self._breaker_tripped_at is None):
                self._breaker_tripped_at = datetime.now()
                logger.error(
                    f"[LIVE RISK] Broker-error circuit breaker TRIPPED "
                    f"after {self._consecutive_broker_errors} failures: {what}")

    def record_broker_success(self):
        with self._lock:
            self._consecutive_broker_errors = 0
            self._breaker_tripped_at = None

    def breaker_tripped(self) -> bool:
        return self._breaker_tripped_at is not None

    def set_kill_switch(self, active: bool):
        self._kill_switch = bool(active)

    def is_killed(self) -> bool:
        return self._kill_switch

    # ── time gates ──────────────────────────────────────────────────
    def in_entry_window(self, now: Optional[datetime] = None) -> bool:
        now = now or datetime.now(IST)
        if now.tzinfo is None:
            now = IST.localize(now)
        return now.time() <= _parse_hhmm(self.config.entry_cutoff, time(14, 30))

    def past_exit_deadline(self, now: Optional[datetime] = None) -> bool:
        now = now or datetime.now(IST)
        if now.tzinfo is None:
            now = IST.localize(now)
        return now.time() >= _parse_hhmm(self.config.force_exit_by, time(15, 5))

    # ── the gate ────────────────────────────────────────────────────
    def preflight_entry(
        self,
        symbol: str,
        direction: str,
        quantity: int,
        expected_price: float,
        quote_ltp: Optional[float],
        stop_loss: float,
        open_positions: List[Dict[str, Any]],
        available_funds: Optional[float],
        open_unrealized: float = 0.0,
        reserved_entries: int = 0,
        now: Optional[datetime] = None,
    ) -> Tuple[bool, str]:
        """
        Full pre-entry risk gate. Returns (allowed, reason). Every missing
        or zero-valued control denies — LIVE defaults fail closed.
        """
        with self._lock:
            cfg = self.config
            if not cfg.enabled:
                return False, "LIVE risk controls disabled"
            if self._kill_switch:
                return False, "LIVE kill switch active"
            if self.breaker_tripped():
                return False, "Broker-error circuit breaker tripped"
            if not self.in_entry_window(now):
                return False, f"Entry cutoff passed ({cfg.entry_cutoff})"
            if self._daily['date'] != date.today().isoformat():
                self._daily = self._load_daily()
            if cfg.max_entries_per_day <= 0:
                return False, "LIVE max_entries_per_day not configured"
            if (self._daily['entries'] + reserved_entries
                    >= cfg.max_entries_per_day):
                return False, "LIVE daily entry limit reached"
            if cfg.max_concurrent_positions <= 0:
                return False, "LIVE max_concurrent_positions not configured"
            if len(open_positions) >= cfg.max_concurrent_positions:
                return False, "LIVE concurrent position limit reached"
            if any(p.get('symbol') == symbol for p in open_positions):
                return False, f"LIVE position already open for {symbol}"
            if quantity <= 0 or expected_price <= 0:
                return False, "Invalid quantity or price"
            if cfg.max_order_qty > 0 and quantity > cfg.max_order_qty:
                return False, f"Quantity {quantity} exceeds max {cfg.max_order_qty}"

            order_value = quantity * expected_price
            if cfg.max_order_value <= 0:
                return False, "LIVE max_order_value not configured"
            if order_value > cfg.max_order_value:
                return False, (
                    f"Order value ₹{order_value:.2f} exceeds "
                    f"max ₹{cfg.max_order_value:.2f}")

            # Per-trade loss at the stop
            per_trade_loss = quantity * abs(expected_price - stop_loss)
            if cfg.max_per_trade_loss <= 0:
                return False, "LIVE max_per_trade_loss not configured"
            if per_trade_loss > cfg.max_per_trade_loss:
                return False, (
                    f"Per-trade loss ₹{per_trade_loss:.2f} exceeds "
                    f"max ₹{cfg.max_per_trade_loss:.2f}")

            # Gross exposure across existing + new positions
            exposure = sum(
                abs(float(p.get('quantity', 0)) * float(p.get('entry_price', 0)))
                for p in open_positions) + order_value
            if cfg.max_gross_exposure <= 0:
                return False, "LIVE max_gross_exposure not configured"
            if exposure > cfg.max_gross_exposure:
                return False, (
                    f"Gross exposure ₹{exposure:.2f} exceeds "
                    f"max ₹{cfg.max_gross_exposure:.2f}")

            # No leverage by default: notional must fit allocated capital
            if not cfg.allow_leverage:
                if cfg.capital <= 0:
                    return False, "LIVE capital not configured"
                if order_value > cfg.capital:
                    return False, (
                        f"Order value ₹{order_value:.2f} exceeds allocated "
                        f"capital ₹{cfg.capital:.2f} (leverage disabled)")
                if available_funds is not None and order_value > available_funds:
                    return False, (
                        f"Insufficient broker funds ₹{available_funds:.2f} "
                        f"for order value ₹{order_value:.2f}")

            # Daily loss limit (realized + open unrealized)
            if cfg.max_daily_loss <= 0:
                return False, "LIVE max_daily_loss not configured"
            day_pnl = self._daily['realized_pnl'] + min(open_unrealized, 0.0)
            if day_pnl <= -cfg.max_daily_loss:
                return False, (
                    f"LIVE daily loss limit reached "
                    f"(₹{day_pnl:.2f} <= -₹{cfg.max_daily_loss:.2f})")

            # Spread/slippage guard — signal price must track the live quote
            if quote_ltp is None or quote_ltp <= 0:
                return False, "No usable quote for slippage guard"
            slip = abs(expected_price - quote_ltp) / quote_ltp * 100.0
            if cfg.max_slippage_pct <= 0:
                return False, "LIVE max_slippage_pct not configured"
            if slip > cfg.max_slippage_pct:
                return False, (
                    f"Slippage {slip:.3f}% exceeds "
                    f"max {cfg.max_slippage_pct}%")

            return True, ""

    def get_status(self, open_positions: Optional[List] = None) -> Dict[str, Any]:
        with self._lock:
            if self._daily['date'] != date.today().isoformat():
                self._daily = self._load_daily()
            return {
                'enabled': self.config.enabled,
                'kill_switch': self._kill_switch,
                'breaker_tripped': self.breaker_tripped(),
                'consecutive_broker_errors': self._consecutive_broker_errors,
                'entries_today': self._daily['entries'],
                'max_entries_per_day': self.config.max_entries_per_day,
                'realized_pnl': self._daily['realized_pnl'],
                'max_daily_loss': self.config.max_daily_loss,
                'open_positions': len(open_positions or []),
                'config': self.config.to_dict(),
            }
