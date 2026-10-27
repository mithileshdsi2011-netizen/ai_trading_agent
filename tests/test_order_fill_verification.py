"""
Fill-verification tests for the Swing OrderExecutor.

A broker order acknowledgement is not a fill. These tests pin the semantics:
  BUY  — REJECTED/CANCELLED after ack opens no position; COMPLETE uses the
         broker's real average price and filled quantity; a still-working order
         keeps the local position but is flagged unverified.
  SELL — REJECTED/CANCELLED after ack keeps the position open and registers a
         retriable failure; a still-working order becomes a tracked pending exit
         (no duplicate sells); fills finalize at the real price/qty.
"""
import pytest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock

from order_executor import OrderExecutor
from risk_manager import Position, PositionStatus


def _executor():
    """OrderExecutor with all heavyweight dependencies mocked."""
    ex = OrderExecutor.__new__(OrderExecutor)
    ex.alert_engine = None
    ex.broker = Mock()
    ex.broker.paper_trading = False
    ex.risk_manager = Mock()
    ex.risk_manager.positions = []
    ex.market_data = Mock()
    ex.market_data.kite = None
    ex.market_data.get_realtime_price.return_value = 100.0
    ex.telegram = Mock()
    ex.email = Mock()
    ex.journal = Mock()
    ex._store = Mock()
    ex._store.load_daily_state.return_value = {}
    ex.reconciliation_engine = Mock()
    ex.executed_orders = []
    ex._pending_order_symbols = set()
    ex._pending_sells = {}
    ex.exit_decision_engine = Mock()
    return ex


def _signal(**kw):
    sig = {
        'symbol': 'RELIANCE',
        'action': 'BUY',
        'current_price': 100.0,
        'position_size': 10,
        'investment_amount': 1000.0,
        'stop_loss': 95.0,
        'target': 110.0,
        'reasoning': 'test',
        'confidence': 0.8,
    }
    sig.update(kw)
    return sig


def _position(sym='RELIANCE', qty=10, status=PositionStatus.OPEN):
    return Position(
        symbol=sym, entry_price=100.0, quantity=qty,
        stop_loss=95.0, target=110.0, entry_time=datetime.now(),
        status=status, average_price=100.0, initial_quantity=qty,
    )


def _real_order(order_id='ORD1'):
    return {'success': True, 'order_id': order_id, 'status': 'PENDING',
            'paper_trading': False, 'retry': False}


# ── BUY path ──────────────────────────────────────────────────────────

class TestBuyFillVerification:

    def test_rejected_after_ack_opens_no_position(self):
        ex = _executor()
        ex.broker.place_order.return_value = _real_order()
        ex.broker.verify_order_fill.return_value = {'status': 'REJECTED'}

        result = ex.execute_signal(_signal())

        assert result['success'] is False
        assert 'rejected' in result['error'].lower()
        ex.risk_manager.open_position.assert_not_called()
        ex.journal.log_entry.assert_not_called()
        assert 'RELIANCE' not in ex._pending_order_symbols

    def test_cancelled_after_ack_opens_no_position(self):
        ex = _executor()
        ex.broker.place_order.return_value = _real_order()
        ex.broker.verify_order_fill.return_value = {'status': 'CANCELLED'}

        result = ex.execute_signal(_signal())

        assert result['success'] is False
        ex.risk_manager.open_position.assert_not_called()

    def test_complete_uses_real_fill_price_and_qty(self):
        ex = _executor()
        ex.broker.place_order.return_value = _real_order()
        ex.broker.verify_order_fill.return_value = {
            'status': 'COMPLETE', 'average_price': 101.5, 'filled_quantity': 7,
        }
        sig = _signal()

        result = ex.execute_signal(sig)

        assert result['success'] is True
        assert sig['current_price'] == 101.5
        assert sig['position_size'] == 7
        assert sig['investment_amount'] == pytest.approx(101.5 * 7)
        opened = ex.risk_manager.open_position.call_args[0][0]
        assert opened['current_price'] == 101.5
        journal_price = ex.journal.log_entry.call_args.kwargs['price']
        assert journal_price == 101.5

    def test_still_open_keeps_position_flagged_unverified(self):
        ex = _executor()
        ex.broker.place_order.return_value = _real_order()
        ex.broker.verify_order_fill.return_value = {'status': 'OPEN'}
        sig = _signal()

        result = ex.execute_signal(sig)

        assert result['success'] is True
        assert sig.get('_fill_unverified') is True
        ex.risk_manager.open_position.assert_called_once()

    def test_paper_order_skips_verification(self):
        ex = _executor()
        ex.broker.place_order.return_value = {
            'success': True, 'order_id': 'P1', 'paper_trading': True,
        }

        ex.execute_signal(_signal())

        ex.broker.verify_order_fill.assert_not_called()
        ex.risk_manager.open_position.assert_called_once()


# ── SELL path ─────────────────────────────────────────────────────────

class TestSellFillVerification:

    def _sell_ex(self):
        ex = _executor()
        pos = _position()
        ex.risk_manager.positions = [pos]
        ex.risk_manager.can_open_position.return_value = True
        return ex, pos

    def test_rejected_after_ack_keeps_position_open(self):
        ex, pos = self._sell_ex()
        ex.broker.place_order.return_value = _real_order('S1')
        ex.broker.verify_order_fill.return_value = {'status': 'REJECTED'}

        result = ex._execute_sell(_signal(action='SELL', current_price=94.0, reasoning='Stop loss hit'))

        assert result['success'] is False
        ex.risk_manager.close_position.assert_not_called()
        ex.journal.log_entry.assert_not_called()
        assert 'RELIANCE' in ex._pending_sells  # retriable failure recorded
        # A rejected order is dead — not a live broker order.
        assert not ex._pending_sells['RELIANCE'].get('live_order_id')
        assert ex._pending_sells['RELIANCE']['retry_count'] == 1

    def test_complete_uses_real_price_and_partial_qty(self):
        ex, pos = self._sell_ex()
        ex.broker.place_order.return_value = _real_order('S1')
        ex.broker.verify_order_fill.return_value = {
            'status': 'COMPLETE', 'average_price': 98.25, 'filled_quantity': 4,
        }
        ex.risk_manager.close_position.return_value = {'net_pnl': -10, 'charges': 2}

        result = ex._execute_sell(_signal(action='SELL', current_price=94.0, reasoning='Stop loss hit'))

        assert result['success'] is True
        args = ex.risk_manager.close_position.call_args[0]
        assert args[0] == 'RELIANCE'
        assert args[1] == 98.25
        assert args[3] == 4  # only the broker-filled qty is closed

    def test_still_open_registers_pending_exit(self):
        ex, pos = self._sell_ex()
        ex.broker.place_order.return_value = _real_order('S9')
        ex.broker.verify_order_fill.return_value = {'status': 'OPEN'}

        result = ex._execute_sell(_signal(action='SELL', current_price=94.0, reasoning='Stop loss hit'))

        assert result['success'] is True
        assert result['exit_pending'] is True
        ex.risk_manager.close_position.assert_not_called()
        ex.journal.log_entry.assert_not_called()
        pending = ex._pending_sells.get('RELIANCE')
        assert pending and pending['live_order_id'] == 'S9'


# ── Pending-exit resolution ───────────────────────────────────────────

class TestPendingExitResolution:

    def _ex_with_pending(self, status_row):
        ex = _executor()
        pos = _position()
        ex.risk_manager.positions = [pos]
        ex._pending_sells['RELIANCE'] = {
            'symbol': 'RELIANCE', 'live_order_id': 'S9',
            'exit_reason': 'Stop loss hit', 'next_retry': None,
        }
        ex.broker.get_order_status.return_value = status_row
        return ex, pos

    def test_fill_finalizes_at_real_price(self):
        ex, pos = self._ex_with_pending({
            'status': 'COMPLETE', 'average_price': 97.5, 'filled_quantity': 10,
        })
        ex.risk_manager.close_position.return_value = {'net_pnl': -25, 'charges': 3}

        outcome = ex._resolve_pending_exit('RELIANCE', pos, 98.0)

        assert outcome == 'filled'
        args = ex.risk_manager.close_position.call_args[0]
        assert args[1] == 97.5 and args[3] == 10
        assert ex.journal.log_entry.call_args.kwargs['price'] == 97.5
        assert 'RELIANCE' not in ex._pending_sells

    def test_partial_fill_closes_only_filled_qty(self):
        ex, pos = self._ex_with_pending({
            'status': 'COMPLETE', 'average_price': 97.5, 'filled_quantity': 4,
        })
        ex.risk_manager.close_position.return_value = {'net_pnl': -10, 'charges': 1}

        ex._resolve_pending_exit('RELIANCE', pos, 98.0)

        assert ex.risk_manager.close_position.call_args[0][3] == 4

    def test_live_order_blocks_duplicate_sell(self):
        ex, pos = self._ex_with_pending({'status': 'OPEN'})

        outcome = ex._resolve_pending_exit('RELIANCE', pos, 98.0)
        assert outcome == 'live'
        ex.risk_manager.close_position.assert_not_called()

        # A fresh sell signal for the same symbol must not place another order.
        result = ex._execute_sell(_signal(action='SELL', current_price=94.0, reasoning='Stop loss hit'))
        assert result['success'] is False
        ex.broker.place_order.assert_not_called()

    def test_rejected_order_resumes_normal_exits(self):
        ex, pos = self._ex_with_pending({'status': 'REJECTED'})

        outcome = ex._resolve_pending_exit('RELIANCE', pos, 98.0)

        assert outcome == 'proceed'
        assert 'RELIANCE' not in ex._pending_sells

    def test_unknown_order_is_conservative(self):
        ex, pos = self._ex_with_pending({'error': 'Order not found'})

        outcome = ex._resolve_pending_exit('RELIANCE', pos, 98.0)

        assert outcome == 'live'  # never risk a duplicate sell

    def test_monitor_skips_symbol_with_live_exit(self):
        ex, pos = self._ex_with_pending({'status': 'OPEN'})
        ex.risk_manager._load_positions = Mock()
        ex.risk_manager.save_positions = Mock()
        ex.exit_decision_engine.decide_all.return_value = [
            SimpleNamespace(symbol='RELIANCE', decision='FULL_EXIT', quantity=10,
                            reason='Stop loss hit', scores={}),
        ]

        executed = ex.monitor_positions('SIDEWAYS')

        assert executed == []
        ex.broker.place_order.assert_not_called()


# ── Broker-level verify_order_fill ────────────────────────────────────

class TestVerifyOrderFill:

    def test_paper_returns_complete(self):
        from broker_integration import BrokerIntegration
        broker = BrokerIntegration.__new__(BrokerIntegration)
        broker.paper_trading = True

        result = broker.verify_order_fill('ANY')

        assert result['status'] == 'COMPLETE'

    def test_maps_terminal_fields(self):
        from broker_integration import BrokerIntegration
        broker = BrokerIntegration.__new__(BrokerIntegration)
        broker.paper_trading = False
        broker.kite = Mock()
        broker.kite.order_history.return_value = [
            {'status': 'OPEN'}, {'status': 'COMPLETE', 'average_price': 101.5,
             'filled_quantity': 7, 'pending_quantity': 0},
        ]

        result = broker.verify_order_fill('ORD1', timeout_sec=0.1)

        assert result['status'] == 'COMPLETE'
        assert result['average_price'] == 101.5
        assert result['filled_quantity'] == 7

    def test_timeout_returns_last_known_status(self):
        from broker_integration import BrokerIntegration
        broker = BrokerIntegration.__new__(BrokerIntegration)
        broker.paper_trading = False
        broker.kite = Mock()
        broker.kite.order_history.return_value = [{'status': 'OPEN'}]

        result = broker.verify_order_fill('ORD1', timeout_sec=0.0)

        assert result['status'] == 'OPEN'

    def test_poll_failure_returns_unknown(self):
        from broker_integration import BrokerIntegration
        broker = BrokerIntegration.__new__(BrokerIntegration)
        broker.paper_trading = False
        broker.kite = Mock()
        broker.kite.order_history.side_effect = Exception('network down')

        result = broker.verify_order_fill('ORD1', timeout_sec=0.0)

        assert result['status'] == 'UNKNOWN'
