"""
Telegram Alerts
Sends trade notifications to a Telegram chat.
"""
import logging
from typing import Dict
import requests

from config import config

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class TelegramAlerter:
    """Sends Telegram alerts for trading events"""

    def __init__(self):
        self.token = config.TELEGRAM_BOT_TOKEN
        self.chat_id = config.TELEGRAM_CHAT_ID
        self.enabled = bool(self.token and self.chat_id)

    def _send(self, message: str):
        if not self.enabled:
            return
        try:
            url = f"https://api.telegram.org/bot{self.token}/sendMessage"
            payload = {"chat_id": self.chat_id, "text": message, "parse_mode": "HTML"}
            response = requests.post(url, json=payload, timeout=10)
            response.raise_for_status()
            logger.info("Telegram alert sent")
        except Exception as e:
            logger.error(f"Telegram alert failed: {e}")

    def buy(self, signal: Dict, order_id: str):
        msg = (
            "🟢 <b>BUY EXECUTED</b>\n\n"
            f"Symbol: {signal['symbol']}\n"
            f"Qty: {signal['position_size']}\n"
            f"Price: ₹{signal['current_price']:.2f}\n"
            f"Target: ₹{signal['target']:.2f}\n"
            f"Stop Loss: ₹{signal['stop_loss']:.2f}\n"
            f"R:R: {signal['risk_reward_ratio']:.2f}\n"
            f"Confidence: {signal.get('confidence', 0):.0%}\n"
            f"Order ID: {order_id}"
        )
        self._send(msg)

    def sell(self, signal: Dict, order_id: str, pnl: float, pnl_pct: float):
        emoji = "🟢" if pnl >= 0 else "🔴"
        msg = (
            f"{emoji} <b>SELL EXECUTED</b>\n\n"
            f"Symbol: {signal['symbol']}\n"
            f"Qty: {signal['position_size']}\n"
            f"Price: ₹{signal['current_price']:.2f}\n"
            f"P&L: ₹{pnl:.2f} ({pnl_pct:+.2f}%)\n"
            f"Order ID: {order_id}"
        )
        self._send(msg)

    def exit(self, exit_signal: Dict, order_id: str):
        pnl = exit_signal.get('pnl', 0)
        pnl_pct = exit_signal.get('pnl_percentage', 0)
        emoji = "🟢" if pnl >= 0 else "🔴"
        msg = (
            f"{emoji} <b>POSITION EXIT</b>\n\n"
            f"Symbol: {exit_signal['symbol']}\n"
            f"Reason: {exit_signal.get('reason', 'Exit')}\n"
            f"Price: ₹{exit_signal['price']:.2f}\n"
            f"P&L: ₹{pnl:.2f} ({pnl_pct:+.2f}%)\n"
            f"Order ID: {order_id}"
        )
        self._send(msg)

    def daily_summary(self, summary: Dict):
        es = summary.get('execution_summary', {})
        ps = es.get('position_summary', {})
        charges   = ps.get('total_charges', 0)
        net_pnl   = ps.get('total_net_pnl', summary.get('daily_pnl', 0) - charges)
        pf        = ps.get('profit_factor', 0)
        avg_win   = ps.get('avg_win', 0)
        avg_loss  = ps.get('avg_loss', 0)
        blacklist = ", ".join(ps.get('daily_blacklist', [])) or "None"
        daily_pnl = summary.get('daily_pnl', 0)
        emoji     = "🟢" if daily_pnl >= 0 else "🔴"
        msg = (
            f"📊 <b>DAILY TRADING SUMMARY</b>\n\n"
            f"{emoji} Gross P&amp;L: ₹{daily_pnl:,.2f}\n"
            f"💸 Charges: ₹{charges:,.2f}\n"
            f"✅ Net P&amp;L: <b>₹{net_pnl:,.2f}</b>\n\n"
            f"Open Positions: {summary.get('open_positions', 0)}\n"
            f"Total Trades: {summary.get('total_trades', 0)}\n"
            f"Win Rate: {summary.get('win_rate', 0):.0%}\n"
            f"Avg Win: ₹{avg_win:.2f} | Avg Loss: ₹{abs(avg_loss):.2f}\n"
            f"Profit Factor: {pf:.2f}\n"
            f"Market Regime: {summary.get('market_regime', 'UNKNOWN')}\n"
            f"Blacklist: {blacklist}"
        )
        self._send(msg)
