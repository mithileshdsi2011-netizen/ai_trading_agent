"""
market_intelligence.py — Enterprise Market Intelligence Engine
Implements the Market Breadth Engine to gauge overall market strength
before allowing BUY decisions.
"""
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

import pandas as pd
import numpy as np

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class MarketBreadthEngine:
    """
    Measures overall market breadth using a universe of NSE equities.

    Outputs:
        - Advance/Decline counts and A/D ratio
        - Percentage of stocks above 20, 50 and 200 EMAs
        - A 0-100 Breadth Score
        - Market Strength label (BULLISH / NEUTRAL / BEARISH)
    """

    DEFAULT_MAX_SYMBOLS = 150

    def __init__(
        self,
        market_data: Optional[Any] = None,
        store: Optional[Any] = None,
        max_symbols: int = DEFAULT_MAX_SYMBOLS,
        universe: Optional[List[str]] = None,
    ):
        self.market_data = market_data
        self.max_symbols = max_symbols
        self.universe = universe
        self._latest: Optional[Dict[str, Any]] = None

        if store is None:
            try:
                from persistence import get_store

                store = get_store()
            except Exception:
                store = None
        self.store = store

    def get_universe_symbols(self) -> List[str]:
        """Return the symbol list to scan for breadth."""
        if self.universe:
            return self.universe[: self.max_symbols]

        # Breadth needs a broad, stable benchmark universe — not the intraday
        # momentum-scored candidates used for signal generation.
        try:
            from dynamic_universe import _NIFTY500_PRIORITY

            return list(_NIFTY500_PRIORITY)[: self.max_symbols]
        except Exception:
            logger.warning("Could not load symbol universe for breadth")
            return []

    @staticmethod
    def _ema(series: pd.Series, period: int) -> pd.Series:
        return series.ewm(span=period, adjust=False).mean()

    def _fetch_symbol_metrics(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Fetch 1-year daily history and compute EMA positioning."""
        if not self.market_data:
            return None

        try:
            df = self.market_data.get_stock_data(symbol, period="1y", interval="1d")
            if df is None or df.empty or len(df) < 200:
                return None

            close = df["Close"].astype(float)
            if close.isna().any():
                close = close.dropna()

            if len(close) < 200:
                return None

            last = float(close.iloc[-1])
            prev = float(close.iloc[-2])

            return {
                "symbol": symbol,
                "last": last,
                "prev": prev,
                "above20": last > float(self._ema(close, 20).iloc[-1]),
                "above50": last > float(self._ema(close, 50).iloc[-1]),
                "above200": last > float(self._ema(close, 200).iloc[-1]),
            }
        except Exception as e:
            logger.debug(f"Breadth fetch failed for {symbol}: {e}")
            return None

    def compute(
        self, symbols: Optional[List[str]] = None, refresh: bool = False
    ) -> Dict[str, Any]:
        """
        Compute the market breadth snapshot.

        Caches the result in memory; pass refresh=True to force recompute.
        """
        if self._latest is not None and not refresh:
            return self._latest

        if symbols is None:
            symbols = self.get_universe_symbols()

        if not symbols:
            self._latest = self._empty_snapshot("No universe available")
            return self._latest

        results: List[Dict[str, Any]] = []
        symbol_list = symbols[: self.max_symbols]

        # Live prices and prior close in one batch
        live_info: Dict[str, Dict[str, Any]] = {}
        if self.market_data:
            try:
                live_info = self.market_data.get_batch_stock_info(symbol_list) or {}
            except Exception as e:
                logger.warning(f"Batch stock info failed: {e}")

        # Historical data for EMAs in one batch
        hist_map: Dict[str, Any] = {}
        if self.market_data:
            try:
                hist_map = self.market_data.get_batch_stock_data(symbol_list, period="6mo", interval="1d") or {}
            except Exception as e:
                logger.warning(f"Batch historical data failed: {e}")

        for sym in symbol_list:
            info = live_info.get(sym)
            if not info:
                continue
            current = float(info.get("current_price", 0) or 0)
            prev = float(info.get("day_close", 0) or 0)
            if current <= 0 or prev <= 0:
                continue

            above20 = above50 = above200 = False
            df = hist_map.get(sym)
            if df is not None and not df.empty and "Close" in df.columns:
                close = df["Close"].astype(float).dropna()
                if len(close) >= 20:
                    above20 = current > float(self._ema(close, 20).iloc[-1])
                if len(close) >= 50:
                    above50 = current > float(self._ema(close, 50).iloc[-1])
                if len(close) >= 200:
                    above200 = current > float(self._ema(close, 200).iloc[-1])

            results.append({
                "symbol": sym,
                "last": current,
                "prev": prev,
                "above20": above20,
                "above50": above50,
                "above200": above200,
            })

        if not results:
            self._latest = self._empty_snapshot("No live price data returned")
            return self._latest

        total = len(results)
        advance = sum(1 for m in results if m["last"] > m["prev"])
        decline = sum(1 for m in results if m["last"] < m["prev"])
        # unchanged ignored for A/D counts

        ad_ratio = round(advance / decline, 2) if decline > 0 else (advance if advance > 0 else 0.0)

        above20 = round(100 * sum(1 for m in results if m["above20"]) / total, 2)
        above50 = round(100 * sum(1 for m in results if m["above50"]) / total, 2)
        above200 = round(100 * sum(1 for m in results if m["above200"]) / total, 2)

        # Composite breadth score
        ad_component = (advance / (advance + decline or 1)) * 100
        ema_component = (above20 + above50 + above200) / 3
        breadth_score = min(
            100.0,
            round(
                0.6 * ad_component
                + 0.4 * ema_component
                + min(ad_ratio, 10.0) * 0.7,
                2,
            ),
        )

        if breadth_score >= 75:
            market_strength = "BULLISH"
        elif breadth_score >= 45:
            market_strength = "NEUTRAL"
        else:
            market_strength = "BEARISH"

        snapshot = {
            "timestamp": datetime.now().isoformat(),
            "advance": advance,
            "decline": decline,
            "ad_ratio": ad_ratio,
            "above20": above20,
            "above50": above50,
            "above200": above200,
            "breadth_score": breadth_score,
            "market_strength": market_strength,
            "total": total,
        }

        self._latest = snapshot
        self._persist_snapshot(snapshot)
        return snapshot

    def _empty_snapshot(self, reason: str) -> Dict[str, Any]:
        now = datetime.now().isoformat()
        return {
            "timestamp": now,
            "advance": 0,
            "decline": 0,
            "ad_ratio": 0.0,
            "above20": 0.0,
            "above50": 0.0,
            "above200": 0.0,
            "breadth_score": 0.0,
            "market_strength": "NEUTRAL",
            "total": 0,
            "reason": reason,
        }

    def _persist_snapshot(self, snapshot: Dict[str, Any]) -> None:
        if self.store is None:
            return
        try:
            self.store.save_market_breadth(snapshot)
        except Exception as e:
            logger.warning(f"Could not persist market breadth snapshot: {e}")

    def latest(self) -> Optional[Dict[str, Any]]:
        """Return the latest snapshot from memory or store."""
        if self._latest is not None:
            return self._latest
        if self.store is None:
            return None
        try:
            snap = self.store.get_latest_market_breadth()
            if snap:
                self._latest = snap
            return snap
        except Exception as e:
            logger.warning(f"Could not load latest market breadth: {e}")
            return None

    def adjust_ai_score(self, ai_score: float) -> Dict[str, Any]:
        """
        Apply the breadth adjustment to a raw AI score.

        Rules:
            Breadth Score > 75  ->  +5
            Breadth Score 50-75 ->   0
            Breadth Score < 50  -> -10
        """
        snapshot = self.latest() or self.compute()
        breadth_score = float(snapshot.get("breadth_score", 0.0))
        market_strength = str(snapshot.get("market_strength", "NEUTRAL"))
        total = int(snapshot.get("total", 0))

        if total == 0:
            # No real data; stay neutral to avoid false signals
            adjustment = 0
            market_strength = "NEUTRAL"
            breadth_score = 50.0
        elif breadth_score > 75:
            adjustment = 5
        elif breadth_score < 50:
            adjustment = -10
        else:
            adjustment = 0

        adjusted = max(0.0, min(100.0, ai_score + adjustment))
        return {
            "breadth_score": breadth_score,
            "market_strength": market_strength,
            "adjustment": adjustment,
            "adjusted_score": round(adjusted, 2),
            "timestamp": snapshot.get("timestamp"),
        }
