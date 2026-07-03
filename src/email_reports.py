"""
Email Trade Reports
Sends daily and weekly email summaries of trading activity.
"""
import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime
from typing import Dict, List

from config import config

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class EmailReporter:
    """Sends email trading reports"""

    def __init__(self):
        self.enabled = config.EMAIL_ENABLED
        self.smtp_server = config.EMAIL_SMTP_SERVER
        self.smtp_port = config.EMAIL_SMTP_PORT
        self.username = config.EMAIL_USERNAME
        self.password = config.EMAIL_PASSWORD
        self.to_email = config.EMAIL_TO

    def send_report(self, subject: str, body: str) -> bool:
        if not self.enabled or not self.username or not self.password or not self.to_email:
            logger.info("Email reports disabled or not configured")
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
            return True
        except Exception as e:
            logger.error(f"Email report failed: {e}")
            return False

    def daily_report(self, summary: Dict, positions: List[Dict], orders: List[Dict]) -> bool:
        date_str = datetime.now().strftime("%d %b %Y")
        subject = f"AI Trading Daily Report — {date_str}"

        pos_rows = ""
        for p in positions:
            pnl = p.get('pnl', 0)
            pos_rows += f"""
                <tr>
                    <td>{p.get('tradingsymbol', p.get('symbol', ''))}</td>
                    <td>{p.get('quantity', 0)}</td>
                    <td>₹{p.get('average_price', 0):,.2f}</td>
                    <td>₹{p.get('last_price', 0):,.2f}</td>
                    <td style='color:{"green" if pnl >= 0 else "red"}'>₹{pnl:,.2f}</td>
                </tr>
            """

        order_rows = ""
        for o in orders[:20]:
            order_rows += f"""
                <tr>
                    <td>{o.get('tradingsymbol', '')}</td>
                    <td>{o.get('transaction_type', '')}</td>
                    <td>{o.get('quantity', 0)}</td>
                    <td>₹{o.get('average_price', 0) or o.get('price', 0):,.2f}</td>
                    <td>{o.get('status', '')}</td>
                </tr>
            """

        es = summary.get('execution_summary', {})
        ps = es.get('position_summary', {})
        charges    = ps.get('total_charges', 0)
        net_pnl    = ps.get('total_net_pnl', ps.get('total_pnl', 0) - charges)
        pf         = ps.get('profit_factor', 0)
        avg_win    = ps.get('avg_win', 0)
        avg_loss   = ps.get('avg_loss', 0)
        blacklist  = ", ".join(ps.get('daily_blacklist', [])) or "None"
        pnl_color  = 'green' if summary.get('daily_pnl', 0) >= 0 else 'red'

        body = f"""
        <html>
        <body style='font-family:Arial,sans-serif;'>
            <h2>AI Trading Daily Report — {date_str}</h2>
            <table style='border-collapse:collapse;' cellpadding='6'>
              <tr><td><b>Cash</b></td><td>₹{summary.get('cash', 0):,.2f}</td></tr>
              <tr><td><b>Invested</b></td><td>₹{summary.get('invested', 0):,.2f}</td></tr>
              <tr><td><b>Daily Gross P&amp;L</b></td><td style='color:{pnl_color}'>₹{summary.get('daily_pnl', 0):,.2f}</td></tr>
              <tr><td><b>Total Charges</b></td><td style='color:red'>₹{charges:,.2f}</td></tr>
              <tr><td><b>Daily Net P&amp;L</b></td><td style='color:{pnl_color}'><b>₹{net_pnl:,.2f}</b></td></tr>
              <tr><td><b>Open Positions</b></td><td>{summary.get('open_positions', 0)}</td></tr>
              <tr><td><b>Total Trades</b></td><td>{summary.get('total_trades', 0)}</td></tr>
              <tr><td><b>Win Rate</b></td><td>{summary.get('win_rate', 0):.0%}</td></tr>
              <tr><td><b>Avg Win / Avg Loss</b></td><td>₹{avg_win:.2f} / ₹{abs(avg_loss):.2f}</td></tr>
              <tr><td><b>Profit Factor</b></td><td>{pf:.2f}</td></tr>
              <tr><td><b>Market Regime</b></td><td>{summary.get('market_regime', 'UNKNOWN')}</td></tr>
              <tr><td><b>Today’s Blacklist</b></td><td>{blacklist}</td></tr>
            </table>

            <h3>Open Positions</h3>
            <table border='1' cellpadding='8' cellspacing='0'>
                <tr><th>Symbol</th><th>Qty</th><th>Avg</th><th>LTP</th><th>P&L</th></tr>
                {pos_rows if pos_rows else '<tr><td colspan=5>No open positions</td></tr>'}
            </table>

            <h3>Today's Orders</h3>
            <table border='1' cellpadding='8' cellspacing='0'>
                <tr><th>Symbol</th><th>Action</th><th>Qty</th><th>Price</th><th>Status</th></tr>
                {order_rows if order_rows else '<tr><td colspan=5>No orders today</td></tr>'}
            </table>
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
