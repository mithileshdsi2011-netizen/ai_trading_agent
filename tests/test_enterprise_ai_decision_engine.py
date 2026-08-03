"""
Unit tests for src/enterprise_ai_decision_engine.py
"""
import os
import sys
import unittest
from types import SimpleNamespace

import pandas as pd
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from enterprise_ai_decision_engine import EnterpriseAIDecisionEngine


def fake_hist(symbol='RELIANCE'):
    dates = pd.date_range(end='2026-08-03', periods=60, freq='D')
    close = 100 + np.linspace(0, 15, 60) + np.random.RandomState(42).randn(60) * 2
    volume = np.full(60, 1_00_000)
    volume[-1] = 2_00_000
    return pd.DataFrame({
        'Open': close * 0.99,
        'High': close * 1.01,
        'Low': close * 0.98,
        'Close': close,
        'Volume': volume,
    }, index=dates)


class FakeJournal:
    def __init__(self, trades=None):
        self._trades = trades or []

    def _load(self):
        return self._trades


class TestEnterpriseAIDecisionEngine(unittest.TestCase):

    def setUp(self):
        self.journal = FakeJournal()
        self.engine = EnterpriseAIDecisionEngine(market_data=None, trade_journal=self.journal)

    def test_compute_scores_full(self):
        research = {
            'recommendation': 'BUY',
            'market_regime': 'BULL',
            'sector_momentum': 0.85,
            'technical_score': 82,
            'confidence': 0.7,
            'technical_analysis': {'rsi': 60, 'macd': 0.5, 'above_ema': True, 'above_vwap': True, 'support': 95, 'resistance': 115},
            'news_sentiment_score': 0.6,
            'sector': 'IT',
        }
        result = self.engine.compute_scores('RELIANCE', current_price=110, hist=fake_hist(), research=research)

        self.assertIn('final_score', result)
        self.assertIn('sub_scores', result)
        self.assertIn('final_confidence', result)
        self.assertIn('explain', result)
        self.assertIn('threshold', result)
        self.assertTrue(0 <= result['final_score'] <= 100)
        self.assertTrue(0 <= result['final_confidence'] <= 100)

    def test_dynamic_threshold_bull(self):
        self.assertEqual(self.engine._dynamic_threshold('BULL'), 65)

    def test_dynamic_threshold_sideways(self):
        self.assertEqual(self.engine._dynamic_threshold('SIDEWAYS'), 75)

    def test_dynamic_threshold_bear(self):
        self.assertEqual(self.engine._dynamic_threshold('BEAR'), 90)

    def test_historical_score_no_data(self):
        score, conf = self.engine._historical_success_score('UNKNOWN')
        self.assertEqual(score, 50.0)

    def test_adaptive_weights(self):
        trades = [
            {'symbol': 'RELIANCE', 'action': 'BUY', 'status': 'CLOSED', 'net_pnl': 500,
             'score_components': {'technical': 80, 'sector': 85}},
            {'symbol': 'RELIANCE', 'action': 'BUY', 'status': 'CLOSED', 'net_pnl': -100,
             'score_components': {'technical': 40, 'sector': 85}},
            {'symbol': 'RELIANCE', 'action': 'BUY', 'status': 'CLOSED', 'net_pnl': 300,
             'score_components': {'technical': 80, 'sector': 60}},
        ]
        engine = EnterpriseAIDecisionEngine(market_data=None, trade_journal=FakeJournal(trades))
        total = round(sum(engine.weights.values()), 3)
        self.assertAlmostEqual(total, 1.0, places=2)
        # Weights should remain positive
        for w in engine.weights.values():
            self.assertGreater(w, 0)


if __name__ == '__main__':
    unittest.main()
