"""
economic_events.py — Economic Event Risk Engine
Tracks scheduled high-impact macro events (RBI, Fed, Budget, Elections, GDP, CPI)
and translates them into position-sizing and trading gates.
"""
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


HIGH_IMPACT_EVENTS = {
    "RBI Policy",
    "Fed Meeting",
    "Budget",
    "Election",
    "GDP",
    "CPI",
}

_UNSET = object()


class EconomicEventRiskEngine:
    """
    Monitors the economic event calendar and returns:
        - Whether a high-impact event is within 6h (block new BUYs)
        - Whether one is within 24h (halve position size)
    """

    def __init__(self, store: Any = _UNSET):
        if store is _UNSET:
            try:
                from persistence import get_store

                store = get_store()
            except Exception:
                store = None
        self.store = store
        self._events: List[Dict[str, Any]] = []

    def add_event(
        self,
        event_type: str,
        event_time: str,
        impact: str = "HIGH",
        description: str = "",
    ) -> Dict[str, Any]:
        """Add a new economic event to the calendar."""
        event = {
            "timestamp": event_time,
            "event_type": event_type,
            "event_date": event_time[:10],
            "impact": impact,
            "description": description,
        }
        self._events.append(event)
        if self.store is not None:
            try:
                self.store.save_economic_event(event)
            except Exception as e:
                logger.warning(f"Could not persist economic event: {e}")
        return event

    def get_upcoming_events(self, now: Optional[datetime] = None) -> List[Dict[str, Any]]:
        """Return high-impact events that are in the future."""
        if now is None:
            now = datetime.now()
        try:
            if self.store is not None:
                stored = self.store.get_upcoming_economic_events(now.isoformat())
            else:
                stored = []
        except Exception as e:
            logger.warning(f"Could not load economic events: {e}")
            stored = []

        # Merge with in-memory events (handy for tests and manual use)
        all_events = list(self._events) + stored
        # De-duplicate by timestamp+event_type
        seen = set()
        unique = []
        for ev in all_events:
            key = (ev.get("timestamp"), ev.get("event_type"))
            if key in seen:
                continue
            seen.add(key)
            try:
                et = datetime.fromisoformat(ev["timestamp"])
            except Exception:
                continue
            if et >= now:
                unique.append(ev)
        return sorted(unique, key=lambda x: x["timestamp"])

    def risk_status(self, now: Optional[datetime] = None) -> Dict[str, Any]:
        """
        Evaluate the current economic event risk.

        Returns:
            - no_new_buy:   high-impact event within 6h
            - reduce_size:  high-impact event within 24h
            - size_factor:  0.5 if reduce_size else 1.0
            - next_event:   the closest upcoming high-impact event (if any)
            - reason:       human-readable risk summary
        """
        if now is None:
            now = datetime.now()

        events = self.get_upcoming_events(now)
        if not events:
            return {
                "no_new_buy": False,
                "reduce_size": False,
                "size_factor": 1.0,
                "next_event": None,
                "reason": "No upcoming high-impact events",
            }

        # Find the closest high-impact event
        closest: Optional[Dict[str, Any]] = None
        closest_delta: Optional[timedelta] = None

        for ev in events:
            if ev.get("impact", "").upper() != "HIGH":
                continue
            try:
                et = datetime.fromisoformat(ev["timestamp"])
            except Exception:
                continue
            delta = et - now
            if delta.total_seconds() < -60:
                # Event is already in the past (allow a 60s grace)
                continue
            if closest_delta is None or delta < closest_delta:
                closest = ev
                closest_delta = delta

        if closest is None or closest_delta is None:
            return {
                "no_new_buy": False,
                "reduce_size": False,
                "size_factor": 1.0,
                "next_event": None,
                "reason": "No upcoming high-impact events",
            }

        hours_to_event = closest_delta.total_seconds() / 3600.0
        no_new_buy = hours_to_event <= 6
        reduce_size = hours_to_event <= 24

        reason = f"{closest['event_type']} in {hours_to_event:.1f}h"
        if no_new_buy:
            reason += " — no new BUY positions"
        elif reduce_size:
            reason += " — position size reduced 50%"

        return {
            "no_new_buy": no_new_buy,
            "reduce_size": reduce_size,
            "size_factor": 0.5 if reduce_size else 1.0,
            "next_event": closest,
            "hours_to_event": round(hours_to_event, 2),
            "reason": reason,
        }
