"""
NSE Trading Calendar — single source of truth.

Shared by the Swing LIVE engine (MarketDataFetcher / orchestrator
_is_trading_day) and the Angel intraday PAPER validator.

Source: NSE Capital Market trading-holidays circular (ref. 1074/2024,
Dec 2025) https://www.nseindia.com/resources/exchange-communication-holidays
Plus the declared special closure on 2026-01-15 (Municipal Corporation
Election, Maharashtra).

Static and offline by design — no network lookup anywhere. Years not
covered by the list FAIL CLOSED (treated as non-trading days) rather
than assuming every weekday is tradable; update annually.
"""
import logging
from datetime import datetime, date, time
from typing import Optional, Set

import pytz

logger = logging.getLogger(__name__)

IST = pytz.timezone("Asia/Kolkata")

# ── Declared NSE trading holidays (weekday closures) ─────────────────
NSE_TRADING_HOLIDAYS: Set[str] = frozenset({
    # 2025
    "2025-01-26", "2025-02-26", "2025-03-14", "2025-03-31",
    "2025-04-10", "2025-04-14", "2025-04-18", "2025-05-01",
    "2025-08-15", "2025-08-27", "2025-10-02", "2025-10-20",
    "2025-10-24", "2025-11-05", "2025-12-25",
    # 2026 — NSE circular 1074/2024 + election closure
    "2026-01-15",   # Thu — Municipal Corporation Election (Maharashtra)
    "2026-01-26",   # Mon — Republic Day
    "2026-03-03",   # Tue — Holi
    "2026-03-26",   # Thu — Shri Ram Navami
    "2026-03-31",   # Tue — Shri Mahavir Jayanti
    "2026-04-03",   # Fri — Good Friday
    "2026-04-14",   # Tue — Dr. B. R. Ambedkar Jayanti
    "2026-05-01",   # Fri — Maharashtra Day
    "2026-05-28",   # Thu — Bakri Id
    "2026-06-26",   # Fri — Muharram
    "2026-09-14",   # Mon — Ganesh Chaturthi
    "2026-10-02",   # Fri — Mahatma Gandhi Jayanti
    "2026-10-20",   # Tue — Dussehra
    "2026-11-10",   # Tue — Diwali-Balipratipada
    "2026-11-24",   # Tue — Prakash Gurpurb Sri Guru Nanak Dev
    "2026-12-25",   # Fri — Christmas
})

# Holidays falling on a weekend — recorded for completeness; they never
# change is_trading_day() (weekend check already excludes them):
#   2026-02-15 Sun Mahashivratri · 2026-03-21 Sat Id-ul-Fitr
#   2026-08-15 Sat Independence Day · 2026-11-08 Sun Diwali Laxmi Pujan

# Special sessions — NOT ordinary trading days. Muhurat trading runs on
# a Sunday evening by separate notification; recording it here must not
# enable regular Sunday trading.
MUHURAT_SESSIONS: Set[str] = frozenset({"2026-11-08"})

# Years the calendar authoritatively covers — derived from the list.
_CALENDAR_YEARS = {int(d[:4]) for d in NSE_TRADING_HOLIDAYS}


def _ist_now() -> datetime:
    return datetime.now(IST)


def is_trading_day(now: Optional[datetime] = None) -> bool:
    """True only on NSE trading days (Mon–Fri, non-holiday, covered year).

    Fails closed for calendar years outside the list — a year not yet
    published is treated as non-trading until the calendar is updated,
    so a stale list can never silently assume every weekday is tradable.
    """
    now = now or _ist_now()
    if now.tzinfo is None:
        now = IST.localize(now)
    if now.weekday() >= 5:
        return False
    if now.year not in _CALENDAR_YEARS:
        logger.warning(
            f"[CALENDAR] {now:%Y-%m-%d} outside covered years "
            f"{sorted(_CALENDAR_YEARS)} — failing closed (non-trading)")
        return False
    return now.strftime("%Y-%m-%d") not in NSE_TRADING_HOLIDAYS


def is_holiday(now: Optional[datetime] = None) -> bool:
    """True if the date is a DECLARED NSE holiday (weekends excluded —
    a Saturday is a non-trading day but not a 'holiday')."""
    now = now or _ist_now()
    if now.tzinfo is None:
        now = IST.localize(now)
    return now.strftime("%Y-%m-%d") in NSE_TRADING_HOLIDAYS


def is_special_session(now: Optional[datetime] = None) -> bool:
    """True on announced special sessions (e.g. Muhurat trading). Does
    NOT make the day an ordinary trading day — special sessions have
    their own timings and must be handled explicitly."""
    now = now or _ist_now()
    if now.tzinfo is None:
        now = IST.localize(now)
    return now.strftime("%Y-%m-%d") in MUHURAT_SESSIONS


# Regular session bounds (NSE Capital Market)
MARKET_OPEN_T = time(9, 15)
MARKET_CLOSE_T = time(15, 30)
