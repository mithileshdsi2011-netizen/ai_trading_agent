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


if __name__ == '__main__':
    unittest.main()
