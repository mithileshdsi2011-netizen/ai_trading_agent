"""EOD safety simulation — synthetic PAPER positions in a temp state dir.
Exercises the REAL engine paths (_maybe_square_off, _refresh_positions,
scheduler tick body, restart rehydration). Never touches the production
ledger: every engine gets its own tmp_path state directory."""
import os
import sys
import threading
import time
from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from intraday.engine import IntradayEngine
from intraday.models import (
    IntradayPosition, PositionStatus, SignalDirection, SignalClassification
)


def _engine(tmp_path, **kw):
    incidents = []
    eng = IntradayEngine(
        state_dir=str(tmp_path),
        alert_hook=lambda action, key, detail: incidents.append(
            (action, key, detail)))
    return eng, incidents


def _position(sym='TEST', price=100.0, sl=95.0, tgt=110.0, entry=None):
    return IntradayPosition(
        id=f'test-{sym}', symbol=sym, direction=SignalDirection.LONG,
        entry_price=price, quantity=10, stop_loss=sl, target=tgt,
        entry_time=entry or datetime.now(), current_price=price,
        unrealized_pnl=0.0, unrealized_pnl_pct=0.0,
        signal_score=80.0, signal_classification=SignalClassification.STRONG_SETUP,
        strategy='test', is_paper=True)


def _add_position(eng, pos):
    eng.paper_executor._paper_positions[pos.id] = pos
    eng.risk_manager.add_position(pos)


def _quotes(ltp, stale=False):
    """Fake get_quotes — optionally carries an (unused-by-engine) stale
    exchange timestamp to prove it is ignored today."""
    return {'TEST': {'ltp': ltp, 'fetched_at': datetime.now(),
                     'exchFeedTime': datetime.now() - timedelta(days=3)
                     if stale else datetime.now()}}


# ── 1. Position open at 14:59 → square-off fires at 15:00 ────────────

def test_position_open_at_1459_exits_at_squareoff(tmp_path):
    eng, _ = _engine(tmp_path)
    _add_position(eng, _position())
    eng.validator.is_square_off_window = MagicMock(return_value=True)  # 15:00 weekday
    eng.validator.is_market_open = MagicMock(return_value=True)        # 15:00 still in session
    eng.market_data.get_quotes = MagicMock(return_value=_quotes(105.0))
    eng._maybe_square_off()
    assert eng.paper_executor.get_open_positions() == []
    trades = eng.trade_manager.get_trades()
    assert len(trades) == 1 and trades[0].exit_reason == 'EOD_SQUARE_OFF'
    assert trades[0].exit_price == 105.0  # fresh ltp used


# ── 2. Scan holding the lock at 15:00 → tick BLOCKS until release ────

def test_squareoff_delayed_behind_scan_lock(tmp_path):
    eng, _ = _engine(tmp_path)
    _add_position(eng, _position())
    eng.validator.is_square_off_window = MagicMock(return_value=True)
    eng.validator.is_market_open = MagicMock(return_value=True)
    eng.market_data.get_quotes = MagicMock(return_value=_quotes(105.0))

    eng._lock.acquire()
    done = threading.Event()

    def tick():
        with eng._lock:                      # scheduler's EOD path
            eng._maybe_square_off()
        done.set()

    t = threading.Thread(target=tick, daemon=True)
    t.start()
    time.sleep(0.5)
    assert not done.is_set()                 # EOD delayed behind scan
    assert eng.paper_executor.get_open_positions()  # position still open
    eng._lock.release()
    assert done.wait(2)                      # exits as soon as lock frees
    assert eng.paper_executor.get_open_positions() == []


# ── 3. Hung scan → EOD never runs; position survives ─────────────────

def test_hung_scan_blocks_eod_indefinitely(tmp_path):
    eng, _ = _engine(tmp_path)
    _add_position(eng, _position())
    eng.validator.is_square_off_window = MagicMock(return_value=True)
    eng.validator.is_market_open = MagicMock(return_value=False)
    eng.market_data.get_quotes = MagicMock(return_value=_quotes(105.0))

    eng._lock.acquire()
    done = threading.Event()
    threading.Thread(
        target=lambda: (eng._lock.acquire(),
                        eng._maybe_square_off(), done.set()),
        daemon=True).start()
    time.sleep(1.0)                          # "hung" for 1s of sim time
    assert not done.is_set()
    assert len(eng.paper_executor.get_open_positions()) == 1
    eng._lock.release()                      # recovery requires lock release
    assert done.wait(2)


# ── 4. Stale exchange timestamps are silently accepted today ─────────

def test_stale_quote_priced_exit_without_warning(tmp_path):
    """Documents the defect: EOD exits at ltp regardless of exchFeedTime
    age — no freshness check, no incident."""
    eng, incidents = _engine(tmp_path)
    _add_position(eng, _position())
    eng.validator.is_square_off_window = MagicMock(return_value=True)
    eng.validator.is_market_open = MagicMock(return_value=False)
    eng.market_data.get_quotes = MagicMock(
        return_value=_quotes(999.0, stale=True))   # 3-day-old exchange stamp
    eng._maybe_square_off()
    trade = eng.trade_manager.get_trades()[0]
    assert trade.exit_price == 999.0         # stale ltp used, unflagged
    assert not any(k for _, k, _ in incidents
                   if 'eod' in k.lower())    # no incident raised


# ── 5. Quote API failure → last-known fallback + incident ────────────

def test_quote_failure_falls_back_with_incident(tmp_path):
    eng, incidents = _engine(tmp_path)
    pos = _position(price=100.0)
    pos.current_price = 102.5                # last stored price
    _add_position(eng, pos)
    eng.validator.is_square_off_window = MagicMock(return_value=True)
    eng.validator.is_market_open = MagicMock(return_value=False)
    eng.market_data.get_quotes = MagicMock(
        side_effect=TimeoutError('angel timeout'))
    eng._maybe_square_off()
    trade = eng.trade_manager.get_trades()[0]
    assert trade.exit_price == 102.5         # last-known fallback
    assert any('eod' in k.lower() for _, k, _ in incidents)


def test_empty_quotes_falls_back_with_incident(tmp_path):
    eng, incidents = _engine(tmp_path)
    pos = _position(price=100.0)
    pos.current_price = 101.0
    _add_position(eng, pos)
    eng.validator.is_square_off_window = MagicMock(return_value=True)
    eng.validator.is_market_open = MagicMock(return_value=False)
    eng.market_data.get_quotes = MagicMock(return_value={})
    eng._maybe_square_off()
    assert eng.trade_manager.get_trades()[0].exit_price == 101.0
    assert any('eod' in k.lower() for _, k, _ in incidents)


# ── 6. Restart recovery ──────────────────────────────────────────────

def test_restart_rehydrates_and_flushes(tmp_path):
    eng1, _ = _engine(tmp_path)
    pos = _position()
    _add_position(eng1, pos)
    eng1.state_store.save_positions([pos])

    eng2, _ = _engine(tmp_path)              # fresh process on same store
    assert len(eng2.paper_executor.get_open_positions()) == 1
    eng2.validator.is_square_off_window = MagicMock(return_value=True)
    eng2.validator.is_market_open = MagicMock(return_value=False)
    eng2.market_data.get_quotes = MagicMock(return_value=_quotes(104.0))
    eng2._maybe_square_off()
    assert eng2.paper_executor.get_open_positions() == []


# ── 7. Overnight carryover: holds during next session, exits at 15:00 ─

def test_carryover_holds_during_session_exits_next_squareoff(tmp_path):
    eng, _ = _engine(tmp_path)
    pos = _position(entry=datetime.now() - timedelta(days=3))
    _add_position(eng, pos)

    # Next-day mid-session: NOT square-off time, market open → held
    eng.validator.is_square_off_window = MagicMock(return_value=False)
    eng.validator.is_market_open = MagicMock(return_value=True)
    eng._maybe_square_off()
    assert len(eng.paper_executor.get_open_positions()) == 1  # held

    # But SL/target monitoring still works during the session
    eng.market_data.get_quotes = MagicMock(return_value=_quotes(94.0))  # < SL 95
    exited = eng._refresh_positions()
    assert exited == 1
    assert eng.trade_manager.get_trades()[0].exit_reason == 'STOP_LOSS'


def test_carryover_exits_at_next_squareoff(tmp_path):
    eng, _ = _engine(tmp_path)
    _add_position(eng, _position(entry=datetime.now() - timedelta(days=3)))
    eng.validator.is_square_off_window = MagicMock(return_value=True)
    eng.validator.is_market_open = MagicMock(return_value=False)
    eng.market_data.get_quotes = MagicMock(return_value=_quotes(103.0))
    eng._maybe_square_off()
    assert eng.paper_executor.get_open_positions() == []


# ── 8. Repeated EOD ticks are idempotent ──────────────────────────────

def test_repeated_squareoff_no_duplicates(tmp_path):
    eng, _ = _engine(tmp_path)
    _add_position(eng, _position())
    eng.validator.is_square_off_window = MagicMock(return_value=True)
    eng.validator.is_market_open = MagicMock(return_value=False)
    eng.market_data.get_quotes = MagicMock(return_value=_quotes(105.0))
    eng._maybe_square_off()
    eng._maybe_square_off()
    eng._maybe_square_off()
    assert len(eng.trade_manager.get_trades()) == 1   # exactly one trade


# ══ Batch A: bounded lock, heartbeat, post-close idle, overnight flag ══

import intraday.engine as engine_mod


def _tick_setup(eng, square_off=True, market_open=True):
    eng.validator.is_square_off_window = MagicMock(return_value=square_off)
    eng.validator.is_square_off_time = MagicMock(return_value=square_off)
    eng.validator.is_market_open = MagicMock(return_value=market_open)
    eng.market_data.get_quotes = MagicMock(return_value=_quotes(105.0))


class TestSchedulerTickSafety:

    def test_normal_eod_tick_flushes(self, tmp_path):
        eng, _ = _engine(tmp_path)
        _add_position(eng, _position())
        _tick_setup(eng)
        eng._scheduler_tick_once()
        assert eng.paper_executor.get_open_positions() == []
        assert eng._eod_completed_at is None or isinstance(
            eng._eod_completed_at, datetime)

    def test_lock_held_tick_times_out_bounded(self, tmp_path):
        eng, incidents = _engine(tmp_path)
        _add_position(eng, _position())
        _tick_setup(eng)
        eng._lock.acquire()
        t0 = time.time()
        try:
            eng._scheduler_tick_once()
        finally:
            eng._lock.release()
        # bounded — returns after EOD_LOCK_TIMEOUT_SECONDS, not indefinitely
        assert time.time() - t0 < engine_mod.EOD_LOCK_TIMEOUT_SECONDS + 2
        assert eng._eod_pending_since is not None      # delay recorded
        assert eng.paper_executor.get_open_positions()  # still open — retry next tick

    def test_repeated_timeouts_alert_once(self, tmp_path, monkeypatch):
        monkeypatch.setattr(engine_mod, 'EOD_DELAY_ALERT_SECONDS', 0.1)
        eng, incidents = _engine(tmp_path)
        _add_position(eng, _position())
        _tick_setup(eng)
        eng._lock.acquire()
        try:
            for _ in range(3):
                eng._scheduler_tick_once()
                time.sleep(0.05)
        finally:
            eng._lock.release()
        notifies = [i for i in incidents
                    if i[0] == 'notify' and 'eod_delayed' in i[1]]
        assert len(notifies) == 1                      # deduplicated

    def test_lock_release_retry_succeeds(self, tmp_path):
        eng, incidents = _engine(tmp_path)
        _add_position(eng, _position())
        _tick_setup(eng)
        eng._lock.acquire()
        eng._scheduler_tick_once()
        eng._lock.release()
        eng._scheduler_tick_once()                     # retry completes
        assert eng.paper_executor.get_open_positions() == []
        # next tick confirms EOD complete → pending cleared
        eng._scheduler_tick_once()
        assert eng._eod_pending_since is None

    def test_no_scans_once_squareoff_due(self, tmp_path):
        eng, _ = _engine(tmp_path)
        _tick_setup(eng, square_off=True, market_open=True)
        eng.maybe_run_cycle = MagicMock()
        eng._scheduler_tick_once()
        eng.maybe_run_cycle.assert_not_called()

    def test_positions_held_after_close_still_retried(self, tmp_path):
        """15:35+ — square_off still due → retry branch stays active."""
        eng, _ = _engine(tmp_path)
        _add_position(eng, _position())
        _tick_setup(eng, square_off=True, market_open=False)
        eng._scheduler_tick_once()
        assert eng.paper_executor.get_open_positions() == []

    def test_overnight_position_held_not_flushed_on_stale_quotes(self, tmp_path):
        """Market closed, not due (early AM/weekend) + open positions →
        flagged but NOT exited on stale broker quotes. Recovery happens
        inside the next valid session."""
        eng, incidents = _engine(tmp_path)
        _add_position(eng, _position(
            entry=datetime.now() - timedelta(days=1)))
        _tick_setup(eng, square_off=False, market_open=False)
        eng._scheduler_tick_once()
        # position still open — no fictitious stale-price exit booked
        assert len(eng.paper_executor.get_open_positions()) == 1
        assert eng.trade_manager.get_trades() == []
        # flagged as overnight exception
        assert any('overnight' in k for _, k, _ in incidents)
        # ledger untouched: entry details preserved
        pos = eng.paper_executor.get_open_positions()[0]
        assert pos.entry_price == 100.0
        assert pos.exit_time is None

    def test_weekend_squareoff_window_blocked(self, tmp_path):
        """Saturday 16:00: is_square_off_time true but NOT a trading day —
        no square-off on stale quotes."""
        eng, _ = _engine(tmp_path)
        _add_position(eng, _position(
            entry=datetime.now() - timedelta(days=1)))
        eng.validator.is_square_off_time = MagicMock(return_value=True)
        eng.validator.is_market_open = MagicMock(return_value=False)
        # real is_square_off_window: mock ist_now → Saturday
        import intraday.validators as vm
        from unittest.mock import patch
        sat = vm.IST.localize(datetime(2026, 10, 24, 16, 0))  # Saturday
        with patch.object(vm, 'ist_now', return_value=sat):
            eng._maybe_square_off()
        assert len(eng.paper_executor.get_open_positions()) == 1
        assert eng.trade_manager.get_trades() == []

    def test_overnight_position_flagged_once(self, tmp_path):
        eng, incidents = _engine(tmp_path)
        _add_position(eng, _position(
            entry=datetime.now() - timedelta(days=1)))
        _tick_setup(eng, square_off=False, market_open=True)
        eng.maybe_run_cycle = MagicMock()
        eng._scheduler_tick_once()
        eng._scheduler_tick_once()
        over = [i for i in incidents if 'overnight' in i[1]]
        assert len(over) == 1

    def test_idle_heartbeat_status(self, tmp_path):
        eng, _ = _engine(tmp_path)
        _tick_setup(eng, square_off=False, market_open=False)  # overnight, nothing open
        eng._scheduler_tick_once()
        s = eng._scheduler_status()
        assert s['mode'] == 'idle'
        assert s['last_tick'] is not None
        assert s['last_success'] is not None
        assert s['tick_age_seconds'] is not None and s['tick_age_seconds'] < 60
        assert s['open_positions'] == 0

    def test_dead_scheduler_distinguishable(self, tmp_path):
        eng, _ = _engine(tmp_path)
        s = eng._scheduler_status()
        assert s['running'] is False          # never started → dead
        assert s['last_tick'] is None         # no heartbeat at all

    def test_status_persisted_atomically(self, tmp_path):
        eng, _ = _engine(tmp_path)
        _tick_setup(eng, square_off=False, market_open=False)
        eng._persist_status()
        import json
        status = json.load(open(tmp_path / 'engine_status.json'))
        assert 'scheduler' in status
        assert 'last_tick' in status['scheduler']

    def test_quote_failure_during_eod_tick_records_incident(self, tmp_path):
        eng, incidents = _engine(tmp_path)
        pos = _position()
        pos.current_price = 101.0
        _add_position(eng, pos)
        eng.validator.is_square_off_window = MagicMock(return_value=True)
        eng.validator.is_market_open = MagicMock(return_value=False)
        eng.market_data.get_quotes = MagicMock(
            side_effect=TimeoutError('angel down'))
        eng._scheduler_tick_once()
        assert eng.trade_manager.get_trades()[0].exit_price == 101.0
        assert any('eod' in k.lower() for _, k, _ in incidents)



# ══ Batch A review: commit-gate, overnight policy, heartbeat truth ══

from intraday.models import IntradaySignal, MarketRegime


def _signal(sym='TEST'):
    return IntradaySignal(
        symbol=sym, direction=SignalDirection.LONG, score=80.0,
        classification=SignalClassification.STRONG_SETUP,
        entry_price=100.0, stop_loss=95.0, target=110.0, risk_reward=2.0,
        vwap_score=0, ema_score=0, volume_score=0, momentum_score=0,
        breakout_score=0, market_regime_score=0,
        market_regime=MarketRegime.BULLISH, current_price=100.0,
        vwap=99.0, ema_9=99.0, ema_20=98.0, rsi=60.0,
        volume=1000, relative_volume=2.0, validation_passed=True)


def test_commit_gate_rejects_entry_after_cutoff(tmp_path):
    """Scan started in-window; commit happens post-cutoff → rejected,
    no position, no trade-slot consumed."""
    eng, _ = _engine(tmp_path)
    eng.validator.is_market_open = MagicMock(return_value=True)
    eng.validator.is_entry_window_open = MagicMock(return_value=False)
    eng.validator.is_square_off_time = MagicMock(return_value=False)
    before = eng.risk_manager.get_daily_state()['trades_today']
    pos = eng.paper_executor.execute_entry(_signal())
    assert pos is None
    assert eng.paper_executor.get_open_positions() == []
    assert eng.risk_manager.get_daily_state()['trades_today'] == before


def test_commit_gate_rejects_entry_at_square_off_time(tmp_path):
    """Entry window technically open but square-off due → rejected."""
    eng, _ = _engine(tmp_path)
    eng.validator.is_market_open = MagicMock(return_value=True)
    eng.validator.is_entry_window_open = MagicMock(return_value=True)
    eng.validator.is_square_off_time = MagicMock(return_value=True)
    assert eng.paper_executor.execute_entry(_signal()) is None
    assert eng.paper_executor.get_open_positions() == []


def test_commit_gate_accepts_valid_entry_mid_session(tmp_path):
    """All commit-time checks pass → entry executes normally."""
    eng, _ = _engine(tmp_path)
    eng.validator.is_market_open = MagicMock(return_value=True)
    eng.validator.is_entry_window_open = MagicMock(return_value=True)
    eng.validator.is_square_off_time = MagicMock(return_value=False)
    eng.risk_manager.can_enter_trade = MagicMock(return_value=(True, 'ok'))
    eng.position_sizer.calculate_quantity = MagicMock(
        return_value=(10, 1000.0))
    pos = eng.paper_executor.execute_entry(_signal())
    assert pos is not None
    assert len(eng.paper_executor.get_open_positions()) == 1


def test_entry_commit_serialized_against_eod(tmp_path):
    """14:59 scan holds the lock through 15:00: the tick's EOD attempt
    times out (bounded); when the scan releases, its commit-time entry
    is rejected (window closed) and the NEXT tick completes EOD. Entry
    and square-off can never interleave — same lock."""
    eng, _ = _engine(tmp_path)
    _add_position(eng, _position())               # position needing EOD
    _tick_setup(eng)
    # in-flight scan holding the engine lock
    eng._lock.acquire()
    eng._scheduler_tick_once()                    # EOD attempt → timeout
    assert eng._eod_pending_since is not None
    assert eng.paper_executor.get_open_positions()  # not silently skipped
    # scan's late entry attempt, post-cutoff
    eng.validator.is_square_off_time = MagicMock(return_value=True)
    eng.validator.is_entry_window_open = MagicMock(return_value=False)
    assert eng.paper_executor.execute_entry(_signal()) is None
    eng._lock.release()
    # next tick completes EOD with zero interference
    eng._scheduler_tick_once()
    assert eng.paper_executor.get_open_positions() == []
    assert eng._eod_completed_at is not None


def test_eod_not_complete_until_zero_positions(tmp_path):
    """EOD completion requires zero open positions — a position created
    or surviving means pending stays set."""
    eng, _ = _engine(tmp_path)
    _tick_setup(eng, square_off=True, market_open=False)
    eng._scheduler_tick_once()                    # no positions → complete
    assert eng._eod_completed_at is not None
    # a (defensive) position appearing later re-pends EOD
    _add_position(eng, _position())
    eng._scheduler_tick_once()
    assert eng.paper_executor.get_open_positions() == []
    st = eng._scheduler_status()
    assert st['open_positions'] == 0


def test_dead_scheduler_not_reported_running(tmp_path):
    """Persisted/flagged running alone is not proof of life — a dead
    thread with _scheduler_started set must report running=False."""
    eng, _ = _engine(tmp_path)
    eng._scheduler_started = True                 # flag says started...
    eng._scheduler_thread = threading.Thread(     # ...but thread is dead
        target=lambda: None)
    eng._scheduler_thread.start()
    eng._scheduler_thread.join()
    s = eng._scheduler_status()
    assert s['running'] is False
    assert s['thread_alive'] is False


def test_idle_tick_still_catches_new_position(tmp_path):
    """120s idle mode cannot park an open position: the next tick sees
    positions + square-off window and executes EOD immediately."""
    eng, _ = _engine(tmp_path)
    _tick_setup(eng, square_off=True, market_open=False)
    eng._scheduler_tick_once()                    # idle confirm
    _add_position(eng, _position())               # position appears late
    eng._scheduler_tick_once()
    assert eng.paper_executor.get_open_positions() == []


def test_eod_incident_visible_until_resolved(tmp_path):
    """eod_delayed stays flagged while pending; resolved only when a
    tick confirms zero positions in the window."""
    eng, incidents = _engine(tmp_path)
    _add_position(eng, _position())
    _tick_setup(eng)
    eng._eod_pending_since = (datetime.now()
                              - timedelta(seconds=999))   # long overdue
    eng._lock.acquire()
    eng._scheduler_tick_once()
    eng._lock.release()
    assert eng._eod_delay_alerted is True
    eng._scheduler_tick_once()                    # completes
    eng._scheduler_tick_once()                    # confirm + resolve
    assert eng._eod_delay_alerted is False
    assert eng._eod_completed_at is not None


# ══ Exchange-calendar hardening: holidays, long weekends, persistence ══

def _fake_date(sim_day):
    """Fake `date` class whose today() returns the simulated day — keeps
    the engine's overnight detection (date.today()) in sim-time."""
    class _FakeDate:
        @staticmethod
        def today():
            return sim_day.date() if isinstance(sim_day, datetime) \
                else sim_day
    return _FakeDate


def test_friday_position_held_over_weekend_exits_monday(tmp_path):
    """Friday 15:00 entry surviving → Sat/Sun ticks hold it (no stale
    exits); Monday 15:00 window executes the square-off."""
    eng, incidents = _engine(tmp_path)
    import intraday.validators as vm
    from unittest.mock import patch
    # entry_time stored naive like production positions
    fri = datetime(2026, 10, 23, 14, 30)                    # Friday
    _add_position(eng, _position(entry=fri))
    eng.market_data.get_quotes = MagicMock(
        return_value=_quotes(97.0, stale=True))             # stale marks
    # Saturday 16:00 + Sunday 18:00 — real is_square_off_window
    for day, hh in [(24, 16), (25, 18)]:
        sim = vm.IST.localize(datetime(2026, 10, day, hh))
        with patch.object(vm, 'ist_now', return_value=sim), \
                patch.object(engine_mod, 'date', _fake_date(sim)):
            eng._scheduler_tick_once()
        assert len(eng.paper_executor.get_open_positions()) == 1
        assert eng.trade_manager.get_trades() == []          # never priced
    # Monday 15:05 — valid window → square-off executes
    mon = vm.IST.localize(datetime(2026, 10, 26, 15, 5))
    with patch.object(vm, 'ist_now', return_value=mon), \
            patch.object(engine_mod, 'date', _fake_date(mon)):
        eng.market_data.get_quotes = MagicMock(
            return_value=_quotes(97.0))
        eng._scheduler_tick_once()
    assert eng.paper_executor.get_open_positions() == []
    assert eng.trade_manager.get_trades()[0].exit_reason == 'EOD_SQUARE_OFF'
    assert any('overnight' in k for _, k, _ in incidents)


def test_long_holiday_weekend_held_until_next_trading_day(tmp_path):
    """Thu position → Fri is a holiday → Sat/Sun weekend → Mon window.
    Position held throughout, flagged once, exits only at Monday's EOD."""
    eng, incidents = _engine(tmp_path)
    import intraday.validators as vm
    from unittest.mock import patch
    thu = datetime(2026, 12, 24, 14, 0)                   # Thursday (naive)
    _add_position(eng, _position(entry=thu))
    # Fri 2026-12-25 is Christmas (declared holiday); Sat 26th, Sun 27th
    for day in [25, 26, 27]:
        sim = vm.IST.localize(datetime(2026, 12, day, 16, 0))
        with patch.object(vm, 'ist_now', return_value=sim), \
                patch.object(engine_mod, 'date', _fake_date(sim)):
            eng._scheduler_tick_once()
        assert len(eng.paper_executor.get_open_positions()) == 1
        assert eng.trade_manager.get_trades() == []
    # Mon 28th 15:05 — first valid window → exit
    mon = vm.IST.localize(datetime(2026, 12, 28, 15, 5))
    with patch.object(vm, 'ist_now', return_value=mon), \
            patch.object(engine_mod, 'date', _fake_date(mon)):
        eng.market_data.get_quotes = MagicMock(
            return_value=_quotes(103.0))
        eng._scheduler_tick_once()
    assert eng.paper_executor.get_open_positions() == []
    overnight_notifies = [i for i in incidents
                          if i[0] == 'notify' and 'overnight' in i[1]]
    assert len(overnight_notifies) == 1                    # deduplicated


def test_failed_exit_position_retained_in_persistence(tmp_path):
    """An execute_exit failure must NOT wipe the position from the
    persisted ledger — a restart would silently lose it."""
    eng, _ = _engine(tmp_path)
    pos = _position()
    _add_position(eng, pos)
    eng.state_store.save_positions([pos])
    eng.validator.is_square_off_window = MagicMock(return_value=True)
    eng.market_data.get_quotes = MagicMock(return_value=_quotes(105.0))
    # force every exit to fail
    eng.paper_executor.execute_exit = MagicMock(return_value=None)
    eng._maybe_square_off()
    # in-memory position remains open...
    assert len(eng.paper_executor.get_open_positions()) == 1
    # ...and the persisted ledger still holds it
    import json
    persisted = json.load(open(tmp_path / 'positions.json'))
    assert len(persisted) == 1
    assert persisted[0]['symbol'] == 'TEST'
    # EOD not marked complete while a position remains
    assert eng._eod_completed_at is None
