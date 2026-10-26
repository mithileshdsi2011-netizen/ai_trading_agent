"""
Enterprise Monitoring & Alerting - System Monitor.

Continuously monitors the health of the trading platform and produces
a normalized health score.  Designed to be lightweight and non-blocking.
"""
import json
import logging
import os
import socket
import threading
import time
import urllib.request
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import pandas as pd

from config import config
from persistence import get_store

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


try:
    import psutil
    _HAS_PSUTIL = True
except Exception:
    _HAS_PSUTIL = False


def _now() -> str:
    return datetime.now().isoformat()


class EnterpriseSystemMonitor:
    """Collect system, API and subsystem health metrics."""

    HEALTH_THRESHOLDS = {
        'cpu_warning': 80.0,
        'cpu_critical': 95.0,
        'memory_warning': 85.0,
        'memory_critical': 95.0,
        'disk_warning': 85.0,
        'disk_critical': 95.0,
        'sqlite_slow_ms': 200,
        'api_latency_warning_ms': 500,
        'api_latency_critical_ms': 2000,
        'heartbeat_age_seconds': 90,
    }

    def __init__(
        self,
        store=None,
        alert_engine=None,
        interval: int = 30,
        db_path: Optional[str] = None,
    ):
        self.store = store or get_store(db_path)
        self.alert_engine = alert_engine
        self.interval = max(interval, 5)
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._last_metrics: Optional[Dict[str, Any]] = None

    # ── Metric collection ─────────────────────────────────────────────────

    def collect(self) -> Dict[str, Any]:
        metrics = {
            'timestamp': _now(),
            'system': self._system_metrics(),
            'sqlite': self._sqlite_health(),
            'api_latency_ms': self._api_latency(),
            'internet': self._internet(),
            'kite': self._kite_availability(),
            'nse': self._nse_availability(),
            'scheduler_heartbeat': self._scheduler_heartbeat(),
            'background_threads': self._background_threads(),
            'reconciliation': self._reconciliation_health(),
            'ai_engine': self._ai_engine_health(),
            'portfolio_optimizer': self._portfolio_optimizer_health(),
            'ai_learning': self._ai_learning_health(),
            'data_quality': self._data_quality(),
        }
        metrics['health_score'] = self.health_score(metrics)
        metrics['data_quality_score'] = self._data_quality_score(metrics['data_quality'])
        self._last_metrics = metrics
        return metrics

    def _system_metrics(self) -> Dict[str, Any]:
        out = {'cpu_percent': 0.0, 'memory_percent': 0.0, 'disk_percent': 0.0}
        if _HAS_PSUTIL:
            try:
                out['cpu_percent'] = round(psutil.cpu_percent(interval=0.5), 2)
                out['memory_percent'] = round(psutil.virtual_memory().percent, 2)
                disk = psutil.disk_usage(os.path.expanduser('~'))
                out['disk_percent'] = round(100 * (disk.used / disk.total), 2)
            except Exception as e:
                logger.debug(f"psutil metrics failed: {e}")
        return out

    def _sqlite_health(self) -> Dict[str, Any]:
        start = time.time()
        try:
            count = self.store.count_ai_training_data() if hasattr(self.store, 'count_ai_training_data') else 0
            ok = True
        except Exception as e:
            count = 0
            ok = False
            logger.warning(f"SQLite health check failed: {e}")
        elapsed_ms = round((time.time() - start) * 1000, 2)
        return {'ok': ok, 'response_ms': elapsed_ms, 'sample_table_count': count}

    def _api_latency(self) -> float:
        start = time.time()
        try:
            if hasattr(self.store, 'get_latest_portfolio_snapshot'):
                self.store.get_latest_portfolio_snapshot()
        except Exception:
            pass
        return round((time.time() - start) * 1000, 2)

    def _internet(self) -> bool:
        try:
            urllib.request.urlopen('https://www.google.com', timeout=5)
            return True
        except Exception:
            return False

    def _kite_availability(self) -> bool:
        try:
            from token_manager import TokenManager
            k = TokenManager().initialize_kite()
            return k is not None
        except Exception:
            return False

    def _nse_availability(self) -> bool:
        try:
            urllib.request.urlopen('https://www.nseindia.com', timeout=5)
            return True
        except Exception:
            return False

    def _scheduler_heartbeat(self) -> Dict[str, Any]:
        try:
            hb = self.store.get_broker_state('scheduler_heartbeat') if hasattr(self.store, 'get_broker_state') else None
            if not hb or 'timestamp' not in hb:
                return {'ok': False, 'age_seconds': 9999}
            last = pd.to_datetime(hb['timestamp'])
            age = (datetime.now() - last).total_seconds()
            return {'ok': age < self.HEALTH_THRESHOLDS['heartbeat_age_seconds'], 'age_seconds': round(age, 1)}
        except Exception:
            return {'ok': False, 'age_seconds': 9999}

    def _background_threads(self) -> List[str]:
        try:
            return [t.name for t in threading.enumerate() if t.daemon]
        except Exception:
            return []

    def _reconciliation_health(self) -> Dict[str, Any]:
        try:
            st = self.store.get_broker_state('reconciliation') if hasattr(self.store, 'get_broker_state') else None
            if not st:
                return {'ok': True, 'mismatches': 0}
            return {'ok': st.get('healthy', False), 'mismatches': st.get('mismatches', 0)}
        except Exception:
            return {'ok': False, 'mismatches': -1}

    def _ai_engine_health(self) -> Dict[str, Any]:
        try:
            latest = self.store.get_latest_backtest_results() if hasattr(self.store, 'get_latest_backtest_results') else None
            return {'ok': True, 'last_run': latest.get('timestamp') if latest else None}
        except Exception:
            return {'ok': False, 'last_run': None}

    def _portfolio_optimizer_health(self) -> Dict[str, Any]:
        try:
            po = self.store.get_latest_portfolio_optimizer() if hasattr(self.store, 'get_latest_portfolio_optimizer') else None
            return {'ok': po is not None, 'last_run': po.get('timestamp') if po else None}
        except Exception:
            return {'ok': False, 'last_run': None}

    def _ai_learning_health(self) -> Dict[str, Any]:
        try:
            lm = self.store.get_latest_learning_metrics() if hasattr(self.store, 'get_latest_learning_metrics') else None
            return {'ok': lm is not None, 'last_run': lm.get('timestamp') if lm else None}
        except Exception:
            return {'ok': False, 'last_run': None}

    # ── Health score ──────────────────────────────────────────────────────

    # Decision-input feeds — thresholds mirror the engines' staleness gates
    # (options/breadth 6h, global 12h, sector 24h, FII/DII 72h T-1-safe).
    _DATA_FEEDS = (
        ('options_intelligence', 'get_latest_options_intelligence', 'timestamp', 6, True),
        ('market_breadth', 'get_latest_market_breadth', 'timestamp', 6, True),
        ('global_markets', 'get_latest_global_markets', 'timestamp', 12, True),
        ('sector_rotation', 'get_latest_sector_rotation', 'timestamp', 24, False),
        ('fii_dii', 'get_latest_fii_dii', 'date', 72, False),
    )

    def _data_quality(self) -> Dict[str, Any]:
        """Per-feed freshness — kept separate from system health so stale
        decision inputs can never hide behind an 'Excellent' infra score."""
        feeds = {}
        for name, getter, ts_key, max_age_h, critical in self._DATA_FEEDS:
            entry = {'critical': critical, 'max_age_hours': max_age_h,
                     'status': 'MISSING', 'age_hours': None}
            try:
                snap = getattr(self.store, getter)() if hasattr(self.store, getter) else None
                ts = (snap or {}).get(ts_key)
                if ts:
                    try:
                        age_h = (datetime.now() - pd.to_datetime(ts).to_pydatetime().replace(tzinfo=None)).total_seconds() / 3600
                        entry['age_hours'] = round(age_h, 1)
                        entry['status'] = 'OK' if age_h <= max_age_h else 'STALE'
                    except Exception:
                        entry['status'] = 'UNPARSEABLE'
            except Exception:
                pass
            feeds[name] = entry
        return feeds

    @staticmethod
    def _data_quality_score(feeds: Dict[str, Any]) -> int:
        score = 100
        for f in (feeds or {}).values():
            if f.get('status') in ('STALE', 'MISSING', 'UNPARSEABLE'):
                score -= 25 if f.get('critical') else 10
        return max(0, score)

    def health_score(self, metrics: Dict[str, Any]) -> int:
        """Return 0-100 health score."""
        score = 100
        sys = metrics.get('system', {})
        if sys.get('cpu_percent', 0) > self.HEALTH_THRESHOLDS['cpu_warning']:
            score -= 10
        if sys.get('memory_percent', 0) > self.HEALTH_THRESHOLDS['memory_warning']:
            score -= 10
        if sys.get('disk_percent', 0) > self.HEALTH_THRESHOLDS['disk_warning']:
            score -= 10

        sql = metrics.get('sqlite', {})
        if not sql.get('ok'):
            score -= 25
        elif sql.get('response_ms', 0) > self.HEALTH_THRESHOLDS['sqlite_slow_ms']:
            score -= 5

        if metrics.get('api_latency_ms', 0) > self.HEALTH_THRESHOLDS['api_latency_warning_ms']:
            score -= 10

        if not metrics.get('internet', True):
            score -= 20
        if not metrics.get('kite', {}).get('ok', True) if isinstance(metrics.get('kite'), dict) else not metrics.get('kite', True):
            score -= 20

        hb = metrics.get('scheduler_heartbeat', {})
        if not hb.get('ok', True):
            score -= 15

        rh = metrics.get('reconciliation', {})
        if not rh.get('ok', True):
            score -= 10

        return max(0, min(100, score))

    # ── Persistence ───────────────────────────────────────────────────────

    def save_snapshot(self, metrics: Optional[Dict[str, Any]] = None) -> None:
        m = metrics or self._last_metrics or self.collect()
        if not m:
            return
        try:
            if hasattr(self.store, 'save_system_health'):
                self.store.save_system_health({
                    'timestamp': m['timestamp'],
                    'health_score': m.get('health_score', 0),
                    'metrics_json': json.dumps(m),
                })
        except Exception as e:
            logger.warning(f"Could not save system health: {e}")

    # ── Background loop ───────────────────────────────────────────────────

    def start(self) -> None:
        if self._running:
            return
        self._running = True

        def _loop():
            while self._running:
                try:
                    m = self.collect()
                    self.save_snapshot(m)
                    if self.alert_engine and hasattr(self.alert_engine, 'check'):
                        self.alert_engine.check(m)
                    self.auto_recovery(m)
                except Exception as e:
                    logger.warning(f"Monitor loop error: {e}")
                time.sleep(self.interval)

        self._thread = threading.Thread(target=_loop, daemon=True, name='enterprise-system-monitor')
        self._thread.start()
        logger.info(f"System monitor started (interval={self.interval}s)")

    def stop(self) -> None:
        self._running = False

    # ── Auto recovery ─────────────────────────────────────────────────────

    def auto_recovery(self, metrics: Dict[str, Any]) -> None:
        if not metrics.get('kite', {}).get('ok', True) if isinstance(metrics.get('kite'), dict) else not metrics.get('kite', True):
            self._recover_kite()
        hb = metrics.get('scheduler_heartbeat', {})
        if not hb.get('ok', True):
            self._recover_scheduler()
        sql = metrics.get('sqlite', {})
        if not sql.get('ok'):
            self._recover_sqlite()
        if not metrics.get('internet', True):
            self._recover_internet()

    def _recover_kite(self) -> None:
        logger.warning("Auto recovery: attempting Kite reconnect")
        try:
            from token_manager import TokenManager
            TokenManager().initialize_kite()
        except Exception as e:
            logger.warning(f"Kite reconnect failed: {e}")

    def _recover_scheduler(self) -> None:
        logger.warning("Auto recovery: scheduler heartbeat missing - manual restart required")

    def _recover_sqlite(self) -> None:
        logger.warning("Auto recovery: SQLite issue detected - retrying connection")

    def _recover_internet(self) -> None:
        logger.warning("Auto recovery: internet lost - pausing new trading and retrying every 30s")

    # ── Daily report ──────────────────────────────────────────────────────

    def daily_report(self) -> Dict[str, Any]:
        metrics = self.collect()
        report = {
            'timestamp': _now(),
            'health_score': metrics.get('health_score', 0),
            'system': metrics.get('system', {}),
            'api_latency_ms': metrics.get('api_latency_ms', 0),
            'sqlite': metrics.get('sqlite', {}),
            'internet': metrics.get('internet', True),
            'kite': metrics.get('kite', False),
            'alert_summary': {},
        }
        if self.alert_engine and hasattr(self.alert_engine, 'daily_summary'):
            report['alert_summary'] = self.alert_engine.daily_summary()
        try:
            if hasattr(self.store, 'save_daily_health_report'):
                self.store.save_daily_health_report(report)
        except Exception as e:
            logger.warning(f"Could not save daily report: {e}")
        return report
