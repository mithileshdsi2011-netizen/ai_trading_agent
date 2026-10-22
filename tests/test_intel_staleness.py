"""
Regression tests — market-intelligence staleness + regime BUY gate (Oct 22 2026 audit).

Covers:
- The LIVE orchestrator's regime gate blocks new BUYs in BEAR (and VOLATILE when configured).
- Persisted intel snapshots older than each engine's max age must never feed decisions.
- Fetch failures produce honest UNAVAILABLE/NEUTRAL data — never synthetic zeros.
"""
import os
import sys
import unittest
from datetime import datetime, timedelta
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from config import config
from trading_orchestrator import new_buys_blocked_regime
from options_intelligence import OptionsIntelligenceEngine
from global_markets import GlobalMarketMonitor
from market_intelligence import MarketBreadthEngine
from sector_rotation import SectorRotationEngine
from fii_dii import FII_DII_Engine


def _iso(days_ago=0, hours_ago=0):
    return (datetime.now() - timedelta(days=days_ago, hours=hours_ago)).isoformat()


class FakeStore:
    def __init__(self, snap=None):
        self.snap = snap
        self.saved = []

    def get_latest_options_intelligence(self): return self.snap
    def get_latest_global_markets(self): return self.snap
    def get_latest_market_breadth(self): return self.snap
    def get_latest_sector_rotation(self): return self.snap
    def get_latest_fii_dii(self): return self.snap
    def save_options_intelligence(self, s): self.saved.append(s)
    def save_global_markets(self, s): self.saved.append(s)
    def save_market_breadth(self, s): self.saved.append(s)
    def save_sector_rotation(self, s): self.saved.append(s)
    def save_fii_dii(self, s): self.saved.append(s)


class TestRegimeBuyGate(unittest.TestCase):
    """The gate the LIVE orchestrator applies to every BUY candidate."""

    def test_bear_blocks_new_buys(self):
        self.assertEqual(new_buys_blocked_regime('BEAR'), 'BEAR')

    def test_volatile_blocks_when_configured(self):
        config.VOLATILE_BLOCK_BUYS = True
        self.assertEqual(new_buys_blocked_regime('VOLATILE'), 'VOLATILE')

    def test_volatile_allowed_when_disabled(self):
        orig = config.VOLATILE_BLOCK_BUYS
        try:
            config.VOLATILE_BLOCK_BUYS = False
            self.assertIsNone(new_buys_blocked_regime('VOLATILE'))
        finally:
            config.VOLATILE_BLOCK_BUYS = orig

    def test_normal_regimes_allow_buys(self):
        for regime in ('BULL', 'SIDEWAYS', 'UNKNOWN'):
            self.assertIsNone(new_buys_blocked_regime(regime), regime)


class TestOptionsStaleness(unittest.TestCase):
    def test_stale_snapshot_never_used(self):
        stale = {"timestamp": _iso(days_ago=80), "pcr": 3.0, "confidence_boost": 15.0}
        eng = OptionsIntelligenceEngine(store=FakeStore(stale))
        self.assertIsNone(eng.latest())

    def test_stale_snapshot_boost_becomes_zero_when_refetch_fails(self):
        stale = {"timestamp": _iso(days_ago=80), "pcr": 3.0, "confidence_boost": 15.0}
        eng = OptionsIntelligenceEngine(store=FakeStore(stale))
        eng.compute = MagicMock(return_value=eng._neutral_snapshot("fetch failed"))
        out = eng.adjust_confidence(50.0)
        self.assertEqual(out["confidence_boost"], 0.0)
        self.assertEqual(out["adjusted_confidence"], 50.0)

    def test_fresh_snapshot_still_used(self):
        fresh = {"timestamp": _iso(hours_ago=1), "pcr": 1.1, "confidence_boost": 10.0}
        eng = OptionsIntelligenceEngine(store=FakeStore(fresh))
        out = eng.adjust_confidence(50.0)
        self.assertEqual(out["confidence_boost"], 10.0)
        self.assertEqual(out["adjusted_confidence"], 60.0)

    def test_compute_store_fallback_also_rejects_stale(self):
        """Fetch failure must not fall back to a stale stored snapshot."""
        stale = {"timestamp": _iso(days_ago=80), "pcr": 3.0, "confidence_boost": 15.0}
        eng = OptionsIntelligenceEngine(store=FakeStore(stale))
        eng.fetch_nse_option_chain = MagicMock(return_value=None)
        snap = eng.compute(refresh=True)
        self.assertEqual(snap["confidence_boost"], 0.0)
        self.assertIn("reason", snap)


class TestGlobalMarketsStaleness(unittest.TestCase):
    def test_stale_snapshot_never_used(self):
        stale = {"timestamp": _iso(days_ago=80), "sentiment_score": 50.0, "assets": {}}
        eng = GlobalMarketMonitor(store=FakeStore(stale))
        self.assertIsNone(eng.latest())

    def test_total_fetch_failure_is_unavailable_not_zeros(self):
        eng = GlobalMarketMonitor(store=FakeStore())
        eng._fetch_returns = lambda name, sym: None
        snap = eng.compute(refresh=True)
        self.assertIsNone(snap["sentiment_score"])
        self.assertEqual(snap["assets"], {})
        self.assertEqual(snap["status"], "UNAVAILABLE")

    def test_unavailable_snapshot_adjusts_zero(self):
        eng = GlobalMarketMonitor(store=FakeStore())
        eng._latest = None
        eng.compute = MagicMock(return_value={
            "timestamp": _iso(), "sentiment_score": None, "assets": {},
            "status": "UNAVAILABLE", "reason": "global market data unavailable",
        })
        out = eng.adjust_confidence(50.0)
        self.assertEqual(out["adjustment"], 0.0)
        self.assertEqual(out["adjusted_confidence"], 50.0)
        self.assertIsNone(out["sentiment_score"])

    def test_failed_assets_not_zero_filled(self):
        eng = GlobalMarketMonitor(store=FakeStore())
        good = {"last": 100.0, "return_1d_pct": 1.0, "return_5d_pct": 2.0}
        eng._fetch_returns = lambda name, sym: good if name == "NASDAQ" else None
        snap = eng.compute(refresh=True)
        self.assertNotIn("Dow Jones", snap["assets"])
        self.assertIn("NASDAQ", snap["assets"])
        self.assertIn("Dow Jones", snap["missing_assets"])


class TestBreadthAndSectorStaleness(unittest.TestCase):
    def test_stale_breadth_rejected(self):
        stale = {"timestamp": _iso(days_ago=80), "breadth_score": 37.3}
        eng = MarketBreadthEngine(store=FakeStore(stale))
        self.assertIsNone(eng.latest())

    def test_stale_sector_rejected(self):
        stale = {"timestamp": _iso(days_ago=80), "top5_strong": [], "top5_weak": []}
        eng = SectorRotationEngine(store=FakeStore(stale))
        self.assertIsNone(eng.latest())

    def test_fresh_breadth_used(self):
        fresh = {"timestamp": _iso(hours_ago=1), "breadth_score": 60.0}
        eng = MarketBreadthEngine(store=FakeStore(fresh))
        self.assertIsNotNone(eng.latest())


class TestFIIDIIStaleness(unittest.TestCase):
    def test_old_report_date_rejected(self):
        old = {"date": "2026-08-03", "timestamp": "03-Aug-2026", "sentiment": "POSITIVE", "net_flow": 5000.0}
        eng = FII_DII_Engine(store=FakeStore(old))
        self.assertIsNone(eng.latest())

    def test_recent_report_date_accepted(self):
        recent = {"date": datetime.now().date().isoformat(), "sentiment": "POSITIVE", "net_flow": 5000.0}
        eng = FII_DII_Engine(store=FakeStore(recent))
        self.assertIsNotNone(eng.latest())

    def test_compute_store_fallback_also_rejects_stale(self):
        """NSE fetch failure must not fall back to a stale stored report."""
        old = {"date": "2026-08-03", "timestamp": "03-Aug-2026", "sentiment": "POSITIVE", "net_flow": 5000.0}
        eng = FII_DII_Engine(store=FakeStore(old))
        eng.fetch_nse = MagicMock(return_value=None)
        snap = eng.compute(refresh=True)
        self.assertEqual(snap["sentiment"], "NEUTRAL")
        self.assertIn("reason", snap)


if __name__ == '__main__':
    unittest.main()
