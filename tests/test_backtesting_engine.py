"""
Unit tests for src/backtesting_engine.py
"""
import os
import sys
import unittest

import pandas as pd
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from backtesting_engine import EnterpriseBacktestEngine


class FakeStore:
    def __init__(self):
        self.backtest = None
        self.walks = []
        self.monte = None

    def save_backtest_run(self, s):
        self.backtest = s
        return 1

    def save_backtest_results(self, s):
        return 1

    def get_latest_backtest_results(self):
        return None

    def save_walk_forward_results(self, s):
        self.walks.append(s)

    def get_latest_walk_forward_results(self, limit=10):
        return self.walks[-limit:][::-1]

    def save_monte_carlo_results(self, s):
        self.monte = s

    def get_latest_monte_carlo_results(self):
        return self.monte


def _make_data(symbols=None, n=60, trend='up'):
    if symbols is None:
        symbols = ['A', 'B']
    idx = pd.date_range(end='2026-08-03', periods=n, freq='D')
    data = {}
    for i, sym in enumerate(symbols):
        base = 100 + np.arange(n) * (0.5 if trend == 'up' else -0.5) + np.random.randn(n) * 0.5 + i * 5
        data[sym] = pd.DataFrame({
            'Open': base,
            'High': base + 1,
            'Low': base - 1,
            'Close': base,
            'Volume': 100000,
        }, index=idx)
    return data


class TestEnterpriseBacktestEngine(unittest.TestCase):

    def test_default_strategy_run(self):
        data = _make_data(['A'], n=40, trend='up')
        engine = EnterpriseBacktestEngine(initial_capital=10000, store=FakeStore())
        result = engine.run(data)
        self.assertIn('total_return_pct', result)
        self.assertIn('sharpe', result)
        self.assertIn('trades_count', result)
        self.assertGreaterEqual(len(result['equity_curve']), 0)

    def test_custom_strategy_bull(self):
        data = _make_data(['A'], n=40, trend='up')
        engine = EnterpriseBacktestEngine(initial_capital=10000, store=FakeStore())
        # always BUY at start, hold
        result = engine.run(data, strategy=lambda sym, price, hist, ctx: 'BUY')
        self.assertEqual(len(result['trades']), 1)  # closed at end
        self.assertGreater(result['final_equity'], 0)

    def test_empty_data(self):
        engine = EnterpriseBacktestEngine(initial_capital=10000, store=FakeStore())
        result = engine.run({}, name='empty')
        self.assertEqual(result['trades_count'], 0)
        self.assertEqual(result['final_equity'], 10000)

    def test_metrics_computed(self):
        data = _make_data(['A', 'B'], n=60, trend='up')
        engine = EnterpriseBacktestEngine(initial_capital=10000, store=FakeStore())
        result = engine.run(data)
        keys = ['cagr_pct', 'total_return_pct', 'win_rate_pct', 'profit_factor',
                'sharpe', 'sortino', 'calmar', 'max_drawdown_pct',
                'avg_win', 'avg_loss', 'expectancy', 'avg_holding_days']
        for k in keys:
            self.assertIn(k, result)

    def test_walk_forward(self):
        data = _make_data(['A', 'B'], n=80, trend='up')
        engine = EnterpriseBacktestEngine(initial_capital=10000, store=FakeStore())
        results = engine.walk_forward(data, train_size=40, test_size=20)
        self.assertGreaterEqual(len(results), 1)
        for r in results:
            self.assertIn('total_return_pct', r)

    def test_monte_carlo(self):
        data = _make_data(['A'], n=60, trend='up')
        engine = EnterpriseBacktestEngine(initial_capital=10000, store=FakeStore())
        result = engine.run(data)
        mc = engine.monte_carlo(result['equity_curve'], n_simulations=100)
        self.assertIn('probability_of_profit', mc)
        self.assertIn('worst_drawdown_pct', mc)
        self.assertIn('ci_95_final_pnl', mc)
        self.assertEqual(mc['n_simulations'], 100)

    def test_compare_strategies(self):
        data = _make_data(['A'], n=60, trend='up')
        engine = EnterpriseBacktestEngine(initial_capital=10000, store=FakeStore())
        current = lambda s, p, h, c: 'BUY' if len(h) > 5 else 'HOLD'
        improved = lambda s, p, h, c: 'BUY'
        comparison = engine.compare_strategies(data, {'current': current, 'improved': improved}, baseline='current')
        self.assertEqual(comparison['baseline'], 'current')
        self.assertEqual(len(comparison['strategies']), 2)

    def test_strategy_improvement_delta(self):
        base = {'cagr_pct': 10}
        other = {'cagr_pct': 15}
        delta = EnterpriseBacktestEngine._improvement(base, other)
        self.assertAlmostEqual(delta['cagr_pct'], 50.0)

    def test_persistence(self):
        store = FakeStore()
        engine = EnterpriseBacktestEngine(initial_capital=10000, store=store)
        data = _make_data(['A'], n=40, trend='up')
        result = engine.run(data)
        engine.save_backtest(result)
        self.assertIsNotNone(store.backtest)

    def test_walk_forward_persistence(self):
        store = FakeStore()
        engine = EnterpriseBacktestEngine(initial_capital=10000, store=store)
        data = _make_data(['A'], n=80, trend='up')
        results = engine.walk_forward(data, train_size=40, test_size=20)
        engine.save_walk_forward(results)
        self.assertEqual(len(store.walks), len(results))

    def test_monte_carlo_persistence(self):
        store = FakeStore()
        engine = EnterpriseBacktestEngine(initial_capital=10000, store=store)
        data = _make_data(['A'], n=40, trend='up')
        result = engine.run(data)
        mc = engine.monte_carlo(result['equity_curve'], n_simulations=50)
        engine.save_monte_carlo(mc)
        self.assertIsNotNone(store.monte)


if __name__ == '__main__':
    unittest.main()
