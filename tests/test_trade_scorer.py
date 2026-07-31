"""Regression tests for the TradeScorer top-3 improvements."""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import pytest
from trade_scorer import TradeScorer


def _make_research(trend='NEUTRAL', rsi=50.0, macd_hist=0.0, macd_prev=0.0,
                   volume_ratio=1.0, sentiment_score=0.0, news_count=0,
                   sector_momentum=0.0):
    return {
        'technical_analysis': {
            'trend': trend,
            'rsi': rsi,
            'macd_histogram': macd_hist,
            'macd_histogram_prev': macd_prev,
            'volume_ratio': volume_ratio,
        },
        'sentiment_analysis': {
            'score': sentiment_score,
            'news_count': news_count,
        },
        'sector_momentum': sector_momentum,
    }


def _base_signal(symbol='TEST'):
    return {'symbol': symbol}


@pytest.fixture
def scorer():
    return TradeScorer()


def test_no_news_sentiment_is_zero(scorer):
    research = _make_research(news_count=0, sentiment_score=0.5)
    result = scorer.score(_base_signal(), research, regime='SIDEWAYS')
    assert result['components']['sentiment'] == 0


def test_positive_news_sentiment_mapped(scorer):
    research = _make_research(news_count=5, sentiment_score=1.0)
    result = scorer.score(_base_signal(), research, regime='SIDEWAYS')
    assert result['components']['sentiment'] == 8


def test_negative_news_sentiment_mapped(scorer):
    research = _make_research(news_count=5, sentiment_score=-1.0)
    result = scorer.score(_base_signal(), research, regime='SIDEWAYS')
    assert result['components']['sentiment'] == 0


def test_rsi_62_still_gets_8_points(scorer):
    research = _make_research(rsi=62.0)
    result = scorer.score(_base_signal(), research, regime='SIDEWAYS')
    assert result['components']['rsi'] == 8


def test_rsi_67_is_penalised_to_3_points(scorer):
    research = _make_research(rsi=67.0)
    result = scorer.score(_base_signal(), research, regime='SIDEWAYS')
    assert result['components']['rsi'] == 3


def test_rsi_72_is_still_overbought_2_points(scorer):
    research = _make_research(rsi=72.0)
    result = scorer.score(_base_signal(), research, regime='SIDEWAYS')
    assert result['components']['rsi'] == 2


def test_sideways_strong_trend_without_mtf_is_halved(scorer):
    research = _make_research(trend='STRONG_UPTREND', volume_ratio=2.5)
    result = scorer.score(_base_signal(), research, regime='SIDEWAYS', mtf_aligned=False)
    # SIDEWAYS strong = 15, halved for no MTF = 7
    assert result['components']['trend'] == 7


def test_sideways_strong_trend_with_mtf_full_credit(scorer):
    research = _make_research(trend='STRONG_UPTREND', volume_ratio=2.5)
    result = scorer.score(_base_signal(), research, regime='SIDEWAYS', mtf_aligned=True)
    assert result['components']['trend'] == 15


def test_bull_ignores_mtf_penalty(scorer):
    research = _make_research(trend='STRONG_UPTREND', volume_ratio=2.5)
    result = scorer.score(_base_signal(), research, regime='BULL', mtf_aligned=False)
    assert result['components']['trend'] == 25


def test_historical_sideways_losers_now_score_lower(scorer):
    """HAPPSTMNDS and LODHA: STRONG_UPTREND in SIDEWAYS, no MTF, high RSI."""
    # HAPPSTMNDS-like
    r1 = _make_research(trend='STRONG_UPTREND', rsi=66.8, volume_ratio=1.43,
                        macd_hist=0.9017, macd_prev=0.5, news_count=0)
    res1 = scorer.score(_base_signal(), r1, regime='SIDEWAYS', mtf_aligned=False)
    # SIDEWAYS strong 15, halved 7, rsi 66.8 -> 3, sentiment no news -> 0
    assert res1['components']['trend'] == 7
    assert res1['components']['rsi'] == 3
    assert res1['components']['sentiment'] == 0
    assert res1['total_score'] < 70

    # LODHA-like
    r2 = _make_research(trend='STRONG_UPTREND', rsi=69.5, volume_ratio=2.08,
                        macd_hist=0.0404, macd_prev=-0.1, news_count=0)
    res2 = scorer.score(_base_signal(), r2, regime='SIDEWAYS', mtf_aligned=False)
    assert res2['components']['trend'] == 7
    assert res2['components']['rsi'] == 3
    assert res2['components']['sentiment'] == 0
    assert res2['total_score'] < 70


def test_bear_strong_trend_is_reduced(scorer):
    research = _make_research(trend='STRONG_UPTREND', volume_ratio=2.5)
    result = scorer.score(_base_signal(), research, regime='BEAR', mtf_aligned=True)
    assert result['components']['trend'] == 8


def test_bear_uptrend_is_reduced(scorer):
    research = _make_research(trend='UPTREND', volume_ratio=2.5)
    result = scorer.score(_base_signal(), research, regime='BEAR', mtf_aligned=True)
    assert result['components']['trend'] == 5


def test_unknown_regime_defaults_to_sideways_trend_points(scorer):
    research = _make_research(trend='STRONG_UPTREND', volume_ratio=2.5)
    result = scorer.score(_base_signal(), research, regime='RANDOM', mtf_aligned=True)
    assert result['components']['trend'] == 15
