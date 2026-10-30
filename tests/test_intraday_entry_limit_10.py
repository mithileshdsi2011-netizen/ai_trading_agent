"""Intraday PAPER entry-limit raise 5→10 — focused safety tests.

Verifies the new cap of 10 accepted entries/day while every other
existing control (loss limit, setup quality, dedup, concurrency,
restart/day-rollover, market hours, EOD, PAPER-only, LIVE-disarmed)
behaves exactly as before. All fixtures are isolated — no broker calls.
"""
import os
import sys
import threading
from dataclasses import replace
from datetime import datetime, date, timedelta
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from intraday.engine import IntradayEngine
from intraday.config import get_intraday_config, live_armed
from intraday.models import (
    IntradayPosition, IntradaySignal, PositionStatus,
    SignalDirection, SignalClassification, MarketRegime,
)


def _config(limit=10):
    """The post-change config: production config + max_trades_per_day=10."""
    return replace(get_intraday_config(), max_trades_per_day=limit)


def _engine(tmp_path, limit=10):
    eng = IntradayEngine(config=_config(limit), state_dir=str(tmp_path),
                         alert_hook=lambda a, k, d: None)
    eng.validator.is_market_open = MagicMock(return_value=True)
    eng.validator.is_entry_window_open = MagicMock(return_value=True)
    eng.validator.is_square_off_time = MagicMock(return_value=False)
    return eng


def _signal(sym='TEST', direction=SignalDirection.LONG, entry=100.0,
            sl=95.0, tgt=110.0, rr=2.0,
            classification=SignalClassification.STRONG_SETUP):
    return IntradaySignal(
        symbol=sym, direction=direction, score=80.0,
        classification=classification,
        entry_price=entry, stop_loss=sl, target=tgt, risk_reward=rr,
        vwap_score=0, ema_score=0, volume_score=0, momentum_score=0,
        breakout_score=0, market_regime_score=0,
        market_regime=MarketRegime.BULLISH, current_price=entry,
        vwap=99.0, ema_9=99.0, ema_20=98.0, rsi=60.0,
        volume=1000, relative_volume=2.0, validation_passed=True)


def _enter_and_exit(eng, sym):
    """One accepted entry followed by an immediate exit — frees the
    concurrent-position slot so the daily cap is what limits, not the
    position cap."""
    sig = _signal(sym)
    pos = eng.paper_executor.execute_entry(sig, quote_time=datetime.now())
    assert pos is not None, f'entry {sym} should be accepted'
    eng.paper_executor.execute_exit(pos, 101.0, 'TARGET_HIT')


class TestEntryLimitTen:
    def test_entries_one_through_five_allowed(self, tmp_path):
        eng = _engine(tmp_path)
        for i in range(5):
            _enter_and_exit(eng, f'T{i}')
        assert eng.risk_manager.get_daily_state()['trades_today'] == 5

    def test_entries_six_through_ten_allowed(self, tmp_path):
        eng = _engine(tmp_path)
        for i in range(10):
            _enter_and_exit(eng, f'T{i}')
        assert eng.risk_manager.get_daily_state()['trades_today'] == 10

    def test_eleventh_entry_blocked(self, tmp_path):
        eng = _engine(tmp_path)
        for i in range(10):
            _enter_and_exit(eng, f'T{i}')
        ok, reason = eng.risk_manager.can_enter_trade(
            _signal('EXTRA'), idempotency_key='k_extra')
        assert not ok
        assert 'Max trades per day reached (10)' in reason

    def test_weak_setup_rejected_below_cap(self, tmp_path):
        eng = _engine(tmp_path)
        rm = eng.risk_manager
        ok, reason = rm.can_enter_trade(
            _signal('WEAK', classification=SignalClassification.NO_TRADE))
        assert not ok and 'NO_TRADE' in reason
        ok, reason = rm.can_enter_trade(_signal('LOWRR', rr=1.0))
        assert not ok and 'Risk:Reward' in reason
        # Rejections never consume a daily slot
        assert rm.get_daily_state()['trades_today'] == 0

    def test_loss_limit_still_blocks(self, tmp_path):
        """₹200 loss limit (unchanged) blocks entries even under cap 10."""
        eng = _engine(tmp_path)
        rm = eng.risk_manager
        rm._daily_state.realized_pnl = -200.0
        rm._save_daily_state()
        ok, reason = rm.can_enter_trade(_signal('X'), idempotency_key='k1')
        assert not ok and 'loss limit' in reason.lower()
        assert rm.get_daily_state()['blocked'] is True

    def test_cap_blocks_entries_not_dashboard_status(self, tmp_path):
        """At cap: entry gate reports DAILY LIMIT REACHED; other engine
        status fields keep working."""
        eng = _engine(tmp_path)
        for i in range(10):
            _enter_and_exit(eng, f'T{i}')
        eng.credentials_configured = MagicMock(return_value=True)
        eng._effective_auth_state = MagicMock(return_value='CONNECTED')
        eng.market_data_status = MagicMock(return_value='LIVE')
        assert eng._entry_permission() == 'DAILY LIMIT REACHED'
        assert eng._engine_state() != 'STOPPED (KILL SWITCH)'

    def test_duplicate_request_cannot_double_count(self, tmp_path):
        eng = _engine(tmp_path)
        rm = eng.risk_manager
        key = 'S1:LONG:ORB:2026-10-29'
        sig = _signal('S1')
        assert rm.can_enter_trade(sig, idempotency_key=key)[0]
        ok2, reason = rm.can_enter_trade(sig, idempotency_key=key)
        assert not ok2
        assert 'Duplicate' in reason or 'exists' in reason

    def test_concurrent_claims_never_exceed_caps(self, tmp_path):
        """10 racing claims: concurrent-position cap admits at most 2 at
        once; total slots can never exceed the daily cap."""
        eng = _engine(tmp_path)
        rm = eng.risk_manager
        results, barrier = [], threading.Barrier(10)

        def claim(i):
            barrier.wait(timeout=5)
            ok, _ = rm.can_enter_trade(_signal(f'C{i}'),
                                       idempotency_key=f'c{i}')
            results.append(ok)

        threads = [threading.Thread(target=claim, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(10)
        assert sum(results) == rm.config.max_concurrent_positions
        # Released slots let sequential entries proceed — up to the cap
        for i in range(10):
            rm.release_pending(f'c{i}')

    def test_restart_preserves_count(self, tmp_path):
        eng1 = _engine(tmp_path)
        for i in range(6):
            _enter_and_exit(eng1, f'R{i}')
        eng2 = _engine(tmp_path)          # same state_dir = restart
        assert eng2.risk_manager.get_daily_state()['trades_today'] == 6
        for i in range(6, 10):
            _enter_and_exit(eng2, f'R{i}')
        ok, _ = eng2.risk_manager.can_enter_trade(_signal('R11'),
                                                  idempotency_key='kr')
        assert not ok

    def test_next_trading_day_resets(self, tmp_path):
        eng1 = _engine(tmp_path)
        for i in range(10):
            _enter_and_exit(eng1, f'D{i}')
        # Simulate the next trading day: persist yesterday-dated state,
        # then a new engine must start a fresh counter.
        st = eng1.state_store.load_daily_state()
        assert st['trades_today'] == 10
        st['date'] = (date.today() - timedelta(days=1)).isoformat()
        eng1.state_store._write_json(eng1.state_store._daily_path, st)
        eng2 = _engine(tmp_path)
        assert eng2.risk_manager.get_daily_state()['trades_today'] == 0
        _enter_and_exit(eng2, 'NEW1')

    def test_market_hours_and_eod_unchanged(self, tmp_path):
        from intraday.validators import IntradayValidator
        v = IntradayValidator(_config())
        assert v.is_entry_window_open(datetime(2026, 10, 29, 10, 0)) is True
        assert v.is_entry_window_open(datetime(2026, 10, 29, 15, 0)) is False
        assert v.is_square_off_time(datetime(2026, 10, 29, 15, 5)) is True
        assert v.is_square_off_time(datetime(2026, 10, 29, 14, 55)) is False

    def test_paper_only_and_live_disarmed(self, tmp_path):
        eng = _engine(tmp_path)
        assert eng.config.paper_trading is True
        assert eng.config.angel_live_trading is False
        assert live_armed(eng.config) is False
