"""
Unit tests for src/sector_rotation.py
"""
import os
import sys
import unittest
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
            'timestamp': '2026-08-03T09:15:00',
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
            'timestamp': '2026-08-03T09:15:00',
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


if __name__ == '__main__':
    unittest.main()
