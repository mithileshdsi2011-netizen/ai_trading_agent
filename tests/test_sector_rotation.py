"""
Unit tests for src/sector_rotation.py
"""
import os
import sys
import unittest
from datetime import datetime
from types import SimpleNamespace

import pandas as pd
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from sector_rotation import SectorRotationEngine


def make_hist(close, n=60):
    arr = np.asarray(close, dtype=float)
    if len(arr) < n:
        arr = np.concatenate([np.full(n - len(arr), arr[0] if len(arr) else 100.0), arr])
    idx = pd.date_range(end='2026-08-03', periods=len(arr), freq='D')
    return pd.DataFrame({
        'Open': arr * 0.99,
        'High': arr * 1.01,
        'Low': arr * 0.98,
        'Close': arr,
        'Volume': np.full(len(arr), 100000),
    }, index=idx)


def up_hist(start=100, end=130, n=60):
    arr = np.linspace(start, end, n)
    return make_hist(arr, n=n)


def down_hist(start=130, end=100, n=60):
    arr = np.linspace(start, end, n)
    return make_hist(arr, n=n)


class FakeMarketData:
    def __init__(self, hists, nifty=None):
        self._hists = hists
        self._nifty = nifty or make_hist(np.linspace(100, 105, 60), n=60)

    def get_stock_data(self, symbol, period, interval):
        if symbol == 'NIFTY 50':
            return self._nifty
        return self._hists.get(symbol, pd.DataFrame())


class TestSectorRotationEngine(unittest.TestCase):

    def setUp(self):
        syms = ['BANK1', 'BANK2', 'IT1', 'IT2', 'PHR1', 'PHR2', 'OTH1']
        hists = {
            'BANK1': up_hist(100, 130),
            'BANK2': up_hist(100, 125),
            'IT1': down_hist(130, 100),
            'IT2': down_hist(120, 100),
            'PHR1': up_hist(100, 115),
            'PHR2': up_hist(100, 118),
            'OTH1': up_hist(100, 108),
        }
        sector_map = {
            'BANK1': 'Banking', 'BANK2': 'Banking',
            'IT1': 'IT', 'IT2': 'IT',
            'PHR1': 'Pharma', 'PHR2': 'Pharma',
            'OTH1': 'Other',
        }
        md = FakeMarketData(hists)
        self.engine = SectorRotationEngine(market_data=md, store=None, universe=syms, sector_map=sector_map)

    def test_compute_returns_keys(self):
        result = self.engine.compute()
        for k in ['top5_strong', 'top5_weak', 'nifty_7d', 'nifty_30d', 'all_sectors']:
            self.assertIn(k, result)
        self.assertTrue(len(result['top5_strong']) <= 5)
        self.assertTrue(len(result['top5_weak']) <= 5)

    def test_top_and_bottom_ranking(self):
        result = self.engine.compute()
        strong = result['top5_strong']
        weak = result['top5_weak']
        self.assertTrue(all(s['momentum_score'] >= 0 for s in strong))
        self.assertTrue(all(s['momentum_score'] >= 0 for s in weak))
        if strong and weak:
            self.assertGreaterEqual(strong[0]['momentum_score'], weak[-1]['momentum_score'])

    def test_momentum_score_bounds(self):
        result = self.engine.compute()
        for s in result['all_sectors']:
            self.assertTrue(0 <= s['momentum_score'] <= 100)

    def test_top3_adjustment(self):
        self.engine._latest = {
            'timestamp': datetime.now().isoformat(),
            'sector_status': 'SUFFICIENT',
            'coverage': {'sufficient': True},
            'top5_strong': [{'sector': 'Banking', 'momentum_score': 90}, {'sector': 'Pharma', 'momentum_score': 80}],
            'top5_weak': [{'sector': 'IT', 'momentum_score': 10}],
            'all_sectors': [
                {'sector': 'Banking', 'momentum_score': 90},
                {'sector': 'Pharma', 'momentum_score': 80},
                {'sector': 'Other', 'momentum_score': 60},
                {'sector': 'IT', 'momentum_score': 10},
            ]
        }
        adj = self.engine.adjust_ai_score(60.0, 'Banking')
        self.assertEqual(adj['adjustment'], 5)
        self.assertEqual(adj['adjusted_score'], 65.0)

    def test_bottom3_adjustment(self):
        self.engine._latest = {
            'timestamp': datetime.now().isoformat(),
            'sector_status': 'SUFFICIENT',
            'coverage': {'sufficient': True},
            'top5_strong': [{'sector': 'Banking', 'momentum_score': 90}],
            'top5_weak': [{'sector': 'IT', 'momentum_score': 10}, {'sector': 'Auto', 'momentum_score': 15}, {'sector': 'FMCG', 'momentum_score': 20}],
            'all_sectors': [
                {'sector': 'Banking', 'momentum_score': 90},
                {'sector': 'IT', 'momentum_score': 10},
            ]
        }
        adj = self.engine.adjust_ai_score(60.0, 'IT')
        self.assertEqual(adj['adjustment'], -5)
        self.assertEqual(adj['adjusted_score'], 55.0)


class TestSectorRotationMissingData(unittest.TestCase):
    """Missing/failed data must produce None/N/A — never fabricated 0%."""

    def test_zero_lookback_price_yields_none(self):
        # Lookback close of 0.0 → return must be None, not 0.0%
        h = make_hist(np.linspace(100, 110, 60), n=60)
        h.iloc[-8, h.columns.get_loc('Close')] = 0.0
        engine = SectorRotationEngine(market_data=FakeMarketData({'X': h}))
        ret = engine._fetch_returns('X')
        self.assertIsNone(ret['return_7d'])
        self.assertIsNotNone(ret['return_30d'])  # unaffected window stays valid

    def test_failed_nifty_baseline_yields_none_relative(self):
        hists = {'B1': up_hist(100, 130), 'B2': up_hist(100, 125)}
        md = FakeMarketData(hists)
        md._nifty = pd.DataFrame()  # empty → baseline fetch fails
        engine = SectorRotationEngine(market_data=md, store=None,
                                      universe=['B1', 'B2'],
                                      sector_map={'B1': 'Banking', 'B2': 'Banking'})
        result = engine.compute()
        self.assertFalse(result['baseline_available'])
        self.assertIsNone(result['nifty_7d'])
        self.assertIsNone(result['nifty_30d'])
        for s in result['all_sectors']:
            self.assertIsNone(s['rel_7d_pct'])
            self.assertIsNone(s['rel_30d_pct'])
            # absolute returns must still be present and valid
            self.assertIsNotNone(s['return_30d_pct'])
            self.assertGreater(s['return_30d_pct'], 0)
            self.assertIsNone(s['momentum_score'])
        # un-scored sectors must not appear in strong/weak rankings
        self.assertEqual(result['top5_strong'], [])
        self.assertEqual(result['top5_weak'], [])

    def test_adjust_score_treats_none_momentum_as_neutral(self):
        engine = SectorRotationEngine(market_data=FakeMarketData({}))
        engine._latest = {
            'timestamp': datetime.now().isoformat(),
            'top5_strong': [], 'top5_weak': [],
            'all_sectors': [{'sector': 'X', 'momentum_score': None}],
        }
        adj = engine.adjust_ai_score(60.0, 'X')
        self.assertEqual(adj['adjustment'], 0)
        self.assertEqual(adj['momentum_score'], 50.0)


class CountingMarketData(FakeMarketData):
    """Records every fetch call so tests can assert no extra broker calls."""

    def __init__(self, hists, fail=None, nifty=None):
        super().__init__(hists, nifty=nifty)
        self._fail = set(fail or [])
        self.calls = []

    def get_stock_data(self, symbol, period, interval):
        if symbol != 'NIFTY 50':
            self.calls.append(symbol)
        if symbol in self._fail:
            raise Exception('429 Too many requests')
        return super().get_stock_data(symbol, period, interval)


def _ten_sector_setup(ok, fail):
    """10 symbols across 10 sectors; `ok` succeed, `fail` raise."""
    syms = [f'S{i}' for i in range(10)]
    sector_map = {s: f'Sector{i}' for i, s in enumerate(syms)}
    # The earliest surviving sector gets the steepest history → top-1 rank.
    hists = {s: up_hist(100, 100 + 5 * (len(ok) - i + 1)) for i, s in enumerate(ok)}
    md = CountingMarketData(hists, fail=fail)
    eng = SectorRotationEngine(market_data=md, store=None,
                               universe=syms, sector_map=sector_map)
    return eng, md


class TestSectorCoverageGate(unittest.TestCase):
    """Percentile rankings over a degraded universe must not move AI scores."""

    def _adj(self, ok, fail, sector):
        eng, _ = _ten_sector_setup(ok, fail)
        snap = eng.compute()
        return snap, eng.adjust_ai_score(60.0, sector)

    def test_zero_coverage(self):
        snap, adj = self._adj([], [f'S{i}' for i in range(10)], 'Sector0')
        self.assertEqual(snap['sector_status'], 'INSUFFICIENT_COVERAGE')
        self.assertEqual(snap['coverage']['coverage_pct'], 0.0)
        self.assertEqual(adj['adjustment'], 0)
        self.assertEqual(adj['adjusted_score'], 60.0)

    def test_twenty_pct_coverage(self):
        snap, adj = self._adj(['S0', 'S1'], [f'S{i}' for i in range(2, 10)], 'Sector0')
        self.assertEqual(snap['sector_status'], 'INSUFFICIENT_COVERAGE')
        self.assertEqual(snap['coverage']['coverage_pct'], 20.0)
        self.assertEqual(adj['adjustment'], 0)
        self.assertEqual(adj['momentum_score'], 50.0)  # neutralized for MIS

    def test_fifty_pct_coverage(self):
        snap, adj = self._adj([f'S{i}' for i in range(5)], [f'S{i}' for i in range(5, 10)], 'Sector0')
        self.assertEqual(snap['coverage']['coverage_pct'], 50.0)
        self.assertEqual(snap['sector_status'], 'INSUFFICIENT_COVERAGE')
        self.assertEqual(adj['adjustment'], 0)

    def test_exactly_70pct_coverage_sufficient(self):
        eng, _ = _ten_sector_setup([f'S{i}' for i in range(7)], [f'S{i}' for i in range(7, 10)])
        snap = eng.compute()
        self.assertEqual(snap['coverage']['coverage_pct'], 70.0)
        self.assertEqual(snap['sector_status'], 'SUFFICIENT')
        # Sector0 has the steepest history → top-1 → +5 as before the gate
        adj = eng.adjust_ai_score(60.0, 'Sector0')
        self.assertEqual(adj['adjustment'], 5)
        self.assertEqual(adj['adjusted_score'], 65.0)

    def test_full_coverage_scoring_unchanged(self):
        eng, _ = _ten_sector_setup([f'S{i}' for i in range(10)], [])
        snap = eng.compute()
        self.assertEqual(snap['coverage']['coverage_pct'], 100.0)
        self.assertEqual(snap['sector_status'], 'SUFFICIENT')
        self.assertEqual(eng.adjust_ai_score(60.0, 'Sector0')['adjustment'], 5)
        self.assertEqual(eng.adjust_ai_score(60.0, 'Sector9')['adjustment'], -5)
        self.assertEqual(eng.adjust_ai_score(60.0, 'Sector5')['adjustment'], 0)

    def test_sector_count_gate_blocks_symbol_rich_snapshot(self):
        """80% symbol coverage but only 2 distinct sectors → insufficient."""
        syms = [f'S{i}' for i in range(10)]
        sector_map = {s: ('Banking' if i < 4 else 'IT') for i, s in enumerate(syms)}
        hists = {s: up_hist(100, 110 + i) for i, s in enumerate(syms[:8])}
        md = CountingMarketData(hists, fail=set(syms[8:]))
        eng = SectorRotationEngine(market_data=md, store=None,
                                   universe=syms, sector_map=sector_map)
        snap = eng.compute()
        self.assertEqual(snap['coverage']['coverage_pct'], 80.0)
        self.assertEqual(snap['coverage']['sectors_covered'], 2)
        self.assertEqual(snap['sector_status'], 'INSUFFICIENT_COVERAGE')
        self.assertEqual(eng.adjust_ai_score(60.0, 'Banking')['adjustment'], 0)

    def test_rate_limit_failures_count_as_failed(self):
        syms = [f'S{i}' for i in range(10)]
        sector_map = {s: f'Sec{i}' for i, s in enumerate(syms)}
        hists = {s: up_hist(100, 110) for s in syms[:3]}
        md = CountingMarketData(hists, fail=set(syms[3:]))  # 70% rate-limited
        eng = SectorRotationEngine(market_data=md, store=None,
                                   universe=syms, sector_map=sector_map)
        snap = eng.compute()
        self.assertEqual(snap['coverage']['symbols_succeeded'], 3)
        self.assertEqual(snap['sector_status'], 'INSUFFICIENT_COVERAGE')
        self.assertEqual(eng.adjust_ai_score(60.0, 'Sec0')['adjustment'], 0)

    def test_malformed_short_history_counts_as_failed(self):
        syms = [f'S{i}' for i in range(10)]
        sector_map = {s: f'Sec{i}' for i, s in enumerate(syms)}
        short = make_hist([100.0] * 10, n=10)  # <30 rows → _fetch_returns None
        hists = {s: (up_hist(100, 110) if i < 5 else short) for i, s in enumerate(syms)}
        md = CountingMarketData(hists)
        eng = SectorRotationEngine(market_data=md, store=None,
                                   universe=syms, sector_map=sector_map)
        snap = eng.compute()
        self.assertEqual(snap['coverage']['symbols_succeeded'], 5)
        self.assertEqual(snap['sector_status'], 'INSUFFICIENT_COVERAGE')

    def test_legacy_snapshot_without_coverage_fails_neutral(self):
        """Old store rows have no coverage metadata → adj 0, no recompute."""
        import tempfile
        from persistence import TradingStore
        fd, db = tempfile.mkstemp(suffix='.db')
        os.close(fd)
        try:
            store = TradingStore(db)
            store.save_sector_rotation({
                'timestamp': datetime.now().isoformat(),
                'top5_strong': [{'sector': 'Banking', 'momentum_score': 90}],
                'top5_weak': [],
                'all_sectors': [{'sector': 'Banking', 'momentum_score': 90}],
                # no coverage / sector_status — pre-gate format
            })
            md = CountingMarketData({})
            eng = SectorRotationEngine(market_data=md, store=store,
                                       universe=['X'], sector_map={'X': 'Banking'})
            adj = eng.adjust_ai_score(60.0, 'Banking')
            self.assertEqual(adj['adjustment'], 0)
            self.assertEqual(adj['adjusted_score'], 60.0)
            self.assertEqual(adj['sector_status'], 'INSUFFICIENT_COVERAGE')
            self.assertEqual(md.calls, [])  # no extra broker calls
        finally:
            os.remove(db)

    def test_stale_snapshot_recomputed_still_neutral(self):
        """A stale SUFFICIENT snapshot is age-gated out; recompute with all
        fetches failing produces an insufficient (neutral) snapshot."""
        eng, _ = _ten_sector_setup([], [f'S{i}' for i in range(10)])
        eng._latest = {
            'timestamp': '2020-01-01T00:00:00',
            'sector_status': 'SUFFICIENT',
            'top5_strong': [{'sector': 'Sector0', 'momentum_score': 95}],
            'top5_weak': [],
            'all_sectors': [{'sector': 'Sector0', 'momentum_score': 95}],
        }
        adj = eng.adjust_ai_score(60.0, 'Sector0')
        self.assertEqual(adj['adjustment'], 0)

    def test_sufficient_snapshot_needs_no_api_calls(self):
        """adjust_ai_score on a fresh stored SUFFICIENT row must not fetch."""
        import tempfile
        from persistence import TradingStore
        fd, db = tempfile.mkstemp(suffix='.db')
        os.close(fd)
        try:
            store = TradingStore(db)
            store.save_sector_rotation({
                'timestamp': datetime.now().isoformat(),
                'sector_status': 'SUFFICIENT',
                'coverage': {'sufficient': True},
                'top5_strong': [{'sector': 'Banking', 'momentum_score': 90}],
                'top5_weak': [],
                'all_sectors': [{'sector': 'Banking', 'momentum_score': 90}],
            })
            md = CountingMarketData({})
            eng = SectorRotationEngine(market_data=md, store=store,
                                       universe=['X'], sector_map={'X': 'Banking'})
            adj = eng.adjust_ai_score(60.0, 'Banking')
            self.assertEqual(adj['adjustment'], 5)
            self.assertEqual(md.calls, [])
        finally:
            os.remove(db)


def _uneven_sector_setup(big_sectors_ok, small_sector_count=13):
    """Uneven sector sizes: each 'big' sector has 10 symbols (all succeed),
    each 'small' sector has 1 symbol (all fail) — lets coverage_pct stay
    high while sectors_covered stays controlled."""
    syms, sector_map, hists, fail = [], {}, {}, set()
    for i in range(big_sectors_ok):
        for j in range(10):
            s = f'BIG{i}_{j}'
            syms.append(s)
            sector_map[s] = f'BigSector{i}'
            hists[s] = up_hist(100, 110 + (10 - i) * 3 + j)
    for i in range(small_sector_count):
        s = f'SML{i}'
        syms.append(s)
        sector_map[s] = f'SmallSector{i}'
        fail.add(s)
    md = CountingMarketData(hists, fail=fail)
    eng = SectorRotationEngine(market_data=md, store=None,
                               universe=syms, sector_map=sector_map)
    return eng


class TestSectorCoverageDenominator(unittest.TestCase):
    """Sector-count gate at the real configured-universe boundary."""

    def test_12_of_24_sectors_sufficient(self):
        eng = _uneven_sector_setup(12, 12)  # 24 configured, 12 covered, 120/132 ok
        snap = eng.compute()
        self.assertEqual(snap['coverage']['sectors_configured'], 24)
        self.assertEqual(snap['coverage']['sectors_covered'], 12)
        self.assertEqual(snap['coverage']['min_sectors_required'], 12)
        self.assertGreaterEqual(snap['coverage']['coverage_pct'], 70.0)
        self.assertEqual(snap['sector_status'], 'SUFFICIENT')
        self.assertEqual(eng.adjust_ai_score(60.0, 'BigSector0')['adjustment'], 5)

    def test_11_of_24_sectors_neutral_despite_high_symbol_coverage(self):
        eng = _uneven_sector_setup(11, 13)  # 24 configured, 11 covered, 110/123 ok
        snap = eng.compute()
        self.assertEqual(snap['coverage']['sectors_covered'], 11)
        self.assertEqual(snap['coverage']['coverage_pct'], 89.4)
        self.assertEqual(snap['sector_status'], 'INSUFFICIENT_COVERAGE')
        self.assertEqual(eng.adjust_ai_score(60.0, 'BigSector0')['adjustment'], 0)

    def test_2_of_24_sectors_neutral(self):
        eng = _uneven_sector_setup(2, 22)  # today's production shape
        snap = eng.compute()
        self.assertEqual(snap['coverage']['sectors_covered'], 2)
        self.assertEqual(snap['sector_status'], 'INSUFFICIENT_COVERAGE')
        self.assertEqual(eng.adjust_ai_score(60.0, 'BigSector0')['adjustment'], 0)


class TestSectorRotationMigration(unittest.TestCase):
    """Pre-gate DB schema must migrate cleanly and preserve old rows."""

    def test_legacy_schema_migrates_and_preserves_rows(self):
        import sqlite3 as _sq
        import tempfile
        from persistence import TradingStore
        fd, db = tempfile.mkstemp(suffix='.db')
        os.close(fd)
        try:
            # Simulate a pre-gate database: old schema + one legacy row.
            conn = _sq.connect(db)
            conn.execute("""CREATE TABLE sector_rotation (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                nifty_7d REAL NOT NULL DEFAULT 0,
                nifty_30d REAL NOT NULL DEFAULT 0,
                top5_strong TEXT NOT NULL DEFAULT '[]',
                top5_weak TEXT NOT NULL DEFAULT '[]',
                all_sectors TEXT NOT NULL DEFAULT '[]')""")
            conn.execute(
                "INSERT INTO sector_rotation (timestamp,nifty_7d,nifty_30d,top5_strong,top5_weak,all_sectors) "
                "VALUES ('2026-10-26T16:47:00',1.2,3.4,'[{\"sector\":\"Banking\",\"momentum_score\":100}]','[]','[]')")
            conn.commit()
            conn.close()

            store = TradingStore(db)
            # Legacy row readable; absent metadata fails neutral downstream.
            row = store.get_latest_sector_rotation()
            self.assertEqual(row['coverage'], {})
            self.assertIsNone(row['sector_status'])
            self.assertEqual(row['top5_strong'][0]['sector'], 'Banking')

            # Idempotent migration: repeated ensure + second store → no error.
            with store._conn() as c:
                store._ensure_sector_rotation_columns(c)
            with store._conn() as c:
                store._ensure_sector_rotation_columns(c)
            store2 = TradingStore(db)

            # New row round-trips coverage metadata without losing old rows.
            store2.save_sector_rotation({
                'timestamp': datetime.now().isoformat(),
                'sector_status': 'SUFFICIENT',
                'coverage': {'symbols_attempted': 150, 'symbols_succeeded': 140,
                             'coverage_pct': 93.3, 'sufficient': True},
                'top5_strong': [{'sector': 'IT', 'momentum_score': 88}],
                'all_sectors': [{'sector': 'IT', 'momentum_score': 88}],
            })
            latest = store2.get_latest_sector_rotation()
            self.assertEqual(latest['sector_status'], 'SUFFICIENT')
            self.assertEqual(latest['coverage']['symbols_succeeded'], 140)
            cnt = _sq.connect(db).execute('SELECT COUNT(*) FROM sector_rotation').fetchone()[0]
            self.assertEqual(cnt, 2)
        finally:
            os.remove(db)


if __name__ == '__main__':
    unittest.main()
