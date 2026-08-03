"""
vix_risk_engine.py — India VIX Risk Engine
Fetches India VIX and translates it into a position-sizing risk factor.
"""
import logging
from datetime import datetime
from typing import Any, Dict, Optional

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class IndiaVIXRiskEngine:
    """
    India VIX → volatility score → risk factor.

    Risk factor rules:
        VIX < 15          -> 1.0
        15 <= VIX <= 20   -> 0.8
        20 < VIX <= 25    -> 0.6
        VIX > 25          -> 0.3
    """

    INDIA_VIX_SYMBOLS = ["INDIAVIX", "INDIA VIX", "NSE:INDIAVIX"]
    DEFAULT_RISK_FACTOR = 1.0

    def __init__(self, market_data: Optional[Any] = None, store: Optional[Any] = None):
        self.market_data = market_data
        self._latest: Optional[Dict[str, Any]] = None

        if store is None:
            try:
                from persistence import get_store

                store = get_store()
            except Exception:
                store = None
        self.store = store

    def fetch_vix(self) -> Optional[float]:
        """Fetch the latest India VIX value."""
        if not self.market_data:
            try:
                from market_data import MarketDataFetcher

                self.market_data = MarketDataFetcher()
            except Exception:
                return None

        for symbol in self.INDIA_VIX_SYMBOLS:
            try:
                price = self.market_data.get_realtime_price(symbol)
                if price and price > 0:
                    return float(price)
            except Exception:
                continue

        # Fallback: try quote() if get_realtime_price did not work
        try:
            quote = self.market_data.get_stock_info(self.INDIA_VIX_SYMBOLS[0])
            price = quote.get("current_price", 0)
            if price and price > 0:
                return float(price)
        except Exception:
            pass

        return None

    @staticmethod
    def risk_factor_from_vix(vix: float) -> float:
        """Translate VIX into a position-sizing risk factor."""
        if vix < 15:
            return 1.0
        if vix <= 20:
            return 0.8
        if vix <= 25:
            return 0.6
        return 0.3

    @staticmethod
    def risk_level(vix: float) -> str:
        if vix < 15:
            return "LOW"
        if vix <= 20:
            return "MODERATE"
        if vix <= 25:
            return "HIGH"
        return "EXTREME"

    def compute(self, refresh: bool = False) -> Dict[str, Any]:
        """Fetch VIX and compute risk metrics, caching unless refresh=True."""
        if self._latest is not None and not refresh:
            return self._latest

        vix = self.fetch_vix()
        if vix is None:
            self._latest = {
                "timestamp": datetime.now().isoformat(),
                "vix": 0.0,
                "volatility_score": 0.0,
                "risk_factor": self.DEFAULT_RISK_FACTOR,
                "risk_level": "UNKNOWN",
                "reason": "Could not fetch India VIX",
            }
            return self._latest

        risk_factor = self.risk_factor_from_vix(vix)
        risk_level = self.risk_level(vix)
        # 0-100 volatility score: 0 = calm, higher = more volatile
        volatility_score = round(min(100.0, vix * 2.5), 2)

        snapshot = {
            "timestamp": datetime.now().isoformat(),
            "vix": round(vix, 2),
            "volatility_score": volatility_score,
            "risk_factor": risk_factor,
            "risk_level": risk_level,
        }

        self._latest = snapshot
        self._persist_snapshot(snapshot)
        return snapshot

    def _persist_snapshot(self, snapshot: Dict[str, Any]) -> None:
        if self.store is None:
            return
        try:
            self.store.save_vix_risk(snapshot)
        except Exception as e:
            logger.warning(f"Could not persist VIX risk snapshot: {e}")

    def latest(self) -> Optional[Dict[str, Any]]:
        if self._latest is not None:
            return self._latest
        if self.store is None:
            return None
        try:
            snap = self.store.get_latest_vix_risk()
            if snap:
                self._latest = snap
            return snap
        except Exception as e:
            logger.warning(f"Could not load latest VIX risk: {e}")
            return None

    def get_risk_factor(self) -> float:
        """Return the current risk factor, computing it if needed."""
        snap = self.latest() or self.compute()
        return float(snap.get("risk_factor", self.DEFAULT_RISK_FACTOR))
