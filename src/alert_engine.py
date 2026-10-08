"""
Enterprise Monitoring & Alerting - Alert Engine.

Generates, stores and routes alerts.  Supports multiple notification
channels and prioritization.  Alerting is purely observational and does
not modify trading decisions.
"""
import hashlib
import json
import logging
import os
import re
import socket
import subprocess
import threading
import uuid
from datetime import datetime, date
from typing import Any, Dict, List, Optional

from config import config
from persistence import get_store
from email_reports import EmailReporter

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now().isoformat()


class EnterpriseAlertEngine:
    """
    Central alert bus for the platform.

    Levels: CRITICAL, WARNING, INFO
    Channels: log, dashboard popup, desktop, telegram, email
    """

    LEVELS = {'CRITICAL', 'WARNING', 'INFO'}

    def __init__(
        self,
        store=None,
        telegram=None,
        email=None,
        channels: Optional[List[str]] = None,
    ):
        self.store = store or get_store()
        self.telegram = telegram
        if email is None and getattr(config, 'EMAIL_ENABLED', False):
            try:
                email = EmailReporter()
            except Exception as _e:
                logger.warning(f"Could not create EmailReporter: {_e}")
        self.email = email
        default_channels = ['log', 'dashboard']
        if self.email is not None:
            default_channels.append('email')
        if self.telegram is not None:
            default_channels.append('telegram')
        self.channels = set(channels or default_channels)
        self._popup_queue: List[Dict[str, Any]] = []
        self._email_cooldowns: Dict[str, datetime] = {}  # Track email cooldowns per source
        self._lock = threading.Lock()

    # ── Core alert lifecycle ──────────────────────────────────────────────

    def alert(
        self,
        level: str,
        source: str,
        message: str,
        data: Optional[Dict[str, Any]] = None,
        notify: bool = True,
        incident_key: Optional[str] = None,
    ) -> int:
        """Create, store and route an alert.  Returns alert id."""
        level = level.upper()
        if level not in self.LEVELS:
            level = 'INFO'

        payload = {
            'timestamp': _now(),
            'level': level,
            'source': source,
            'message': message,
            'data_json': json.dumps(data or {}),
            'acknowledged': 0,
            'incident_key': incident_key,
        }

        alert_id = -1
        try:
            if hasattr(self.store, 'save_system_alert'):
                alert_id = self.store.save_system_alert(payload)
        except Exception as e:
            logger.warning(f"Could not store alert: {e}")

        if notify:
            self._route(payload, alert_id)

        return alert_id

    def _route(self, alert: Dict[str, Any], alert_id: int) -> None:
        channels = list(self.channels)
        for ch in channels:
            try:
                if ch == 'log':
                    self._to_log(alert)
                elif ch == 'dashboard':
                    self._to_dashboard(alert)
                elif ch == 'desktop':
                    self._to_desktop(alert)
                elif ch == 'telegram':
                    self._to_telegram(alert)
                elif ch == 'email':
                    self._to_email(alert)
                self._record_notification(alert_id, ch, 'OK')
            except Exception as e:
                self._record_notification(alert_id, ch, f'FAILED: {e}')

    def _record_notification(self, alert_id: int, channel: str, status: str) -> None:
        try:
            if hasattr(self.store, 'save_notification_history'):
                self.store.save_notification_history({
                    'timestamp': _now(),
                    'alert_id': alert_id,
                    'channel': channel,
                    'status': status,
                    'content': json.dumps({'level': 'CRITICAL'}),
                })
        except Exception:
            pass

    def _to_log(self, alert: Dict[str, Any]) -> None:
        msg = f"[{alert['level']}] {alert['source']}: {alert['message']}"
        if alert['level'] == 'CRITICAL':
            logger.error(msg)
        elif alert['level'] == 'WARNING':
            logger.warning(msg)
        else:
            logger.info(msg)

    def _to_dashboard(self, alert: Dict[str, Any]) -> None:
        with self._lock:
            self._popup_queue.append(alert)
            if len(self._popup_queue) > 100:
                self._popup_queue = self._popup_queue[-50:]

    def _to_desktop(self, alert: Dict[str, Any]) -> None:
        if os.uname().sysname == 'Darwin':
            try:
                subprocess.run([
                    'osascript', '-e',
                    f'display notification "{alert["message"]}" with title "AI Trading Alert"'
                ], check=False, capture_output=True, text=True)
            except Exception:
                pass

    def _to_telegram(self, alert: Dict[str, Any]) -> None:
        if self.telegram and hasattr(self.telegram, 'send'):
            try:
                self.telegram.send(f"*{alert['level']}* {alert['source']}: {alert['message']}")
            except Exception:
                pass

    def _incident_key(self, alert: Dict[str, Any]) -> str:
        """Stable per-incident key.  Explicit keys win; the fallback masks
        only digit runs so 'Disk 90%' and 'Disk 91%' share one incident
        while different symbols/names never merge."""
        explicit = alert.get('incident_key')
        if explicit:
            return str(explicit)
        masked = re.sub(r'\d+', '#', alert['message'])
        digest = hashlib.sha1(masked.encode()).hexdigest()[:12]
        return f"{alert['source']}:{digest}"

    def _to_email(self, alert: Dict[str, Any]) -> None:
        if not (self.email and hasattr(self.email, 'send_report')):
            return
        if not hasattr(self.store, 'upsert_alert_incident'):
            # Store lacks incident support — fail open so alerts are not lost.
            try:
                self.email.send_report(
                    subject=f"[{alert['level']}] {alert['source']}",
                    body=f"<html><body><p><b>{alert['source']}</b> — {alert['message']}</p></body></html>",
                )
            except Exception:
                pass
            return

        key = self._incident_key(alert)
        res = self.store.upsert_alert_incident(key, alert['source'], alert['level'])
        event = res['event']
        if event == 'repeat':
            # Suppress unless the last delivery failed — retries must not be
            # silently swallowed, or a critical alert is lost.
            if res['incident'].get('notify_state') != 'FAILED':
                self._audit_suppressed(alert, key, res['incident'])
                return
        self._send_incident_email(key, alert, event)

    def _audit_suppressed(
        self, alert: Dict[str, Any], incident_key: str, incident: Dict[str, Any]
    ) -> None:
        try:
            self.store.save_notification_history({
                'timestamp': _now(),
                'alert_id': 0,
                'channel': 'incident_dedup',
                'status': 'SUPPRESSED',
                'content': json.dumps({
                    'incident_key': incident_key,
                    'level': alert['level'],
                    'source': alert['source'],
                    'message': alert['message'],
                    'detection_count': incident.get('detection_count'),
                }),
            })
        except Exception:
            pass

    def _claim_owner(self) -> str:
        return f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"

    def _send_incident_email(
        self, incident_key: str, alert: Dict[str, Any], event: str
    ) -> None:
        owner = self._claim_owner()
        if not self.store.claim_incident_notification(incident_key, owner):
            return  # another process/thread owns the pending send
        subject = f"[{alert['level']}] {alert['source']}"
        if event == 'escalated':
            subject = f"[ESCALATED {alert['level']}] {alert['source']}"
        elif event == 'reopened':
            subject = f"[RECURRED {alert['level']}] {alert['source']}"
        body = (
            f"<html><body><p><b>{alert['source']}</b> — {alert['message']}</p>"
            f"<p>Incident: <code>{incident_key}</code></p></body></html>"
        )
        # Bypass send_report's own subject-keyed dedup (EmailReporter._deliver)
        # so distinct incidents sharing a subject are never merged here.
        deliver = getattr(self.email, '_deliver', None) or self.email.send_report
        try:
            ok = bool(deliver(subject=subject, body=body))
        except Exception:
            ok = False
        self.store.complete_incident_notification(incident_key, owner, ok)

    def resolve_incident(self, incident_key: str, summary: str = '') -> None:
        """Mark an incident recovered.  Sends exactly one recovery email.

        Resolution is recorded even without an email sender, and the
        recovery claim is attempted independently — so whichever process
        (dashboard or orchestrator) has working email delivers it once.
        """
        if not hasattr(self.store, 'resolve_alert_incident'):
            return
        try:
            self.store.resolve_alert_incident(incident_key)
        except Exception:
            return
        if not (self.email and hasattr(self.email, 'send_report')):
            return
        owner = self._claim_owner()
        try:
            if not self.store.claim_incident_notification(
                    incident_key, owner, kind='recovery'):
                return
        except Exception:
            return
        inc = {}
        try:
            inc = self.store.get_alert_incident(incident_key) or {}
        except Exception:
            pass
        body = (
            f"<html><body><p><b>{inc.get('source', incident_key)}</b> recovered"
            f"{(' — ' + summary) if summary else ''}. "
            f"Detections: {inc.get('detection_count', '?')}.</p>"
            f"<p>Incident: <code>{incident_key}</code></p></body></html>"
        )
        try:
            ok = bool(self.email.send_report(
                subject=f"[RESOLVED] {inc.get('source', incident_key)}", body=body))
        except Exception:
            ok = False
        try:
            self.store.complete_incident_notification(
                incident_key, owner, ok, kind='recovery')
        except Exception:
            pass

    # ── Monitoring checks ─────────────────────────────────────────────────

    def check(self, metrics: Dict[str, Any]) -> None:
        """Generate alerts from a monitor metrics snapshot."""
        sys = metrics.get('system', {})
        if sys.get('cpu_percent', 0) > 80:
            self.alert('WARNING', 'CPU', f"CPU usage {sys['cpu_percent']}%", sys,
                       incident_key='system:cpu')
        else:
            self.resolve_incident('system:cpu')
        if sys.get('memory_percent', 0) > 85:
            self.alert('WARNING', 'Memory', f"Memory usage {sys['memory_percent']}%", sys,
                       incident_key='system:memory')
        else:
            self.resolve_incident('system:memory')
        if sys.get('disk_percent', 0) > 85:
            self.alert('WARNING', 'Disk', f"Disk usage {sys['disk_percent']}%", sys,
                       incident_key='system:disk')
        else:
            self.resolve_incident('system:disk')

        sql = metrics.get('sqlite', {})
        if not sql.get('ok'):
            self.alert('CRITICAL', 'SQLite', 'SQLite health check failed', sql,
                       incident_key='sqlite:health')
        elif sql.get('response_ms', 0) > 200:
            self.alert('WARNING', 'SQLite', f"SQLite slow: {sql['response_ms']}ms", sql,
                       incident_key='sqlite:health')
        else:
            self.resolve_incident('sqlite:health')

        if metrics.get('api_latency_ms', 0) > 500:
            self.alert('WARNING', 'API', f"API latency {metrics['api_latency_ms']}ms", metrics,
                       incident_key='api:latency')
        else:
            self.resolve_incident('api:latency')
        if not metrics.get('kite', {}).get('ok', True) if isinstance(metrics.get('kite'), dict) else not metrics.get('kite', True):
            self.alert('CRITICAL', 'Kite', 'Kite API unavailable', metrics,
                       incident_key='kite:connectivity')
        else:
            self.resolve_incident('kite:connectivity')

        hb = metrics.get('scheduler_heartbeat', {})
        if not hb.get('ok', True):
            self.alert('CRITICAL', 'Scheduler', 'Scheduler heartbeat missing', hb,
                       incident_key='scheduler:heartbeat')
        else:
            self.resolve_incident('scheduler:heartbeat')

        rh = metrics.get('reconciliation', {})
        if not rh.get('ok', True):
            self.alert('CRITICAL', 'Reconciliation', 'Reconciliation health check failed', rh,
                       incident_key='reconciliation:health')
        else:
            self.resolve_incident('reconciliation:health')

    # ── Event-driven convenience alerts ───────────────────────────────────

    def info(self, source: str, message: str, data: Optional[Dict] = None) -> int:
        return self.alert('INFO', source, message, data)

    def warning(self, source: str, message: str, data: Optional[Dict] = None) -> int:
        return self.alert('WARNING', source, message, data)

    def critical(self, source: str, message: str, data: Optional[Dict] = None) -> int:
        return self.alert('CRITICAL', source, message, data)

    # ── Queue for dashboard ───────────────────────────────────────────────

    def pop_dashboard_alerts(self, limit: int = 20) -> List[Dict[str, Any]]:
        with self._lock:
            out = list(self._popup_queue[-limit:])
            self._popup_queue = []
            return out

    def get_unacknowledged(self, limit: int = 50) -> List[Dict[str, Any]]:
        try:
            if hasattr(self.store, 'get_system_alerts'):
                return self.store.get_system_alerts(limit=limit)
        except Exception:
            pass
        return []

    def acknowledge(self, alert_id: int) -> None:
        try:
            if hasattr(self.store, 'acknowledge_alert'):
                self.store.acknowledge_alert(alert_id)
        except Exception:
            pass

    # ── Daily summary ─────────────────────────────────────────────────────

    def daily_summary(self) -> Dict[str, Any]:
        today = date.today().isoformat()
        try:
            if hasattr(self.store, 'get_alert_counts'):
                counts = self.store.get_alert_counts(today)
            else:
                counts = {'CRITICAL': 0, 'WARNING': 0, 'INFO': 0}
        except Exception:
            counts = {'CRITICAL': 0, 'WARNING': 0, 'INFO': 0}
        return {
            'date': today,
            'counts': counts,
            'total': sum(counts.values()),
        }
