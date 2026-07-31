"""Regression tests for strategy attribution in TradeJournal."""
import sys
import os
import tempfile
import json
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import pytest
from trade_journal import TradeJournal


def _make_journal():
    fd, path = tempfile.mkstemp(suffix='.json')
    os.close(fd)
    return TradeJournal(path=path), path


def test_compute_attribution_win():
    tj, path = _make_journal()
    buy = {
        'score_components': {'trend': 15, 'rsi': 8, 'macd': 15, 'volume': 20, 'sentiment': 0, 'regime': 6, 'sector': 4},
        'rsi': 55.0,
        'volume_ratio': 2.5,
        'trend': 'STRONG_UPTREND',
        'market_regime': 'BULL',
        'mtf_aligned': True,
    }
    attr = tj._compute_attribution(buy, 32.81)
    assert attr['win'] is True
    assert attr['dominant_component'] == 'volume'
    assert attr['rsi_bucket'] == '40-60'
    assert attr['volume_bucket'] == '>=2.5'
    assert 'Win driven by volume and volume confirmation' in attr['why']
    os.remove(path)


def test_compute_attribution_loss():
    tj, path = _make_journal()
    buy = {
        'score_components': {'trend': 12, 'rsi': 3, 'macd': 15, 'volume': 12, 'sentiment': 0, 'regime': 6, 'sector': 4},
        'rsi': 66.8,
        'volume_ratio': 1.43,
        'trend': 'STRONG_UPTREND',
        'market_regime': 'SIDEWAYS',
        'mtf_aligned': False,
    }
    attr = tj._compute_attribution(buy, -2.59)
    assert attr['win'] is False
    assert attr['dominant_component'] == 'macd'
    assert attr['rsi_bucket'] == '65-70'
    assert attr['volume_bucket'] == '1.3-1.8'
    assert 'without MTF alignment' in attr['why']
    os.remove(path)


def test_attribution_report_groups_by_factor():
    tj, path = _make_journal()
    try:
        _today = date.today().isoformat()
        # WIN — MTF aligned, RSI 55, BULL
        tj.log_entry(
            symbol='WIN_MTF', action='BUY', price=100, quantity=10,
            buy_reason='test', trade_score=80,
            score_components={'trend': 25, 'rsi': 8, 'macd': 15, 'volume': 20, 'sentiment': 4, 'regime': 10, 'sector': 5},
            market_regime='BULL', sector='IT', sentiment='POSITIVE', sentiment_score=0.5, news_count=1,
            rsi=55.0, macd_histogram=0.5, volume_ratio=2.5, trend='STRONG_UPTREND', atr=20,
            mtf_aligned=True, mtf_strict=True, confidence=0.70,
        )
        tj.log_entry(
            symbol='WIN_MTF', action='SELL', price=110, quantity=10,
            entry_price=100, entry_date=_today, exit_reason='Target hit',
            gross_pnl=100, net_pnl=95, charges=5, trade_score=80, confidence=0.70,
        )

        # LOSS — MTF not aligned, RSI 66.8, SIDEWAYS
        tj.log_entry(
            symbol='LOSS_NO_MTF', action='BUY', price=100, quantity=10,
            buy_reason='test', trade_score=63,
            score_components={'trend': 7, 'rsi': 3, 'macd': 15, 'volume': 12, 'sentiment': 0, 'regime': 6, 'sector': 4},
            market_regime='SIDEWAYS', sector='REALTY', sentiment='NEUTRAL', sentiment_score=0.0, news_count=0,
            rsi=66.8, macd_histogram=0.9, volume_ratio=1.43, trend='STRONG_UPTREND', atr=20,
            mtf_aligned=False, mtf_strict=False, confidence=0.60,
        )
        tj.log_entry(
            symbol='LOSS_NO_MTF', action='SELL', price=99, quantity=10,
            entry_price=100, entry_date=_today, exit_reason='Stop loss',
            gross_pnl=-10, net_pnl=-12, charges=2, trade_score=63, confidence=0.60,
        )

        report = tj.attribution_report()
        assert report['total_trades'] == 2
        assert report['by_mtf']['mtf_aligned']['win_rate'] == 100.0
        assert report['by_mtf']['mtf_not_aligned']['win_rate'] == 0.0
        assert report['by_rsi_bucket']['40-60']['trades'] == 1
        assert report['by_rsi_bucket']['65-70']['trades'] == 1
        assert report['by_trend']['STRONG_UPTREND']['trades'] == 2
    finally:
        os.remove(path)
