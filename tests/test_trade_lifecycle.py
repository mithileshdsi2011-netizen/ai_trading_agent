"""
Unit tests for src/trade_lifecycle_manager.py
"""
import os
import sys
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from trade_lifecycle_manager import TradeLifecycleManager
from risk_manager import Position, PositionStatus


def make_pos(symbol='RELIANCE', entry=100, qty=100, sl=95, atr=3, days_held=0, partial=0):
    pos = Position(
        symbol=symbol,
        entry_price=entry,
        quantity=qty,
        stop_loss=sl,
        target=110,
        entry_time=datetime.now() - timedelta(days=days_held),
        status=PositionStatus.OPEN,
        highest_price=entry,
        trailing_stop=None,
        atr_at_entry=atr,
        initial_quantity=qty,
        partial_count=partial,
        partial_qty=0,
    )
    return pos


class TestTradeLifecycleManager(unittest.TestCase):

    def setUp(self):
        self.mgr = TradeLifecycleManager(market_data=None)

    def test_trailing_stop_ATR(self):
        pos = make_pos(entry=100, qty=100, sl=95, atr=3)
        # Highest reaches 118
        current = 118
        pos.highest_price = 118
        self.mgr._update_trailing_stop(pos, current, {'atr': 3, 'ema20': 100, 'swing_low': 90})
        # 118 - 2*3 = 112, 5% trail = 112.1, ema = 100, swing = 89.1
        # max of those ~112.1
        self.assertIsNotNone(pos.trailing_stop)
        self.assertAlmostEqual(pos.trailing_stop, 112.1, places=0)

    def test_break_even_triggered(self):
        pos = make_pos(entry=100, qty=100, sl=95, atr=3)
        self.assertLess(pos.stop_loss, pos.entry_price)
        # RR>1 when current >= 2*100 - 95 = 105
        self.mgr._break_even_check(pos, 106)
        self.assertEqual(pos.stop_loss, pos.entry_price)

    def test_scale_out_first_partial(self):
        pos = make_pos(entry=100, qty=100, sl=95, atr=3)
        # Partial 1 at 100 + 1.5*3 = 104.5
        action = self.mgr._scale_out(pos, 105)
        self.assertIsNotNone(action)
        self.assertEqual(action['action'], 'SELL')
        self.assertEqual(action['quantity'], 30)  # 30% of 100

    def test_stop_loss_exit(self):
        pos = make_pos(entry=100, qty=100, sl=95, atr=3)
        pos.trailing_stop = 97
        action = self.mgr._stop_loss_exit(pos, 96)
        self.assertIsNotNone(action)
        self.assertEqual(action['action'], 'SELL')
        self.assertIn('trailing', action['reason'].lower())

    def test_gap_exit(self):
        pos = make_pos(entry=100, qty=100, sl=95, atr=3)
        action = self.mgr._gap_exit(pos, 90, {'prev_close': 100})
        self.assertIsNotNone(action)
        self.assertIn('Gap', action['reason'])

    def test_time_exit(self):
        pos = make_pos(entry=100, qty=100, sl=95, atr=3, days_held=15)
        pos.planned_exit_date = datetime.now() - timedelta(days=1)
        action = self.mgr._time_exit(pos, 96)
        self.assertIsNotNone(action)
        self.assertIn('Time', action['reason'])

    def test_process_positions_no_crash(self):
        pos = make_pos(entry=100, qty=100, sl=95, atr=3)
        actions = self.mgr.process_positions([pos], {'RELIANCE': 100})
        self.assertEqual(len(actions), 1)


if __name__ == '__main__':
    unittest.main()
