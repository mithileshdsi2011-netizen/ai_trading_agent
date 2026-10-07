"""
Intraday Trade Journal Module

Logs intraday trading activity.
Completely separate from Swing trade journal.
"""
import logging
from typing import Dict, Any

logger = logging.getLogger(__name__)


class IntradayJournal:
    """Logs intraday trading activity."""
    
    def __init__(self):
        """Initialize journal."""
        pass
    
    def log_signal(self, signal_data: Dict[str, Any]):
        """
        Log a signal.
        
        Args:
            signal_data: Signal data dict
        """
        logger.info(
            f"[INTRADAY JOURNAL] SIGNAL: {signal_data.get('symbol')} "
            f"Score: {signal_data.get('score')} "
            f"Direction: {signal_data.get('direction')} "
            f"Classification: {signal_data.get('classification')}"
        )
    
    def log_entry(self, position_data: Dict[str, Any]):
        """
        Log a trade entry.
        
        Args:
            position_data: Position data dict
        """
        logger.info(
            f"[INTRADAY JOURNAL] ENTRY: {position_data.get('symbol')} "
            f"Direction: {position_data.get('direction')} "
            f"Price: ₹{position_data.get('entry_price'):.2f} "
            f"Qty: {position_data.get('quantity')} "
            f"SL: ₹{position_data.get('stop_loss'):.2f} "
            f"Target: ₹{position_data.get('target'):.2f}"
        )
    
    def log_exit(self, trade_data: Dict[str, Any]):
        """
        Log a trade exit.
        
        Args:
            trade_data: Trade data dict
        """
        logger.info(
            f"[INTRADAY JOURNAL] EXIT: {trade_data.get('symbol')} "
            f"Price: ₹{trade_data.get('exit_price'):.2f} "
            f"P&L: ₹{trade_data.get('net_pnl'):.2f} "
            f"Reason: {trade_data.get('exit_reason')}"
        )
    
    def log_rejection(self, symbol: str, reason: str):
        """
        Log a signal rejection.
        
        Args:
            symbol: Symbol
            reason: Rejection reason
        """
        logger.info(f"[INTRADAY JOURNAL] REJECTION: {symbol} - {reason}")
    
    def log_risk_event(self, event_type: str, details: str):
        """
        Log a risk event.
        
        Args:
            event_type: Event type
            details: Event details
        """
        logger.warning(f"[INTRADAY JOURNAL] RISK: {event_type} - {details}")
