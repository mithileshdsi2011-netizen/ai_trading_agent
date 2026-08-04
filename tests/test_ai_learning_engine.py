"""
Unit tests for src/ai_learning_engine.py
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from ai_learning_engine import EnterpriseLearningEngine


class FakeStore:
    def __init__(self):
        self.training = []
        self.feature_importance = []
        self.model_weights = []
        self.metrics = []

    def save_ai_training_data(self, s):
        self.training.append(s)

    def count_ai_training_data(self):
        return len(self.training)

    def prune_ai_training_data(self, keep):
        if len(self.training) > keep:
            self.training = self.training[-keep:]

    def get_ai_training_data(self, limit=5000):
        return list(self.training[-limit:])

    def save_feature_importance(self, s):
        self.feature_importance.append(s)

    def get_latest_feature_importance(self, limit=30):
        return self.feature_importance[-limit:]

    def save_model_weights(self, s):
        self.model_weights.append(s)

    def get_latest_model_weights(self, limit=30):
        return self.model_weights[-limit:]

    def save_learning_metrics(self, s):
        self.metrics.append(s)

    def get_latest_learning_metrics(self):
        return self.metrics[-1] if self.metrics else None

    def get_learning_curve(self, limit=30):
        return self.metrics[-limit:][::-1]


class FakeDecisionEngine:
    def __init__(self):
        self.weights = {}
        self.updated = None

    def update_weights(self, w):
        self.updated = w


def _trade(pnl=100, ts='2026-07-01T10:00:00', **kwargs):
    t = {
        'id': 'T1',
        'entry_date': ts,
        'exit_date': ts,
        'net_pnl': pnl,
        'score_components': {
            'technical': 75,
            'market_intelligence_score': 70,
            'sector': 60,
            'breadth_score': 55,
            'vix': {'vix': 15},
            'fii_dii': {'net_flow': 120},
            'options_intelligence': {'pcr': 1.1},
            'global_markets': {'sentiment_score': 72},
            'final_confidence': 0.85,
        },
        'technical_analysis': {'rsi': 55, 'macd': 0.5, 'above_ema': 1, 'above_vwap': 1, 'support': 100, 'resistance': 110},
        '_enterprise_risk': {'volatility': {'atr_pct': 0.02, 'beta': 1.1}},
        'exit_reason': 'target',
    }
    t.update(kwargs)
    return t


class TestEnterpriseLearningEngine(unittest.TestCase):

    def test_extract_features(self):
        store = FakeStore()
        eng = EnterpriseLearningEngine(store=store, async_retrain=False)
        f = eng._extract_features(_trade())
        self.assertEqual(f['win'], 1)
        self.assertEqual(f['technical_score'], 75)
        self.assertIn('market_intelligence_score', f)
        self.assertEqual(f['exit_reason'], 'target')

    def test_on_trade_closed_saves(self):
        store = FakeStore()
        eng = EnterpriseLearningEngine(store=store, retrain_every=100, async_retrain=False)
        eng.on_trade_closed(_trade())
        self.assertEqual(len(store.training), 1)

    def test_retrain_computes_importance_and_weights(self):
        store = FakeStore()
        eng = EnterpriseLearningEngine(store=store, retrain_every=2, async_retrain=False)
        for i in range(10):
            t = _trade(pnl=100 + i * 5)
            eng.on_trade_closed(t)
        self.assertGreater(len(store.feature_importance), 0)
        self.assertGreater(len(store.model_weights), 0)
        self.assertGreater(len(store.metrics), 0)

    def test_retrain_updates_decision_engine_weights(self):
        de = FakeDecisionEngine()
        store = FakeStore()
        eng = EnterpriseLearningEngine(store=store, retrain_every=2, async_retrain=False, decision_engine=de)
        for i in range(10):
            t = _trade(pnl=100 + i * 5)
            eng.on_trade_closed(t)
        self.assertIsNotNone(de.updated)
        self.assertIn('technical', de.updated)

    def test_rolling_prune(self):
        store = FakeStore()
        eng = EnterpriseLearningEngine(store=store, retrain_every=1000, max_history=5, async_retrain=False)
        for i in range(10):
            eng.on_trade_closed(_trade())
        self.assertEqual(len(store.training), 5)

    def test_dashboard_data(self):
        store = FakeStore()
        eng = EnterpriseLearningEngine(store=store, retrain_every=2, async_retrain=False)
        for i in range(4):
            eng.on_trade_closed(_trade())
        data = eng.get_dashboard_data()
        self.assertIn('metrics', data)
        self.assertIn('top_indicators', data)
        self.assertIn('model_weights', data)

    def test_empty_retrain(self):
        store = FakeStore()
        eng = EnterpriseLearningEngine(store=store, async_retrain=False)
        result = eng.retrain()
        self.assertEqual(result['trades_used'], 0)

    def test_learning_curve(self):
        store = FakeStore()
        eng = EnterpriseLearningEngine(store=store, retrain_every=2, async_retrain=False)
        for i in range(6):
            eng.on_trade_closed(_trade())
        curve = eng.get_dashboard_data()['learning_curve']
        self.assertGreaterEqual(len(curve), 1)

    def test_win_flag_negative(self):
        store = FakeStore()
        eng = EnterpriseLearningEngine(store=store, async_retrain=False)
        f = eng._extract_features(_trade(pnl=-50))
        self.assertEqual(f['win'], 0)


if __name__ == '__main__':
    unittest.main()
