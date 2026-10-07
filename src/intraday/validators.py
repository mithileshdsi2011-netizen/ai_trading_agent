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


class IntradayValidator:
    """Hard entry validation for intraday signals."""

    def __init__(self, config: IntradayConfig):
        self.config = config

    def is_market_open(self, now: Optional[datetime] = None) -> bool:
        """NSE regular session: Mon-Fri 09:15-15:30 IST."""
        now = now or ist_now()
        if now.tzinfo is None:
            now = IST.localize(now)
        if now.weekday() >= 5:
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
