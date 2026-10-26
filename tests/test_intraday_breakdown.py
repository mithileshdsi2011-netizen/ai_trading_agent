"""Entry-date vs exit-date aggregation — carryover exits must not inflate
entry metrics and the split must reconcile to total realized P&L."""
import os
import sys
from datetime import datetime
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from intraday.engine import daily_breakdown


def _trade(sym, entry, exit_, pnl):
    return SimpleNamespace(symbol=sym, entry_time=entry,
                           exit_time=exit_, net_pnl=pnl)


D26 = datetime(2026, 10, 26, 10, 0)
D23 = datetime(2026, 10, 23, 13, 40)


def test_carryover_split_matches_ledger():
    """Reproduces the real 26-Oct ledger: 5 same-day + 2 carryover exits."""
    trades = [
        _trade('ONGC',      D23, datetime(2026, 10, 26, 7, 20), 103.30),
        _trade('SUNPHARMA', D23, datetime(2026, 10, 26, 7, 20),  22.48),
        _trade('KOTAKBANK', datetime(2026, 10, 26, 9, 38),  datetime(2026, 10, 26, 9, 43),  -13.19),
        _trade('BPCL',      datetime(2026, 10, 26, 9, 45),  datetime(2026, 10, 26, 9, 48),  -33.97),
        _trade('APOLLOHOSP',datetime(2026, 10, 26, 9, 53),  datetime(2026, 10, 26, 9, 57),   15.98),
        _trade('GRASIM',    datetime(2026, 10, 26, 9, 55),  datetime(2026, 10, 26, 9, 57),   39.56),
        _trade('JSWSTEEL',  datetime(2026, 10, 26, 9, 58),  datetime(2026, 10, 26, 10, 12), -15.98),
    ]
    db = daily_breakdown(trades, entries_today=5, max_entries=5)
    assert db['entries_today'] == 5
    assert db['exits_today'] == 7
    assert db['same_day'] == {'exits': 5, 'wins': 2, 'losses': 3, 'pnl': -7.60}
    assert db['carryover'] == {'exits': 2, 'wins': 2, 'losses': 0, 'pnl': 125.78}
    assert db['realized_pnl'] == 118.18
    # split must reconcile: same-day + carryover == total
    assert round(db['same_day']['pnl'] + db['carryover']['pnl'], 2) == db['realized_pnl']


def test_pnl_reconciles_generic():
    trades = [
        _trade('A', D23, D26, 100.0),
        _trade('B', D26, D26, -50.0),
        _trade('C', D26, D26, 25.0),
    ]
    db = daily_breakdown(trades, entries_today=2, max_entries=5)
    assert round(db['same_day']['pnl'] + db['carryover']['pnl'], 2) == db['realized_pnl'] == 75.0
    assert db['exits_today'] == 3 and db['entries_today'] == 2


def test_midnight_boundary_counts_as_carryover():
    trades = [_trade('X', datetime(2026, 10, 25, 23, 59),
                     datetime(2026, 10, 26, 0, 1), 10.0)]
    db = daily_breakdown(trades, entries_today=0, max_entries=5)
    assert db['carryover']['exits'] == 1
    assert db['same_day']['exits'] == 0


def test_empty_history():
    db = daily_breakdown([], entries_today=0, max_entries=5)
    assert db['exits_today'] == 0
    assert db['realized_pnl'] == 0.0
    assert db['same_day'] == {'exits': 0, 'wins': 0, 'losses': 0, 'pnl': 0.0}


def test_none_trades():
    db = daily_breakdown(None, entries_today=0, max_entries=5)
    assert db['exits_today'] == 0


def test_missing_timestamps_treated_as_same_day_not_crash():
    bad = [SimpleNamespace(symbol='X', entry_time=None,
                           exit_time=D26, net_pnl=5.0),
           SimpleNamespace(symbol='Y', entry_time='not-a-date',
                           exit_time=D26, net_pnl=-3.0)]
    db = daily_breakdown(bad, entries_today=2, max_entries=5)
    assert db['exits_today'] == 2
    assert db['carryover']['exits'] == 0  # can't prove carryover → don't claim it
    assert db['realized_pnl'] == 2.0


def test_ledger_not_mutated():
    t = _trade('A', D23, D26, 10.0)
    snapshot = dict(vars(t))
    daily_breakdown([t], entries_today=0, max_entries=5)
    assert vars(t) == snapshot
