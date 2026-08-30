"""
Email Trade Reports
Sends daily and weekly email summaries of trading activity.
"""
import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime, date
from typing import Dict, List, Set

from config import config

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

    def send_report(self, subject: str, body: str) -> bool:
        if not self.enabled or not self.username or not self.password or not self.to_email:
            logger.info("Email reports disabled or not configured")
            return False
        
        # Check cooldown: only send if 24 hours have passed since last email with same subject prefix
        cooldown_key = f"cooldown_{subject[:50]}"  # Use first 50 chars of subject as key
        now = datetime.now()
        last_sent = EmailReporter._cooldowns.get(cooldown_key)
        
        if last_sent and (now - last_sent).total_seconds() < 86400:  # 24 hour cooldown (1 day)
            logger.info(f"Email cooldown active for subject prefix, skipping duplicate: {subject[:50]}")
            return False
        
        if self._already_sent(subject):
            logger.info(f"Email already sent today, skipping duplicate: {subject}")
            return False
        try:
            msg = MIMEMultipart()
            msg["From"] = self.username
            msg["To"] = self.to_email
            msg["Subject"] = subject
            msg.attach(MIMEText(body, "html"))

            with smtplib.SMTP(self.smtp_server, self.smtp_port) as server:
                server.starttls()
                server.login(self.username, self.password)
                server.send_message(msg)

            logger.info(f"Email report sent: {subject}")
            self._mark_sent(subject)
            # Store cooldown timestamp in separate dict
            EmailReporter._cooldowns[cooldown_key] = now
            return True
        except Exception as e:
            logger.error(f"Email report failed: {e}")
            return False

    def daily_report(self, summary: Dict, positions: List[Dict], orders: List[Dict]) -> bool:
        now = datetime.now()
        date_str = now.strftime("%d %b %Y")
        time_str = now.strftime("%I:%M %p IST")
        subject = f"AI Swing Trading Bot – Daily Trading Report – {date_str}"

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

        # ── Trading activity rows (today's completed orders) ──────────────────
        activity_rows = ""
        for o in orders:
            ts = o.get('exchange_timestamp') or o.get('order_timestamp') or ''
            if isinstance(ts, datetime):
                ts = ts.isoformat()
            if not str(ts).startswith(today_str):
                continue
            sym = o.get('tradingsymbol', o.get('symbol', ''))
            action = o.get('transaction_type', '')
            qty = int(o.get('quantity', 0) or 0)
            price = float(o.get('average_price', 0) or o.get('price', 0) or 0)
            status = '✅ Completed' if o.get('status') == 'COMPLETE' else o.get('status', '')
            reason = o.get('status_message') or o.get('tag') or '—'
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
            sym = p.get('tradingsymbol', p.get('symbol', ''))
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
            status = p.get('status', 'HOLD')
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

        body = f"""
        <html>
        <head>
            <style>
                body {{ font-family: Arial, sans-serif; color: #333; }}
                h2 {{ color: #1a5276; }}
                h3 {{ color: #1a5276; margin-top: 24px; }}
                table {{ border-collapse: collapse; width: 100%; margin-top: 8px; }}
                th, td {{ border: 1px solid #ddd; padding: 8px; text-align: left; font-size: 14px; }}
                th {{ background-color: #f2f3f4; font-weight: bold; }}
                .metric {{ font-weight: bold; width: 45%; }}
                .section {{ margin-bottom: 24px; }}
            </style>
        </head>
        <body>
            <h2>AI Swing Trading Bot – Daily Trading Report</h2>
            <p><strong>Date:</strong> {date_str}<br><strong>Report Time:</strong> {time_str}</p>

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
                    <tr><td class="metric">Overall Portfolio Return</td><td>{overall_return:.2f}%</td></tr>
                    <tr><td class="metric">Market Regime</td><td>{summary.get('market_regime', 'UNKNOWN')}</td></tr>
                    <tr><td class="metric">Open Positions</td><td>{open_count}</td></tr>
                    <tr><td class="metric">Closed Today</td><td>{closed_today}</td></tr>
                    <tr><td class="metric">Available Buying Capacity</td><td>₹{buying_capacity:,.2f}</td></tr>
                </table>
            </div>

            <div class="section">
                <h3>🎯 Today's Trading Activity</h3>
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
        </body>
        </html>
        """
        return self.send_report(subject, body)

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
