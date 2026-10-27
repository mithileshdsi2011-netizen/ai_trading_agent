"""Scheduler heartbeat instrumentation + FII/DII latest-row ordering.

- scheduler_heartbeat was never written by anything, so the monitor
  permanently reported MISSING and fired a phantom CRITICAL alert.
  The orchestrator now stamps it from a daemon thread every 30s and
  writes one heartbeat_log row per completed cycle.
- fii_dii.timestamp holds display strings ('31-Aug-2026') that do not
  sort chronologically; latest-row selection must use the ISO 'date'
  column with an insertion-order tiebreak.
"""
import os
import sys
import time
import threading
from datetime import datetime, timedelta
from unittest.mock import Mock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.trading_orchestrator import TradingOrchestrator
from src.persistence import TradingStore


def _orch(store):
    """Cheap orchestrator: heavy deps mocked, real TradingStore swapped in."""
    with patch('trading_orchestrator.MarketDataFetcher'), \
         patch('trading_orchestrator.SignalGenerator'), \
         patch('trading_orchestrator.OrderExecutor'), \
         patch('trading_orchestrator.ReconciliationEngine'):
        orch = TradingOrchestrator()
    orch._store = store
    return orch


class TestSchedulerHeartbeatLoop:
    def test_writes_fresh_record(self, tmp_path):
        store = TradingStore(str(tmp_path / 't.db'))
        orch = _orch(store)
        orch.is_running = True
        orch.SCHEDULER_HEARTBEAT_INTERVAL_SECONDS = 0.05
        orch._sched_interval_minutes = 15
        th = threading.Thread(target=orch._scheduler_heartbeat_loop, daemon=True)
        th.start()
        time.sleep(0.3)
        orch.is_running = False
        th.join(timeout=2)

        hb = store.get_broker_state('scheduler_heartbeat')
        assert hb and hb['timestamp']
        age = (datetime.now() - datetime.fromisoformat(hb['timestamp'])).total_seconds()
        assert age < 90  # monitor HEALTH_THRESHOLDS heartbeat_age_seconds
        assert hb['interval_minutes'] == 15

    def test_monitor_reads_healthy(self, tmp_path):
        store = TradingStore(str(tmp_path / 't.db'))
        store.save_broker_state('scheduler_heartbeat',
                                {'timestamp': datetime.now().isoformat()})
        from system_monitor import EnterpriseSystemMonitor
        mon = EnterpriseSystemMonitor()
        mon.store = store
        assert mon._scheduler_heartbeat()['ok'] is True

    def test_cycle_activity_recorded_on_wrapper(self, tmp_path):
        store = TradingStore(str(tmp_path / 't.db'))
        orch = _orch(store)
        orch.run_once = Mock(return_value={})
        orch._run_once_wrapper()
        assert orch._last_cycle_started and orch._last_cycle_completed
        assert orch._last_cycle_status == 'ok'

    def test_write_failure_never_kills_thread(self, tmp_path):
        store = TradingStore(str(tmp_path / 't.db'))
        store.save_broker_state = Mock(side_effect=RuntimeError('db gone'))
        orch = _orch(store)
        orch.is_running = True
        orch.SCHEDULER_HEARTBEAT_INTERVAL_SECONDS = 0.02
        th = threading.Thread(target=orch._scheduler_heartbeat_loop, daemon=True)
        th.start()
        time.sleep(0.15)
        orch.is_running = False
        th.join(timeout=2)
        assert not th.is_alive()  # exited cleanly, did not die or hang


def _hb(store, **kw):
    store.save_broker_state('scheduler_heartbeat', kw)


def _mon(store):
    from system_monitor import EnterpriseSystemMonitor
    mon = EnterpriseSystemMonitor()
    mon.store = store
    return mon


class TestStallDetection:
    """A hung run_once() must report stalled, never falsely healthy."""

    def test_fresh_heartbeat_and_cycle_is_ok(self, tmp_path):
        store = TradingStore(str(tmp_path / 't.db'))
        now = datetime.now().isoformat()
        _hb(store, timestamp=now, heartbeat_started=now,
            interval_minutes=15, last_cycle_completed=now, last_cycle_status='ok')
        r = _mon(store)._scheduler_heartbeat()
        assert r['ok'] is True and r['reason'] == 'ok'

    def test_stalled_cycle_flagged_despite_fresh_heartbeat(self, tmp_path):
        store = TradingStore(str(tmp_path / 't.db'))
        old = (datetime.now() - timedelta(minutes=45)).isoformat()
        _hb(store, timestamp=datetime.now().isoformat(),  # thread alive
            heartbeat_started=old, interval_minutes=15,
            last_cycle_started=old, last_cycle_completed=old)
        r = _mon(store)._scheduler_heartbeat()
        assert r['ok'] is False and r['reason'] == 'cycle_stalled'

    def test_hung_cycle_never_completing_flagged(self, tmp_path):
        # cycle started >2 intervals ago, never finished → last activity stale
        store = TradingStore(str(tmp_path / 't.db'))
        old = (datetime.now() - timedelta(minutes=40)).isoformat()
        _hb(store, timestamp=datetime.now().isoformat(),
            heartbeat_started=old, interval_minutes=15,
            last_cycle_started=old, last_cycle_completed=None)
        assert _mon(store)._scheduler_heartbeat()['reason'] == 'cycle_stalled'

    def test_cycle_running_long_not_immediately_stalled(self, tmp_path):
        # cycle started 20min ago at 15min interval: long but within window
        store = TradingStore(str(tmp_path / 't.db'))
        started = (datetime.now() - timedelta(minutes=20)).isoformat()
        _hb(store, timestamp=datetime.now().isoformat(),
            heartbeat_started=started, interval_minutes=15,
            last_cycle_started=started, last_cycle_completed=None)
        assert _mon(store)._scheduler_heartbeat()['ok'] is True

    def test_stale_heartbeat_distinct_from_stalled_cycle(self, tmp_path):
        store = TradingStore(str(tmp_path / 't.db'))
        old = (datetime.now() - timedelta(seconds=200)).isoformat()
        _hb(store, timestamp=old, heartbeat_started=old, interval_minutes=15)
        r = _mon(store)._scheduler_heartbeat()
        assert r['ok'] is False and r['reason'] == 'heartbeat_stale'

    def test_missing_record_reason(self, tmp_path):
        store = TradingStore(str(tmp_path / 't.db'))
        r = _mon(store)._scheduler_heartbeat()
        assert r['ok'] is False and r['reason'] == 'missing'

    def test_alert_message_distinguishes_stall(self, tmp_path):
        from alert_engine import EnterpriseAlertEngine
        store = TradingStore(str(tmp_path / 't.db'))
        eng = EnterpriseAlertEngine(store=store, channels=['log'])
        eng.alert = Mock()
        eng.check({'scheduler_heartbeat': {'ok': False, 'reason': 'cycle_stalled'}})
        sched = [c for c in eng.alert.call_args_list
                 if c.kwargs.get('incident_key') == 'scheduler:heartbeat']
        assert sched and 'stalled' in sched[0].args[2]


class TestCycleHeartbeatLog:
    def test_successful_cycle_logged(self, tmp_path):
        store = TradingStore(str(tmp_path / 't.db'))
        orch = _orch(store)
        orch.run_once = Mock(return_value={'cycle': 'done'})
        orch._run_once_wrapper()
        logs = store.get_latest_heartbeat_logs(limit=5)
        assert len(logs) == 1
        assert logs[0]['source'] == 'scheduler_cycle'
        assert logs[0]['status'] == 'ok'
        assert logs[0]['latency_ms'] >= 0

    def test_failed_cycle_logged_and_reraises(self, tmp_path):
        store = TradingStore(str(tmp_path / 't.db'))
        orch = _orch(store)
        orch.run_once = Mock(side_effect=RuntimeError('cycle boom'))
        with pytest.raises(RuntimeError):
            orch._run_once_wrapper()
        logs = store.get_latest_heartbeat_logs(limit=5)
        assert len(logs) == 1
        assert logs[0]['status'] == 'error'

    def test_skipped_cycle_not_logged(self, tmp_path):
        """A cycle skipped for overlap is not a cycle — no fake heartbeat row."""
        store = TradingStore(str(tmp_path / 't.db'))
        orch = _orch(store)
        orch._run_once_lock.acquire()
        try:
            result = orch._run_once_wrapper()
        finally:
            orch._run_once_lock.release()
        assert result == {}
        assert store.get_latest_heartbeat_logs(limit=5) == []

    def test_lock_released_after_cycle(self, tmp_path):
        store = TradingStore(str(tmp_path / 't.db'))
        orch = _orch(store)
        orch.run_once = Mock(return_value={})
        orch._run_once_wrapper()
        assert orch._run_once_lock.acquire(blocking=False)
        orch._run_once_lock.release()


class TestFiiDiiLatestOrdering:
    def _seed(self, store):
        # Reproduce production shape: 'timestamp' is a display string that
        # sorts lexically ('31-Aug-2026' > '27-Oct-2026') while 'date' is ISO.
        for ts, d, net in [('25-Aug-2026', '2026-08-25', 10.0),
                           ('31-Aug-2026', '2026-08-31', 20.0),
                           ('27-Oct-2026', '2026-10-27', 30.0)]:
            store.save_fii_dii({'timestamp': ts, 'date': d, 'fii_net': net})

    def test_latest_returns_newest_date(self, tmp_path):
        store = TradingStore(str(tmp_path / 't.db'))
        self._seed(store)
        latest = store.get_latest_fii_dii()
        assert latest['date'] == '2026-10-27'
        assert latest['fii_net'] == 30.0

    def test_same_date_tiebreak_by_insertion(self, tmp_path):
        store = TradingStore(str(tmp_path / 't.db'))
        store.save_fii_dii({'timestamp': 'a', 'date': '2026-10-27', 'fii_net': 1.0})
        store.save_fii_dii({'timestamp': 'b', 'date': '2026-10-27', 'fii_net': 2.0})
        assert store.get_latest_fii_dii()['fii_net'] == 2.0

    def test_monitor_feed_fresh_after_fix(self, tmp_path):
        store = TradingStore(str(tmp_path / 't.db'))
        self._seed(store)
        # the newest row is ~0-1 days old regardless of wall clock
        from system_monitor import EnterpriseSystemMonitor
        mon = EnterpriseSystemMonitor()
        mon.store = store
        feed = mon._data_quality()['fii_dii']
        expected_age = (datetime.now() - datetime(2026, 10, 27)).total_seconds() / 3600
        assert abs(feed['age_hours'] - expected_age) < 2

    def test_empty_table_returns_none(self, tmp_path):
        store = TradingStore(str(tmp_path / 't.db'))
        assert store.get_latest_fii_dii() is None
