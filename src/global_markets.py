"""
global_markets.py — Global Market Monitor
Tracks global asset performance and translates it into a sentiment-driven
confidence adjustment for the AI engine.
"""
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

import pandas as pd

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class GlobalMarketMonitor:
    """
    Monitors global markets and returns a 0-100 sentiment score.

    Tracked assets:
        - NASDAQ
        - Dow Jones
        - S&P 500
        - SGX Nifty
        - Brent Crude
        - Gold
        - USD/INR
    """

    # Yahoo Finance style tickers used as defaults
    DEFAULT_ASSETS = {
        "NASDAQ": "^IXIC",
        "Dow Jones": "^DJI",
        "S&P500": "^GSPC",
        "SGX Nifty": "NIFTY",
        "Brent": "BZ=F",
        "Gold": "GC=F",
        "USDINR": "INR=X",
    }

    # Sentiment weights for each asset (sum is positive for risk-on, negative for risk-off)
    WEIGHTS = {
        "NASDAQ": 0.18,
        "Dow Jones": 0.18,
        "S&P500": 0.18,
        "SGX Nifty": 0.20,
        "Brent": 0.08,
        "Gold": -0.03,
        "USDINR": -0.15,
    }

    def __init__(
        self,
        store: Optional[Any] = None,
        market_data: Optional[Any] = None,
        assets: Optional[Dict[str, str]] = None,
    ):
        self.market_data = market_data
        self.assets = assets or dict(self.DEFAULT_ASSETS)
        self._latest: Optional[Dict[str, Any]] = None

        if store is None:
            try:
                from persistence import get_store

                store = get_store()
            except Exception:
                store = None
        self.store = store

    def fetch_yf(self, symbol: str, period: str = "5d") -> Optional[pd.DataFrame]:
        """Try to fetch OHLCV from yfinance. Returns None if not available."""
        try:
            import yfinance as yf

            return yf.download(symbol, period=period, interval="1d", progress=False)
        except Exception:
            return None

    def _fetch_returns(self, name: str, symbol: str) -> Optional[Dict[str, float]]:
        """Return 1d and 5d (or period) returns for a symbol."""
        # Try market_data first if it exposes global data (do not yfinance-fallback if configured)
        if self.market_data is not None and hasattr(self.market_data, "get_global_data"):
            try:
                df = self.market_data.get_global_data(name, symbol)
                if df is not None and not df.empty and len(df) >= 2:
                    close = df["Close"].astype(float).dropna()
                    return self._returns_from_series(close)
            except Exception:
                pass
            return None

        # Fallback to yfinance
        df = self.fetch_yf(symbol)
        if df is not None and not df.empty and len(df) >= 2:
            close = df["Close"].astype(float).dropna()
            return self._returns_from_series(close)

        return None

    @staticmethod
    def _returns_from_series(close: pd.Series) -> Dict[str, float]:
        last = float(close.iloc[-1])
        prev = float(close.iloc[-2])
        first = float(close.iloc[0]) if len(close) > 0 else last
        return_1d = (last - prev) / prev if prev > 0 else 0.0
        return_5d = (last - first) / first if first > 0 else 0.0
        return {
            "last": round(last, 2),
            "return_1d_pct": round(return_1d * 100, 2),
            "return_5d_pct": round(return_5d * 100, 2),
        }

    def compute(self, refresh: bool = False) -> Dict[str, Any]:
        """Compute the latest global market snapshot and sentiment."""
        if self._latest is not None and not refresh:
            return self._latest

        asset_data: Dict[str, Dict[str, float]] = {}
        sentiment = 50.0
        raw_score = 0.0

        for name, symbol in self.assets.items():
            ret = self._fetch_returns(name, symbol)
            if ret is None:
                ret = {"last": 0.0, "return_1d_pct": 0.0, "return_5d_pct": 0.0}
            asset_data[name] = ret
            weight = self.WEIGHTS.get(name, 0.0)
            raw_score += ret["return_5d_pct"] * weight

        # Map raw score to a 0-100 sentiment index
        sentiment = round(max(0.0, min(100.0, 50.0 + raw_score)), 2)

        snapshot = {
            "timestamp": datetime.now().isoformat(),
            "sentiment_score": sentiment,
            "raw_score": round(raw_score, 2),
            "assets": asset_data,
        }

        self._latest = snapshot
        self._persist_snapshot(snapshot)
        return snapshot

    def _persist_snapshot(self, snapshot: Dict[str, Any]) -> None:
        if self.store is None:
            return
        try:
            self.store.save_global_markets(snapshot)
        except Exception as e:
            logger.warning(f"Could not persist global markets snapshot: {e}")

    def latest(self) -> Optional[Dict[str, Any]]:
        if self._latest is not None:
            return self._latest
        if self.store is None:
            return None
        try:
            snap = self.store.get_latest_global_markets()
            if snap:
                self._latest = snap
            return snap
        except Exception as e:
            logger.warning(f"Could not load latest global markets: {e}")
            return None

    def adjust_confidence(self, confidence: float) -> Dict[str, Any]:
        """Apply global sentiment confidence adjustment."""
        snap = self.latest() or self.compute()
        sentiment = float(snap.get("sentiment_score", 50.0))

        if sentiment >= 70:
            adjustment = 5.0
        elif sentiment <= 30:
            adjustment = -5.0
        else:
            adjustment = round((sentiment - 50.0) / 10.0, 2)

        adjusted = max(0.0, min(100.0, confidence + adjustment))
        return {
            "sentiment_score": sentiment,
            "adjustment": adjustment,
            "adjusted_confidence": round(adjusted, 2),
            "timestamp": snap.get("timestamp"),
        }
