"""
Unit tests for src/reconciliation_engine.py
Run from project root: PYTHONPATH=src venv/bin/python -m unittest tests.test_reconciliation_engine
"""
import os
import sys
import json
import tempfile
import unittest
from datetime import datetime
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from persistence import TradingStore
from reconciliation_engine import ReconciliationEngine


class FakeBroker:
    """Minimal broker double for testing the reconciliation engine."""

    def __init__(self, positions=None, orders=None, cash=10000):
        self.paper_trading = True
        self.live_ready = False
        self.paper_portfolio = {
            'cash': cash,
            'positions': {p['symbol']: p for p in (positions or [])},
            'orders': list(orders or [])
        }

    def _get_paper_positions(self):
        return [
            {
                'symbol': k,
                'quantity': v['quantity'],
                'entry_price': v.get('average_price', v.get('entry_price', 0)),
                'average_price': v.get('average_price', v.get('entry_price', 0)),
                'last_price': v.get('last_price', v.get('entry_price', 0)),
                'product': v.get('product', 'MIS'),
                'exchange': v.get('exchange', 'NSE'),
                'stop_loss': v.get('stop_loss', 0),
                'target': v.get('target', 0)
            }
            for k, v in self.paper_portfolio['positions'].items()
        ]

    def get_holdings(self):
        positions = self._get_paper_positions()
        total_value = self.paper_portfolio['cash'] + sum(
            p['quantity'] * p.get('last_price', p.get('entry_price', 0)) for p in positions
        )
        return {
            'cash': self.paper_portfolio['cash'],
            'positions': positions,
            'total_value': total_value
        }


class TestReconciliationEngine(unittest.TestCase):
    """Test suite for the enterprise reconciliation engine."""

    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix='.db')
        os.close(fd)
        self.store = TradingStore(self.db_path)

    def tearDown(self):
        try:
            os.remove(self.db_path)
        except Exception:
            pass

    def _make_engine(self, broker=None):
        return ReconciliationEngine(broker=broker, store=self.store)

    # ── 1. holdings mismatch ──────────────────────────────────────────────
    def test_holdings_missing_in_sqlite(self):
        """Kite has a holding that SQLite does not — must create it."""
        broker = FakeBroker(positions=[{
            'symbol': 'RELIANCE',
            'quantity': 10,
            'average_price': 100.0,
            'last_price': 105.0,
            'product': 'CNC',
            'exchange': 'NSE'
        }], cash=20000)
        engine = self._make_engine(broker)
        status = engine.reconcile_all()

        positions = self.store.load_positions()
        self.assertEqual(len(positions), 1)
        self.assertEqual(positions[0]['symbol'], 'RELIANCE')
        self.assertEqual(positions[0]['quantity'], 10)
        self.assertTrue(status['healthy'])

    # ── 2. position mismatch ─────────────────────────────────────────────
    def test_position_quantity_mismatch(self):
        """Kite quantity differs from SQLite — must repair."""
        self.store.save_position({
            'symbol': 'INFY',
            'status': 'OPEN',
            'quantity': 5,
            'average_price': 200.0,
            'last_price': 205.0,
            'product': 'MIS',
            'exchange': 'NSE'
        })
        broker = FakeBroker(positions=[{
            'symbol': 'INFY',
            'quantity': 15,
            'average_price': 210.0,
            'last_price': 215.0,
            'product': 'MIS',
            'exchange': 'NSE'
        }], cash=20000)
        engine = self._make_engine(broker)
        status = engine.reconcile_all()

        positions = self.store.load_positions()
        self.assertEqual(positions[0]['quantity'], 15)
        self.assertTrue(status['healthy'])

    # ── 3. order mismatch ───────────────────────────────────────────────
    def test_order_missing(self):
        """Kite has a completed order SQLite does not — must create order record."""
        broker = FakeBroker(
            orders=[{
                'order_id': 'ORDER1',
                'symbol': 'TCS',
                'transaction_type': 'BUY',
                'quantity': 5,
                'average_price': 3500.0,
                'price': 3500.0,
                'status': 'COMPLETE',
                'order_timestamp': datetime.now().isoformat(),
                'product': 'CNC',
                'exchange': 'NSE'
            }],
            cash=20000
        )
        engine = self._make_engine(broker)
        status = engine.reconcile_all()

        orders = self.store.get_orders()
        self.assertEqual(len(orders), 1)
        self.assertEqual(orders[0]['order_id'], 'ORDER1')

    # ── 4. missing trade ────────────────────────────────────────────────
    def test_missing_trade_added_to_journal(self):
        """A completed Kite order must be reflected in the trade journal."""
        broker = FakeBroker(
            orders=[{
                'order_id': 'ORDER2',
                'symbol': 'HDFCBANK',
                'transaction_type': 'BUY',
                'quantity': 8,
                'average_price': 1500.0,
                'price': 1500.0,
                'status': 'COMPLETE',
                'order_timestamp': datetime.now().isoformat(),
                'product': 'CNC',
                'exchange': 'NSE'
            }],
            cash=20000
        )
        engine = self._make_engine(broker)
        status = engine.reconcile_all()

        trades = self.store.all_trades()
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0]['symbol'], 'HDFCBANK')

    # ── 5. duplicate trade detection ─────────────────────────────────────
    def test_duplicate_trade_detected(self):
        """Duplicate journal entries by (order_id, symbol, timestamp) must be flagged."""
        ts = datetime.now().isoformat()
        self.store.add_trade({
            'order_id': 'ORDER3',
            'symbol': 'ICICIBANK',
            'action': 'BUY',
            'quantity': 10,
            'entry_price': 950.0,
            'timestamp': ts,
            'status': 'OPEN'
        })
        # Intentional duplicate
        self.store.add_trade({
            'order_id': 'ORDER3',
            'symbol': 'ICICIBANK',
            'action': 'BUY',
            'quantity': 10,
            'entry_price': 950.0,
            'timestamp': ts,
            'status': 'OPEN'
        })

        engine = self._make_engine(None)  # no broker, only journal checks
        status = engine.reconcile_all()
        details = [m['type_'] for m in status['details']]
        self.assertIn('duplicate_trades', details)

    # ── 6. pending sell mismatch ─────────────────────────────────────────
    def test_pending_sell_missing_recreated(self):
        """An open SELL order without a pending-sell entry must be recreated."""
        broker = FakeBroker(
            orders=[{
                'order_id': 'SELL1',
                'symbol': 'SBIN',
                'transaction_type': 'SELL',
                'quantity': 5,
                'average_price': 650.0,
                'price': 650.0,
                'status': 'OPEN',
                'order_timestamp': datetime.now().isoformat(),
                'product': 'CNC',
                'exchange': 'NSE'
            }],
            cash=20000
        )
        engine = self._make_engine(broker)
        status = engine.reconcile_all()

        state = self.store.load_daily_state()
        pending = state.get('pending_sells', {})
        self.assertIn('SELL1', pending)
        self.assertEqual(pending['SELL1']['symbol'], 'SBIN')

    # ── 7. portfolio mismatch ───────────────────────────────────────────
    def test_portfolio_snapshot_updated(self):
        """Broker cash/holdings must update the portfolio snapshot table."""
        broker = FakeBroker(
            positions=[{
                'symbol': 'KOTAKBANK',
                'quantity': 20,
                'average_price': 1800.0,
                'last_price': 1850.0,
                'product': 'CNC',
                'exchange': 'NSE'
            }],
            cash=25000
        )
        engine = self._make_engine(broker)
        engine.reconcile_all()

        snap = self.store.get_latest_portfolio_snapshot()
        self.assertIsNotNone(snap)
        self.assertEqual(snap['cash'], 25000)
        self.assertGreater(snap['total_value'], 25000)

    # ── 8. broker status mismatch ───────────────────────────────────────
    def test_broker_state_saved(self):
        """Broker mode / live ready / API status must be persisted."""
        broker = FakeBroker(cash=20000)
        engine = self._make_engine(broker)
        engine.reconcile_all()

        broker_state = self.store.get_broker_state('status')
        self.assertIsNotNone(broker_state)
        self.assertEqual(broker_state.get('mode'), 'PAPER')
        self.assertEqual(broker_state.get('live_ready'), True)

    # ── 9. dashboard refresh data ───────────────────────────────────────
    def test_dashboard_status_api_shape(self):
        """Reconciliation status must have the exact shape the dashboard API expects."""
        broker = FakeBroker(cash=20000)
        engine = self._make_engine(broker)
        status = engine.reconcile_all()

        self.assertIn('healthy', status)
        self.assertIn('last_sync', status)
        self.assertIn('mismatches', status)
        self.assertIn('repairs', status)
        self.assertIn('duration_ms', status)

    # ── 10. startup reconciliation ──────────────────────────────────────
    def test_startup_full_reconcile(self):
        """A full reconcile on engine creation covers all object types."""
        broker = FakeBroker(
            positions=[{
                'symbol': 'ITC',
                'quantity': 100,
                'average_price': 450.0,
                'last_price': 455.0,
                'product': 'CNC',
                'exchange': 'NSE'
            }],
            orders=[{
                'order_id': 'BUY100',
                'symbol': 'ITC',
                'transaction_type': 'BUY',
                'quantity': 100,
                'average_price': 450.0,
                'price': 450.0,
                'status': 'COMPLETE',
                'order_timestamp': datetime.now().isoformat(),
                'product': 'CNC',
                'exchange': 'NSE'
            }],
            cash=15000
        )
        engine = self._make_engine(broker)
        status = engine.reconcile_all()

        # At least one position, one order, one trade, one portfolio snapshot, broker state
        self.assertGreater(self.store.load_positions().__len__(), 0)
        self.assertGreater(self.store.get_orders().__len__(), 0)
        self.assertGreater(self.store.all_trades().__len__(), 0)
        self.assertIsNotNone(self.store.get_latest_portfolio_snapshot())
        self.assertIsNotNone(self.store.get_broker_state('status'))
        self.assertIsNotNone(self.store.get_broker_state('reconciliation'))
        self.assertTrue(status['healthy'])


class TestBrokerFetchSafety(unittest.TestCase):
    """Auth-failure handling in broker fetches.

    A failed Kite call must (a) self-heal by reloading the token file,
    and (b) mark reconciliation unhealthy so the orchestrator's order
    gate blocks new LIVE BUYs — an empty fetch must never pass as
    empty broker state."""

    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix='.db')
        os.close(fd)
        self.store = TradingStore(self.db_path)
        self._token_dir = tempfile.TemporaryDirectory()
        self._token_path = os.path.join(self._token_dir.name, 'kite_token.json')
        self._patcher = unittest.mock.patch(
            'reconciliation_engine.data_path',
            lambda name: os.path.join(self._token_dir.name, name))
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()
        self._token_dir.cleanup()
        try:
            os.remove(self.db_path)
        except Exception:
            pass

    def _write_token(self, token='FRESH_TOKEN'):
        with open(self._token_path, 'w') as f:
            json.dump({'access_token': token}, f)

    def _live_broker(self):
        b = FakeBroker()
        b.paper_trading = False
        b.kite = MagicMock()
        return b

    def test_auth_error_reloads_token_and_retries(self):
        """First call rejected → token file reloaded → retry succeeds."""
        from reconciliation_engine import _kite_holdings
        broker = self._live_broker()
        broker.kite.holdings.side_effect = [
            Exception("Incorrect `api_key` or `access_token`."),
            [{'symbol': 'X', 'quantity': 1, 't1_quantity': 0}],
        ]
        self._write_token('ROTATED_TOKEN')
        errors = []
        res = _kite_holdings(broker, errors)
        self.assertEqual(len(res), 1)
        broker.kite.set_access_token.assert_called_once_with('ROTATED_TOKEN')
        self.assertEqual(errors, [])

    def test_persistent_auth_failure_records_error(self):
        """Reload doesn't help (stale file) → error recorded, empty list."""
        from reconciliation_engine import _kite_holdings
        broker = self._live_broker()
        broker.kite.holdings.side_effect = Exception(
            "Incorrect `api_key` or `access_token`.")
        self._write_token('STILL_BAD')
        errors = []
        self.assertEqual(_kite_holdings(broker, errors), [])
        self.assertEqual(len(errors), 1)

    def test_non_auth_error_skips_reload(self):
        """Network blips don't trigger token reload — different failure mode."""
        from reconciliation_engine import _kite_orders
        broker = self._live_broker()
        broker.kite.orders.side_effect = ConnectionError("remote closed")
        self._write_token()
        errors = []
        self.assertEqual(_kite_orders(broker, errors), [])
        broker.kite.set_access_token.assert_not_called()
        self.assertEqual(len(errors), 1)

    def test_fetch_failures_make_reconciliation_unhealthy(self):
        """Failed broker fetches → healthy=False → orchestrator blocks BUYs."""
        broker = self._live_broker()
        auth_err = Exception("Incorrect `api_key` or `access_token`.")
        broker.kite.holdings.side_effect = auth_err
        broker.kite.positions.side_effect = auth_err
        broker.kite.orders.side_effect = auth_err
        self._write_token('STILL_BAD')
        engine = ReconciliationEngine(broker=broker, store=self.store)
        status = engine.reconcile_all()
        self.assertFalse(status['healthy'])
        self.assertTrue(status.get('broker_fetch_errors'))

    def test_successful_fetch_after_reload_restores_health(self):
        """Token reload mid-reconcile recovers and stays healthy."""
        broker = self._live_broker()
        broker.kite.holdings.side_effect = [
            Exception("Incorrect `api_key` or `access_token`."),
            [],  # succeeds after reload
        ]
        broker.kite.positions.return_value = {'day': [], 'net': []}
        broker.kite.orders.return_value = []
        self._write_token('ROTATED_TOKEN')
        engine = ReconciliationEngine(broker=broker, store=self.store)
        status = engine.reconcile_all()
        self.assertTrue(status['healthy'])
        broker.kite.set_access_token.assert_called_with('ROTATED_TOKEN')


if __name__ == '__main__':
    unittest.main()
