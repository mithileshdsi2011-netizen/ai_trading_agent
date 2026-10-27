"""
Intraday Entry Validation Module

Hard deterministic validation rules for intraday trade entries.
No trade may bypass these checks — AI/LLM has no order authority.
Completely separate from Swing Trading validation.
"""
import logging
from datetime import datetime, time
from typing import Optional, Tuple

import pytz

from .models import IntradayConfig, IntradaySignal, SignalClassification

logger = logging.getLogger(__name__)

IST = pytz.timezone("Asia/Kolkata")

# NSE regular market session
MARKET_OPEN = time(9, 15)
MARKET_CLOSE = time(15, 30)


def _parse_hhmm(value: str, fallback: time) -> time:
    """Parse a HH:MM string into a time, falling back safely."""
    try:
        hh, mm = value.split(":")
        return time(int(hh), int(mm))
    except Exception:
        return fallback


def ist_now() -> datetime:
    """Current time in IST."""
    return datetime.now(IST)


# ── Shared execution-quote policy ────────────────────────────────────
# One validation chain for EVERY quote that will drive a simulated paper
# execution — new entries, automated SL/target exits and EOD square-off.
# A quote is usable only when:
#   - it carries an ltp
#   - its symbol (when supplied) matches the target symbol ('-EQ' suffix ok)
#   - its exchange (when supplied) is NSE
#   - a local receipt timestamp exists and is 0-30s old (future rejected)
#   - an exchange timestamp, when supplied, is 0-30s old (fresh-receipt
#     plus stale exchange stamp is NOT a fresh valid quote)
# VERIFIED provenance ≠ an executable broker fill — paper exits only ever
# simulate against this chain; nothing here asserts a real fill occurred.
EXEC_QUOTE_MAX_AGE_S = 30.0


def quote_usable_for_execution(quote, symbol: str,
                               receipt_time: Optional[datetime],
                               now: Optional[datetime] = None,
                               max_age_s: float = EXEC_QUOTE_MAX_AGE_S
                               ) -> Tuple[bool, Optional[str]]:
    """Return (usable, reason) for a quote about to drive a paper execution."""
    now = now or datetime.now()
    if not quote or quote.get('ltp') in (None, 0):
        return False, "missing ltp"
    qs = quote.get('trading_symbol') or quote.get('symbol')
    if qs and qs.split('-')[0] != symbol:
        return False, f"symbol mismatch ({qs} != {symbol})"
    exch = quote.get('exchange')
    if exch and exch != 'NSE':
        return False, f"exchange mismatch ({exch})"
    if receipt_time is None or not isinstance(receipt_time, datetime):
        return False, "missing quote receipt timestamp"
    try:
        age = (now - receipt_time).total_seconds()
    except Exception:
        return False, "unparseable receipt timestamp"
    if not (0 <= age <= max_age_s):
        return False, f"receipt age {age:.1f}s outside 0-{max_age_s:.0f}s"
    exch_ts = quote.get('exchange_ts')
    if exch_ts is not None:
        if not isinstance(exch_ts, datetime):
            return False, "unparseable exchange timestamp"
        eage = (now - exch_ts).total_seconds()
        if not (0 <= eage <= max_age_s):
            return False, f"exchange timestamp age {eage:.1f}s outside 0-{max_age_s:.0f}s"
    return True, None


class IntradayValidator:
    """Hard entry validation for intraday signals."""

    def __init__(self, config: IntradayConfig):
        self.config = config

    def is_market_open(self, now: Optional[datetime] = None) -> bool:
        """NSE regular session: trading days 09:15-15:30 IST.

        Includes the NSE holiday check — a weekday holiday is not a
        session, so no scans/entries run against stale non-session data."""
        now = now or ist_now()
        if now.tzinfo is None:
            now = IST.localize(now)
        if not self.is_trading_day(now):
            return False
        return MARKET_OPEN <= now.time() <= MARKET_CLOSE

    def is_entry_window_open(self, now: Optional[datetime] = None) -> bool:
        """New entries only between entry_start_time and last_entry_time."""
        now = now or ist_now()
        if now.tzinfo is None:
            now = IST.localize(now)
        start = _parse_hhmm(self.config.entry_start_time, MARKET_OPEN)
        last = _parse_hhmm(self.config.last_entry_time, time(14, 30))
        return start <= now.time() <= last

    def is_square_off_time(self, now: Optional[datetime] = None) -> bool:
        """True once the mandatory square-off deadline has passed."""
        now = now or ist_now()
        if now.tzinfo is None:
            now = IST.localize(now)
        square_off = _parse_hhmm(self.config.square_off_time, time(15, 0))
        return now.time() >= square_off

    def is_trading_day(self, now: Optional[datetime] = None) -> bool:
        """True only on NSE trading days (Mon–Fri, non-holiday).

        Uses the same authoritative static NSE holiday list the Swing
        LIVE orchestrator trusts (MarketDataFetcher._NSE_HOLIDAYS) —
        offline, no network dependency. If the calendar import fails,
        FAILS CLOSED: not-a-trading-day is the safe answer, since the
        worst case is merely deferring work, never exiting on stale
        quotes."""
        now = now or ist_now()
        if now.tzinfo is None:
            now = IST.localize(now)
        if now.weekday() >= 5:
            return False
        try:
            from trading_calendar import is_trading_day
            return is_trading_day(now)
        except Exception as e:
            logger.warning(
                f"[INTRADAY] NSE trading calendar unavailable ({e!r}) — "
                f"failing closed (not a trading day)")
            return False

    def is_square_off_window(self, now: Optional[datetime] = None) -> bool:
        """Square-off may only execute inside the EOD window ON A TRADING
        DAY. Weekends/holidays: overnight positions stay flagged and are
        recovered inside the next valid session — never exited on stale
        non-session quotes."""
        now = now or ist_now()
        if now.tzinfo is None:
            now = IST.localize(now)
        return self.is_trading_day(now) and self.is_square_off_time(now)

    def is_data_fresh(self, last_data_time: Optional[datetime]) -> bool:
        """Market data must be no older than max_data_age_seconds."""
        if last_data_time is None:
            return False
        age = (datetime.now() - last_data_time).total_seconds()
        return age <= self.config.max_data_age_seconds

    def validate_signal(self, signal: IntradaySignal) -> Tuple[bool, Optional[str]]:
        """
        Validate signal structure: stop loss, target, R:R, classification.

        Returns:
            (valid, rejection_reason)
        """
        # Classification gate
        if signal.classification == SignalClassification.NO_TRADE:
            return False, "Signal classified as NO_TRADE"

        # Mandatory stop loss — intraday trades MUST have a stop
        if not signal.stop_loss or signal.stop_loss <= 0:
            return False, "Missing or invalid stop loss"

        # Mandatory target
        if not signal.target or signal.target <= 0:
            return False, "Missing or invalid target"

        # Stop must be on the correct side of entry
        if signal.direction.value == "LONG":
            if signal.stop_loss >= signal.entry_price:
                return False, "Stop loss must be below entry for LONG"
            if signal.target <= signal.entry_price:
                return False, "Target must be above entry for LONG"
        else:
            if signal.stop_loss <= signal.entry_price:
                return False, "Stop loss must be above entry for SHORT"
            if signal.target >= signal.entry_price:
                return False, "Target must be below entry for SHORT"

        # Minimum risk:reward
        if signal.risk_reward < self.config.min_risk_reward:
            return False, (
                f"Risk:Reward {signal.risk_reward:.2f} below minimum "
                f"{self.config.min_risk_reward}"
            )

        # Volume confirmation
        if signal.relative_volume < 1.0:
            return False, f"Weak volume confirmation (rel vol {signal.relative_volume:.2f})"

        return True, None

    def validate_entry_conditions(
        self,
        signal: IntradaySignal,
        last_data_time: Optional[datetime],
        now: Optional[datetime] = None,
    ) -> Tuple[bool, Optional[str]]:
        """
        Validate environment-level entry conditions.

        Returns:
            (valid, rejection_reason)
        """
        # Market hours
        if not self.is_market_open(now):
            return False, "Outside market hours"

        # Entry window (respects last_entry_time)
        if not self.is_entry_window_open(now):
            return False, "Outside entry window"

        # Fresh market data — never trade on stale prices
        if not self.is_data_fresh(last_data_time):
            return False, "Market data stale or unavailable"

        # Structural signal validation
        return self.validate_signal(signal)


def signal_idempotency_key(signal: IntradaySignal) -> str:
    """
    Build an idempotency key for a signal so a scanner running repeatedly
    cannot generate duplicate entries for the same setup.
    """
    day = ist_now().date().isoformat()
    return f"{signal.symbol}:{signal.direction.value}:{signal.strategy}:{day}"
