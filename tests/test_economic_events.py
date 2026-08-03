"""
Unit tests for src/economic_events.py
"""
import os
import sys
import unittest
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from economic_events import EconomicEventRiskEngine


class TestEconomicEventRiskEngine(unittest.TestCase):

    def test_no_event_normal(self):
        engine = EconomicEventRiskEngine(store=None)
        status = engine.risk_status()
        self.assertFalse(status['no_new_buy'])
        self.assertFalse(status['reduce_size'])
        self.assertEqual(status['size_factor'], 1.0)

    def test_within_24h_reduces_size(self):
        engine = EconomicEventRiskEngine(store=None)
        now = datetime(2026, 8, 3, 12, 0, 0)
        future = (now + timedelta(hours=12)).isoformat()
        engine.add_event('RBI Policy', future, impact='HIGH')
        status = engine.risk_status(now)
        self.assertFalse(status['no_new_buy'])
        self.assertTrue(status['reduce_size'])
        self.assertEqual(status['size_factor'], 0.5)

    def test_within_6h_blocks_buys(self):
        engine = EconomicEventRiskEngine(store=None)
        now = datetime(2026, 8, 3, 12, 0, 0)
        future = (now + timedelta(hours=4)).isoformat()
        engine.add_event('Fed Meeting', future, impact='HIGH')
        status = engine.risk_status(now)
        self.assertTrue(status['no_new_buy'])
        self.assertTrue(status['reduce_size'])

    def test_event_type_supported(self):
        engine = EconomicEventRiskEngine(store=None)
        now = datetime(2026, 8, 3, 12, 0, 0)
        for et in ['RBI Policy', 'Fed Meeting', 'Budget', 'Election', 'GDP', 'CPI']:
            engine.add_event(et, (now + timedelta(hours=3)).isoformat())
        # multiple events should find closest
        status = engine.risk_status(now)
        self.assertLessEqual(status['hours_to_event'], 3.0)

    def test_past_event_ignored(self):
        engine = EconomicEventRiskEngine(store=None)
        now = datetime(2026, 8, 3, 12, 0, 0)
        past = (now - timedelta(hours=2)).isoformat()
        engine.add_event('Budget', past, impact='HIGH')
        status = engine.risk_status(now)
        self.assertFalse(status['no_new_buy'])
        self.assertFalse(status['reduce_size'])


if __name__ == '__main__':
    unittest.main()
