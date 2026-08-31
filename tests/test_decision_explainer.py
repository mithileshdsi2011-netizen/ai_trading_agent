"""Regression tests for DecisionExplainer."""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import pytest
import re
from decision_explainer import DecisionExplainer


def _score_result(total=80, components=None):
    if components is None:
        components = {
            'trend': 15, 'rsi': 8, 'macd': 15,
            'volume': 18, 'sector': 6, 'sentiment': 5, 'regime': 8,
        }
    return {
        'total_score': total,
        'components': components,
        'size_fraction': 1.0,
        'skip': False,
        'grade': 'A',
    }


def test_format_buy_contains_key_fields():
    e = DecisionExplainer()
    out = e.format_buy(
        symbol='RELIANCE',
        score_result=_score_result(),
        mtf_result={'aligned': True},
        confidence=72,
        overall_score=0.84,
        rr=2.3,
        regime='BULL',
    )
    assert 'BUY RELIANCE' in out
    assert 'Trade Score:        80' in out
    assert 'Overall AI Score:   0.84' in out
    assert re.search(r'Confidence:\s+72%', out)
    assert 'Risk:Reward:        2.3' in out
    assert 'Regime:             BULL' in out
    assert 'Decision:           BUY' in out
    assert 'Trend' in out
    assert 'MTF               PASS' in out


def test_format_skip_with_score_breakdown():
    e = DecisionExplainer()
    out = e.format_skip(
        symbol='HAPPSTMNDS',
        reason='MTF not aligned',
        score_result=_score_result(total=63, components={
            'trend': 12, 'rsi': 3, 'macd': 15,
            'volume': 12, 'sector': 4, 'sentiment': 0, 'regime': 6,
        }),
        mtf_result={'aligned': False},
        confidence=60,
        overall_score=0.37,
        rr=1.2,
        regime='SIDEWAYS',
    )
    assert 'SKIP HAPPSTMNDS' in out
    assert 'Decision:           SKIP' in out
    assert 'Trade Score:        63' in out
    assert re.search(r'Confidence:\s+60%', out)
    assert 'MTF               FAIL' in out


def test_format_skip_without_score_is_simple():
    e = DecisionExplainer()
    out = e.format_skip(
        symbol='TCS',
        reason='Already held',
    )
    assert 'SKIP TCS' in out
    assert 'Already held' in out
    assert 'Score Breakdown' not in out
