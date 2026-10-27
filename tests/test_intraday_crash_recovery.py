"""Crash/restart lifecycle + unified execution-quote policy tests.

Simulates a dead process by constructing a second engine on the same
tmp_path state directory — engine init runs the durable-claim
reconciliation and phantom-position dedup exactly as production restart.
The production ledger is never touched."""
import os
import sys
import threading
from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from intraday.engine import IntradayEngine
from intraday.models import (
    IntradayPosition, IntradaySignal, PositionStatus,
    SignalDirection, SignalClassification, MarketRegime,
)
from intraday.validators import (
    signal_idempotency_key, quote_usable_for_execution)


def _engine(tmp_path):
    incidents = []
    eng = IntradayEngine(
        state_dir=str(tmp_path),
        alert_hook=lambda a, k, d: incidents.append((a, k, d)))
    return eng, incidents


def _signal(sym='TEST', direction=SignalDirection.LONG, entry=100.0,
            sl=95.0, tgt=110.0):
    return IntradaySignal(
        symbol=sym, direction=direction, score=80.0,
        classification=SignalClassification.STRONG_SETUP,
        entry_price=entry, stop_loss=sl, target=tgt, risk_reward=2.0,
        vwap_score=0, ema_score=0, volume_score=0, momentum_score=0,
        breakout_score=0, market_regime_score=0,
        market_regime=MarketRegime.BULLISH, current_price=entry,
        vwap=99.0, ema_9=99.0, ema_20=98.0, rsi=60.0,
        volume=1000, relative_volume=2.0, validation_passed=True)


def _position(sym='TEST', price=100.0, qty=10, sl=95.0, tgt=110.0,
              entry=None):
    return IntradayPosition(
        id=f'p-{sym}', symbol=sym, direction=SignalDirection.LONG,
        entry_price=price, quantity=qty, stop_loss=sl, target=tgt,
        entry_time=entry or datetime.now(), current_price=price,
        unrealized_pnl=0.0, unrealized_pnl_pct=0.0,
        signal_score=80.0,
        signal_classification=SignalClassification.STRONG_SETUP,
        strategy='test', is_paper=True)


def _add_position(eng, pos):
    eng.paper_executor._paper_positions[pos.id] = pos
    eng.risk_manager.add_position(pos)


# ══════════ Unified execution-quote policy — every rejection path ═════

class TestUnifiedQuotePolicy:

    def test_valid_quote_accepted(self):
        now = datetime.now()
        ok, _ = quote_usable_for_execution(
            {'ltp': 100.5, 'trading_symbol': 'TEST-EQ', 'exchange': 'NSE',
             'exchange_ts': now}, 'TEST', receipt_time=now, now=now)
        assert ok

    def test_missing_receipt_rejected(self):
        ok, reason = quote_usable_for_execution(
            {'ltp': 100.0}, 'TEST', receipt_time=None, now=datetime.now())
        assert not ok
        assert 'receipt' in reason

    def test_wrong_symbol_rejected(self):
        now = datetime.now()
        ok, reason = quote_usable_for_execution(
            {'ltp': 100.0, 'trading_symbol': 'OTHER-EQ'}, 'TEST',
            receipt_time=now, now=now)
        assert not ok
        assert 'symbol' in reason

    def test_wrong_exchange_rejected(self):
        now = datetime.now()
        ok, reason = quote_usable_for_execution(
            {'ltp': 100.0, 'exchange': 'BSE'}, 'TEST',
            receipt_time=now, now=now)
        assert not ok
        assert 'exchange' in reason

    def test_stale_receipt_rejected(self):
        now = datetime.now()
        ok, reason = quote_usable_for_execution(
            {'ltp': 100.0}, 'TEST',
            receipt_time=now - timedelta(seconds=45), now=now)
        assert not ok
        assert 'age' in reason

    def test_stale_exchange_ts_rejected(self):
        now = datetime.now()
        ok, reason = quote_usable_for_execution(
            {'ltp': 100.0, 'exchange_ts': now - timedelta(days=3)}, 'TEST',
            receipt_time=now, now=now)
        assert not ok
        assert 'exchange' in reason.lower() or 'timestamp' in reason

    def test_future_receipt_rejected(self):
        now = datetime.now()
        ok, _ = quote_usable_for_execution(
            {'ltp': 100.0}, 'TEST',
            receipt_time=now + timedelta(seconds=5), now=now)
        assert not ok

    def test_future_exchange_ts_rejected(self):
        now = datetime.now()
        ok, _ = quote_usable_for_execution(
            {'ltp': 100.0, 'exchange_ts': now + timedelta(minutes=1)}, 'TEST',
            receipt_time=now, now=now)
        assert not ok

    def test_missing_ltp_rejected(self):
        now = datetime.now()
        ok, _ = quote_usable_for_execution(
            {'ltp': 0}, 'TEST', receipt_time=now, now=now)
        assert not ok

    def test_entry_rejected_on_stale_quote_receipt(self, tmp_path):
        """_try_enter uses the same policy — a 45s-old data receipt blocks
        the entry even when is_data_fresh's looser bound would pass."""
        eng, _ = _engine(tmp_path)
        eng.validator.is_market_open = MagicMock(return_value=True)
        eng.validator.is_entry_window_open = MagicMock(return_value=True)
        # is_data_fresh uses the same 30s bound — mock it aside so the
        # shared execution-quote check is what fires.
        eng.validator.is_data_fresh = MagicMock(return_value=True)
        sig = _signal()
        eng._last_data_time = datetime.now() - timedelta(seconds=45)
        eng.market_data.symbol_last_update = MagicMock(return_value=None)
        eng._try_enter(sig)
        assert eng.paper_executor.get_open_positions() == []
        assert 'Quote unusable' in (sig.rejection_reason or '')


# ══════════ Crash / restart lifecycle of an entry claim ═══════════════

class TestDurableEntryIdempotency:

    def test_crash_before_entry_persistence_releases_claim(self, tmp_path):
        """Process dies after the durable claim but before the position is
        persisted. Restart releases the stale claim — the signal may
        re-enter (no lost entry, no stuck reservation, count unaffected)."""
        eng1, _ = _engine(tmp_path)
        sig = _signal()
        key = signal_idempotency_key(sig)
        ok, _ = eng1.risk_manager.can_enter_trade(sig, idempotency_key=key)
        assert ok                                   # claim persisted now

        eng2, _ = _engine(tmp_path)                 # restart
        ds = eng2.risk_manager.get_daily_state()
        assert ds['trades_today'] == 0              # no phantom count
        assert eng2.paper_executor.get_open_positions() == []
        # Same signal may claim a slot again — reservation did not stick
        ok2, _ = eng2.risk_manager.can_enter_trade(sig, idempotency_key=key)
        assert ok2

    def test_crash_after_position_persist_completes_entry(self, tmp_path):
        """Process dies after positions.json was written but before
        record_trade_entry — restart completes the entry: count 1, key
        consumed, duplicate blocked."""
        eng1, _ = _engine(tmp_path)
        sig = _signal()
        key = signal_idempotency_key(sig)
        assert eng1.risk_manager.can_enter_trade(sig, idempotency_key=key)[0]
        pos = _position()
        eng1.paper_executor._paper_positions[pos.id] = pos
        eng1.risk_manager.add_position(pos)         # persists position
        # crash — record_trade_entry never ran

        eng2, _ = _engine(tmp_path)                 # restart
        assert eng2.risk_manager.get_daily_state()['trades_today'] == 1
        # The consumed key now blocks a duplicate entry for the same signal
        ok2, reason = eng2.risk_manager.can_enter_trade(
            sig, idempotency_key=key)
        assert not ok2
        assert 'Duplicate' in reason or 'exists' in reason

    def test_duplicate_signal_rejected_after_restart(self, tmp_path):
        """A recorded entry's idempotency key is durable — a re-scanned
        signal after restart cannot create a second position."""
        eng1, _ = _engine(tmp_path)
        sig = _signal()
        key = signal_idempotency_key(sig)
        assert eng1.paper_executor.execute_entry(
            sig, quote_time=datetime.now()) is not None
        assert eng1.risk_manager.get_daily_state()['trades_today'] == 1

        eng2, _ = _engine(tmp_path)                 # restart
        ok, reason = eng2.risk_manager.can_enter_trade(
            sig, idempotency_key=key)
        assert not ok
        assert eng2.paper_executor.get_open_positions()[0].symbol == 'TEST'

    def test_restart_with_pending_reservation_no_double_count(self, tmp_path):
        """Claim + position persisted mid-entry; restart counts it exactly
        once — reconcile does not double-count an already-recorded entry."""
        eng1, _ = _engine(tmp_path)
        sig = _signal()
        pos = eng1.paper_executor.execute_entry(
            sig, quote_time=datetime.now())
        assert pos is not None
        assert eng1.risk_manager.get_daily_state()['trades_today'] == 1

        eng2, _ = _engine(tmp_path)                 # clean restart
        assert eng2.risk_manager.get_daily_state()['trades_today'] == 1

    def test_pending_claim_released_does_not_consume_limit(self, tmp_path):
        """Released crash claims must not permanently eat the 5/day limit
        or the concurrent cap."""
        eng1, _ = _engine(tmp_path)
        cap = eng1.risk_manager.config.max_concurrent_positions
        # Claims reserve slots — exactly 'cap' stuck claims die with process
        for i in range(cap):
            sig = _signal(f'SYM{i}')
            assert eng1.risk_manager.can_enter_trade(
                sig, idempotency_key=f'k{i}')[0]
        # cap+1th claim correctly refused — claims count against exposure
        assert not eng1.risk_manager.can_enter_trade(
            _signal('SYM_CAP'), idempotency_key='k_cap')[0]
        # crash before any position persisted

        eng2, _ = _engine(tmp_path)                 # restart
        rm = eng2.risk_manager
        assert rm._daily_state.pending_entry_claims == {}
        assert rm.get_daily_state()['trades_today'] == 0
        # Full limit available — 5 real entries record and exit cleanly
        for i in range(rm.config.max_trades_per_day):
            pos = eng2.paper_executor.execute_entry(
                _signal(f'NEW{i}'), quote_time=datetime.now())
            assert pos is not None, f'entry {i} blocked by stale claim'
            eng2.paper_executor.execute_exit(pos, 101.0, 'TARGET_HIT')
        assert rm.get_daily_state()['trades_today'] == 5
        ok, _ = rm.can_enter_trade(_signal('EXTRA'), idempotency_key='kx')
        assert not ok                                 # limit still enforced

    def test_phantom_position_dropped_on_restart(self, tmp_path):
        """Exit journaled but positions.json write lost — the phantom
        position must be dropped, not exited a second time."""
        eng1, _ = _engine(tmp_path)
        pos = _position()
        trade_time = pos.entry_time
        # Simulate: exit trade written to journal...
        trade = eng1.paper_executor.execute_exit(pos, 110.0, 'STOP_LOSS')
        trade.entry_time = trade_time
        eng1.trade_manager.add_trade(trade)
        # ...but positions.json still holds the 'open' position (crash)
        eng1.state_store.save_positions([pos])

        eng2, _ = _engine(tmp_path)                 # restart
        assert eng2.paper_executor.get_open_positions() == []
        assert len(eng2.trade_manager.get_trades()) == 1   # no duplicate exit


# ══════════ EOD unresolved lifecycle ═══════════════════════════════════

class TestEodUnresolvedLifecycle:

    def test_unresolved_marked_persisted_visible(self, tmp_path):
        eng, _ = _engine(tmp_path)
        _add_position(eng, _position())
        eng.validator.is_square_off_window = MagicMock(return_value=True)
        eng.validator.is_market_open = MagicMock(return_value=False)
        eng.market_data.get_quotes = MagicMock(return_value={})
        eng._maybe_square_off()
        pos = eng.paper_executor.get_open_positions()[0]
        assert pos.eod_pending is True
        # persisted + visible via the dashboard payload
        row = eng._position_to_dict(pos)
        assert row['eod_pending'] is True
        # survives restart still flagged
        eng2, _ = _engine(tmp_path)
        pos2 = eng2.paper_executor.get_open_positions()[0]
        assert pos2.eod_pending is True

    def test_outage_through_close_recovers_next_session(self, tmp_path):
        """Quotes down at 15:00 → retained. Window ends (still down) →
        held overnight, never exited. Next session: monitoring resumes and
        the next square-off exits on a fresh quote."""
        eng, incidents = _engine(tmp_path)
        pos = _position(entry=datetime.now() - timedelta(days=1))
        _add_position(eng, pos)
        eng.market_data.get_quotes = MagicMock(return_value={})
        eng.validator.is_square_off_window = MagicMock(return_value=True)
        eng.validator.is_market_open = MagicMock(return_value=False)
        eng._maybe_square_off()
        assert len(eng.paper_executor.get_open_positions()) == 1

        # Window closes with quotes still down — no exit path runs at all
        eng.validator.is_square_off_window = MagicMock(return_value=False)
        eng._maybe_square_off()
        assert len(eng.paper_executor.get_open_positions()) == 1

        # Next session — restart, monitoring resumes on fresh quotes
        eng2, _ = _engine(tmp_path)
        assert len(eng2.paper_executor.get_open_positions()) == 1
        eng2.market_data.get_quotes = MagicMock(return_value={
            'TEST': {'ltp': 104.0, 'trading_symbol': 'TEST-EQ',
                     'exchange': 'NSE', 'exchange_ts': datetime.now()}})
        eng2.validator.is_square_off_window = MagicMock(return_value=True)
        eng2.validator.is_market_open = MagicMock(return_value=False)
        eng2._maybe_square_off()
        t = eng2.trade_manager.get_trades()[0]
        assert t.exit_reason == 'EOD_SQUARE_OFF'
        assert t.exit_price == 104.0
        assert t.exit_price_source == 'VERIFIED'

    def test_unresolved_positions_still_count_against_entry_cap(self, tmp_path):
        """Two retained unresolved positions still fill the concurrent cap —
        no new entry may violate exposure limits."""
        eng, _ = _engine(tmp_path)
        _add_position(eng, _position('A'))
        _add_position(eng, _position('B'))
        cap = eng.risk_manager.config.max_concurrent_positions
        assert len(eng.paper_executor.get_open_positions()) == 2 == cap
        ok, reason = eng.risk_manager.can_enter_trade(
            _signal('C'), idempotency_key='kC')
        assert not ok
        assert 'concurrent' in reason.lower()

    def test_no_duplicate_eod_exit_after_restart(self, tmp_path):
        """Restart mid-EOD (trade journaled, position save lost) — phantom
        dedup prevents a second exit of the same position."""
        eng1, _ = _engine(tmp_path)
        pos = _position()
        trade = eng1.paper_executor.execute_exit(pos, 110.0, 'EOD_SQUARE_OFF')
        trade.entry_time = pos.entry_time
        eng1.trade_manager.add_trade(trade)
        eng1.state_store.save_positions([pos])      # stale write survives
        eng2, _ = _engine(tmp_path)
        assert eng2.paper_executor.get_open_positions() == []
        assert len(eng2.trade_manager.get_trades()) == 1
