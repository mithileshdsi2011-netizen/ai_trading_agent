"""
Enterprise Monitoring & Alerting - Alert Engine.

Generates, stores and routes alerts.  Supports multiple notification
channels and prioritization.  Alerting is purely observational and does
not modify trading decisions.
"""
import json
import logging
import os
import subprocess
import threading
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
        self._lock = threading.Lock()

    # ── Core alert lifecycle ──────────────────────────────────────────────

    def alert(
        self,
        level: str,
        source: str,
        message: str,
        data: Optional[Dict[str, Any]] = None,
        notify: bool = True,
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

    def _to_email(self, alert: Dict[str, Any]) -> None:
        if self.email and hasattr(self.email, 'send_report'):
            try:
                body = f"<html><body><p><b>{alert['source']}</b> — {alert['message']}</p></body></html>"
                self.email.send_report(subject=f"[{alert['level']}] {alert['source']}", body=body)
            except Exception:
                pass

    # ── Monitoring checks ─────────────────────────────────────────────────

    def check(self, metrics: Dict[str, Any]) -> None:
        """Generate alerts from a monitor metrics snapshot."""
        sys = metrics.get('system', {})
        if sys.get('cpu_percent', 0) > 80:
            self.alert('WARNING', 'CPU', f"CPU usage {sys['cpu_percent']}%", sys)
        if sys.get('memory_percent', 0) > 85:
            self.alert('WARNING', 'Memory', f"Memory usage {sys['memory_percent']}%", sys)
        if sys.get('disk_percent', 0) > 85:
            self.alert('WARNING', 'Disk', f"Disk usage {sys['disk_percent']}%", sys)

        sql = metrics.get('sqlite', {})
        if not sql.get('ok'):
            self.alert('CRITICAL', 'SQLite', 'SQLite health check failed', sql)
        elif sql.get('response_ms', 0) > 200:
            self.alert('WARNING', 'SQLite', f"SQLite slow: {sql['response_ms']}ms", sql)

        if metrics.get('api_latency_ms', 0) > 500:
            self.alert('WARNING', 'API', f"API latency {metrics['api_latency_ms']}ms", metrics)
        if not metrics.get('internet', True):
            self.alert('CRITICAL', 'Internet', 'Internet unavailable', metrics)
        if not metrics.get('kite', {}).get('ok', True) if isinstance(metrics.get('kite'), dict) else not metrics.get('kite', True):
            self.alert('CRITICAL', 'Kite', 'Kite API unavailable', metrics)

        hb = metrics.get('scheduler_heartbeat', {})
        if not hb.get('ok', True):
            self.alert('CRITICAL', 'Scheduler', 'Scheduler heartbeat missing', hb)

        rh = metrics.get('reconciliation', {})
        if not rh.get('ok', True):
            self.alert('CRITICAL', 'Reconciliation', 'Reconciliation health check failed', rh)

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
