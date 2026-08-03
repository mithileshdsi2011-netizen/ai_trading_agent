"""
Unit tests for src/system_monitor.py and src/alert_engine.py
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from system_monitor import EnterpriseSystemMonitor
from alert_engine import EnterpriseAlertEngine


class FakeStore:
    def __init__(self):
        self.health = []
        self.alerts = []
        self.heartbeats = []
        self.notifications = []
        self.reports = []
        self.broker_state = {'reconciliation': {'healthy': True, 'mismatches': 0}}

    def save_system_health(self, s):
        self.health.append(s)

    def get_latest_system_health(self):
        return self.health[-1] if self.health else None

    def save_system_alert(self, s):
        s['id'] = len(self.alerts) + 1
        self.alerts.append(s)
        return s['id']

    def get_system_alerts(self, limit=50, level=None, unacknowledged=False):
        out = self.alerts[-limit:]
        if level:
            out = [a for a in out if a['level'] == level]
        if unacknowledged:
            out = [a for a in out if not a.get('acknowledged', 0)]
        return out[::-1]

    def acknowledge_alert(self, alert_id):
        for a in self.alerts:
            if a.get('id') == alert_id:
                a['acknowledged'] = 1

    def get_alert_counts(self, date=None):
        counts = {}
        for a in self.alerts:
            counts[a['level']] = counts.get(a['level'], 0) + 1
        return counts

    def save_heartbeat_log(self, s):
        self.heartbeats.append(s)

    def get_latest_heartbeat_logs(self, limit=20):
        return self.heartbeats[-limit:][::-1]

    def save_notification_history(self, s):
        self.notifications.append(s)

    def get_notification_history(self, limit=50):
        return self.notifications[-limit:][::-1]

    def save_daily_health_report(self, s):
        self.reports.append(s)

    def get_latest_daily_health_report(self):
        return self.reports[-1] if self.reports else None

    def get_broker_state(self, key):
        return self.broker_state.get(key)

    def count_ai_training_data(self):
        return 0


class TestEnterpriseSystemMonitor(unittest.TestCase):

    def test_collect_returns_metrics(self):
        mon = EnterpriseSystemMonitor(store=FakeStore())
        m = mon.collect()
        self.assertIn('health_score', m)
        self.assertIn('system', m)
        self.assertIn('sqlite', m)
        self.assertIn('internet', m)
        self.assertIn('kite', m)

    def test_health_score_excellent(self):
        mon = EnterpriseSystemMonitor(store=FakeStore())
        m = {
            'system': {'cpu_percent': 30, 'memory_percent': 40, 'disk_percent': 30},
            'sqlite': {'ok': True, 'response_ms': 10},
            'api_latency_ms': 50,
            'internet': True,
            'kite': {'ok': True},
            'scheduler_heartbeat': {'ok': True},
            'reconciliation': {'ok': True},
        }
        self.assertEqual(mon.health_score(m), 100)

    def test_health_score_degraded_cpu(self):
        mon = EnterpriseSystemMonitor(store=FakeStore())
        m = {
            'system': {'cpu_percent': 85, 'memory_percent': 50, 'disk_percent': 30},
            'sqlite': {'ok': True, 'response_ms': 10},
            'api_latency_ms': 50,
            'internet': True,
            'kite': {'ok': True},
            'scheduler_heartbeat': {'ok': True},
            'reconciliation': {'ok': True},
        }
        score = mon.health_score(m)
        self.assertLess(score, 100)
        self.assertGreaterEqual(score, 0)

    def test_health_score_sqlite_failure(self):
        mon = EnterpriseSystemMonitor(store=FakeStore())
        m = {
            'system': {'cpu_percent': 30, 'memory_percent': 40, 'disk_percent': 30},
            'sqlite': {'ok': False, 'response_ms': 0},
            'api_latency_ms': 50,
            'internet': True,
            'kite': {'ok': True},
            'scheduler_heartbeat': {'ok': True},
            'reconciliation': {'ok': True},
        }
        score = mon.health_score(m)
        self.assertLess(score, 100)

    def test_save_snapshot(self):
        store = FakeStore()
        mon = EnterpriseSystemMonitor(store=store)
        mon.save_snapshot({'health_score': 95, 'timestamp': '2026-08-03T20:00:00', 'system': {}})
        self.assertEqual(len(store.health), 1)

    def test_daily_report(self):
        store = FakeStore()
        mon = EnterpriseSystemMonitor(store=store)
        r = mon.daily_report()
        self.assertIn('health_score', r)
        self.assertEqual(len(store.reports), 1)

    def test_auto_recovery_kite(self):
        store = FakeStore()
        mon = EnterpriseSystemMonitor(store=store)
        # should not raise
        mon.auto_recovery({'kite': {'ok': False}, 'internet': True, 'sqlite': {'ok': True}, 'scheduler_heartbeat': {'ok': True}})

    def test_scheduler_heartbeat_ok(self):
        store = FakeStore()
        store.broker_state['scheduler_heartbeat'] = {'timestamp': __import__('datetime').datetime.now().isoformat()}
        mon = EnterpriseSystemMonitor(store=store)
        hb = mon._scheduler_heartbeat()
        self.assertIn('ok', hb)

    def test_reconciliation_health(self):
        store = FakeStore()
        mon = EnterpriseSystemMonitor(store=store)
        rh = mon._reconciliation_health()
        self.assertIn('ok', rh)

    def test_system_metrics_returns_floats(self):
        mon = EnterpriseSystemMonitor(store=FakeStore())
        s = mon._system_metrics()
        for k in ['cpu_percent', 'memory_percent', 'disk_percent']:
            self.assertIsInstance(s[k], float)


class TestEnterpriseAlertEngine(unittest.TestCase):

    def test_alert_stores_and_queues(self):
        store = FakeStore()
        eng = EnterpriseAlertEngine(store=store)
        aid = eng.critical('SQLite', 'Corruption detected')
        self.assertGreater(aid, 0)
        self.assertEqual(len(store.alerts), 1)
        self.assertEqual(len(eng.pop_dashboard_alerts()), 1)

    def test_check_generates_alerts(self):
        store = FakeStore()
        eng = EnterpriseAlertEngine(store=store)
        eng.check({
            'system': {'cpu_percent': 90, 'memory_percent': 90, 'disk_percent': 90},
            'sqlite': {'ok': False, 'response_ms': 0},
            'api_latency_ms': 600,
            'internet': False,
            'kite': {'ok': False},
            'scheduler_heartbeat': {'ok': False},
            'reconciliation': {'ok': False},
        })
        self.assertGreater(len(store.alerts), 0)

    def test_levels(self):
        store = FakeStore()
        eng = EnterpriseAlertEngine(store=store)
        eng.alert('INFO', 'X', 'm')
        eng.alert('warning', 'X', 'm')
        eng.alert('critical', 'X', 'm')
        self.assertEqual(store.alerts[0]['level'], 'INFO')
        self.assertEqual(store.alerts[1]['level'], 'WARNING')
        self.assertEqual(store.alerts[2]['level'], 'CRITICAL')

    def test_daily_summary(self):
        store = FakeStore()
        eng = EnterpriseAlertEngine(store=store)
        eng.warning('CPU', 'high')
        eng.critical('Internet', 'down')
        s = eng.daily_summary()
        self.assertEqual(s['counts'].get('WARNING', 0), 1)
        self.assertEqual(s['counts'].get('CRITICAL', 0), 1)

    def test_unacknowledged(self):
        store = FakeStore()
        eng = EnterpriseAlertEngine(store=store)
        eng.info('X', 'm')
        self.assertEqual(len(eng.get_unacknowledged()), 1)

    def test_acknowledge(self):
        store = FakeStore()
        eng = EnterpriseAlertEngine(store=store)
        eng.info('X', 'm')
        eng.acknowledge(1)
        for a in store.alerts:
            if a.get('id') == 1:
                self.assertEqual(a['acknowledged'], 1)

    def test_notification_channels(self):
        store = FakeStore()
        eng = EnterpriseAlertEngine(store=store, channels=['log', 'dashboard'])
        eng.critical('X', 'm')
        self.assertEqual(len(store.notifications), 2)


class TestMonitoringDashboard(unittest.TestCase):

    def test_dashboard_data_shape(self):
        mon = EnterpriseSystemMonitor(store=FakeStore())
        m = mon.collect()
        self.assertIn('health_score', m)
        self.assertIn('timestamp', m)


if __name__ == '__main__':
    unittest.main()
