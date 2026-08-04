"""
fii_dii.py — FII / DII Institutional Flow Intelligence Engine
Tracks FII and DII buy/sell activity and translates net flow into an AI score adjustment.
"""
import logging
from datetime import datetime, date
from typing import Any, Dict, Optional

import pandas as pd

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class FII_DII_Engine:
    """
    FII/DII flow intelligence.

    Output metrics:
        - fii_buy, fii_sell, fii_net
        - dii_buy, dii_sell, dii_net
        - net_flow (fii_net + dii_net)
        - sentiment: POSITIVE / NEGATIVE / NEUTRAL

    AI score adjustment:
        Net Positive -> +3
        Net Negative -> -3
        Net Neutral  -> 0
    """

    DEFAULT_RISK_FACTOR = 0

    def __init__(self, store: Optional[Any] = None, market_data: Optional[Any] = None):
        self.market_data = market_data
        self._latest: Optional[Dict[str, Any]] = None

        if store is None:
            try:
                from persistence import get_store

                store = get_store()
            except Exception:
                store = None
        self.store = store

    def fetch_nse(self) -> Optional[Dict[str, Any]]:
        """
        Fetch live FII/DII trade statistics from NSE via nsepython.
        Returns None if the fetch fails or the format is unexpected.
        """
        try:
            from nsepython import nse_fiidii

            df = nse_fiidii()
            if df is None or df.empty:
                return None

            fii_buy = fii_sell = dii_buy = dii_sell = 0.0
            for row in df.to_dict("records"):
                cat = str(row.get("category", "")).upper().replace(" ", "")
                buy = float(row.get("buyValue", 0) or 0)
                sell = float(row.get("sellValue", 0) or 0)
                if "FII" in cat:
                    fii_buy += buy
                    fii_sell += sell
                elif "DII" in cat:
                    dii_buy += buy
                    dii_sell += sell

            as_of = str(df.iloc[0].get("date", "")) if not df.empty else None
            return self._build_snapshot(fii_buy, fii_sell, dii_buy, dii_sell, as_of)

        except Exception as e:
            logger.warning(f"FII/DII NSE fetch failed: {e}")
            return None

    def ingest(
        self,
        fii_buy: float,
        fii_sell: float,
        dii_buy: float,
        dii_sell: float,
        as_of: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Manually ingest a FII/DII snapshot and persist it."""
        snapshot = self._build_snapshot(fii_buy, fii_sell, dii_buy, dii_sell, as_of)
        self._latest = snapshot
        self._persist_snapshot(snapshot)
        return snapshot

    @staticmethod
    def _build_snapshot(
        fii_buy: float,
        fii_sell: float,
        dii_buy: float,
        dii_sell: float,
        as_of: Optional[str] = None,
    ) -> Dict[str, Any]:
        fii_net = round(fii_buy - fii_sell, 2)
        dii_net = round(dii_buy - dii_sell, 2)
        net_flow = round(fii_net + dii_net, 2)

        if net_flow > 0:
            sentiment = "POSITIVE"
        elif net_flow < 0:
            sentiment = "NEGATIVE"
        else:
            sentiment = "NEUTRAL"

        return {
            "timestamp": as_of or datetime.now().isoformat(),
            "date": (as_of or date.today().isoformat())[:10],
            "fii_buy": round(fii_buy, 2),
            "fii_sell": round(fii_sell, 2),
            "dii_buy": round(dii_buy, 2),
            "dii_sell": round(dii_sell, 2),
            "fii_net": fii_net,
            "dii_net": dii_net,
            "net_flow": net_flow,
            "sentiment": sentiment,
        }

    def compute(self, refresh: bool = False) -> Dict[str, Any]:
        """Return latest FII/DII snapshot, fetching if needed."""
        if self._latest is not None and not refresh:
            return self._latest

        snap = self.fetch_nse()
        if snap is None:
            # Try to load from store; otherwise return a neutral snapshot
            snap = self._load_from_store()
            if snap is None:
                snap = self._neutral_snapshot("No FII/DII data available")

        self._latest = snap
        self._persist_snapshot(snap)
        return snap

    def _neutral_snapshot(self, reason: str) -> Dict[str, Any]:
        now = datetime.now().isoformat()
        return {
            "timestamp": now,
            "date": date.today().isoformat(),
            "fii_buy": 0.0,
            "fii_sell": 0.0,
            "dii_buy": 0.0,
            "dii_sell": 0.0,
            "fii_net": 0.0,
            "dii_net": 0.0,
            "net_flow": 0.0,
            "sentiment": "NEUTRAL",
            "reason": reason,
        }

    def _load_from_store(self) -> Optional[Dict[str, Any]]:
        if self.store is None:
            return None
        try:
            return self.store.get_latest_fii_dii()
        except Exception as e:
            logger.debug(f"Could not load FII/DII from store: {e}")
            return None

    def _persist_snapshot(self, snapshot: Dict[str, Any]) -> None:
        if self.store is None:
            return
        try:
            self.store.save_fii_dii(snapshot)
        except Exception as e:
            logger.warning(f"Could not persist FII/DII snapshot: {e}")

    def latest(self) -> Optional[Dict[str, Any]]:
        if self._latest is not None:
            return self._latest
        return self._load_from_store()

    def adjust_ai_score(self, ai_score: float) -> Dict[str, Any]:
        """
        Apply FII/DII flow adjustment.

        Rules:
            Net Positive -> +3
            Net Negative -> -3
        """
        snap = self.latest() or self.compute()
        sentiment = str(snap.get("sentiment", "NEUTRAL"))

        if sentiment == "POSITIVE":
            adjustment = 3
        elif sentiment == "NEGATIVE":
            adjustment = -3
        else:
            adjustment = 0

        adjusted = max(0.0, min(100.0, ai_score + adjustment))
        return {
            "fii_net": snap.get("fii_net", 0.0),
            "dii_net": snap.get("dii_net", 0.0),
            "net_flow": snap.get("net_flow", 0.0),
            "sentiment": sentiment,
            "adjustment": adjustment,
            "adjusted_score": round(adjusted, 2),
            "timestamp": snap.get("timestamp"),
        }
