"""
sector_rotation.py — Enterprise Sector Rotation Engine
Calculates sector momentum scores and ranks sectors by 7/30-day performance
relative to NIFTY.
"""
import json
import logging
import math
from datetime import datetime
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _snapshot_age_seconds(snap: Dict[str, Any]) -> float:
    """Age of a snapshot in seconds; unparseable/missing timestamps count as infinitely old."""
    try:
        ts = snap.get("timestamp")
        if not ts:
            return float("inf")
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        now = datetime.now(dt.tzinfo) if dt.tzinfo else datetime.now()
        return max(0.0, (now - dt).total_seconds())
    except Exception:
        return float("inf")


class SectorRotationEngine:
    """
    Sector rotation analysis for the enterprise trading bot.

    Outputs:
        - Top 5 strongest sectors
        - Top 5 weakest sectors
        - 7-day and 30-day sector returns
        - Relative strength vs NIFTY
        - Sector Momentum Score (0-100)
    """

    DEFAULT_MAX_SYMBOLS = 150
    DEFAULT_NIFTY_SYMBOL = "NIFTY 50"

    # Sector momentum is daily intel — never reuse a snapshot older than this.
    SNAPSHOT_MAX_AGE_SECONDS = 24 * 3600

    # Coverage gate: the momentum score is a percentile placement across the
    # sector universe. When most fetches fail (e.g. Kite rate limiting), the
    # surviving handful produces arbitrary 0/100 extremes — Banking=100,
    # IT=0 — that then drive ±5 LIVE confidence adjustments. Below these
    # thresholds the snapshot is marked INSUFFICIENT_COVERAGE and the
    # adjustment is neutralized (0.0) while raw rows stay for diagnostics.
    MIN_COVERAGE_RATIO = 0.70          # of attempted symbols must return data
    MIN_COVERED_SECTORS_FRACTION = 0.5  # ≥ half of configured sectors have data
    ABS_MIN_COVERED_SECTORS = 3         # floor when the sector map is tiny

    def __init__(
        self,
        market_data: Optional[Any] = None,
        store: Optional[Any] = None,
        max_symbols: int = DEFAULT_MAX_SYMBOLS,
        universe: Optional[List[str]] = None,
        sector_map: Optional[Dict[str, str]] = None,
        nifty_symbol: str = DEFAULT_NIFTY_SYMBOL,
    ):
        self.market_data = market_data
        self.max_symbols = max_symbols
        self.universe = universe
        self.sector_map = sector_map or {}
        self.nifty_symbol = nifty_symbol
        self._latest: Optional[Dict[str, Any]] = None

        if store is None:
            try:
                from persistence import get_store

                store = get_store()
            except Exception:
                store = None
        self.store = store

    def _get_sector_map(self) -> Dict[str, str]:
        if self.sector_map:
            return self.sector_map
        try:
            from dynamic_universe import SECTOR_MAP

            return SECTOR_MAP
        except Exception:
            return {}

    def get_universe_symbols(self) -> List[str]:
        if self.universe:
            return self.universe[: self.max_symbols]

        if self.market_data is not None:
            try:
                from dynamic_universe import DynamicUniverse

                du = DynamicUniverse(self.market_data.kite)
                syms = du.get_universe(top_n=self.max_symbols)
                if syms:
                    return syms
            except Exception:
                pass

        try:
            from dynamic_universe import _NIFTY500_PRIORITY

            return list(_NIFTY500_PRIORITY)[: self.max_symbols]
        except Exception:
            return []

    def _fetch_returns(self, symbol: str, lookback_days: int = 35) -> Optional[Dict[str, float]]:
        """Fetch historical close and compute 7-day and 30-day returns."""
        if not self.market_data:
            return None

        try:
            df = self.market_data.get_stock_data(symbol, period="3mo", interval="1d")
            if df is None or df.empty or len(df) < 30:
                return None

            close = df["Close"].astype(float).dropna()
            if len(close) < 30:
                return None

            current = float(close.iloc[-1])
            d7 = float(close.iloc[-8]) if len(close) >= 8 else float(close.iloc[0])
            d30 = float(close.iloc[-31]) if len(close) >= 31 else float(close.iloc[0])

            return {
                "return_7d": (current - d7) / d7 if d7 > 0 else None,
                "return_30d": (current - d30) / d30 if d30 > 0 else None,
                "current": current,
            }
        except Exception as e:
            logger.debug(f"Sector rotation fetch failed for {symbol}: {e}")
            return None

    def _sector_momentum_score(
        self, rel_7d: float, rel_30d: float, all_scores: List[float]
    ) -> float:
        """
        Map sector relative returns to a 0-100 momentum score.
        Uses a simple percentile-like placement within the universe.
        """
        raw = rel_7d * 0.4 + rel_30d * 0.6
        if not all_scores:
            return 50.0
        min_s = min(all_scores)
        max_s = max(all_scores)
        if max_s == min_s:
            return 50.0
        normalized = (raw - min_s) / (max_s - min_s)
        return round(max(0.0, min(100.0, normalized * 100)), 2)

    def compute(self, refresh: bool = False) -> Dict[str, Any]:
        """Compute sector rotation snapshot, caching unless refresh=True."""
        if self._latest is not None and not refresh:
            return self._latest

        symbols = self.get_universe_symbols()
        if not symbols:
            self._latest = self._empty_snapshot("No universe available")
            return self._latest

        # NIFTY benchmark returns — a failed baseline must NOT masquerade
        # as 0%, or absolute sector returns get mislabeled as relative.
        nifty = self._fetch_returns(self.nifty_symbol)
        nifty_ok = bool(nifty) and nifty.get("return_7d") is not None

        # Fetch individual stock returns and assign to sectors
        sector_map = self._get_sector_map()
        sector_data: Dict[str, List[Dict[str, float]]] = {}

        attempted = symbols[: self.max_symbols]
        symbols_succeeded = 0
        for sym in attempted:
            ret = self._fetch_returns(sym)
            if ret is None:
                continue
            symbols_succeeded += 1
            sector = sector_map.get(sym, "Other")
            sector_data.setdefault(sector, []).append(ret)

        # Coverage is measured against the CONFIGURED universe attempted —
        # not just the sectors that survived — so a rate-limited run can't
        # masquerade as a complete ranking.
        sectors_configured = len({sector_map.get(s, "Other") for s in attempted})
        sectors_covered = len(sector_data)
        min_sectors = (
            max(self.ABS_MIN_COVERED_SECTORS,
                math.ceil(sectors_configured * self.MIN_COVERED_SECTORS_FRACTION))
            if sectors_configured else self.ABS_MIN_COVERED_SECTORS
        )
        coverage_ratio = (symbols_succeeded / len(attempted)) if attempted else 0.0
        sufficient = (
            bool(attempted)
            and coverage_ratio >= self.MIN_COVERAGE_RATIO
            and sectors_covered >= min_sectors
        )
        coverage = {
            "symbols_attempted": len(attempted),
            "symbols_succeeded": symbols_succeeded,
            "coverage_pct": round(coverage_ratio * 100, 1),
            "sectors_configured": sectors_configured,
            "sectors_covered": sectors_covered,
            "min_sectors_required": min_sectors,
            "sufficient": sufficient,
        }

        if not sector_data:
            self._latest = self._empty_snapshot("No sector data returned", coverage)
            return self._latest

        # Build per-sector aggregated metrics
        sector_metrics: List[Dict[str, Any]] = []
        for sector, rows in sector_data.items():
            if not rows:
                continue
            v7 = [r["return_7d"] for r in rows if r["return_7d"] is not None]
            v30 = [r["return_30d"] for r in rows if r["return_30d"] is not None]
            avg_7d = float(np.mean(v7)) if v7 else None
            avg_30d = float(np.mean(v30)) if v30 else None
            # Relative returns need BOTH a valid sector average and a valid
            # NIFTY baseline — otherwise None, never 0%.
            rel_7d = (avg_7d - nifty["return_7d"]) if (nifty_ok and avg_7d is not None) else None
            rel_30d = (avg_30d - nifty["return_30d"]) if (nifty_ok and avg_30d is not None) else None
            sector_metrics.append({
                "sector": sector,
                "return_7d_pct": round(avg_7d * 100, 2) if avg_7d is not None else None,
                "return_30d_pct": round(avg_30d * 100, 2) if avg_30d is not None else None,
                "rel_7d_pct": round(rel_7d * 100, 2) if rel_7d is not None else None,
                "rel_30d_pct": round(rel_30d * 100, 2) if rel_30d is not None else None,
                "raw_score": (rel_7d * 0.4 + rel_30d * 0.6)
                    if (rel_7d is not None and rel_30d is not None) else None,
            })

        # Second pass to compute 0-100 momentum score with min/max normalization
        raw_scores = [s["raw_score"] for s in sector_metrics if s["raw_score"] is not None]
        for s in sector_metrics:
            s["momentum_score"] = (self._sector_momentum_score(s["raw_score"], s["raw_score"], raw_scores)
                                   if s["raw_score"] is not None else None)
            del s["raw_score"]

        # None-momentum sectors sort last; valid scores rank normally
        sector_metrics.sort(key=lambda x: (x["momentum_score"] is not None, x["momentum_score"] or 0), reverse=True)

        scored = [s for s in sector_metrics if s["momentum_score"] is not None]
        top5_strong = scored[:5]
        remaining = scored[5:]
        remaining.sort(key=lambda x: x["momentum_score"])
        top5_weak = remaining[:5]

        snapshot = {
            "timestamp": datetime.now().isoformat(),
            "nifty_7d": round(nifty["return_7d"] * 100, 2) if nifty_ok else None,
            "nifty_30d": round(nifty["return_30d"] * 100, 2) if (nifty_ok and nifty.get("return_30d") is not None) else None,
            "baseline_available": nifty_ok,
            "top5_strong": top5_strong,
            "top5_weak": top5_weak,
            "all_sectors": sector_metrics,
            "coverage": coverage,
            "sector_status": "SUFFICIENT" if sufficient else "INSUFFICIENT_COVERAGE",
        }

        self._latest = snapshot
        self._persist_snapshot(snapshot)
        return snapshot

    def _empty_snapshot(self, reason: str,
                        coverage: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        now = datetime.now().isoformat()
        return {
            "timestamp": now,
            "nifty_7d": 0.0,
            "nifty_30d": 0.0,
            "top5_strong": [],
            "top5_weak": [],
            "all_sectors": [],
            "coverage": coverage or {},
            "sector_status": "INSUFFICIENT_COVERAGE",
            "reason": reason,
        }

    def _persist_snapshot(self, snapshot: Dict[str, Any]) -> None:
        if self.store is None:
            return
        try:
            self.store.save_sector_rotation(snapshot)
        except Exception as e:
            logger.warning(f"Could not persist sector rotation snapshot: {e}")

    def latest(self) -> Optional[Dict[str, Any]]:
        snap = self._latest
        if snap is None:
            if self.store is None:
                return None
            try:
                snap = self.store.get_latest_sector_rotation()
            except Exception as e:
                logger.warning(f"Could not load latest sector rotation: {e}")
                return None
        if snap is None:
            return None
        if _snapshot_age_seconds(snap) > self.SNAPSHOT_MAX_AGE_SECONDS:
            self._latest = None
            return None
        self._latest = snap
        return snap

    def adjust_ai_score(self, ai_score: float, sector: str) -> Dict[str, Any]:
        """
        Apply sector rotation adjustment to a raw AI score.

        Rules:
            Stock sector in top 3 strongest -> +5
            Stock sector in bottom 3 weakest -> -5
        """
        snapshot = self.latest() or self.compute()
        strong = snapshot.get("top5_strong", [])
        weak = snapshot.get("top5_weak", [])

        top3 = [s.get("sector") for s in strong[:3]]
        bottom3 = [w.get("sector") for w in weak[:3]]

        all_sectors = snapshot.get("all_sectors", [])
        sector_info = next((s for s in all_sectors if s.get("sector") == sector), None)
        raw_momentum = (sector_info.get("momentum_score") or 50.0) if sector_info else 50.0
        rank = next((i for i, s in enumerate(all_sectors) if s.get("sector") == sector), -1) + 1

        # Coverage gate: only a SUFFICIENT snapshot may move the score. Old
        # snapshots without coverage metadata fail neutral rather than
        # assume full coverage.
        sufficient = snapshot.get("sector_status") == "SUFFICIENT"

        if sufficient and sector in top3:
            adjustment = 5
        elif sufficient and sector in bottom3:
            adjustment = -5
        else:
            adjustment = 0

        adjusted = max(0.0, min(100.0, ai_score + adjustment))
        return {
            "sector": sector,
            # Neutral momentum when coverage is insufficient so downstream
            # composites (market_intelligence_score) aren't skewed by a
            # degraded percentile — the raw value stays for diagnostics.
            "momentum_score": raw_momentum if sufficient else 50.0,
            "raw_momentum_score": raw_momentum,
            "sector_status": snapshot.get("sector_status") or "INSUFFICIENT_COVERAGE",
            "coverage": snapshot.get("coverage"),
            "rank": rank if sufficient else None,
            "adjustment": adjustment,
            "adjusted_score": round(adjusted, 2),
            "timestamp": snapshot.get("timestamp"),
        }
