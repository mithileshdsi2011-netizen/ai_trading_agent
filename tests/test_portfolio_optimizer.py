"""
Unit tests for src/portfolio_optimizer.py
"""
import os
import sys
import unittest

import pandas as pd
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from portfolio_optimizer import EnterprisePortfolioOptimizer


class FakeStore:
    def __init__(self):
        self._po = None
        self._cm = None
        self._metrics = None
        self._alloc = []

    def save_portfolio_optimizer(self, s):
        self._po = s

    def get_latest_portfolio_optimizer(self):
        return self._po

    def save_correlation_matrix(self, s):
        self._cm = s

    def get_latest_correlation_matrix(self):
        return self._cm

    def save_portfolio_metrics(self, s):
        self._metrics = s

    def get_latest_portfolio_metrics(self):
        return self._metrics

    def save_allocation_history(self, s):
        self._alloc.append(s)

    def get_allocation_history(self, limit=50):
        return self._alloc[-limit:]


class TestEnterprisePortfolioOptimizer(unittest.TestCase):

    def test_analyze_empty_portfolio(self):
        opt = EnterprisePortfolioOptimizer(store=FakeStore())
        result = opt.analyze(positions=[], cash=15000, regime='BULL')
        self.assertEqual(result['capital_used'], 0)
        self.assertAlmostEqual(result['capital_limit_pct'], 0.9, places=2)
        self.assertIn('cash_remaining', result)
        self.assertGreaterEqual(result['diversification_score'], 0)

    def test_analyze_with_positions_and_sectors(self):
        positions = [
            {'symbol': 'INFY', 'quantity': 10, 'entry_price': 1500, 'sector': 'IT', 'industry': 'Software'},
            {'symbol': 'HDFCBANK', 'quantity': 10, 'entry_price': 1500, 'sector': 'Banking', 'industry': 'Banks'},
            {'symbol': 'RELIANCE', 'quantity': 10, 'entry_price': 2500, 'sector': 'Energy', 'industry': 'Oil'},
        ]
        opt = EnterprisePortfolioOptimizer(store=FakeStore())
        result = opt.analyze(positions=positions, cash=15000, regime='BULL')
        self.assertEqual(result['capital_used'], 55000)
        self.assertIn('IT', result['sector_exposure'])
        self.assertIn('Banking', result['sector_exposure'])
        self.assertIn('Energy', result['sector_exposure'])
        self.assertGreaterEqual(result['diversification_score'], 0)

    def test_dynamic_capital_limits(self):
        opt = EnterprisePortfolioOptimizer(store=FakeStore())
        self.assertAlmostEqual(opt.dynamic_capital_limit('BULL', 0), 0.90)
        self.assertAlmostEqual(opt.dynamic_capital_limit('SIDEWAYS', 0), 0.60)
        self.assertAlmostEqual(opt.dynamic_capital_limit('BEAR', 0), 0.30)
        # High VIX caps
        self.assertAlmostEqual(opt.dynamic_capital_limit('BULL', 25), 0.40)

    def test_correlation_matrix(self):
        idx = pd.date_range(end='2026-08-03', periods=10, freq='D')
        # Two series that move together
        a = pd.Series([100, 101, 102, 103, 104, 105, 106, 107, 108, 109], index=idx)
        b = pd.Series([200, 202, 204, 206, 208, 210, 212, 214, 216, 218], index=idx)
        hist = {'A': a, 'B': b}
        opt = EnterprisePortfolioOptimizer(store=FakeStore())
        cm = opt.correlation_matrix([{'symbol': 'A'}, {'symbol': 'B'}], hist=hist)
        self.assertIn('A', cm.columns)
        self.assertIn('B', cm.columns)
        self.assertAlmostEqual(cm.loc['A', 'B'], 1.0, places=2)

    def test_can_open_position_blocks_high_correlation(self):
        idx = pd.date_range(end='2026-08-03', periods=10, freq='D')
        a = pd.Series([100, 101, 102, 103, 104, 105, 106, 107, 108, 109], index=idx)
        c = pd.Series([50, 50.5, 51, 51.5, 52, 52.5, 53, 53.5, 54, 54.5], index=idx)  # highly correlated
        hist = {'A': a, 'C': c}
        positions = [{'symbol': 'A', 'quantity': 1, 'entry_price': 100, 'sector': 'IT'}]
        opt = EnterprisePortfolioOptimizer(store=FakeStore())
        allowed, reason, max_corr = opt.can_open_position('C', positions=positions, hist=hist, threshold=0.80)
        self.assertFalse(allowed)
        self.assertIn('correlation', reason.lower())

    def test_rank_candidates(self):
        candidates = [
            {'symbol': 'A', 'ai_score': 80, 'market_intelligence_score': 70, 'confidence': 0.8, 'risk_reward_ratio': 2.0, 'liquidity_score': 80},
            {'symbol': 'B', 'ai_score': 90, 'market_intelligence_score': 80, 'confidence': 0.85, 'risk_reward_ratio': 2.5, 'liquidity_score': 90},
            {'symbol': 'C', 'ai_score': 60, 'market_intelligence_score': 50, 'confidence': 0.6, 'risk_reward_ratio': 1.5, 'liquidity_score': 70},
        ]
        opt = EnterprisePortfolioOptimizer(store=FakeStore())
        ranked = opt.rank_candidates(candidates, max_slots=2)
        self.assertEqual(len(ranked), 2)
        self.assertEqual(ranked[0]['symbol'], 'B')

    def test_rebalance_suggestions(self):
        positions = [
            {'symbol': 'INFY', 'quantity': 20, 'entry_price': 1500, 'sector': 'IT'},
            {'symbol': 'TCS', 'quantity': 10, 'entry_price': 3000, 'sector': 'IT'},
            {'symbol': 'HDFCBANK', 'quantity': 10, 'entry_price': 1500, 'sector': 'Banking'},
        ]
        # total value = 20*1500 + 10*3000 + 10*1500 + 10000 cash = 85,000; IT = 60,000 (70.5%)
        opt = EnterprisePortfolioOptimizer(store=FakeStore())
        suggestions = opt.rebalance_suggestions(positions, cash=10000, target_sector_pct=0.25)
        self.assertGreater(len(suggestions), 0)
        self.assertEqual(suggestions[0]['action'], 'TRIM')

    def test_persistence(self):
        store = FakeStore()
        opt = EnterprisePortfolioOptimizer(store=store)
        result = opt.analyze(positions=[], cash=15000, regime='BULL')
        opt.persist_analysis(result)
        opt.persist_correlation([{'symbol': 'A'}])
        opt.store.save_portfolio_metrics({'beta': 1.0, 'volatility': 10, 'diversification': 80})
        opt.persist_allocation('A', 1000, 'test')

        self.assertIsNotNone(opt.get_latest_portfolio())
        self.assertIsNotNone(opt.get_latest_metrics())
        self.assertEqual(len(opt.get_latest_allocations()), 1)

    def test_portfolio_beta_and_volatility(self):
        idx = pd.date_range(end='2026-08-03', periods=20, freq='D')
        # stock moves 1.5x benchmark
        benchmark = pd.Series(100 + np.arange(20), index=idx)
        stock = pd.Series(100 + 1.5 * np.arange(20), index=idx)
        positions = [{'symbol': 'STK', 'quantity': 1, 'entry_price': 100, 'sector': 'IT'}]
        hist = {'STK': stock, 'NIFTY 50': benchmark}
        opt = EnterprisePortfolioOptimizer(store=FakeStore())
        beta = opt.portfolio_beta(positions, hist=hist)
        vol = opt.portfolio_volatility(positions, hist=hist)
        self.assertGreater(beta, 1.0)
        self.assertGreater(vol, 0)


if __name__ == '__main__':
    unittest.main()
