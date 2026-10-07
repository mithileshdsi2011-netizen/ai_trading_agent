"""
Intraday Performance Metrics Module

Calculates performance analytics for intraday trading.
Completely separate from Swing performance metrics.
"""
import logging
from typing import Dict, Any, List
from datetime import date, timedelta
import numpy as np

from .models import IntradayTrade

logger = logging.getLogger(__name__)


class IntradayMetrics:
    """Calculates intraday performance metrics."""
    
    def __init__(self):
        """Initialize metrics calculator."""
        pass
    
    def calculate_metrics(self, trades: List[IntradayTrade]) -> Dict[str, Any]:
        """
        Calculate performance metrics from trades.
        
        Args:
            trades: List of IntradayTrade
            
        Returns:
            Dict with performance metrics
        """
        if not trades:
            return self._empty_metrics()
        
        winning = [t for t in trades if t.net_pnl > 0]
        losing = [t for t in trades if t.net_pnl < 0]
        
        total_trades = len(trades)
        winning_trades = len(winning)
        losing_trades = len(losing)
        
        gross_profit = sum(t.net_pnl for t in winning)
        gross_loss = abs(sum(t.net_pnl for t in losing))
        net_pnl = sum(t.net_pnl for t in trades)
        
        win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0.0
        profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else 0.0
        
        avg_winner = np.mean([t.net_pnl for t in winning]) if winning else 0.0
        avg_loser = np.mean([t.net_pnl for t in losing]) if losing else 0.0
        
        # Calculate drawdown
        cumulative_pnl = np.cumsum([t.net_pnl for t in trades])
        running_max = np.maximum.accumulate(cumulative_pnl)
        drawdown = running_max - cumulative_pnl
        max_drawdown = np.max(drawdown) if len(drawdown) > 0 else 0.0
        
        # Average R multiple
        r_multiples = []
        for t in trades:
            if t.direction.value == "LONG":
                risk = t.entry_price - t.stop_loss
                reward = t.exit_price - t.entry_price
            else:
                risk = t.stop_loss - t.entry_price
                reward = t.entry_price - t.exit_price
            
            if risk > 0:
                r_multiples.append(reward / risk)
        
        avg_r_multiple = np.mean(r_multiples) if r_multiples else 0.0
        
        # Strategy performance
        orb_trades = [t for t in trades if t.strategy == "ORB"]
        vwap_trades = [t for t in trades if t.strategy == "VWAP_MOMENTUM"]
        
        orb_pnl = sum(t.net_pnl for t in orb_trades)
        vwap_pnl = sum(t.net_pnl for t in vwap_trades)
        
        return {
            'total_trades': total_trades,
            'winning_trades': winning_trades,
            'losing_trades': losing_trades,
            'win_rate': round(win_rate, 2),
            'gross_profit': round(gross_profit, 2),
            'gross_loss': round(gross_loss, 2),
            'net_pnl': round(net_pnl, 2),
            'profit_factor': round(profit_factor, 2),
            'avg_winner': round(avg_winner, 2),
            'avg_loser': round(avg_loser, 2),
            'max_drawdown': round(max_drawdown, 2),
            'avg_r_multiple': round(avg_r_multiple, 2),
            'orb_trades': len(orb_trades),
            'orb_pnl': round(orb_pnl, 2),
            'vwap_trades': len(vwap_trades),
            'vwap_pnl': round(vwap_pnl, 2)
        }
    
    def _empty_metrics(self) -> Dict[str, Any]:
        """Return empty metrics."""
        return {
            'total_trades': 0,
            'winning_trades': 0,
            'losing_trades': 0,
            'win_rate': 0.0,
            'gross_profit': 0.0,
            'gross_loss': 0.0,
            'net_pnl': 0.0,
            'profit_factor': 0.0,
            'avg_winner': 0.0,
            'avg_loser': 0.0,
            'max_drawdown': 0.0,
            'avg_r_multiple': 0.0,
            'orb_trades': 0,
            'orb_pnl': 0.0,
            'vwap_trades': 0,
            'vwap_pnl': 0.0
        }
    
    def get_daily_pnl(self, trades: List[IntradayTrade], days: int = 7) -> Dict[str, float]:
        """
        Get daily P&L for last N days.
        
        Args:
            trades: List of IntradayTrade
            days: Number of days
            
        Returns:
            Dict mapping date to P&L
        """
        daily_pnl = {}
        
        cutoff_date = date.today() - timedelta(days=days)
        
        for trade in trades:
            trade_date = trade.exit_time.date()
            if trade_date >= cutoff_date:
                date_str = trade_date.isoformat()
                if date_str not in daily_pnl:
                    daily_pnl[date_str] = 0.0
                daily_pnl[date_str] += trade.net_pnl
        
        return daily_pnl
