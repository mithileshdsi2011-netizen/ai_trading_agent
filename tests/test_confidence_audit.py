"""
Confidence representation audit tests.

These tests document the current (2026-08-11) confidence scale used by each
engine and expose the places where a 0-100 value is double-scaled for display
or incorrectly compared against 0-1 thresholds.
"""

import numpy as np
import pandas as pd
import pytest
from unittest.mock import MagicMock, patch

import sys
sys.path.insert(0, 'src')

from ai_research_agent import AIResearchAgent
from enterprise_ai_decision_engine import EnterpriseAIDecisionEngine
from trade_scorer import TradeScorer


class TestConfidenceSourceRanges:
    """Canonical ranges at the point each engine produces confidence."""

    def test_ai_research_agent_returns_0_1(self):
        agent = AIResearchAgent()
        rec, conf = agent._generate_recommendation(
            overall_score=0.55,
            technical_confidence=0.80,
            news_count=1,
        )
        assert 0.0 <= conf <= 1.0, f"AIResearchAgent confidence should be 0-1, got {conf}"
        assert pytest.approx(conf, 0.01) == 0.85

    def test_enterprise_decision_engine_returns_0_100(self):
        engine = EnterpriseAIDecisionEngine(market_data=MagicMock())
        sub_scores = {'technical': 70, 'sector': 65, 'market': 55}
        confidences = {'technical': 0.8, 'sector': 0.75, 'market': 0.7}
        final_conf = engine._compute_confidence(sub_scores, confidences)
        assert 0.0 <= final_conf <= 100.0, f"Enterprise confidence should be 0-100, got {final_conf}"
        # _determine_action uses min_conf * 100, so 0.55 threshold becomes 55
        action = engine._determine_action(
            final_score=75,
            threshold=68,
            confidence=final_conf,
        )
        assert action == 'BUY'

    def test_enterprise_threshold_conversion(self):
        """0-100 confidence compared against 0-1 config threshold via *100."""
        engine = EnterpriseAIDecisionEngine(market_data=MagicMock())
        engine._last_regime = 'SIDEWAYS'
        assert engine._determine_action(75, 68, 80.0) == 'BUY'
        assert engine._determine_action(75, 68, 54.0) == 'HOLD'
        # min_conf 0.55 * 100 = 55.0; use slightly above to avoid FP edge
        assert engine._determine_action(75, 68, 55.0001) == 'BUY'


class TestLogAndDisplayFormatting:
    """Where the 8503% display bug comes from."""

    def test_signal_log_double_percent(self):
        """
        trading_orchestrator.py logs the signal with
            f"confidence={sig['confidence']:.0%}"
        But the signal confidence is already 0-100, so .0% multiplies again.
        """
        confidence = 85.03  # as stored in the signal
        assert f"{confidence:.0%}" == "8503%"

    def test_tradescore_log_does_not_double_percent(self):
        """
        The TradeScore log uses .2f, so it prints 85.03 for the same value.
        """
        confidence = 85.03
        assert f"{confidence:.2f}" == "85.03"

    def test_canonical_0_1_display(self):
        """
        If the engine ever returns 0-1, the correct display formatting is:
            f"{0.8503:.0%}" == "85%"
            f"{0.8503:.2%}" == "85.03%"
        """
        confidence = 0.8503
        assert f"{confidence:.0%}" == "85%"
        assert f"{confidence:.2%}" == "85.03%"


class TestThresholdInterpretation:
    """Engines that compare confidence to config thresholds."""

    def test_risk_manager_confidence_check_with_0_100(self):
        """
        risk_manager.py does:
            conf_pct = round(signal['confidence'] * 100)
            min_pct  = round(threshold * 100)
            if conf_pct < min_pct: return False
        With signal['confidence']=85.03 (0-100), conf_pct becomes 8503,
        so the gate effectively always passes for any non-zero confidence.
        """
        signal_confidence = 85.03
        threshold = 0.55
        conf_pct = round(signal_confidence * 100)
        min_pct = round(threshold * 100)
        # Demonstrates the magnitudes involved
        assert conf_pct == 8503
        assert min_pct == 55
        assert conf_pct >= min_pct

    def test_risk_manager_confidence_check_if_canonical_0_1(self):
        """
        If confidence were 0-1, the same code would produce 85 vs 55 and still pass.
        The bug only surfaces for low-confidence signals (<0.01 in 0-1 terms).
        """
        signal_confidence = 0.8503
        threshold = 0.55
        conf_pct = round(signal_confidence * 100)
        min_pct = round(threshold * 100)
        assert conf_pct == 85
        assert min_pct == 55
        assert conf_pct >= min_pct

    def test_trade_scorer_is_confidence_agnostic(self):
        """
        TradeScorer does NOT use signal['confidence']; it builds a new 0-100
        score from research technical/sentiment components.
        """
        scorer = TradeScorer()
        signal = {'symbol': 'TEST', 'action': 'BUY', 'current_price': 100}
        research = {
            'technical_analysis': {
                'trend': 'STRONG_UPTREND',
                'rsi': 45,
                'macd_histogram': 1.5,
                'macd_histogram_prev': 1.0,
                'volume_ratio': 2.0,
            },
            'sentiment_analysis': {'score': 0.3, 'news_count': 3},
        }
        result = scorer.score(signal, research, regime='SIDEWAYS')
        assert 0 <= result['total_score'] <= 100
        assert 'skip' in result


class TestExampleSignal:
    """Reconstruct one real signal from the 11 Aug log."""

    def test_example_devyani(self):
        """
        Log line (11 Aug 09:23):
            Signal: DEVYANI action=BUY confidence=8503% score=91.400 rr=2.00
        The raw internal confidence was ~85.03 (0-100 scale).
        .0% formatting produced the displayed 8503%.
        """
        raw_internal = 85.03
        normalized_0_1 = raw_internal / 100.0
        displayed_current = f"{raw_internal:.0%}"    # broken
        displayed_canonical = f"{normalized_0_1:.2%}"  # desired

        assert raw_internal == 85.03
        assert pytest.approx(normalized_0_1, 0.001) == 0.8503
        assert displayed_current == "8503%"
        assert displayed_canonical == "85.03%"
