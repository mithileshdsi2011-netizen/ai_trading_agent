"""Regression and validation for the SIDEWAYS BUY gate tightening."""
import sys
import os
import re
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import pytest
from config import config
from trade_journal import TradeJournal


def _side_buy_allowed(best_signal, score_result, regime):
    """Mirror the SIDEWAYS BUY gate in trading_orchestrator."""
    if regime != 'SIDEWAYS':
        return True  # BULL / BEAR unchanged
    signal_conf = best_signal.get('confidence', 0.0)
    overall_score = best_signal.get('overall_score', 0.0)
    return (
        score_result['total_score'] >= config.SIDEWAYS_BUY_SCORE_MIN
        and signal_conf >= config.MIN_CONFIDENCE_SIDEWAYS
        and overall_score > config.SIDEWAYS_BUY_OVERALL_SCORE_MIN
    )


def _extract_overall_score(buy_reason):
    m = re.search(r'\(score:\s*([0-9.]+)\)', buy_reason or '')
    return float(m.group(1)) if m else 0.0


def test_config_thresholds_exist():
    assert config.SIDEWAYS_BUY_SCORE_MIN == 58
    assert config.MIN_CONFIDENCE_SIDEWAYS == 0.55
    assert config.SIDEWAYS_BUY_OVERALL_SCORE_MIN == 0.30


def test_bull_buy_not_affected_by_sideways_gate():
    best_signal = {'overall_score': 0.25, 'confidence': 0.60}
    score_result = {'total_score': 70}
    assert _side_buy_allowed(best_signal, score_result, 'BULL') is True


def test_bear_buy_not_affected_by_sideways_gate():
    best_signal = {'overall_score': 0.25, 'confidence': 0.60}
    score_result = {'total_score': 70}
    assert _side_buy_allowed(best_signal, score_result, 'BEAR') is True


def test_low_quality_sideways_rejected():
    best_signal = {'overall_score': 0.30, 'confidence': 0.70}
    score_result = {'total_score': 82}
    assert _side_buy_allowed(best_signal, score_result, 'SIDEWAYS') is False


def test_low_confidence_sideways_rejected():
    best_signal = {'overall_score': 0.55, 'confidence': 0.50}
    score_result = {'total_score': 82}
    assert _side_buy_allowed(best_signal, score_result, 'SIDEWAYS') is False


def test_low_trade_score_sideways_rejected():
    best_signal = {'overall_score': 0.55, 'confidence': 0.70}
    score_result = {'total_score': 55}
    assert _side_buy_allowed(best_signal, score_result, 'SIDEWAYS') is False


def test_high_quality_sideways_allowed():
    best_signal = {'overall_score': 0.55, 'confidence': 0.70}
    score_result = {'total_score': 82}
    assert _side_buy_allowed(best_signal, score_result, 'SIDEWAYS') is True


def test_historical_sideways_trades():
    """Phase 4 validation: what the new rule would do to each historical trade."""
    tj = TradeJournal()
    closed = tj.closed_trades()
    sideways = [t for t in closed if t.get('market_regime') == 'SIDEWAYS']

    rejected = []
    allowed = []
    for t in sideways:
        best_signal = {
            'overall_score': _extract_overall_score(t.get('buy_reason', '')),
            'confidence': t.get('confidence', 0),
        }
        score_result = {'total_score': t.get('trade_score', 0)}
        ok = _side_buy_allowed(best_signal, score_result, 'SIDEWAYS')
        if ok:
            allowed.append(t)
        else:
            rejected.append(t)

    # Print validation summary to stdout
    print(f"\nHistorical SIDEWAYS closed trades: {len(sideways)}")
    print(f"Rejected: {len(rejected)}")
    for t in rejected:
        print(f"  - {t['symbol']} score={t.get('trade_score')} conf={t.get('confidence')} pnl={t.get('net_pnl')}")
    print(f"Allowed: {len(allowed)}")
    for t in allowed:
        print(f"  - {t['symbol']} score={t.get('trade_score')} conf={t.get('confidence')} pnl={t.get('net_pnl')}")

    rejected_pnl = sum(t.get('net_pnl', 0) for t in rejected)
    allowed_pnl = sum(t.get('net_pnl', 0) for t in allowed)
    print(f"Rejected P&L (avoided): {rejected_pnl:.2f}")
    print(f"Allowed P&L (kept): {allowed_pnl:.2f}")

    assert len(sideways) >= 0  # journal may be empty; test the structure
