"""FII/DII store-fallback decision-input regression.

get_latest_fii_dii feeds FII_DII_Engine.adjust_ai_score (±3 on final_score,
which is compared against the regime BUY threshold). The ordering fix makes
the store fallback return the real newest row instead of a lexical artifact:
- fresh store row + failed fetch → real ±3 sentiment applied (intended)
- genuinely stale row (>72h) → still rejected to NEUTRAL ±0
- healthy fetch → store never consulted (unchanged)
"""
import os
import sys
from datetime import date, datetime, timedelta
from unittest.mock import Mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.fii_dii import FII_DII_Engine
from src.persistence import TradingStore


def _engine(store):
    eng = FII_DII_Engine(store=store)
    eng.fetch_nse = Mock(return_value=None)  # simulate NSE outage
    return eng


def _row(ts_display, iso_date, net):
    return {'timestamp': ts_display, 'date': iso_date,
            'fii_net': net, 'dii_net': 0.0, 'net_flow': net,
            'sentiment': 'POSITIVE' if net > 0 else 'NEGATIVE'}


class TestStoreFallbackAdjustment:
    def test_fresh_row_applies_positive_adjustment(self, tmp_path):
        store = TradingStore(str(tmp_path / 't.db'))
        store.save_fii_dii(_row('27-Oct-2026', date.today().isoformat(), 500.0))
        r = _engine(store).adjust_ai_score(68.0)
        assert r['sentiment'] == 'POSITIVE' and r['adjustment'] == 3
        assert r['adjusted_score'] == 71.0  # crosses a 70 threshold

    def test_fresh_row_applies_negative_adjustment(self, tmp_path):
        store = TradingStore(str(tmp_path / 't.db'))
        store.save_fii_dii(_row('27-Oct-2026', date.today().isoformat(), -500.0))
        r = _engine(store).adjust_ai_score(72.0)
        assert r['sentiment'] == 'NEGATIVE' and r['adjustment'] == -3
        assert r['adjusted_score'] == 69.0  # drops below a 70 threshold

    def test_lexical_trap_row_not_used_for_decisions(self, tmp_path):
        # '31-Aug-2026' sorts above '27-Oct-2026' as text but is older data;
        # decisions must consume the genuinely newest row.
        store = TradingStore(str(tmp_path / 't.db'))
        store.save_fii_dii(_row('31-Aug-2026', '2026-08-31', -999.0))
        store.save_fii_dii(_row('27-Oct-2026', date.today().isoformat(), 500.0))
        r = _engine(store).adjust_ai_score(68.0)
        assert r['adjustment'] == 3 and r['adjusted_score'] == 71.0

    def test_stale_row_rejected_to_neutral(self, tmp_path):
        store = TradingStore(str(tmp_path / 't.db'))
        old = (date.today() - timedelta(days=10)).isoformat()
        store.save_fii_dii(_row('old', old, 500.0))
        r = _engine(store).adjust_ai_score(68.0)
        # stale snapshot discarded → neutral fallback → no score change
        assert r['sentiment'] == 'NEUTRAL' and r['adjustment'] == 0
        assert r['adjusted_score'] == 68.0

    def test_adjustment_clamped_to_bounds(self, tmp_path):
        store = TradingStore(str(tmp_path / 't.db'))
        store.save_fii_dii(_row('t', date.today().isoformat(), 500.0))
        assert _engine(store).adjust_ai_score(98.5)['adjusted_score'] == 100.0

        store2 = TradingStore(str(tmp_path / 't2.db'))
        store2.save_fii_dii(_row('t', date.today().isoformat(), -500.0))
        assert _engine(store2).adjust_ai_score(2.0)['adjusted_score'] == 0.0


class TestHealthyFetchUnchanged:
    def test_live_fetch_ignores_stale_store(self, tmp_path):
        # When NSE fetch succeeds the store is not consulted at all —
        # ordering fix cannot alter the live-data path.
        store = TradingStore(str(tmp_path / 't.db'))
        store.save_fii_dii(_row('31-Aug-2026', '2026-08-31', -999.0))
        eng = FII_DII_Engine(store=store)
        eng.fetch_nse = Mock(return_value=_row('live', date.today().isoformat(), 800.0))
        r = eng.compute()
        assert r['net_flow'] == 800.0 and r['sentiment'] == 'POSITIVE'
