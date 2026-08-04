"""
options_intelligence.py — Options Chain Intelligence Engine
Derives option-flow signals (PCR, Max Pain, OI build-up, OI support) from NSE
option-chain data to boost AI confidence.
"""
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

import pandas as pd

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class OptionsIntelligenceEngine:
    """
    Options chain intelligence for the enterprise trading bot.

    Outputs:
        - PCR (put/call open interest ratio)
        - Max Pain strike
        - Long Build-up / Short Build-up flags
        - OI Build-up trend
        - Strong OI Support flag
        - Confidence boost suggestion
    """

    def __init__(
        self,
        symbol: str = "NIFTY",
        store: Optional[Any] = None,
        market_data: Optional[Any] = None,
    ):
        self.symbol = symbol
        self.market_data = market_data
        self._latest: Optional[Dict[str, Any]] = None

        if store is None:
            try:
                from persistence import get_store

                store = get_store()
            except Exception:
                store = None
        self.store = store

    def fetch_nse_option_chain(self) -> Optional[Dict[str, Any]]:
        """Fetch NSE option chain for the configured symbol."""
        try:
            import requests

            url = f"https://www.nseindia.com/api/option-chain-indices?symbol={self.symbol.upper()}"
            headers = {
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36",
                "Accept": "application/json",
                "Accept-Language": "en-US,en;q=0.9",
            }

            session = requests.Session()
            # Prime NSE cookies
            session.get("https://www.nseindia.com", headers=headers, timeout=20)
            r = session.get(url, headers=headers, timeout=20)
            r.raise_for_status()
            return r.json()

        except Exception as e:
            logger.debug(f"Options chain NSE fetch failed for {self.symbol}: {e}")
            return None

    def ingest(self, raw_data: Dict[str, Any]) -> Dict[str, Any]:
        """Parse a raw NSE option-chain response and store the snapshot."""
        snapshot = self._parse(raw_data)
        self._latest = snapshot
        self._persist_snapshot(snapshot)
        return snapshot

    def compute(self, refresh: bool = False) -> Dict[str, Any]:
        """Compute options intelligence, fetching if needed."""
        if self._latest is not None and not refresh:
            return self._latest

        raw = self.fetch_nse_option_chain()
        if raw is None:
            snap = self._load_from_store()
            if snap is None:
                snap = self._neutral_snapshot("No options chain data available")
            self._latest = snap
            return snap

        snap = self._parse(raw)
        self._latest = snap
        self._persist_snapshot(snap)
        return snap

    def _parse(self, raw: Dict[str, Any]) -> Dict[str, Any]:
        records = raw.get("records", {})
        data = records.get("data", [])
        spot = float(records.get("underlyingValue", 0) or 0)
        timestamp = records.get("timestamp") or datetime.now().isoformat()

        if not data or not spot:
            return self._neutral_snapshot("Malformed options chain data")

        calls: List[Dict[str, float]] = []
        puts: List[Dict[str, float]] = []

        for row in data:
            strike = float(row.get("strikePrice", 0))
            ce = row.get("CE") or {}
            pe = row.get("PE") or {}

            call_oi = float(ce.get("openInterest", 0) or 0)
            call_oi_change = float(ce.get("changeinOpenInterest", 0) or 0)
            put_oi = float(pe.get("openInterest", 0) or 0)
            put_oi_change = float(pe.get("changeinOpenInterest", 0) or 0)

            calls.append({
                "strike": strike,
                "oi": call_oi,
                "oi_change": call_oi_change,
            })
            puts.append({
                "strike": strike,
                "oi": put_oi,
                "oi_change": put_oi_change,
            })

        total_call_oi = sum(c["oi"] for c in calls)
        total_put_oi = sum(p["oi"] for p in puts)
        total_call_oi_change = sum(c["oi_change"] for c in calls)
        total_put_oi_change = sum(p["oi_change"] for p in puts)

        pcr = round(total_put_oi / total_call_oi, 2) if total_call_oi > 0 else 1.0

        # Max pain: the strike where the least total money is lost by writers
        strikes = sorted({c["strike"] for c in calls}.union({p["strike"] for p in puts}))
        min_pain = float("inf")
        max_pain_strike = 0.0
        for s in strikes:
            pain = 0.0
            for c in calls:
                pain += max(0.0, s - c["strike"]) * c["oi"]
            for p in puts:
                pain += max(0.0, p["strike"] - s) * p["oi"]
            if pain < min_pain:
                min_pain = pain
                max_pain_strike = s

        # OI build-up: net change in puts vs calls
        oi_build_up = total_put_oi_change - total_call_oi_change
        long_buildup = total_put_oi_change > 0 and total_put_oi_change > total_call_oi_change
        short_buildup = total_call_oi_change > 0 and total_call_oi_change > total_put_oi_change

        # Strong OI support: put wall within 3% of spot
        support_strike = 0.0
        support_oi = 0.0
        if puts:
            support_strike = max(puts, key=lambda p: p["oi"])["strike"]
            support_oi = next(p["oi"] for p in puts if p["strike"] == support_strike)
        strong_oi_support = (
            support_strike > 0
            and abs(spot - support_strike) / support_strike <= 0.03
        )

        # Confidence boost: +5% per bullish OI signal
        confidence_boost = 0.0
        if pcr > 1.0:
            confidence_boost += 5.0
        if long_buildup:
            confidence_boost += 5.0
        if strong_oi_support:
            confidence_boost += 5.0

        return {
            "timestamp": timestamp,
            "symbol": self.symbol,
            "spot": round(spot, 2),
            "pcr": pcr,
            "max_pain": round(max_pain_strike, 2),
            "oi_build_up": round(oi_build_up, 2),
            "long_buildup": long_buildup,
            "short_buildup": short_buildup,
            "put_wall_strike": round(support_strike, 2),
            "put_wall_oi": round(support_oi, 2),
            "strong_oi_support": strong_oi_support,
            "confidence_boost": round(confidence_boost, 2),
            "total_call_oi": round(total_call_oi, 2),
            "total_put_oi": round(total_put_oi, 2),
        }

    def _neutral_snapshot(self, reason: str) -> Dict[str, Any]:
        now = datetime.now().isoformat()
        return {
            "timestamp": now,
            "symbol": self.symbol,
            "spot": 0.0,
            "pcr": 1.0,
            "max_pain": 0.0,
            "oi_build_up": 0.0,
            "long_buildup": False,
            "short_buildup": False,
            "put_wall_strike": 0.0,
            "put_wall_oi": 0.0,
            "strong_oi_support": False,
            "confidence_boost": 0.0,
            "total_call_oi": 0.0,
            "total_put_oi": 0.0,
            "reason": reason,
        }

    def _load_from_store(self) -> Optional[Dict[str, Any]]:
        if self.store is None:
            return None
        try:
            return self.store.get_latest_options_intelligence()
        except Exception as e:
            logger.debug(f"Could not load options intelligence from store: {e}")
            return None

    def _persist_snapshot(self, snapshot: Dict[str, Any]) -> None:
        if self.store is None:
            return
        try:
            self.store.save_options_intelligence(snapshot)
        except Exception as e:
            logger.warning(f"Could not persist options intelligence snapshot: {e}")

    def latest(self) -> Optional[Dict[str, Any]]:
        if self._latest is not None:
            return self._latest
        return self._load_from_store()

    def adjust_confidence(self, confidence: float) -> Dict[str, Any]:
        """Apply options-derived confidence boost."""
        snap = self.latest() or self.compute()
        boost = float(snap.get("confidence_boost", 0.0))
        adjusted = max(0.0, min(100.0, confidence + boost))
        return {
            "pcr": snap.get("pcr", 1.0),
            "max_pain": snap.get("max_pain", 0.0),
            "long_buildup": snap.get("long_buildup", False),
            "short_buildup": snap.get("short_buildup", False),
            "strong_oi_support": snap.get("strong_oi_support", False),
            "confidence_boost": boost,
            "adjusted_confidence": round(adjusted, 2),
            "timestamp": snap.get("timestamp"),
        }
