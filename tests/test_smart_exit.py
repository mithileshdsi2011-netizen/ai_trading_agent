"""Unit tests for SmartExit profit gate and volume confirmation."""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import pytest
import pandas as pd
import numpy as np
from datetime import datetime

from smart_exit import SmartExitAI
from risk_manager import Position, PositionStatus


class MockMDF:
    """Mock MarketDataFetcher returning a fixed DataFrame."""

    def __init__(self, df):
        self.df = df

    def get_stock_data(self, *args, **kwargs):
        return self.df


def _position(entry=100.0):
    return Position(
        symbol='TEST', entry_price=entry, quantity=1, stop_loss=95.0,
        target=110.0, entry_time=datetime.now(), status=PositionStatus.OPEN,
        highest_price=entry, atr_at_entry=5.0, product_type='CNC',
    )


def _engulfing_df(last_vol=20000):
    base = pd.DataFrame({
        'Open':   [99] * 24,
        'High':   [101] * 24,
        'Low':    [98] * 24,
        'Close':  [100] * 24,
        'Volume': [10000] * 24,
    })
    prev = pd.DataFrame(
        {'Open': [98], 'High': [103], 'Low': [97], 'Close': [102], 'Volume': [10000]})
    last = pd.DataFrame(
        {'Open': [102], 'High': [103], 'Low': [96], 'Close': [97], 'Volume': [last_vol]})
    return pd.concat([base, prev, last], ignore_index=True)


def _rsi_df():
    n = 30
    close = np.linspace(100, 200, n)
    open_ = close - 0.5
    return pd.DataFrame({
        'Open':   open_,
        'High':   close + 1,
        'Low':    open_ - 1,
        'Close':  close,
        'Volume': [10000] * n,
    })


def _flat_df(vol_last=10000, n=25):
    df = pd.DataFrame({
        'Open':   [99] * n,
        'High':   [101] * n,
        'Low':    [98] * n,
        'Close':  [100.2] * n,
        'Volume': [10000] * n,
    })
    df.at[n - 1, 'Volume'] = vol_last
    return df


def test_no_trigger_at_minimum_profit():
    ai = SmartExitAI(MockMDF(_flat_df()))
    sig = ai.check_position(_position(), 101.5, 'SIDEWAYS')
    assert sig is None


def test_profit_gate_blocks_small_gain():
    ai = SmartExitAI(MockMDF(_engulfing_df()))
    sig = ai.check_position(_position(), 100.5, 'SIDEWAYS')
    assert sig is None


def test_volume_confirmation_blocks_bearish_candle():
    ai = SmartExitAI(MockMDF(_engulfing_df(last_vol=4000)))
    sig = ai.check_position(_position(), 103.0, 'SIDEWAYS')
    assert sig is None


def test_bearish_engulfing_high_volume():
    ai = SmartExitAI(MockMDF(_engulfing_df(last_vol=20000)))
    sig = ai.check_position(_position(), 103.0, 'SIDEWAYS')
    assert sig is not None
    assert 'Bearish engulfing candle' in sig['reason']


def test_rsi_overbought():
    ai = SmartExitAI(MockMDF(_rsi_df()))
    sig = ai.check_position(_position(), 200.0, 'SIDEWAYS')
    assert sig is not None
    assert 'RSI overbought' in sig['reason']


def test_volume_collapse():
    ai = SmartExitAI(MockMDF(_engulfing_df(last_vol=500)))
    sig = ai.check_position(_position(), 102.0, 'SIDEWAYS')
    assert sig is not None
    assert 'Volume collapse' in sig['reason']


def test_bear_regime_exit():
    ai = SmartExitAI(MockMDF(_flat_df()))
    sig = ai.check_position(_position(), 103.0, 'BEAR')
    assert sig is not None
    assert 'Market regime turned BEAR' in sig['reason']


def test_loss_no_exit():
    ai = SmartExitAI(MockMDF(_flat_df()))
    sig = ai.check_position(_position(), 99.0, 'SIDEWAYS')
    assert sig is None


def test_check_all_returns_list():
    p = _position()
    ai = SmartExitAI(MockMDF(_flat_df()))
    exits = ai.check_all([p], {'TEST': 103.0}, 'BEAR')
    assert isinstance(exits, list)
    assert len(exits) == 1
