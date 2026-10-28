"""Dashboard engine-cycle vs signal-scan labeling.

The dashboard's own 150-stock signal scan (_SIGNAL_CACHE) and the
orchestrator's scheduler cycle (scheduler_heartbeat record) are separate
clocks. These tests pin the engine_cycle payload and the labels that keep
an overdue signal scan from being presented as a missed engine cycle.
"""
import os
import sys
import importlib.util
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

_spec = importlib.util.spec_from_file_location(
    'dashboard_mod',
    os.path.join(os.path.dirname(__file__), '..', 'dashboard.py'))
_dash = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_dash)

SRC = open(os.path.join(os.path.dirname(__file__), '..', 'dashboard.py')).read()


class _FakeStore:
    def __init__(self, record):
        self._record = record

    def get_broker_state(self, type_):
        assert type_ == 'scheduler_heartbeat'
        return self._record


def _payload(monkeypatch, record):
    monkeypatch.setattr(_dash, 'get_store', lambda: _FakeStore(record))
    return _dash._engine_cycle_payload()


def _hb(**kw):
    base = {
        'timestamp': '2026-10-28T12:11:00',
        'source': 'run_scheduled',
        'interval_minutes': 15,
        'last_cycle_started': '2026-10-28T11:55:31',
        'last_cycle_completed': '2026-10-28T11:55:32',
        'last_cycle_status': 'ok',
    }
    base.update(kw)
    return base


class TestEngineCyclePayload:
    def test_fields_from_heartbeat_record(self, monkeypatch):
        p = _payload(monkeypatch, _hb())
        assert p['last_cycle_completed'] == '2026-10-28T11:55:32'
        assert p['last_cycle_status'] == 'ok'
        assert p['interval_minutes'] == 15
        assert p['cycle_running'] is False

    def test_next_expected_is_completed_plus_interval(self, monkeypatch):
        p = _payload(monkeypatch, _hb())
        assert p['next_expected'] == '2026-10-28T12:10:32'

    def test_cycle_running_when_started_after_completed(self, monkeypatch):
        p = _payload(monkeypatch, _hb(last_cycle_started='2026-10-28T12:10:01'))
        assert p['cycle_running'] is True

    def test_cycle_running_when_no_completion(self, monkeypatch):
        p = _payload(monkeypatch, _hb(last_cycle_completed=None))
        assert p['cycle_running'] is True
        assert p['next_expected'] is None

    def test_missing_record_returns_empty_payload(self, monkeypatch):
        p = _payload(monkeypatch, None)
        assert p['last_cycle_completed'] is None
        assert p['next_expected'] is None
        assert p['cycle_running'] is False

    def test_store_failure_returns_none(self, monkeypatch):
        monkeypatch.setattr(
            _dash, 'get_store',
            lambda: (_ for _ in ()).throw(RuntimeError('db down')))
        assert _dash._engine_cycle_payload() is None

    def test_malformed_completed_timestamp_no_next(self, monkeypatch):
        p = _payload(monkeypatch, _hb(last_cycle_completed='27-Oct'))
        assert p['next_expected'] is None


class TestScanClockLabels:
    def test_header_labels_distinguish_signal_scan(self):
        assert '<div class="stat-label">Signal Scan</div>' in SRC
        assert '<div class="stat-label">Next Signal Scan</div>' in SRC
        assert '<div class="stat-label">Engine Cycle</div>' in SRC
        assert 'hdr-engine-cycle' in SRC

    def test_bot_activity_labels_distinguish_signal_scan(self):
        assert '<div class="stat-label">Last Signal Scan</div>' in SRC
        assert '<div class="stat-label">Next Signal Scan</div>' in SRC
        assert 'bs-engine-cycle' in SRC

    def test_signal_scan_not_labeled_engine(self):
        assert "engine running'" not in SRC
        assert '>Last Scan<' not in SRC

    def test_next_scan_marked_estimate(self):
        assert "'~'+d.next_scan+' est'" in SRC

    def test_engine_cycle_wired_into_api_payload(self):
        assert "data['engine_cycle'] = _engine_cycle_payload()" in SRC

    def test_running_scan_still_shows_scanning(self):
        assert "· scanning now'" in SRC
        assert 'Scanning signals' in SRC
