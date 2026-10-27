"""
LIVE-readiness tests: mock-broker order lifecycle, risk controls,
reconciliation, process singleton and the separated dashboard status
axes. No real broker calls — the gateway is fully mocked, and the
arm-gate tests verify real submission is impossible while disarmed.
"""
import json
import os
import threading
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from intraday.live_risk import LiveRiskConfig, LiveRiskManager
from intraday.live_adapter import (
    LiveExecutionAdapter, LiveOrderStore,
    EXIT_REQUESTED, EXIT_ACCEPTED, EXIT_PARTIAL, EXIT_FILLED,
    EXIT_REJECTED, EXIT_UNKNOWN)
from intraday.angel.orders import (
    AngelOrderGateway, classify_broker_status, DISARMED_RESULT)
from intraday.models import IntradayConfig


# ── fixtures ──────────────────────────────────────────────────────────

def make_cfg(**kw):
    """Fully-enabled LIVE risk config (tests override per-case)."""
    base = dict(
        enabled=True, capital=50000.0, max_order_value=20000.0,
        max_order_qty=500, max_per_trade_loss=500.0, max_daily_loss=400.0,
        max_entries_per_day=3, max_concurrent_positions=2,
        max_gross_exposure=40000.0, allow_leverage=False,
        max_slippage_pct=1.0, entry_cutoff="23:59", force_exit_by="23:59",
        max_broker_errors=3, order_timeout_s=30.0, poll_interval_s=5.0)
    base.update(kw)
    return LiveRiskConfig(**base)


def book_row(orderid="BO1", status="complete", tag=None, filled=10, avg=100.0):
    return {'orderid': orderid, 'status': status, 'ordertag': tag,
            'filledshares': str(filled), 'averageprice': str(avg),
            'tradingsymbol': 'RELIANCE-EQ'}


class MockGateway:
    """Scriptable broker gateway — same surface as AngelOrderGateway."""

    def __init__(self, armed=True):
        self._armed = armed
        self.place_calls = []
        self.cancel_calls = []
        self.place_result = {'success': True, 'broker_order_id': 'BO1'}
        self._order_seq = 0
        self.book = []
        self.positions_rows = []
        self.funds_result = {'success': True, 'available_cash': 1e9}
        self.book_fail = False

    def armed(self):
        return self._armed

    def place(self, params):
        self.place_calls.append(dict(params))
        if not self._armed:
            return dict(DISARMED_RESULT)
        result = dict(self.place_result)
        if result.get('success') and 'broker_order_id' in result:
            self._order_seq += 1
            result['broker_order_id'] = f"BO{self._order_seq}"
        return result

    def modify(self, params):
        return dict(DISARMED_RESULT) if not self._armed else {'success': True}

    def cancel(self, order_id, variety="NORMAL"):
        self.cancel_calls.append(order_id)
        return dict(DISARMED_RESULT) if not self._armed else {'success': True}

    def order_book(self):
        if self.book_fail:
            return {'success': False, 'error': 'network down', 'orders': []}
        return {'success': True, 'orders': list(self.book)}

    def trade_book(self):
        return {'success': True, 'trades': []}

    def positions(self):
        return {'success': True, 'positions': list(self.positions_rows)}

    def funds(self):
        return dict(self.funds_result)


def make_adapter(tmp_path, armed=True, gateway=None, risk=None, alerts=None):
    risk = risk or LiveRiskManager(make_cfg(), str(tmp_path))
    gw = gateway or MockGateway(armed=armed)
    hook = (lambda a, k, d: alerts.append((a, k, d))) \
        if alerts is not None else None
    return LiveExecutionAdapter(
        gw, risk, instruments=None, state_dir=str(tmp_path),
        alert_hook=hook, armed=armed), gw, risk


ENTRY = dict(symbol='RELIANCE', direction='LONG', quantity=10,
             expected_price=100.0, stop_loss=98.0, target=104.0,
             quote_ltp=100.0, intent_key='sig1')


# ── arm gate ──────────────────────────────────────────────────────────

class TestArmGate:
    def test_disarmed_adapter_never_calls_gateway(self, tmp_path):
        adapter, gw, _ = make_adapter(tmp_path, armed=False)
        result = adapter.submit_entry(**ENTRY)
        assert result['success'] is False and result.get('disarmed')
        assert gw.place_calls == []

    def test_gateway_refuses_place_when_unarmed(self):
        client = MagicMock()
        gw = AngelOrderGateway(client, armed=lambda: False)
        out = gw.place({'tradingsymbol': 'X-EQ'})
        assert out['success'] is False and out.get('disarmed')
        client._call.assert_not_called()

    def test_gateway_arm_check_is_calltime(self):
        state = {'armed': False}
        client = MagicMock()
        client._call.return_value = {'status': True, 'data': {'orderid': 'B9'}}
        gw = AngelOrderGateway(client, armed=lambda: state['armed'])
        assert gw.place({}).get('disarmed')
        state['armed'] = True
        out = gw.place({})
        assert out['success'] and out['broker_order_id'] == 'B9'

    def test_readonly_calls_allowed_unarmed(self):
        client = MagicMock()
        client.get_order_book.return_value = {'success': True,
                                              'data': {'data': [{'orderid': 'x'}]}}
        gw = AngelOrderGateway(client, armed=lambda: False)
        out = gw.order_book()
        assert out['success'] and out['orders'][0]['orderid'] == 'x'

    def test_arm_requires_flag_and_env(self):
        from intraday.config import live_armed
        cfg = IntradayConfig(angel_live_trading=True)
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop('ANGEL_LIVE_ARMED', None)
            assert live_armed(cfg) is False
        with patch.dict(os.environ, {'ANGEL_LIVE_ARMED': 'CONFIRM'}):
            assert live_armed(cfg) is True
        with patch.dict(os.environ, {'ANGEL_LIVE_ARMED': 'yes'}):
            assert live_armed(cfg) is False


# ── entry/exit lifecycle ──────────────────────────────────────────────

class TestEntryExitLifecycle:
    def test_ack_is_not_a_fill(self, tmp_path):
        adapter, gw, _ = make_adapter(tmp_path)
        gw.book = [book_row(status='open', tag=None, filled=0, avg=0)]
        result = adapter.submit_entry(**ENTRY)
        assert result['success'] and result['state'] == 'OPEN'
        assert adapter.open_positions() == []

    def test_long_entry_fill_creates_position(self, tmp_path):
        adapter, gw, _ = make_adapter(tmp_path)
        result = adapter.submit_entry(**ENTRY)
        cid = result['client_order_id']
        gw.book = [book_row(tag=cid[:20], filled=10, avg=100.25)]
        adapter.poll_orders()
        pos = adapter.open_positions()
        assert len(pos) == 1 and pos[0]['entry_price'] == 100.25
        assert adapter.risk.get_status()['entries_today'] == 1

    def test_long_exit_fill_closes_and_books_pnl(self, tmp_path):
        adapter, gw, _ = make_adapter(tmp_path)
        r = adapter.submit_entry(**ENTRY)
        gw.book = [book_row(tag=r['client_order_id'][:20], filled=10, avg=100.0)]
        adapter.poll_orders()
        gw.book = []  # next poll shouldn't re-finalize
        out = adapter.submit_exit('RELIANCE', 'TARGET_HIT')
        assert out['success']
        pos = next(p for p in adapter._positions if p['symbol'] == 'RELIANCE')
        assert pos['exit_state'] in (EXIT_ACCEPTED, EXIT_UNKNOWN, EXIT_REJECTED)
        gw.book = [book_row(orderid='BO2', tag=out['client_order_id'][:20],
                            filled=10, avg=104.0)]
        adapter.poll_orders()
        assert pos['exit_state'] == EXIT_FILLED and pos['state'] == 'CLOSED'
        assert pos['realized_pnl'] == pytest.approx(40.0)

    def test_short_pnl_sign(self, tmp_path):
        adapter, gw, _ = make_adapter(tmp_path)
        e = dict(ENTRY, direction='SHORT', intent_key='sigS')
        r = adapter.submit_entry(**e)
        gw.book = [book_row(tag=r['client_order_id'][:20], avg=100.0)]
        adapter.poll_orders()
        out = adapter.submit_exit('RELIANCE', 'STOP_LOSS')
        gw.book = [book_row(tag=out['client_order_id'][:20], avg=105.0)]
        adapter.poll_orders()
        pos = adapter._positions[0]
        assert pos['realized_pnl'] == pytest.approx(-50.0)

    def test_partial_fill_then_complete(self, tmp_path):
        adapter, gw, _ = make_adapter(tmp_path)
        r = adapter.submit_entry(**ENTRY)
        gw.book = [book_row(tag=r['client_order_id'][:20], filled=10)]
        adapter.poll_orders()
        out = adapter.submit_exit('RELIANCE', 'EOD_SQUARE_OFF')
        gw.book = [book_row(tag=out['client_order_id'][:20], status='open',
                            filled=4, avg=103.0)]
        adapter.poll_orders()
        order = adapter._find_order(out['client_order_id'])
        assert order['state'] == 'PARTIAL'
        pos = adapter._get_position('RELIANCE')
        assert pos['exit_state'] == EXIT_PARTIAL and pos['state'] == 'OPEN'
        gw.book = [book_row(tag=out['client_order_id'][:20],
                            filled=10, avg=103.0)]
        adapter.poll_orders()
        assert pos['exit_state'] == EXIT_FILLED and pos['state'] == 'CLOSED'

    def test_rejected_exit_keeps_position_open(self, tmp_path):
        alerts = []
        adapter, gw, _ = make_adapter(tmp_path, alerts=alerts)
        r = adapter.submit_entry(**ENTRY)
        gw.book = [book_row(tag=r['client_order_id'][:20])]
        adapter.poll_orders()
        out = adapter.submit_exit('RELIANCE', 'STOP_LOSS')
        gw.book = [book_row(tag=out['client_order_id'][:20],
                            status='rejected', filled=0)]
        adapter.poll_orders()
        pos = adapter._get_position('RELIANCE')
        assert pos['exit_state'] == EXIT_REJECTED and pos['state'] == 'OPEN'
        assert any('exit_rejected' in a[1] for a in alerts)


# ── idempotency / concurrency ─────────────────────────────────────────

class TestIdempotency:
    def test_same_intent_resumes_not_duplicates(self, tmp_path):
        adapter, gw, _ = make_adapter(tmp_path)
        r1 = adapter.submit_entry(**ENTRY)
        r2 = adapter.submit_entry(**ENTRY)
        assert r2.get('resumed') and len(gw.place_calls) == 1

    def test_client_order_id_deterministic(self):
        a = LiveExecutionAdapter.client_order_id('key1')
        b = LiveExecutionAdapter.client_order_id('key1')
        c = LiveExecutionAdapter.client_order_id('key2')
        assert a == b and a != c

    def test_concurrent_distinct_signals(self, tmp_path):
        adapter, gw, _ = make_adapter(tmp_path)
        results = []
        def submit(i):
            results.append(adapter.submit_entry(
                **dict(ENTRY, symbol=f'SYM{i}', intent_key=f'k{i}')))
        threads = [threading.Thread(target=submit, args=(i,)) for i in range(4)]
        for t in threads: t.start()
        for t in threads: t.join()
        assert sum(1 for r in results if r['success']) >= 1
        assert len(gw.place_calls) <= 2  # concurrent cap = 2


# ── unknown / timeout outcomes ────────────────────────────────────────

class TestUnknownOutcomes:
    def test_submit_exception_is_unknown_not_retried(self, tmp_path):
        alerts = []
        adapter, gw, _ = make_adapter(tmp_path, alerts=alerts)
        gw.place_result = {'success': False, 'error': 'timeout', 'unknown': True}
        r = adapter.submit_entry(**ENTRY)
        assert r['state'] == 'UNKNOWN'
        # UNKNOWN blocks new entries until reconciled
        assert adapter.blocked_reason() is not None
        r2 = adapter.submit_entry(**dict(ENTRY, symbol='TCS', intent_key='k2'))
        assert r2['success'] is False
        assert any('order_unknown' in a[1] for a in alerts)

    def test_unknown_resolves_via_ordertag_reconcile(self, tmp_path):
        adapter, gw, _ = make_adapter(tmp_path)
        gw.place_result = {'success': False, 'error': 'timeout', 'unknown': True}
        r = adapter.submit_entry(**ENTRY)
        cid = r['client_order_id']
        # Broker DID receive it — found later by ordertag
        gw.book = [book_row(tag=cid[:20], filled=10, avg=100.5)]
        adapter.poll_orders()
        order = adapter._find_order(cid)
        assert order['state'] == 'FILLED'
        assert len(adapter.open_positions()) == 1
        assert adapter.blocked_reason() is None

    def test_acked_order_missing_from_book_times_out_unknown(self, tmp_path):
        alerts = []
        adapter, gw, _ = make_adapter(tmp_path, alerts=alerts)
        r = adapter.submit_entry(**ENTRY)
        order = adapter._find_order(r['client_order_id'])
        order['submitted_at'] = (datetime.now()
                                 - timedelta(seconds=60)).isoformat()
        gw.book = []
        adapter.poll_orders()
        assert order['state'] == 'UNKNOWN'
        assert any('order_unknown' in a[1] for a in alerts)


# ── risk controls ─────────────────────────────────────────────────────

class TestLiveRiskControls:
    def preflight(self, risk, **over):
        args = dict(symbol='X', direction='LONG', quantity=10,
                    expected_price=100.0, quote_ltp=100.0, stop_loss=98.0,
                    open_positions=[], available_funds=1e9)
        args.update(over)
        return risk.preflight_entry(**args)

    def test_disabled_config_denies(self, tmp_path):
        risk = LiveRiskManager(make_cfg(enabled=False), str(tmp_path))
        ok, reason = self.preflight(risk)
        assert not ok and 'disabled' in reason

    def test_zero_limits_deny(self, tmp_path):
        risk = LiveRiskManager(LiveRiskConfig(enabled=True), str(tmp_path))
        ok, reason = self.preflight(risk)
        assert not ok  # every unset limit fails closed

    def test_order_value_cap(self, tmp_path):
        risk = LiveRiskManager(make_cfg(max_order_value=500), str(tmp_path))
        ok, reason = self.preflight(risk)
        assert not ok and 'Order value' in reason

    def test_per_trade_loss_cap(self, tmp_path):
        risk = LiveRiskManager(make_cfg(max_per_trade_loss=10), str(tmp_path))
        ok, reason = self.preflight(risk)
        assert not ok and 'Per-trade loss' in reason

    def test_no_leverage_capital_cap(self, tmp_path):
        risk = LiveRiskManager(make_cfg(capital=500), str(tmp_path))
        ok, reason = self.preflight(risk)
        assert not ok and 'leverage' in reason.lower()

    def test_insufficient_broker_funds(self, tmp_path):
        risk = LiveRiskManager(make_cfg(), str(tmp_path))
        ok, reason = self.preflight(risk, available_funds=100.0)
        assert not ok and 'Insufficient' in reason

    def test_daily_entry_limit(self, tmp_path):
        risk = LiveRiskManager(make_cfg(max_entries_per_day=1), str(tmp_path))
        assert self.preflight(risk)[0]
        risk.record_entry()
        ok, reason = self.preflight(risk)
        assert not ok and 'entry limit' in reason

    def test_daily_loss_limit(self, tmp_path):
        risk = LiveRiskManager(make_cfg(max_daily_loss=100), str(tmp_path))
        risk.record_realized(-150.0)
        ok, reason = self.preflight(risk)
        assert not ok and 'loss limit' in reason

    def test_concurrent_cap_and_duplicate_symbol(self, tmp_path):
        risk = LiveRiskManager(make_cfg(max_concurrent_positions=1), str(tmp_path))
        pos = [{'symbol': 'Y', 'quantity': 1, 'entry_price': 50.0}]
        ok, _ = self.preflight(risk, open_positions=pos)
        assert not ok
        risk2 = LiveRiskManager(make_cfg(), str(tmp_path))
        ok, reason = self.preflight(
            risk2, symbol='Y', open_positions=pos)
        assert not ok and 'already open' in reason

    def test_gross_exposure_cap(self, tmp_path):
        risk = LiveRiskManager(make_cfg(max_gross_exposure=1500), str(tmp_path))
        pos = [{'symbol': 'Y', 'quantity': 10, 'entry_price': 100.0}]
        ok, reason = self.preflight(risk, open_positions=pos)
        assert not ok and 'exposure' in reason

    def test_slippage_guard(self, tmp_path):
        risk = LiveRiskManager(make_cfg(max_slippage_pct=0.5), str(tmp_path))
        ok, reason = self.preflight(risk, quote_ltp=95.0)  # 5% off expected
        assert not ok and 'Slippage' in reason

    def test_missing_quote_denies(self, tmp_path):
        risk = LiveRiskManager(make_cfg(), str(tmp_path))
        ok, reason = self.preflight(risk, quote_ltp=None)
        assert not ok and 'quote' in reason.lower()

    def test_entry_cutoff(self, tmp_path):
        import pytz
        IST = pytz.timezone("Asia/Kolkata")
        risk = LiveRiskManager(make_cfg(entry_cutoff='14:30'), str(tmp_path))
        late = IST.localize(datetime.now().replace(hour=14, minute=45))
        ok, reason = self.preflight(risk, now=late)
        assert not ok and 'cutoff' in reason

    def test_kill_switch(self, tmp_path):
        risk = LiveRiskManager(make_cfg(), str(tmp_path))
        risk.set_kill_switch(True)
        ok, reason = self.preflight(risk)
        assert not ok and 'kill switch' in reason

    def test_circuit_breaker(self, tmp_path):
        risk = LiveRiskManager(make_cfg(max_broker_errors=2), str(tmp_path))
        risk.record_broker_error('x')
        assert self.preflight(risk)[0]
        risk.record_broker_error('y')
        ok, reason = self.preflight(risk)
        assert not ok and 'breaker' in reason
        risk.record_broker_success()
        assert self.preflight(risk)[0]

    def test_daily_counters_persist(self, tmp_path):
        risk = LiveRiskManager(make_cfg(max_entries_per_day=1), str(tmp_path))
        risk.record_entry()
        risk2 = LiveRiskManager(make_cfg(max_entries_per_day=1), str(tmp_path))
        assert risk2.get_status()['entries_today'] == 1
        ok, _ = self.preflight(risk2)
        assert not ok


# ── restart / reconciliation ──────────────────────────────────────────

class TestReconciliation:
    def test_restart_during_entry_recovers_via_book(self, tmp_path):
        adapter, gw, _ = make_adapter(tmp_path)
        r = adapter.submit_entry(**ENTRY)
        cid = r['client_order_id']
        # Simulate crash: new adapter over the same state dir
        gw.book = [book_row(tag=cid[:20], filled=10, avg=100.0)]
        adapter2, _, _ = make_adapter(tmp_path, gateway=gw)
        assert adapter2._find_order(cid)['state'] in ('OPEN', 'ACKED')
        adapter2.poll_orders()
        assert adapter2._find_order(cid)['state'] == 'FILLED'
        assert len(adapter2.open_positions()) == 1

    def test_restart_during_pending_exit(self, tmp_path):
        adapter, gw, _ = make_adapter(tmp_path)
        r = adapter.submit_entry(**ENTRY)
        gw.book = [book_row(tag=r['client_order_id'][:20])]
        adapter.poll_orders()
        out = adapter.submit_exit('RELIANCE', 'EOD_SQUARE_OFF')
        # Crash before the exit order completed
        gw.book = [book_row(tag=out['client_order_id'][:20], avg=103.0)]
        adapter2, _, _ = make_adapter(tmp_path, gateway=gw)
        adapter2.poll_orders()
        pos = adapter2._positions[0]
        assert pos['exit_state'] == EXIT_FILLED and pos['state'] == 'CLOSED'

    def test_broker_position_mismatch_blocks_entries(self, tmp_path):
        alerts = []
        adapter, gw, _ = make_adapter(tmp_path, alerts=alerts)
        gw.positions_rows = [{'tradingsymbol': 'SBIN-EQ', 'netqty': '15'}]
        report = adapter.reconcile()
        assert report['reconciled'] is False
        assert adapter.blocked_reason() is not None
        r = adapter.submit_entry(**ENTRY)
        assert r['success'] is False
        assert any('position_mismatch' in a[1] for a in alerts)

    def test_matched_positions_reconcile_clean(self, tmp_path):
        adapter, gw, _ = make_adapter(tmp_path)
        r = adapter.submit_entry(**ENTRY)
        gw.book = [book_row(tag=r['client_order_id'][:20])]
        adapter.poll_orders()
        gw.positions_rows = [{'tradingsymbol': 'RELIANCE-EQ', 'netqty': '10'}]
        report = adapter.reconcile()
        assert report['reconciled'] is True

    def test_corrupt_orders_file_loads_empty(self, tmp_path):
        live_dir = tmp_path / 'live'
        live_dir.mkdir(parents=True)
        (live_dir / 'orders.json').write_text('{corrupt')
        adapter, _, _ = make_adapter(tmp_path)
        assert adapter._orders == [] and adapter.get_status()['armed'] is True

    def test_network_outage_poll_fails_safe(self, tmp_path):
        adapter, gw, risk = make_adapter(tmp_path)
        r = adapter.submit_entry(**ENTRY)
        gw.book_fail = True
        assert adapter.poll_orders() == 0
        # order stays non-terminal; never marked closed
        assert adapter._find_order(r['client_order_id'])['state'] in ('ACKED', 'OPEN')


# ── status classification ─────────────────────────────────────────────

class TestBrokerStatusClassify:
    @pytest.mark.parametrize('status,filled,qty,expected', [
        ('complete', 10, 10, 'FILLED'),
        ('traded', 10, 10, 'FILLED'),
        ('open', 4, 10, 'PARTIAL'),
        ('pending', 0, 10, 'OPEN'),
        ('trigger pending', 0, 10, 'OPEN'),
        ('rejected', 0, 10, 'REJECTED'),
        ('cancelled', 0, 10, 'CANCELLED'),
        ('', 0, 10, 'UNKNOWN'),
        ('weird-status', 0, 10, 'UNKNOWN'),
    ])
    def test_classification(self, status, filled, qty, expected):
        assert classify_broker_status(status, filled, qty) == expected


# ── engine: status axes + process singleton ───────────────────────────

def _make_engine(tmp_path, **over):
    from intraday.engine import IntradayEngine
    over.setdefault('paper_trading', True)
    cfg = IntradayConfig(**over)
    with patch('intraday.engine.get_live_config',
               return_value=make_cfg(enabled=False)):
        eng = IntradayEngine(config=cfg, state_dir=str(tmp_path))
    return eng


class TestEngineStatusAxes:
    def test_execution_mode_paper(self, tmp_path):
        eng = _make_engine(tmp_path)
        assert eng._execution_mode() == 'PAPER'

    def test_execution_mode_disarmed(self, tmp_path):
        eng = _make_engine(tmp_path, paper_trading=False)
        assert eng._execution_mode() == 'LIVE DISARMED'

    def test_execution_mode_armed(self, tmp_path):
        eng = _make_engine(tmp_path, angel_live_trading=True)
        with patch.dict(os.environ, {'ANGEL_LIVE_ARMED': 'CONFIRM'}):
            assert eng._execution_mode() == 'LIVE ARMED'

    def test_engine_state_display(self, tmp_path):
        eng = _make_engine(tmp_path)
        with patch.object(eng.validator, 'is_market_open', return_value=True):
            eng._cycle_running = True
            assert eng._engine_state_display() == 'SCANNING'
            eng._cycle_running = False
            assert eng._engine_state_display() == 'MONITORING'
        eng.risk_manager.set_kill_switch(True)
        assert eng._engine_state_display() == 'STOPPED'

    def test_entry_permission_values(self, tmp_path):
        eng = _make_engine(tmp_path)
        with patch.object(eng.validator, 'is_market_open', return_value=True), \
             patch.object(eng.validator, 'is_entry_window_open', return_value=True), \
             patch.object(eng, 'credentials_configured', return_value=True):
            eng._auth_state = 'CONNECTED'
            with patch.object(eng, 'market_data_status', return_value='LIVE'):
                assert eng._entry_permission() == 'ALLOWED'
            eng.risk_manager._daily_state.trades_today = 5
            assert eng._entry_permission() == 'DAILY LIMIT REACHED'
            eng.risk_manager._daily_state.trades_today = 0
            eng.risk_manager._daily_state.blocked = True
            eng.risk_manager._daily_state.block_reason = 'Daily loss limit reached'
            assert eng._entry_permission() == 'LOSS LIMIT REACHED'
            eng.risk_manager.set_kill_switch(True)
            assert eng._entry_permission() == 'KILL SWITCH'

    def test_entry_permission_market_closed(self, tmp_path):
        eng = _make_engine(tmp_path)
        with patch.object(eng.validator, 'is_market_open', return_value=False):
            assert eng._entry_permission() == 'MARKET CLOSED'

    def test_signals_only_flag(self, tmp_path):
        eng = _make_engine(tmp_path)
        sig = MagicMock()
        eng._opportunities = [sig]
        with patch.object(eng.validator, 'is_market_open', return_value=False):
            st = eng.get_status()
            assert st['signals_only'] is True
            assert st['entry_permission'] == 'MARKET CLOSED'

    def test_status_exposes_live_block(self, tmp_path):
        eng = _make_engine(tmp_path)
        st = eng.get_status()
        assert st['live']['armed'] is False
        assert st['execution_mode'] == 'PAPER'
        assert 'entry_permission' in st and 'engine_state_display' in st


class TestProcessSingleton:
    def test_second_engine_same_dir_is_secondary(self, tmp_path):
        eng1 = _make_engine(tmp_path)
        assert eng1._singleton_role == 'primary'
        eng2 = _make_engine(tmp_path)
        assert eng2._singleton_role == 'secondary'
        out = eng2.run_cycle()
        assert out['skipped_reason'] == 'Secondary engine (no trading)'
        eng2.start_scheduler()
        assert eng2._scheduler_started is False

    def test_separate_dirs_both_primary(self, tmp_path):
        eng1 = _make_engine(tmp_path / 'a')
        eng2 = _make_engine(tmp_path / 'b')
        assert eng1._singleton_role == 'primary'
        assert eng2._singleton_role == 'primary'
