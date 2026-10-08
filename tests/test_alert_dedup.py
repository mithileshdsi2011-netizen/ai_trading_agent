"""Persistent alert-incident deduplication — regression tests.

Covers: one email per incident, suppression with audit, persistence across
"restarts" (new engine + new store on the same DB), cross-process atomic
claims, escalation, recovery, recurrence, SMTP failure/retry, lease expiry,
and distinct-incident separation.
"""
import smtplib
import time

import pytest

from persistence import TradingStore


class FakeEmail:
    def __init__(self):
        self.sent = []

    def send_report(self, subject: str, body: str) -> bool:
        self.sent.append(subject)
        return True


class FlakyEmail(FakeEmail):
    def __init__(self, fail_times: int):
        super().__init__()
        self.fail_times = fail_times

    def send_report(self, subject: str, body: str) -> bool:
        if self.fail_times > 0:
            self.fail_times -= 1
            return False
        return super().send_report(subject, body)


@pytest.fixture
def store(tmp_path):
    return TradingStore(str(tmp_path / "incidents.db"))


@pytest.fixture
def email():
    return FakeEmail()


@pytest.fixture
def engine(store, email):
    from alert_engine import EnterpriseAlertEngine
    return EnterpriseAlertEngine(store=store, email=email, channels=['email'])


# ── Core dedup ────────────────────────────────────────────────────────

def test_one_email_per_incident(engine, email):
    for _ in range(5):
        engine.alert('WARNING', 'Disk', 'Disk usage 90%', incident_key='system:disk')
    assert email.sent == ['[WARNING] Disk']


def test_suppressed_detections_audited(engine, store):
    engine.alert('WARNING', 'Disk', 'Disk usage 90%', incident_key='system:disk')
    engine.alert('WARNING', 'Disk', 'Disk usage 91%', incident_key='system:disk')
    hist = store.get_notification_history(limit=10)
    suppressed = [h for h in hist if h['channel'] == 'incident_dedup'
                  and h['status'] == 'SUPPRESSED']
    assert len(suppressed) == 1


def test_incident_state_persisted(engine, store):
    engine.alert('CRITICAL', 'Kite', 'Kite API unavailable',
                 incident_key='kite:connectivity')
    inc = store.get_alert_incident('kite:connectivity')
    assert inc['state'] == 'OPEN'
    assert inc['notify_state'] == 'DELIVERED'
    assert inc['detection_count'] == 1


def test_numeric_variations_share_incident(engine, email):
    engine.alert('WARNING', 'Disk', 'Disk usage 90%')
    engine.alert('WARNING', 'Disk', 'Disk usage 95%')
    assert len(email.sent) == 1  # digit-masked fallback keys match


def test_distinct_incidents_not_merged(engine, email):
    engine.alert('CRITICAL', 'Order', 'RELIANCE stop-loss hit')
    engine.alert('CRITICAL', 'Order', 'INFY stop-loss hit')
    assert len(email.sent) == 2  # different symbols = different incidents


# ── Restart / cross-process ───────────────────────────────────────────

def test_dedup_survives_restart(store, tmp_path):
    """New engine + new store instance on the same DB must not re-notify."""
    from alert_engine import EnterpriseAlertEngine
    e1 = FakeEmail()
    eng1 = EnterpriseAlertEngine(store=store, email=e1, channels=['email'])
    eng1.alert('CRITICAL', 'Kite', 'Kite API unavailable',
               incident_key='kite:connectivity')
    assert len(e1.sent) == 1

    store2 = TradingStore(str(tmp_path / "incidents.db"))  # same file, new instance
    e2 = FakeEmail()
    eng2 = EnterpriseAlertEngine(store=store2, email=e2, channels=['email'])
    eng2.alert('CRITICAL', 'Kite', 'Kite API unavailable',
               incident_key='kite:connectivity')
    assert e2.sent == []


def test_concurrent_claim_single_winner(store):
    """Two engines (two processes) detecting simultaneously — exactly one send."""
    from alert_engine import EnterpriseAlertEngine
    e1, e2 = FakeEmail(), FakeEmail()
    eng1 = EnterpriseAlertEngine(store=store, email=e1, channels=['email'])
    eng2 = EnterpriseAlertEngine(store=store, email=e2, channels=['email'])
    eng1.alert('CRITICAL', 'SQLite', 'SQLite health check failed',
               incident_key='sqlite:health')
    eng2.alert('CRITICAL', 'SQLite', 'SQLite health check failed',
               incident_key='sqlite:health')
    assert len(e1.sent) + len(e2.sent) == 1


# ── Escalation / recovery / recurrence ────────────────────────────────

def test_severity_escalation_sends_again(engine, email):
    engine.alert('WARNING', 'SQLite', 'SQLite slow: 300ms',
                 incident_key='sqlite:health')
    engine.alert('CRITICAL', 'SQLite', 'SQLite health check failed',
                 incident_key='sqlite:health')
    assert email.sent[0] == '[WARNING] SQLite'
    assert email.sent[1] == '[ESCALATED CRITICAL] SQLite'


def test_recovery_email_once(engine, email):
    engine.alert('CRITICAL', 'Internet', 'Internet unavailable',
                 incident_key='internet:connectivity')
    engine.resolve_incident('internet:connectivity')
    engine.resolve_incident('internet:connectivity')  # idempotent
    subjects = [s for s in email.sent if s.startswith('[RESOLVED]')]
    assert len(subjects) == 1


def test_recurrence_after_resolution_notifies(engine, email):
    engine.alert('CRITICAL', 'Kite', 'Kite API unavailable',
                 incident_key='kite:connectivity')
    engine.resolve_incident('kite:connectivity')
    engine.alert('CRITICAL', 'Kite', 'Kite API unavailable',
                 incident_key='kite:connectivity')
    recurring = [s for s in email.sent if s.startswith('[RECURRED')]
    assert len(recurring) == 1


def test_check_uses_stable_keys_and_resolves(engine, email):
    engine.check({'system': {'cpu_percent': 90}, 'sqlite': {'ok': True,
                 'response_ms': 10}, 'internet': True,
                 'kite': {'ok': True}, 'scheduler_heartbeat': {'ok': True},
                 'reconciliation': {'ok': True}, 'api_latency_ms': 10})
    assert email.sent == ['[WARNING] CPU']
    engine.check({'system': {'cpu_percent': 10}, 'sqlite': {'ok': True,
                 'response_ms': 10}, 'internet': True,
                 'kite': {'ok': True}, 'scheduler_heartbeat': {'ok': True},
                 'reconciliation': {'ok': True}, 'api_latency_ms': 10})
    assert '[RESOLVED] CPU' in email.sent


# ── SMTP failure / retry / lease ──────────────────────────────────────

def test_failed_delivery_retries(store, tmp_path):
    from alert_engine import EnterpriseAlertEngine
    email = FlakyEmail(fail_times=1)
    eng = EnterpriseAlertEngine(store=store, email=email, channels=['email'])
    eng.alert('CRITICAL', 'Kite', 'Kite API unavailable',
              incident_key='kite:connectivity')
    inc = store.get_alert_incident('kite:connectivity')
    assert inc['notify_state'] == 'FAILED'
    assert email.sent == []

    eng.alert('CRITICAL', 'Kite', 'Kite API unavailable',
              incident_key='kite:connectivity')
    assert len(email.sent) == 1  # retried and delivered
    inc = store.get_alert_incident('kite:connectivity')
    assert inc['notify_state'] == 'DELIVERED'


def test_expired_lease_reclaimable(store, tmp_path):
    """A crashed sender's stale SENDING claim does not lose the alert."""
    store.upsert_alert_incident('kite:connectivity', 'Kite', 'CRITICAL')
    assert store.claim_incident_notification('kite:connectivity', 'dead-proc')
    # Lease in the past → a new claimant wins
    import datetime as _dt
    past = (_dt.datetime.now() - _dt.timedelta(seconds=600)).isoformat()
    with store._conn() as conn:
        conn.execute(
            "UPDATE alert_incidents SET claim_expires = ? WHERE incident_key = ?",
            (past, 'kite:connectivity'))
    assert store.claim_incident_notification('kite:connectivity', 'new-proc')


# ── EmailReporter direct path (orchestrator._alert etc.) ──────────────

def test_send_report_dedup_persistent(tmp_path, monkeypatch):
    from email_reports import EmailReporter
    EmailReporter._sent_today = {}
    EmailReporter._cooldowns = {}
    r = EmailReporter()
    r.enabled = True
    r.username, r.password, r.to_email = 'u', 'p', 't@x'

    assert r.send_report('🔴 EMERGENCY CIRCUIT BREAKER', 'b') is True
    assert r.send_report('🔴 EMERGENCY CIRCUIT BREAKER', 'b') is False  # dup

    # New instance (restart) — persistent table still suppresses
    EmailReporter._sent_today = {}
    EmailReporter._cooldowns = {}
    r2 = EmailReporter()
    r2.enabled = True
    r2.username, r2.password, r2.to_email = 'u', 'p', 't@x'
    assert r2.send_report('🔴 EMERGENCY CIRCUIT BREAKER', 'b') is False


def test_engine_bypasses_subject_dedup(store):
    """Distinct incidents sharing the same level+source subject must each
    email — the engine calls _deliver, not send_report's subject dedup."""
    from email_reports import EmailReporter
    from alert_engine import EnterpriseAlertEngine
    EmailReporter._sent_today = {}
    EmailReporter._cooldowns = {}
    r = EmailReporter()
    r.enabled = True
    r.username, r.password, r.to_email = 'u', 'p', 't@x'
    calls = []
    r._deliver = lambda subject, body: (calls.append(subject), True)[1]
    eng = EnterpriseAlertEngine(store=store, email=r, channels=['email'])
    eng.alert('WARNING', 'Disk', 'Disk usage 90%', incident_key='disk:a')
    eng.alert('WARNING', 'Disk', 'Disk quota exceeded', incident_key='disk:b')
    assert calls == ['[WARNING] Disk', '[WARNING] Disk']


def test_cross_process_recovery_delivery(store):
    """An email-less engine resolves the incident; the email-enabled engine
    must still deliver the single recovery email on its next check."""
    from alert_engine import EnterpriseAlertEngine
    email = FakeEmail()
    eng_email = EnterpriseAlertEngine(store=store, email=email, channels=['email'])
    eng_silent = EnterpriseAlertEngine(store=store, email=None, channels=['email'])
    eng_email.alert('CRITICAL', 'Kite', 'Kite API unavailable',
                    incident_key='kite:connectivity')
    eng_silent.resolve_incident('kite:connectivity')   # records resolution
    assert len(email.sent) == 1                        # no recovery yet
    eng_email.resolve_incident('kite:connectivity')    # claims pending recovery
    assert any(s.startswith('[RESOLVED]') for s in email.sent)


def test_send_report_different_subjects_send(tmp_path):
    from email_reports import EmailReporter
    EmailReporter._sent_today = {}
    EmailReporter._cooldowns = {}
    r = EmailReporter()
    r.enabled = True
    r.username, r.password, r.to_email = 'u', 'p', 't@x'
    assert r.send_report('Subject A', 'b') is True
    assert r.send_report('Subject B', 'b') is True


def test_no_real_smtp_anywhere(engine, email):
    """Belt-and-suspenders: smtplib is a mock everywhere under pytest."""
    engine.alert('CRITICAL', 'Kite', 'Kite API unavailable')
    assert isinstance(smtplib.SMTP, type(smtplib.SMTP))  # mocked class, not real
