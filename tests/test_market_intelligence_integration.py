"""
Integration test for unified market intelligence in EnterpriseAIDecisionEngine.
"""
import os
import sys
import unittest

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from enterprise_ai_decision_engine import EnterpriseAIDecisionEngine


class FakeBreadth:
    def adjust_ai_score(self, score):
        return {'adjusted_score': score + 2, 'breadth_score': 60, 'adjustment': 2, 'market_strength': 'BULLISH'}


class FakeSector:
    def adjust_ai_score(self, score, sector):
        return {'adjusted_score': score + 1, 'momentum_score': 65, 'rank': 2, 'sector': sector, 'adjustment': 1}


class FakeFii:
    def adjust_ai_score(self, score):
        return {'adjusted_score': score + 3, 'fii_net': 100, 'dii_net': 50, 'net_flow': 150, 'sentiment': 'POSITIVE', 'adjustment': 3}


class FakeOptions:
    def adjust_confidence(self, confidence):
        return {'adjusted_confidence': confidence + 5, 'pcr': 1.1, 'max_pain': 22500, 'long_buildup': True, 'short_buildup': False, 'strong_oi_support': True, 'confidence_boost': 5}


class FakeGlobal:
    def adjust_confidence(self, confidence):
        return {'adjusted_confidence': confidence + 2, 'sentiment_score': 72, 'adjustment': 2}


class FakeVix:
    def compute(self):
        return {'vix': 14.5, 'volatility_score': 75, 'risk_factor': 0.85, 'risk_level': 'LOW'}


class FakeEvents:
    def risk_status(self):
        return {'no_new_buy': False, 'reduce_size': False, 'size_factor': 1.0, 'next_event': None, 'reason': 'No upcoming events'}


class TestMarketIntelligenceIntegration(unittest.TestCase):

    def _make_hist(self, n=30):
        idx = pd.date_range(end='2026-08-03', periods=n, freq='D')
        close = 100 + pd.Series(range(n), index=idx) * 0.5
        return pd.DataFrame({
            'Open': close,
            'High': close + 1,
            'Low': close - 1,
            'Close': close,
            'Volume': 100000,
        })

    def test_combined_market_intelligence_score(self):
        engine = EnterpriseAIDecisionEngine(market_data=None)
        engine.breadth_engine = FakeBreadth()
        engine.sector_rotation = FakeSector()
        engine.fii_dii_engine = FakeFii()
        engine.options_intelligence = FakeOptions()
        engine.global_markets = FakeGlobal()
        engine.vix_engine = FakeVix()
        engine.economic_events = FakeEvents()

        hist = self._make_hist()
        research = {
            'market_regime': 'BULL',
            'sector': 'IT',
            'technical_score': 75,
            'technical_analysis': {'rsi': 55, 'macd': 0.5, 'above_ema': True, 'above_vwap': True, 'support': 100, 'resistance': 110},
        }

        result = engine.compute_scores('RELIANCE', float(hist['Close'].iloc[-1]), hist, research)

        self.assertIn('market_intelligence_score', result['score_components'])
        self.assertIn('vix', result['score_components'])
        self.assertIn('vix_volatility_score', result['score_components'])
        self.assertIn('global_sentiment_score', result['score_components'])
        self.assertIn('economic_event_size_factor', result['score_components'])
        self.assertTrue(0 <= result['final_confidence'] <= 100)
        self.assertTrue(0 <= result['score_components']['market_intelligence_score'] <= 100)
        self.assertIn('VIX Risk', result['explain']['text'])
        self.assertIn('Market Intelligence', result['explain']['text'])


if __name__ == '__main__':
    unittest.main()
