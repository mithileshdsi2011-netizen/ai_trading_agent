"""
Unit tests for src/market_intelligence.py
"""
import os
import sys
import unittest
from types import SimpleNamespace

import pandas as pd
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from market_intelligence import MarketBreadthEngine


def fake_close_series(trend='up'):
    n = 250
    if trend == 'up':
        base = np.linspace(100, 130, n)
    elif trend == 'down':
        base = np.linspace(130, 100, n)
    else:
        base = np.full(n, 115.0)
    noise = np.random.RandomState(7).randn(n) * 1.5
    return pd.Series(base + noise)


def make_hist(close):
    arr = np.asarray(close, dtype=float)
    idx = pd.date_range(end='2026-08-03', periods=len(arr), freq='D')
    return pd.DataFrame({
        'Open': arr * 0.99,
        'High': arr * 1.01,
        'Low': arr * 0.98,
        'Close': arr,
        'Volume': np.full(len(arr), 100000),
    }, index=idx)


class FakeMarketData:
    def __init__(self, symbol_hists):
        self._hists = symbol_hists

    def get_stock_data(self, symbol, period, interval):
        return self._hists.get(symbol, pd.DataFrame())

    def get_batch_stock_data(self, symbols, period=None, interval=None):
        return {s: self._hists.get(s, pd.DataFrame()).copy() for s in symbols}

    def get_batch_stock_info(self, symbols):
        info = {}
        for s in symbols:
            df = self._hists.get(s)
            if df is not None and not df.empty and 'Close' in df.columns:
                close = df['Close'].astype(float)
                current = float(close.iloc[-1])
                prev = float(close.iloc[-2]) if len(close) > 1 else current
                info[s] = {'current_price': current, 'day_close': prev}
        return info


class TestMarketBreadthEngine(unittest.TestCase):

    def setUp(self):
        # Construct symbols with strong uptrend -> nearly all above EMAs
        ups = {f'SYM{i}': make_hist(fake_close_series('up')) for i in range(10)}
        # Insert one downtrend for a decline count
        ups['SYM9'] = make_hist(fake_close_series('down'))
        self.market_data = FakeMarketData(ups)
        self.engine = MarketBreadthEngine(market_data=self.market_data, store=None, universe=list(ups.keys()))

    def test_compute_returns_required_keys(self):
        result = self.engine.compute()
        for k in ["advance", "decline", "ad_ratio", "above20", "above50", "above200", "breadth_score", "market_strength"]:
            self.assertIn(k, result)
        self.assertTrue(0 <= result["breadth_score"] <= 100)
        self.assertIn(result["market_strength"], {"BULLISH", "NEUTRAL", "BEARISH"})

    def test_advance_decline_counts(self):
        result = self.engine.compute()
        total = result["advance"] + result["decline"] + result.get("unchanged", 0)
        self.assertEqual(total, 10)

    def test_percentage_bounds(self):
        result = self.engine.compute()
        for k in ["above20", "above50", "above200"]:
            self.assertTrue(0 <= result[k] <= 100)

    def test_bullish_score_boosts_ai(self):
        # Fake a bullish snapshot by directly setting latest
        self.engine._latest = {
            "breadth_score": 80.0,
            "market_strength": "BULLISH",
            "total": 10,
            "timestamp": "2026-08-03T09:15:00",
        }
        adj = self.engine.adjust_ai_score(60.0)
        self.assertEqual(adj["adjustment"], 5)
        self.assertEqual(adj["adjusted_score"], 65.0)

    def test_bearish_score_penalises_ai(self):
        self.engine._latest = {
            "breadth_score": 40.0,
            "market_strength": "BEARISH",
            "total": 10,
            "timestamp": "2026-08-03T09:15:00",
        }
        adj = self.engine.adjust_ai_score(60.0)
        self.assertEqual(adj["adjustment"], -10)
        self.assertEqual(adj["adjusted_score"], 50.0)


if __name__ == '__main__':
    unittest.main()
