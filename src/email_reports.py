"""
Email Trade Reports
Sends daily and weekly email summaries of trading activity.
"""
import html as _html
import json
import logging
import os
import smtplib
import socket
import uuid
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime, date, timedelta, time as dtime
from typing import Dict, List, Optional, Set

from config import config, data_path

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class EmailReporter:
    """Sends email trading reports"""

    # Class-level dedup: maps (date_str, subject_prefix) -> bool
    # Shared across all instances so restarts within the same day also dedup.
    _sent_today: dict = {}
    
    # Separate cooldown dict for rate limiting (not pruned by date)
    _cooldowns: dict = {}

    def __init__(self):
        self.enabled = config.EMAIL_ENABLED
        self.smtp_server = config.EMAIL_SMTP_SERVER
        self.smtp_port = config.EMAIL_SMTP_PORT
        self.username = config.EMAIL_USERNAME
        self.password = config.EMAIL_PASSWORD
        self.to_email = config.EMAIL_TO

    def _dedup_key(self, subject: str) -> str:
        """Return a dedup key: today's date + first 60 chars of subject."""
        return f"{date.today().isoformat()}|{subject[:60]}"

    def _already_sent(self, subject: str) -> bool:
        """Return True if this subject was already sent today."""
        return EmailReporter._sent_today.get(self._dedup_key(subject), False)

    def _mark_sent(self, subject: str):
        """Record that this subject was sent today."""
        EmailReporter._sent_today[self._dedup_key(subject)] = True
        # Prune old dates to avoid unbounded growth
        today = date.today().isoformat()
        EmailReporter._sent_today = {
            k: v for k, v in EmailReporter._sent_today.items() if k.startswith(today)
        }

    def _incident_store(self):
        """Shared persistent store for cross-process incident dedup.
        Returns None when unavailable — callers fall back to in-memory dedup."""
        try:
            from persistence import get_store
            store = get_store()
            return store if hasattr(store, 'upsert_alert_incident') else None
        except Exception:
            return None

    def _deliver(self, subject: str, body: str, text: Optional[str] = None) -> bool:
        """One SMTP delivery attempt.  True = relay accepted the message."""
        try:
            msg = MIMEMultipart("alternative")
            msg["From"] = self.username
            msg["To"] = self.to_email
            msg["Subject"] = subject
            if text:
                msg.attach(MIMEText(text, "plain"))
            msg.attach(MIMEText(body, "html"))

            # timeout is mandatory: a stalled SMTP socket once froze
            # run_once mid-alert and silently killed the trading scheduler.
            with smtplib.SMTP(self.smtp_server, self.smtp_port, timeout=15) as server:
                server.starttls()
                server.login(self.username, self.password)
                server.send_message(msg)

            logger.info(f"Email report sent: {subject}")
            return True
        except Exception as e:
            logger.error(f"Email report failed: {e}")
            return False

    def send_report(self, subject: str, body: str,
                    incident_key: Optional[str] = None,
                    text: Optional[str] = None) -> bool:
        if not self.enabled or not self.username or not self.password or not self.to_email:
            logger.info("Email reports disabled or not configured")
            return False

        # Persistent incident dedup — survives restarts and coordinates
        # across dashboard/orchestrator processes via the shared store.
        # Default key keeps the existing semantics: same-day, same-subject
        # dedup; callers may pass a stable cross-path incident key instead.
        store = self._incident_store()
        if store is not None:
            incident_key = incident_key or f"email:{date.today().isoformat()}:{subject[:60]}"
            try:
                res = store.upsert_alert_incident(incident_key, 'EmailReporter', 'INFO')
                if (res['event'] == 'repeat'
                        and res['incident'].get('notify_state') != 'FAILED'):
                    logger.info(f"Email already notified for incident, skipping: {subject[:50]}")
                    return False
                owner = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"
                if not store.claim_incident_notification(incident_key, owner):
                    return False
                ok = self._deliver(subject, body, text)
                store.complete_incident_notification(incident_key, owner, ok)
                return ok
            except Exception as e:
                logger.warning(f"Incident dedup unavailable ({e}); using in-memory dedup")

        # In-memory fallback (also used when the store is unreachable)
        cooldown_key = f"cooldown_{subject[:50]}"  # Use first 50 chars of subject as key
        now = datetime.now()
        last_sent = EmailReporter._cooldowns.get(cooldown_key)

        if last_sent and (now - last_sent).total_seconds() < 86400:  # 24 hour cooldown (1 day)
            logger.info(f"Email cooldown active for subject prefix, skipping duplicate: {subject[:50]}")
            return False

        if self._already_sent(subject):
            logger.info(f"Email already sent today, skipping duplicate: {subject}")
            return False

        if self._deliver(subject, body, text):
            self._mark_sent(subject)
            EmailReporter._cooldowns[cooldown_key] = now
            return True
        return False

    @staticmethod
    def _esc(value) -> str:
        return _html.escape(str(value))

    def _swing_engine_health(self) -> Dict[str, str]:
        """Reconciliation + scheduler heartbeat from the persisted broker
        records — the authoritative EOD health snapshot."""
        out = {'reconciliation': 'Unavailable', 'engine': 'Unavailable'}
        try:
            from persistence import get_store
            store = get_store()
            rec = store.get_broker_state('reconciliation') or {}
            if rec:
                out['reconciliation'] = (
                    f"{'Healthy' if rec.get('healthy') else 'ATTENTION'} — "
                    f"{rec.get('mismatches', 0)} mismatch(es), "
                    f"last sync {str(rec.get('last_sync', '—'))[:19]}")
            hb = store.get_broker_state('scheduler_heartbeat') or {}
            if hb:
                out['engine'] = (
                    f"Cycle {hb.get('last_cycle_status', '—')} — "
                    f"last completed {str(hb.get('last_cycle_completed', '—'))[:19]}")
        except Exception:
            pass
        return out

    # ── Consolidated EOD coordinator ────────────────────────────────
    # Exactly one daily email covers both workspaces.  Each workspace
    # publishes its EOD snapshot as an atomic JSON file under
    # data/eod_reports/ — orchestrator and dashboard share the same
    # filesystem, so no schema change is needed for readiness state.
    # Send dedup/claim/retry reuses the alert_incidents machinery in
    # send_report() via the swingpulse:consolidated_eod:{date} key —
    # atomic across processes, retryable on FAILED, and reclaimable
    # after a crashed sender's lease expires.
    CONSOLIDATED_DEADLINE = dtime(16, 0)      # hard bound on waiting
    CONSOLIDATED_RETRY_UNTIL = dtime(23, 30)  # last same-day retry

    def _eod_dir(self) -> str:
        return data_path('eod_reports')

    def _record_ready(self, workspace: str, trading_date: str, snapshot: Dict) -> None:
        """Atomically publish a workspace EOD snapshot for the peer
        process; prunes files older than a week."""
        try:
            d = self._eod_dir()
            os.makedirs(d, exist_ok=True)
            cutoff = (date.today() - timedelta(days=7)).isoformat()
            for fn in os.listdir(d):
                if fn[:10] < cutoff:
                    try:
                        os.remove(os.path.join(d, fn))
                    except OSError:
                        pass
            path = os.path.join(d, f"{trading_date}.{workspace}.json")
            tmp = f"{path}.tmp.{os.getpid()}"
            with open(tmp, 'w') as f:
                json.dump(snapshot, f)
            os.replace(tmp, path)
        except Exception as e:
            logger.warning(f"EOD snapshot publish failed ({workspace}): {e}")

    def _load_ready(self, workspace: str, trading_date: str) -> Optional[Dict]:
        try:
            with open(os.path.join(self._eod_dir(),
                                   f"{trading_date}.{workspace}.json")) as f:
                snap = json.load(f)
            return snap if isinstance(snap, dict) else None
        except Exception:
            return None

    def consolidated_pending(self, now: Optional[datetime] = None) -> bool:
        """True inside the same-day window where a consolidated send is
        still plausible — lets retry ticks no-op cheaply outside it."""
        now = now or datetime.now()
        return dtime(15, 0) <= now.time() <= self.CONSOLIDATED_RETRY_UNTIL

    def attempt_consolidated(self, now: Optional[datetime] = None) -> bool:
        """Send the single consolidated EOD email when ready.

        Sends when both workspace snapshots exist, or once the hard
        deadline passes with missing sections labelled INCOMPLETE.
        Dedup, cross-process claiming and retry all live in
        send_report() under the consolidated incident key — repeated
        calls and concurrent processes are safe."""
        now = now or datetime.now()
        try:
            from trading_calendar import is_trading_day
            if not is_trading_day(now):
                return False
        except Exception:
            return False  # fail-closed — never email on unknown calendar
        trading_date = now.strftime('%Y-%m-%d')
        swing = self._load_ready('swing', trading_date)
        intraday = self._load_ready('intraday', trading_date)
        if swing is None and intraday is None:
            # Nothing was published today — either both engines are down
            # or the day predates this feature.  Never send an empty /
            # all-INCOMPLETE email.
            return False
        missing = [label for label, snap in
                   (('Swing LIVE', swing), ('Intraday PAPER', intraday))
                   if snap is None]
        if missing and now.time() < self.CONSOLIDATED_DEADLINE:
            logger.info(f"Consolidated EOD report waiting on: {', '.join(missing)}")
            return False
        date_disp = (swing or intraday or {}).get('date') or now.strftime('%d %b %Y')
        subject = f"SWINGPULSE TERMINAL | Daily Trading Report | {date_disp}"
        body, text = self._consolidated_body(swing, intraday, date_disp, now)
        return self.send_report(
            subject, body, text=text,
            incident_key=f"swingpulse:consolidated_eod:{trading_date}")

    def _consolidated_body(self, swing, intraday, date_disp, now):
        """Compose the unified daily email: executive summary, each
        workspace section (or an INCOMPLETE panel), then EOD status."""
        time_str = now.strftime('%I:%M %p IST')

        def _summary_row(label, snap, extra_fn):
            if snap is None:
                return (f'<tr><td class="metric">{label}</td>'
                        '<td style="color:#b45307"><b>INCOMPLETE</b> — '
                        'EOD report not received by deadline</td></tr>')
            net = float(snap.get('net', 0) or 0)
            col = 'green' if net >= 0 else 'red'
            return (f'<tr><td class="metric">{label}</td>'
                    f"<td style='color:{col}'><b>₹{net:,.2f}</b>{extra_fn(snap)}</td></tr>")

        def _incomplete(label):
            return f"""
            <div style="margin-top:26px;border:1px dashed #d97706;border-radius:6px;
                        padding:12px 16px;background:#fffbeb">
                <h2 style="color:#92400e;margin:0;font-size:17px">{label}</h2>
                <p style="font-size:13px;color:#92400e;margin:8px 0 0">
                    <b>INCOMPLETE</b> — this workspace's EOD report was not
                    received by {self.CONSOLIDATED_DEADLINE.strftime('%I:%M %p')} IST.
                    Likely cause: engine offline or still processing.</p>
            </div>"""

        status_rows = ""
        for label, snap in (('Swing LIVE', swing), ('Intraday PAPER', intraday)):
            if snap is None:
                status_rows += (f'<tr><td class="metric">{label}</td>'
                                '<td style="color:#b45307">INCOMPLETE — '
                                'snapshot missing at deadline</td></tr>')
            else:
                eod = snap.get('eod_completed_at')
                detail = f" · EOD completed {self._esc(eod)}" if eod else ""
                status_rows += (f'<tr><td class="metric">{label}</td>'
                                f"<td>READY — report generated "
                                f"{self._esc(snap.get('generated_at', '—'))}{detail}</td></tr>")

        alert_items = ""
        for snap in (swing, intraday):
            if not snap:
                continue
            for note in ([snap.get('activity_note')] if snap.get('activity_note')
                         else []) + list(snap.get('notes') or []):
                alert_items += f'<li style="margin:4px 0">{self._esc(note)}</li>'
        alerts_html = (f'<ul style="font-size:13px;margin:6px 0">{alert_items}</ul>'
                       if alert_items
                       else '<p style="font-size:13px;color:#555">None.</p>')

        body = f"""
        <html>
        <head>
            <style>
                body {{ font-family: Arial, sans-serif; color: #333; }}
                h2 {{ color: #1a5276; }}
                h3 {{ color: #1a5276; margin-top: 24px; }}
                table {{ border-collapse: collapse; width: 100%; margin-top: 8px; }}
                th, td {{ border: 1px solid #ddd; padding: 8px; text-align: left; font-size: 13px; }}
                th {{ background-color: #f2f3f4; font-weight: bold; }}
                .metric {{ font-weight: bold; width: 45%; }}
                .section {{ margin-bottom: 24px; }}
            </style>
        </head>
        <body>
            <div style="background:#111827;color:#f9fafb;padding:14px 18px;border-radius:6px">
                <div style="font-size:18px;font-weight:800;letter-spacing:.04em">SWINGPULSE TERMINAL</div>
                <div style="font-size:13px;font-weight:600;margin-top:4px">Daily Trading Report</div>
                <div style="font-size:11px;color:#9ca3af;margin-top:2px">Swing LIVE (Zerodha Kite) + Intraday PAPER (Angel One)</div>
            </div>
            <p><strong>Date:</strong> {self._esc(date_disp)}<br>
               <strong>Generated:</strong> {time_str}</p>

            <div class="section">
                <h3>📋 Executive Summary</h3>
                <table>
                    <tr><th>Workspace</th><th>Today's Net P&amp;L</th></tr>
                    {_summary_row('Swing LIVE (Zerodha Kite)', swing,
                                 lambda s: f" · Regime {self._esc(s.get('regime', '—'))} · Orders {s.get('orders_today', 0)}")}
                    {_summary_row('Intraday PAPER (Angel One)', intraday,
                                 lambda s: f" · Entries {s.get('entries', '—')}/{s.get('max_trades', '—')} · <b>SIMULATED</b>")}
                </table>
                <p style="font-size:11px;color:#888">P&amp;L figures are reported
                per workspace and never combined — Intraday PAPER results are
                simulated, not real profit or loss.</p>
            </div>

            {swing['html'] if swing else _incomplete('Swing LIVE — Zerodha Kite')}
            {intraday['html'] if intraday else _incomplete('Intraday PAPER — Angel One')}

            <div class="section">
                <h3>⚠️ EOD Status &amp; Alerts</h3>
                <table>{status_rows}</table>
                {alerts_html}
            </div>
        </body>
        </html>
        """

        lines = [f"SWINGPULSE TERMINAL — Daily Trading Report",
                 f"Date: {date_disp} · Generated: {time_str}", ""]
        for label, snap in (('Swing LIVE (Kite)', swing),
                            ('Intraday PAPER (Angel)', intraday)):
            if snap is None:
                lines.append(f"{label}: INCOMPLETE — EOD report not received")
            else:
                sim = " [SIMULATED]" if snap.get('workspace') == 'intraday' else ""
                lines.append(f"{label}: net ₹{float(snap.get('net', 0) or 0):,.2f}{sim}")
        lines.append("")
        lines.append((swing or {}).get('text') or 'Swing LIVE: INCOMPLETE')
        lines.append("")
        lines.append((intraday or {}).get('text') or 'Intraday PAPER: INCOMPLETE')
        return body, "\n".join(lines)

    def daily_report(self, summary: Dict, positions: List[Dict], orders: List[Dict]) -> bool:
        """Swing LIVE EOD snapshot → consolidated coordinator.

        Publishes this workspace's readiness and lets the coordinator
        send the single daily email once both workspaces are ready (or
        the bounded wait expires). Never sends a standalone email."""
        now = datetime.now()
        date_str = now.strftime("%d %b %Y")
        time_str = now.strftime("%I:%M %p IST")
        regime = str(summary.get('market_regime', 'UNKNOWN') or 'UNKNOWN')
        es = summary.get('execution_summary', {})
        ps = es.get('position_summary', {})

        # ── Portfolio metrics ───────────────────────────────────────────────
        cash = float(summary.get('cash', 0))
        invested = float(summary.get('invested', 0))
        portfolio_value = float(summary.get('total_value', cash + invested))
        daily_gross = float(summary.get('daily_pnl', 0))
        charges = float(ps.get('total_charges', 0))
        daily_net = daily_gross - charges

        # Unrealized / realized P&L from open positions and summary
        unrealized = 0.0
        for p in positions:
            qty = int(p.get('quantity', 0) or 0)
            avg = float(p.get('average_price', 0) or 0)
            ltp = float(p.get('last_price', 0) or 0)
            if qty and ltp and avg:
                unrealized += (ltp - avg) * qty
        realized = float(ps.get('realized_pnl', ps.get('total_pnl', 0))) - unrealized

        overall_return = 0.0
        base = portfolio_value - daily_gross
        if base > 0:
            overall_return = daily_gross / base * 100

        open_count = int(summary.get('open_positions', 0))

        # Count orders completed today
        today_str = now.strftime("%Y-%m-%d")
        closed_today = 0
        for o in orders:
            ts = o.get('exchange_timestamp') or o.get('order_timestamp') or ''
            if isinstance(ts, datetime):
                ts = ts.isoformat()
            if str(ts).startswith(today_str) and o.get('status') == 'COMPLETE':
                closed_today += 1
        # Fallback to closed positions count if available
        if not closed_today:
            closed_today = int(ps.get('closed_positions', 0))

        buying_capacity = cash * float(getattr(config, 'MAX_CAPITAL_USAGE', 0.87))

        # BUY permission — same regime rule the orchestrator enforces
        _reg = regime.upper()
        if _reg == 'BEAR':
            buy_perm = 'NO — BEAR regime (new BUYs blocked, exits only)'
        elif getattr(config, 'VOLATILE_BLOCK_BUYS', False) and _reg == 'VOLATILE':
            buy_perm = 'NO — VOLATILE regime'
        elif _reg in ('UNAVAILABLE', 'UNKNOWN'):
            buy_perm = 'NO — regime unavailable (fail-closed)'
        else:
            buy_perm = 'YES'

        # ── Trading activity rows (today's completed orders) ──────────────────
        activity_rows = ""
        for o in orders:
            ts = o.get('exchange_timestamp') or o.get('order_timestamp') or ''
            if isinstance(ts, datetime):
                ts = ts.isoformat()
            if not str(ts).startswith(today_str):
                continue
            sym = self._esc(o.get('tradingsymbol', o.get('symbol', '')))
            action = self._esc(o.get('transaction_type', ''))
            qty = int(o.get('quantity', 0) or 0)
            price = float(o.get('average_price', 0) or o.get('price', 0) or 0)
            status = '✅ Completed' if o.get('status') == 'COMPLETE' else self._esc(o.get('status', ''))
            reason = self._esc(o.get('status_message') or o.get('tag') or '—')
            activity_rows += f"""
                <tr>
                    <td>{sym}</td>
                    <td>{action}</td>
                    <td>{qty}</td>
                    <td>₹{price:,.2f}</td>
                    <td>{status}</td>
                    <td>{reason}</td>
                </tr>
            """
        if not activity_rows:
            activity_rows = '<tr><td colspan="6" style="text-align:center;">No trades today</td></tr>'

        # ── Current holdings rows ─────────────────────────────────────────────
        holdings_rows = ""
        for p in positions:
            sym = self._esc(p.get('tradingsymbol', p.get('symbol', '')))
            qty = int(p.get('quantity', 0) or 0)
            avg = float(p.get('average_price', 0) or 0)
            ltp = float(p.get('last_price', 0) or 0)
            day_pnl = float(p.get('day_pnl', 0))
            total_pnl = float(p.get('pnl', 0))
            ret = 0.0
            if avg and ltp and avg > 0:
                ret = (ltp - avg) / avg * 100
            sl = p.get('stop_loss')
            target = p.get('target')
            status = self._esc(p.get('status', 'HOLD'))
            holdings_rows += f"""
                <tr>
                    <td>{sym}</td>
                    <td>{qty}</td>
                    <td>₹{avg:,.2f}</td>
                    <td>₹{ltp:,.2f}</td>
                    <td style='color:{"green" if day_pnl >= 0 else "red"}'>₹{day_pnl:,.2f}</td>
                    <td style='color:{"green" if total_pnl >= 0 else "red"}'>₹{total_pnl:,.2f}</td>
                    <td style='color:{"green" if ret >= 0 else "red"}'>{ret:.2f}%</td>
                    <td>{f"₹{sl:,.2f}" if sl else "—"}</td>
                    <td>{f"₹{target:,.2f}" if target else "—"}</td>
                    <td>{status}</td>
                </tr>
            """
        if not holdings_rows:
            holdings_rows = '<tr><td colspan="10" style="text-align:center;">No open positions</td></tr>'

        pnl_color = 'green' if daily_net >= 0 else 'red'
        gross_color = 'green' if daily_gross >= 0 else 'red'
        unreal_color = 'green' if unrealized >= 0 else 'red'
        real_color = 'green' if realized >= 0 else 'red'

        # No-trade explanation — why the day produced zero orders
        activity_note = ''
        if not closed_today and open_count == 0:
            if buy_perm.startswith('NO'):
                activity_note = f'No trades — BUY permission blocked ({self._esc(buy_perm)}).'
            else:
                activity_note = 'No trades — no signal passed the entry gates today.'
        health = self._swing_engine_health()

        # Workspace fragment — the consolidated email supplies the
        # document wrapper, brand header and executive summary.
        fragment = f"""
            <div style="margin-top:26px;border-top:3px solid #1a5276;padding-top:8px">
                <h2 style="color:#1a5276;margin:0;font-size:17px">Swing LIVE — Zerodha Kite
                    <span style="font-size:11px;color:#059669;font-weight:700"> ● LIVE</span></h2>
            </div>
            <div class="section">
                <h3>📊 Portfolio Summary</h3>
                <table>
                    <tr><td class="metric">Portfolio Value</td><td>₹{portfolio_value:,.2f}</td></tr>
                    <tr><td class="metric">Available Cash</td><td>₹{cash:,.2f}</td></tr>
                    <tr><td class="metric">Invested Capital</td><td>₹{invested:,.2f}</td></tr>
                    <tr><td class="metric">Today's Gross P&L</td><td style='color:{gross_color}'>₹{daily_gross:,.2f}</td></tr>
                    <tr><td class="metric">Today's Charges</td><td style='color:red'>₹{charges:,.2f}</td></tr>
                    <tr><td class="metric">Today's Net P&L</td><td style='color:{pnl_color}'><b>₹{daily_net:,.2f}</b></td></tr>
                    <tr><td class="metric">Total Unrealized P&L</td><td style='color:{unreal_color}'>₹{unrealized:,.2f}</td></tr>
                    <tr><td class="metric">Total Realized P&L</td><td style='color:{real_color}'>₹{realized:,.2f}</td></tr>
                    <tr><td class="metric">Overall Portfolio Return (today)</td><td>{overall_return:.2f}%</td></tr>
                    <tr><td class="metric">Market Regime</td><td>{self._esc(regime)}</td></tr>
                    <tr><td class="metric">BUY Permission</td><td>{self._esc(buy_perm)}</td></tr>
                    <tr><td class="metric">Open Positions</td><td>{open_count}</td></tr>
                    <tr><td class="metric">Closed Today</td><td>{closed_today}</td></tr>
                    <tr><td class="metric">Available Buying Capacity</td><td>₹{buying_capacity:,.2f}</td></tr>
                </table>
            </div>

            <div class="section">
                <h3>🎯 Today's Trading Activity</h3>
                {f'<p style="font-size:13px;color:#555">{activity_note}</p>' if activity_note else ''}
                <table>
                    <tr>
                        <th>Symbol</th><th>Action</th><th>Qty</th><th>Price</th><th>Status</th><th>Reason</th>
                    </tr>
                    {activity_rows}
                </table>
            </div>

            <div class="section">
                <h3>💼 Current Holdings</h3>
                <table>
                    <tr>
                        <th>Symbol</th><th>Qty</th><th>Avg Price</th><th>LTP</th><th>Day P&L</th>
                        <th>Total P&L</th><th>Return</th><th>Stop Loss</th><th>Target</th><th>Status</th>
                    </tr>
                    {holdings_rows}
                </table>
            </div>

            <div class="section">
                <h3>🩺 Engine Health (EOD)</h3>
                <table>
                    <tr><td class="metric">Broker Reconciliation</td><td>{self._esc(health['reconciliation'])}</td></tr>
                    <tr><td class="metric">Engine Cycle</td><td>{self._esc(health['engine'])}</td></tr>
                </table>
            </div>
        """

        text = (
            f"Swing LIVE — Zerodha Kite\n"
            f"Portfolio: ₹{portfolio_value:,.2f} (cash ₹{cash:,.2f}, invested ₹{invested:,.2f})\n"
            f"Today: gross ₹{daily_gross:,.2f} · charges ₹{charges:,.2f} · net ₹{daily_net:,.2f}\n"
            f"Unrealized ₹{unrealized:,.2f} · Realized ₹{realized:,.2f}\n"
            f"Regime: {regime} · BUY permission: {buy_perm}\n"
            f"Orders completed today: {closed_today} · Open positions: {open_count}\n"
            f"{activity_note or ''}\n"
            f"Reconciliation: {health['reconciliation']}\n"
            f"Engine: {health['engine']}\n"
        )
        snapshot = {
            'workspace': 'swing',
            'trading_date': now.strftime('%Y-%m-%d'),
            'date': date_str, 'generated_at': time_str,
            'html': fragment, 'text': text,
            'gross': daily_gross, 'charges': charges, 'net': daily_net,
            'regime': regime, 'buy_permission': buy_perm,
            'orders_today': closed_today, 'open_positions': open_count,
            'activity_note': activity_note, 'health': health,
        }
        self._record_ready('swing', snapshot['trading_date'], snapshot)
        return self.attempt_consolidated()

    def intraday_daily_report(self, ctx: Dict) -> bool:
        """Intraday Angel PAPER end-of-day report. `ctx` is assembled by the
        intraday engine at EOD completion — trades, risk state and health
        all come from persisted records, never recomputed here."""
        now = datetime.now()
        date_str = ctx.get('date') or now.strftime("%d %b %Y")
        time_str = ctx.get('generated_at') or now.strftime("%I:%M %p IST")
        trades = ctx.get('trades') or []
        open_pos = ctx.get('open_positions') or []
        entries = int(ctx.get('entries_today', len(trades)))
        max_trades = ctx.get('max_trades', '—')
        gross = float(ctx.get('gross_pnl', 0) or 0)
        charges = float(ctx.get('charges', 0) or 0)
        net = float(ctx.get('net_pnl', 0) or 0)
        wins = int(ctx.get('wins', 0) or 0)
        losses = int(ctx.get('losses', 0) or 0)
        win_rate = ctx.get('win_rate')
        capital = ctx.get('capital', '—')
        auth = self._esc(ctx.get('auth_status', '—'))
        mdata = self._esc(ctx.get('market_data_status', '—'))
        eod_at = self._esc(ctx.get('eod_completed_at', '—'))
        eod_pending = ctx.get('eod_pending_positions', 0)

        pnl_color = 'green' if net >= 0 else 'red'
        gross_color = 'green' if gross >= 0 else 'red'

        trade_rows = ""
        for t in trades:
            tnet = float(t.get('net_pnl', 0) or 0)
            tcol = 'green' if tnet >= 0 else 'red'
            trade_rows += f"""
                <tr>
                    <td>{self._esc(t.get('symbol'))}</td>
                    <td>{self._esc(t.get('direction'))}</td>
                    <td>{int(t.get('quantity', 0) or 0)}</td>
                    <td>₹{float(t.get('entry_price', 0) or 0):,.2f}</td>
                    <td>₹{float(t.get('exit_price', 0) or 0):,.2f}</td>
                    <td>₹{float(t.get('gross_pnl', 0) or 0):,.2f}</td>
                    <td>₹{float(t.get('charges', 0) or 0):,.2f}</td>
                    <td style='color:{tcol}'>₹{tnet:,.2f}</td>
                    <td>{self._esc(t.get('exit_reason'))}</td>
                    <td>{self._esc(t.get('strategy') or t.get('signal_classification') or '—')}</td>
                </tr>
            """
        if not trade_rows:
            trade_rows = '<tr><td colspan="10" style="text-align:center;">No trades today</td></tr>'

        pos_rows = ""
        for p in open_pos:
            pos_rows += f"""
                <tr>
                    <td>{self._esc(p.get('symbol'))}</td>
                    <td>{int(p.get('quantity', 0) or 0)}</td>
                    <td>₹{float(p.get('entry_price', 0) or 0):,.2f}</td>
                    <td>{f"₹{float(p.get('stop_loss')):,.2f}" if p.get('stop_loss') else '—'}</td>
                    <td>{f"₹{float(p.get('target')):,.2f}" if p.get('target') else '—'}</td>
                </tr>
            """
        if not pos_rows:
            pos_rows = '<tr><td colspan="5" style="text-align:center;">None — all positions closed</td></tr>'

        strat_rows = ""
        for name, cnt in sorted((ctx.get('strategy_breakdown') or {}).items()):
            strat_rows += f"<tr><td>{self._esc(name)}</td><td>{int(cnt)}</td></tr>"
        if not strat_rows:
            strat_rows = '<tr><td colspan="2" style="text-align:center;">—</td></tr>'

        notes = "".join(
            f"<li style='margin:4px 0'>{self._esc(n)}</li>"
            for n in (ctx.get('notes') or []))

        fragment = f"""
            <div style="margin-top:26px;border-top:3px solid #b45307;padding-top:8px">
                <h2 style="color:#b45307;margin:0;font-size:17px">Intraday PAPER — Angel One
                    <span style="font-size:11px;color:#92400e;font-weight:700"> ● PAPER SIMULATION</span></h2>
            </div>
            <div style="background:#fef3c7;border:1px solid #f59e0b;border-radius:6px;
                        padding:8px 14px;margin-top:12px;font-size:13px;font-weight:700;color:#92400e">
                PAPER TRADING — SIMULATED RESULTS, NOT REAL PROFIT OR LOSS.
            </div>

            <div class="section">
                <h3>📊 Daily Summary</h3>
                <table>
                    <tr><td class="metric">PAPER Capital</td><td>{self._esc(capital)}</td></tr>
                    <tr><td class="metric">Today's Gross P&L</td><td style='color:{gross_color}'>₹{gross:,.2f}</td></tr>
                    <tr><td class="metric">Today's Charges (verified)</td><td style='color:red'>₹{charges:,.2f}</td></tr>
                    <tr><td class="metric">Today's Net P&L</td><td style='color:{pnl_color}'><b>₹{net:,.2f}</b></td></tr>
                    <tr><td class="metric">Entries Used</td><td>{entries} / {max_trades}</td></tr>
                    <tr><td class="metric">Wins / Losses</td><td>{wins} / {losses}</td></tr>
                    <tr><td class="metric">Win Rate</td><td>{f'{float(win_rate):.0f}%' if win_rate is not None else '—'}</td></tr>
                    <tr><td class="metric">Daily Loss Limit</td><td>{self._esc(ctx.get('daily_loss_limit', '—'))} · {self._esc(ctx.get('loss_limit_status', 'not hit'))}</td></tr>
                    <tr><td class="metric">Entry Permission (EOD)</td><td>{self._esc(ctx.get('entry_permission', '—'))}</td></tr>
                </table>
            </div>

            <div class="section">
                <h3>🎯 Trades ({len(trades)})</h3>
                <table>
                    <tr>
                        <th>Symbol</th><th>Dir</th><th>Qty</th><th>Entry</th><th>Exit</th>
                        <th>Gross</th><th>Charges</th><th>Net</th><th>Exit Reason</th><th>Strategy</th>
                    </tr>
                    {trade_rows}
                </table>
            </div>

            <div class="section">
                <h3>🧠 Strategy Breakdown</h3>
                <table>
                    <tr><th>Strategy / Signal</th><th>Trades</th></tr>
                    {strat_rows}
                </table>
            </div>

            <div class="section">
                <h3>📦 Open Positions at EOD</h3>
                <table>
                    <tr><th>Symbol</th><th>Qty</th><th>Entry</th><th>Stop Loss</th><th>Target</th></tr>
                    {pos_rows}
                </table>
                <p style="font-size:12px;color:#555">EOD square-off completed: {eod_at}
                {f' · <b>{eod_pending} position(s) PENDING square-off</b>' if eod_pending else ''}</p>
            </div>

            <div class="section">
                <h3>🩺 Connectivity</h3>
                <table>
                    <tr><td class="metric">Angel SmartAPI</td><td>{auth}</td></tr>
                    <tr><td class="metric">Market Data</td><td>{mdata}</td></tr>
                </table>
            </div>

            {f'<div class="section"><h3>📝 Notes</h3><ul style="font-size:13px">{notes}</ul></div>' if notes else ''}

            <p style="font-size:11px;color:#888">
                Simulated fills use verified quote receipts where available; records
                without receipts are labelled UNVERIFIED in the journal. Historical
                (pre-provenance) records used a legacy charge model — only today's
                figures above use the current verified Angel charge schedule.
            </p>
        """

        lines = [
            "Intraday PAPER — Angel One",
            "PAPER TRADING — SIMULATED RESULTS, NOT REAL PROFIT OR LOSS.",
            "",
            f"Capital: {capital} · Entries: {entries}/{max_trades} · Wins/Losses: {wins}/{losses}",
            f"Gross: ₹{gross:,.2f} · Charges: ₹{charges:,.2f} · Net: ₹{net:,.2f}",
            f"Daily loss limit: {ctx.get('daily_loss_limit', '—')} · {ctx.get('loss_limit_status', 'not hit')}",
            f"Entry permission: {ctx.get('entry_permission', '—')}", "",
        ]
        for t in trades:
            lines.append(
                f"{t.get('symbol')} {t.get('direction')} x{int(t.get('quantity', 0) or 0)} "
                f"{float(t.get('entry_price', 0) or 0):,.2f}→{float(t.get('exit_price', 0) or 0):,.2f} "
                f"net ₹{float(t.get('net_pnl', 0) or 0):,.2f} ({t.get('exit_reason')})")
        lines += ["", f"Open at EOD: {len(open_pos)} · EOD completed: {ctx.get('eod_completed_at', '—')}",
                  f"SmartAPI: {ctx.get('auth_status', '—')} · Market data: {ctx.get('market_data_status', '—')}"]
        for n in (ctx.get('notes') or []):
            lines.append(f"- {n}")

        snapshot = {
            'workspace': 'intraday',
            'trading_date': ctx.get('trading_date') or now.strftime('%Y-%m-%d'),
            'date': date_str, 'generated_at': time_str,
            'html': fragment, 'text': "\n".join(lines),
            'gross': gross, 'charges': charges, 'net': net,
            'entries': entries, 'max_trades': max_trades,
            'wins': wins, 'losses': losses,
            'eod_completed_at': ctx.get('eod_completed_at'),
            'notes': ctx.get('notes') or [],
        }
        self._record_ready('intraday', snapshot['trading_date'], snapshot)
        return self.attempt_consolidated()

    def weekly_report(self, summary: Dict) -> bool:
        date_str = datetime.now().strftime("%d %b %Y")
        subject = f"AI Trading Weekly Report — {date_str}"
        body = f"""
        <html>
        <body>
            <h2>AI Trading Weekly Report — {date_str}</h2>
            <p><b>Weekly P&L:</b> ₹{summary.get('weekly_pnl', 0):,.2f}</p>
            <p><b>Monthly P&L:</b> ₹{summary.get('monthly_pnl', 0):,.2f}</p>
            <p><b>Weekly Win Rate:</b> {summary.get('weekly_win_rate', 0):.0%}</p>
            <p><b>Monthly Win Rate:</b> {summary.get('monthly_win_rate', 0):.0%}</p>
            <p><b>Market Regime:</b> {summary.get('market_regime', 'UNKNOWN')}</p>
        </body>
        </html>
        """
        return self.send_report(subject, body)
