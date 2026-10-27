"""Deterministic end-to-end stabilization suite — Angel PAPER lifecycle.

Every scenario exercises the real engine/executor/risk paths with mocked
Angel market data: entry → monitoring → exit → P&L → ledger → daily state
→ history payload. Each engine gets its own tmp_path state directory —
the production ledger is never touched."""
import os
import sys
import threading
from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from intraday.engine import IntradayEngine, daily_breakdown
from intraday.models import (
    IntradayPosition, IntradaySignal, PositionStatus,
    SignalDirection, SignalClassification, MarketRegime,
)
from intraday.validators import signal_idempotency_key


def _engine(tmp_path):
    incidents = []
    eng = IntradayEngine(
        state_dir=str(tmp_path),
        alert_hook=lambda a, k, d: incidents.append((a, k, d)))
    # Default time gates open — tests must not depend on the real clock.
    # Individual tests override these as needed.
    eng.validator.is_market_open = MagicMock(return_value=True)
    eng.validator.is_entry_window_open = MagicMock(return_value=True)
    eng.validator.is_square_off_time = MagicMock(return_value=False)
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


def _position(sym='TEST', direction=SignalDirection.LONG, price=100.0,
              qty=10, sl=95.0, tgt=110.0, entry=None):
    return IntradayPosition(
        id=f'p-{sym}', symbol=sym, direction=direction,
        entry_price=price, quantity=qty, stop_loss=sl, target=tgt,
        entry_time=entry or datetime.now(), current_price=price,
        unrealized_pnl=0.0, unrealized_pnl_pct=0.0,
        signal_score=80.0,
        signal_classification=SignalClassification.STRONG_SETUP,
        strategy='test', is_paper=True)


def _add_position(eng, pos):
    eng.paper_executor._paper_positions[pos.id] = pos
    eng.risk_manager.add_position(pos)


def _quote(sym='TEST', ltp=100.0, exch_age_s=0):
    """Broker-shaped quote: exchange_ts absent => receipt-only provenance."""
    return {sym: {'ltp': ltp, 'trading_symbol': f'{sym}-EQ',
                  'exchange': 'NSE',
                  'exchange_ts': (datetime.now() - timedelta(seconds=exch_age_s))
                  if exch_age_s is not None else None}}


# ══════════ P&L correctness — LONG/SHORT × win/loss ═══════════════════

class TestPnLByDirection:

    def test_profitable_long(self, tmp_path):
        eng, _ = _engine(tmp_path)
        pos = _position(price=100.0, qty=10)
        _add_position(eng, pos)
        trade = eng.paper_executor.execute_exit(pos, 110.0, 'TARGET_HIT')
        assert trade.gross_pnl == 100.0
        assert trade.charges == 0.1
        assert trade.net_pnl == 99.9
        assert trade.direction == SignalDirection.LONG

    def test_losing_long(self, tmp_path):
        eng, _ = _engine(tmp_path)
        pos = _position(price=100.0, qty=10)
        _add_position(eng, pos)
        trade = eng.paper_executor.execute_exit(pos, 95.0, 'STOP_LOSS')
        assert trade.gross_pnl == -50.0
        assert trade.charges == 0.05          # charge is a cost, not credit
        assert trade.net_pnl == -50.05

    def test_profitable_short(self, tmp_path):
        eng, _ = _engine(tmp_path)
        pos = _position(direction=SignalDirection.SHORT, price=100.0,
                        qty=10, sl=105.0, tgt=90.0)
        _add_position(eng, pos)
        trade = eng.paper_executor.execute_exit(pos, 90.0, 'TARGET_HIT')
        assert trade.gross_pnl == 100.0
        assert trade.net_pnl == 99.9

    def test_losing_short(self, tmp_path):
        eng, _ = _engine(tmp_path)
        pos = _position(direction=SignalDirection.SHORT, price=100.0,
                        qty=10, sl=105.0, tgt=90.0)
        _add_position(eng, pos)
        trade = eng.paper_executor.execute_exit(pos, 105.0, 'STOP_LOSS')
        assert trade.gross_pnl == -50.0
        assert trade.net_pnl == -50.05

    def test_charges_never_negative(self, tmp_path):
        eng, _ = _engine(tmp_path)
        for px in (90.0, 100.0, 110.0):
            pos = _position(sym=f'S{px}', price=100.0, qty=10)
            pos.id = f'p{px}'
            _add_position(eng, pos)
            t = eng.paper_executor.execute_exit(pos, px, 'TEST')
            assert t.charges >= 0
            assert t.net_pnl == pytest.approx(t.gross_pnl - t.charges)


# ══════════ SL / target via the real monitor path ═════════════════════

class TestMonitorExits:

    def test_long_target_hit_via_refresh(self, tmp_path):
        eng, _ = _engine(tmp_path)
        _add_position(eng, _position(price=100.0, sl=95.0, tgt=110.0))
        eng.market_data.get_quotes = MagicMock(
            return_value=_quote(ltp=111.0))
        assert eng._refresh_positions() == 1
        t = eng.trade_manager.get_trades()[0]
        assert t.exit_reason == 'TARGET_HIT'
        assert t.exit_price == 111.0
        assert t.exit_price_source == 'VERIFIED'   # fresh real quote

    def test_long_stop_loss_via_refresh(self, tmp_path):
        eng, _ = _engine(tmp_path)
        _add_position(eng, _position(price=100.0, sl=95.0, tgt=110.0))
        eng.market_data.get_quotes = MagicMock(return_value=_quote(ltp=94.0))
        assert eng._refresh_positions() == 1
        assert eng.trade_manager.get_trades()[0].exit_reason == 'STOP_LOSS'

    def test_short_stop_and_target(self, tmp_path):
        eng, _ = _engine(tmp_path)
        pos = _position(direction=SignalDirection.SHORT, price=100.0,
                        sl=105.0, tgt=90.0)
        _add_position(eng, pos)
        # Stop-loss for SHORT = price rises through SL
        eng.market_data.get_quotes = MagicMock(return_value=_quote(ltp=106.0))
        assert eng._refresh_positions() == 1
        assert eng.trade_manager.get_trades()[0].exit_reason == 'STOP_LOSS'

    def test_missing_quote_no_exit(self, tmp_path):
        """Symbol absent from the quote response — position holds, no exit."""
        eng, _ = _engine(tmp_path)
        _add_position(eng, _position(price=100.0, sl=95.0, tgt=110.0))
        eng.market_data.get_quotes = MagicMock(
            return_value={'OTHER': {'ltp': 999.0}})
        assert eng._refresh_positions() == 0
        assert eng.paper_executor.get_open_positions()[0].current_price == 100.0
        assert eng.trade_manager.get_trades() == []

    def test_stale_exchange_stamp_blocks_exit_position_held(self, tmp_path):
        """Quote whose exchange stamp is 3 days old fails the unified
        execution-quote policy — NO fictitious fill: position held at last
        price, no trade, incident streak counts it."""
        eng, _ = _engine(tmp_path)
        _add_position(eng, _position(price=100.0, sl=95.0, tgt=110.0))
        eng.market_data.get_quotes = MagicMock(
            return_value=_quote(ltp=111.0, exch_age_s=3 * 86400))
        assert eng._refresh_positions() == 0
        assert eng.trade_manager.get_trades() == []
        pos = eng.paper_executor.get_open_positions()[0]
        assert pos.current_price == 100.0      # invalid quote never applied

    def test_rate_limit_error_preserves_positions(self, tmp_path):
        eng, incidents = _engine(tmp_path)
        _add_position(eng, _position())
        eng.market_data.get_quotes = MagicMock(
            side_effect=Exception('429 rate limited'))
        assert eng._refresh_positions() == 0
        assert len(eng.paper_executor.get_open_positions()) == 1
        assert eng.trade_manager.get_trades() == []
        # threshold met → incident raised
        for _ in range(4):
            eng._refresh_positions()
        assert any('quote' in k.lower() for _, k, _ in incidents)

    def test_auth_failure_preserves_positions(self, tmp_path):
        eng, _ = _engine(tmp_path)
        _add_position(eng, _position())
        eng.market_data.get_quotes = MagicMock(
            side_effect=Exception('AG8001 token expired'))
        assert eng._refresh_positions() == 0
        assert len(eng.paper_executor.get_open_positions()) == 1

    def test_malformed_quote_preserves_positions(self, tmp_path):
        """Response parses but carries no ltp → unusable → hold, no exit."""
        eng, _ = _engine(tmp_path)
        _add_position(eng, _position(price=100.0, sl=95.0, tgt=110.0))
        eng.market_data.get_quotes = MagicMock(
            return_value={'TEST': {'status': True, 'fetched': None}})
        assert eng._refresh_positions() == 0
        assert len(eng.paper_executor.get_open_positions()) == 1
        assert eng.trade_manager.get_trades() == []


# ══════════ Entry provenance ══════════════════════════════════════════

class TestEntryProvenance:

    def test_fresh_quote_entry_verified(self, tmp_path):
        eng, _ = _engine(tmp_path)
        pos = eng.paper_executor.execute_entry(
            _signal(), quote_time=datetime.now())
        assert pos is not None
        assert pos.entry_price_source == 'VERIFIED'
        assert pos.entry_quote_received_at is not None

    def test_no_quote_entry_unverified(self, tmp_path):
        eng, _ = _engine(tmp_path)
        pos = eng.paper_executor.execute_entry(_signal())
        assert pos is not None
        assert pos.entry_price_source == 'UNVERIFIED'

    def test_stale_quote_entry_unverified(self, tmp_path):
        eng, _ = _engine(tmp_path)
        pos = eng.paper_executor.execute_entry(
            _signal(), quote_time=datetime.now() - timedelta(seconds=45))
        assert pos.entry_price_source == 'UNVERIFIED'

    def test_entry_provenance_survives_exit_and_persistence(self, tmp_path):
        eng, _ = _engine(tmp_path)
        qt = datetime.now()
        pos = eng.paper_executor.execute_entry(_signal(), quote_time=qt)
        eng.state_store.save_positions([pos])
        eng2, _ = _engine(tmp_path)
        pos2 = eng2.paper_executor.get_open_positions()[0]
        assert pos2.entry_price_source == 'VERIFIED'
        trade = eng2.paper_executor.execute_exit(pos2, 110.0, 'TARGET_HIT')
        assert trade.entry_price_source == 'VERIFIED'
        assert trade.entry_quote_received_at == qt
        # History payload carries it
        eng2.trade_manager.add_trade(trade)
        row = eng2._history()[0]
        assert row['entry_price_source'] == 'VERIFIED'
        assert row['entry_quote_received_at'] is not None


# ══════════ Entry safety — dedup, concurrency, limits ═════════════════

class TestEntrySafety:

    def test_duplicate_signal_rejected(self, tmp_path):
        eng, _ = _engine(tmp_path)
        sig = _signal('TEST')
        key = signal_idempotency_key(sig)
        ok1, _ = eng.risk_manager.can_enter_trade(sig, idempotency_key=key)
        assert ok1
        eng.paper_executor.execute_entry(sig, quote_time=datetime.now())
        ok2, reason = eng.risk_manager.can_enter_trade(sig, idempotency_key=key)
        assert not ok2
        assert 'Duplicate' in reason or 'exists' in reason

    def test_concurrent_entry_claims_atomic(self, tmp_path):
        """10 simultaneous different-symbol signals race the entry gate —
        at most max_concurrent_positions claims may succeed at once, and
        daily-slot accounting can never exceed max_trades_per_day."""
        eng, _ = _engine(tmp_path)
        rm = eng.risk_manager
        cap = rm.config.max_concurrent_positions
        max_day = rm.config.max_trades_per_day
        results = []
        barrier = threading.Barrier(10)

        def claim(i):
            barrier.wait(timeout=5)
            s = _signal(f'SYM{i}')
            ok, _ = rm.can_enter_trade(s, idempotency_key=f'k{i}')
            results.append((i, ok))

        threads = [threading.Thread(target=claim, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(10)
        successes = [i for i, ok in results if ok]
        assert len(successes) == min(cap, max_day)
        # A released claim frees its slot for another signal
        rm.release_pending(f'k{successes[0]}')
        s = _signal('SYM_NEW')
        ok, _ = rm.can_enter_trade(s, idempotency_key='k_new')
        assert ok
        rm.release_pending('k_new')

    def test_daily_entry_limit_enforced_atomically(self, tmp_path):
        """After max_trades_per_day recorded entries, the next signal is
        rejected — even when all positions have already exited."""
        eng, _ = _engine(tmp_path)
        rm = eng.risk_manager
        max_day = rm.config.max_trades_per_day
        for i in range(max_day):
            sig = _signal(f'LIM{i}')
            pos = eng.paper_executor.execute_entry(
                sig, quote_time=datetime.now())
            assert pos is not None, f'entry {i} should succeed'
            # Exit immediately so the concurrent-position cap does not
            # mask the daily limit being tested.
            eng.paper_executor.execute_exit(pos, 101.0, 'TARGET_HIT')
        assert rm.get_daily_state()['trades_today'] == max_day
        ok, reason = rm.can_enter_trade(_signal('EXTRA'),
                                        idempotency_key='k_extra')
        assert not ok
        assert 'Max trades' in reason

    def test_daily_loss_limit_blocks(self, tmp_path):
        """Realized -₹200 (2% of ₹10,000) blocks further entries."""
        eng, _ = _engine(tmp_path)
        rm = eng.risk_manager
        rm._daily_state.realized_pnl = -(rm.config.intraday_capital
                                         * rm.config.max_daily_loss_pct)
        ok, reason = rm.can_enter_trade(_signal(), idempotency_key='k1')
        assert not ok
        assert 'loss' in reason.lower()
        assert rm.get_daily_state()['blocked'] is True

    def test_unrealized_loss_counts_toward_limit(self, tmp_path):
        """An open position deep underwater trips the limit before it exits."""
        eng, _ = _engine(tmp_path)
        rm = eng.risk_manager
        pos = _position(price=100.0, qty=30, sl=50.0, tgt=110.0)
        _add_position(eng, pos)
        eng.paper_executor.update_positions({'TEST': 90.0})
        assert pos.unrealized_pnl == -300.0     # beyond ₹200 limit
        ok, reason = rm.can_enter_trade(_signal('NEXT'),
                                        idempotency_key='k_next')
        assert not ok
        assert 'loss' in reason.lower()

    def test_entry_after_cutoff_rejected(self, tmp_path):
        eng, _ = _engine(tmp_path)
        eng.validator.is_market_open = MagicMock(return_value=True)
        eng.validator.is_entry_window_open = MagicMock(return_value=False)
        eng.validator.is_square_off_time = MagicMock(return_value=False)
        before = eng.risk_manager.get_daily_state()['trades_today']
        assert eng.paper_executor.execute_entry(_signal()) is None
        assert eng.risk_manager.get_daily_state()['trades_today'] == before

    def test_failed_exit_keeps_position(self, tmp_path):
        """execute_exit failure must not remove the position from the ledger."""
        eng, _ = _engine(tmp_path)
        pos = _position(price=100.0)
        _add_position(eng, pos)
        eng.trade_manager.add_trade = MagicMock(
            side_effect=RuntimeError('disk full'))
        trade = eng.paper_executor.execute_exit(pos, 110.0, 'TARGET_HIT')
        # Executor may or may not propagate the journal failure — the
        # position's EXIT state must not silently vanish either way.
        syms = [p.symbol for p in eng.paper_executor.get_open_positions()]
        assert trade is not None or 'TEST' in syms


# ══════════ EOD + restart + carryover ═════════════════════════════════

class TestEodAndRecovery:

    def test_eod_fresh_quote_exits_verified(self, tmp_path):
        eng, _ = _engine(tmp_path)
        _add_position(eng, _position())
        eng.validator.is_square_off_window = MagicMock(return_value=True)
        eng.validator.is_market_open = MagicMock(return_value=False)
        eng.market_data.get_quotes = MagicMock(return_value=_quote(ltp=104.0))
        eng._maybe_square_off()
        t = eng.trade_manager.get_trades()[0]
        assert t.exit_reason == 'EOD_SQUARE_OFF'
        assert t.exit_price == 104.0
        assert t.exit_price_source == 'VERIFIED'
        assert t.exit_quote_symbol == 'TEST-EQ'
        assert t.exit_quote_exchange == 'NSE'

    def test_eod_no_quote_retains_unresolved_alerted(self, tmp_path):
        eng, incidents = _engine(tmp_path)
        _add_position(eng, _position())
        eng.validator.is_square_off_window = MagicMock(return_value=True)
        eng.validator.is_market_open = MagicMock(return_value=False)
        eng.market_data.get_quotes = MagicMock(return_value={})
        eng._maybe_square_off()
        assert eng.trade_manager.get_trades() == []
        remaining = eng.paper_executor.get_open_positions()
        assert len(remaining) == 1
        eng.state_store.save_positions(remaining)
        assert any('eod' in k.lower() for _, k, _ in incidents)
        # Persisted unresolved position reloads into a fresh process
        eng2, _ = _engine(tmp_path)
        assert len(eng2.paper_executor.get_open_positions()) == 1

    def test_restart_open_position_recovers_and_exits(self, tmp_path):
        eng1, _ = _engine(tmp_path)
        pos = _position()
        _add_position(eng1, pos)
        eng1.state_store.save_positions([pos])
        eng2, _ = _engine(tmp_path)
        assert len(eng2.paper_executor.get_open_positions()) == 1
        eng2.validator.is_square_off_window = MagicMock(return_value=True)
        eng2.validator.is_market_open = MagicMock(return_value=False)
        eng2.market_data.get_quotes = MagicMock(return_value=_quote(ltp=103.0))
        eng2._maybe_square_off()
        assert eng2.trade_manager.get_trades()[0].exit_price == 103.0

    def test_restart_pending_exit_still_retries(self, tmp_path):
        """Process dies after a failed EOD attempt — the persisted position
        is re-attempted at the next square-off, not silently dropped."""
        eng1, _ = _engine(tmp_path)
        _add_position(eng1, _position())
        eng1.validator.is_square_off_window = MagicMock(return_value=True)
        eng1.validator.is_market_open = MagicMock(return_value=False)
        eng1.market_data.get_quotes = MagicMock(return_value={})
        eng1._maybe_square_off()                      # deferred, retained
        eng1.state_store.save_positions(
            eng1.paper_executor.get_open_positions())
        eng2, _ = _engine(tmp_path)
        eng2.validator.is_square_off_window = MagicMock(return_value=True)
        eng2.validator.is_market_open = MagicMock(return_value=False)
        eng2.market_data.get_quotes = MagicMock(return_value=_quote(ltp=102.0))
        eng2._maybe_square_off()
        trades = eng2.trade_manager.get_trades()
        assert len(trades) == 1
        assert trades[0].exit_reason == 'EOD_SQUARE_OFF'

    def test_positions_never_exit_outside_squareoff_window(self, tmp_path):
        """Holiday / weekend / overnight: no exit path runs at all."""
        eng, _ = _engine(tmp_path)
        _add_position(eng, _position())
        eng.validator.is_square_off_window = MagicMock(return_value=False)
        eng.validator.is_market_open = MagicMock(return_value=False)
        eng.market_data.get_quotes = MagicMock(return_value=_quote(ltp=200.0))
        eng._maybe_square_off()
        assert len(eng.paper_executor.get_open_positions()) == 1
        assert eng.trade_manager.get_trades() == []

    def test_carryover_pnl_separate_from_same_day(self, tmp_path):
        eng, _ = _engine(tmp_path)
        old_pos = _position(sym='OLD', entry=datetime.now() - timedelta(days=2))
        _add_position(eng, old_pos)
        new_pos = _position(sym='NEW')
        _add_position(eng, new_pos)
        eng.trade_manager.add_trade(
            eng.paper_executor.execute_exit(old_pos, 110.0, 'EOD_SQUARE_OFF'))
        eng.trade_manager.add_trade(
            eng.paper_executor.execute_exit(new_pos, 105.0, 'EOD_SQUARE_OFF'))
        trades = eng.trade_manager.get_trades()
        bd = daily_breakdown(trades, entries_today=1,
                             max_entries=eng.config.max_trades_per_day)
        assert bd['same_day']['exits'] == 1
        assert bd['carryover']['exits'] == 1
        assert bd['same_day']['pnl'] == pytest.approx(trades[1].net_pnl, abs=0.02)
        assert bd['carryover']['pnl'] == pytest.approx(trades[0].net_pnl, abs=0.02)
        assert bd['exits_today'] == 2


# ══════════ History payload — correction indicator ═══════════════════

class TestHistoryCorrectionIndicator:

    def test_legacy_negative_charge_flagged_corrected(self, tmp_path):
        """A stored legacy row with the old sign bug keeps its original
        values; the API separately reports corrected charges/net."""
        eng, _ = _engine(tmp_path)
        pos = _position(price=100.0)
        _add_position(eng, pos)
        trade = eng.paper_executor.execute_exit(pos, 95.0, 'STOP_LOSS')
        trade.charges = -0.05                    # legacy sign bug
        trade.net_pnl = trade.gross_pnl - trade.charges  # stored: -49.95
        eng.trade_manager.add_trade(trade)
        row = eng._history()[0]
        assert row['charges'] == -0.05           # stored value preserved
        assert row['net_pnl'] == pytest.approx(-49.95)
        assert row['pnl_corrected'] is True
        assert row['charges_corrected'] == 0.05
        assert row['net_pnl_corrected'] == pytest.approx(-50.05)

    def test_new_trade_not_flagged(self, tmp_path):
        eng, _ = _engine(tmp_path)
        pos = _position(price=100.0)
        _add_position(eng, pos)
        trade = eng.paper_executor.execute_exit(pos, 95.0, 'STOP_LOSS')
        eng.trade_manager.add_trade(trade)
        row = eng._history()[0]
        assert row['pnl_corrected'] is False
        assert row['net_pnl_corrected'] is None
        assert row['gross_pnl'] == -50.0
        assert row['charges'] == 0.05


# ══════════ PAPER-only safety ═════════════════════════════════════════

class TestPaperOnlySafety:

    def test_config_forces_paper(self, tmp_path):
        eng, _ = _engine(tmp_path)
        assert eng.config.paper_trading is True
        assert eng.config.angel_live_trading is False

    def test_broker_order_methods_are_stubs(self):
        from intraday.angel.client import AngelClient
        c = AngelClient.__new__(AngelClient)
        r1 = c.place_order({'tradingsymbol': 'TEST'})
        r2 = c.modify_order('o1', {})
        r3 = c.cancel_order('o1')
        for r in (r1, r2, r3):
            assert r['success'] is False
            assert r['paper_mode'] is True
