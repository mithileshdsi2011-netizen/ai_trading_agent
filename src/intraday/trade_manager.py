"""
Intraday Trade Manager Module

Manages intraday trade journal and history.
Completely separate from Swing trade journal.
"""
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime, date
import json
import os

from .models import IntradayTrade

logger = logging.getLogger(__name__)


class IntradayTradeManager:
    """Manages intraday trade journal."""
    
    def __init__(self, data_file: Optional[str] = None):
        """
        Initialize trade manager.
        
        Args:
            data_file: Optional custom data file path
        """
        self._trades: List[IntradayTrade] = []
        self._data_file = data_file or os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "data", "intraday", "intraday_trades.json"
        )
        self._load_trades()
    
    def _load_trades(self):
        """Load trades from file."""
        try:
            if os.path.exists(self._data_file):
                with open(self._data_file, 'r') as f:
                    data = json.load(f)
                    for trade_data in data:
                        trade = self._deserialize_trade(trade_data)
                        if trade is not None:
                            self._trades.append(trade)
                logger.info(f"[INTRADAY TRADE] Loaded {len(self._trades)} trades from file")
        except Exception as e:
            logger.error(f"[INTRADAY TRADE] Load error: {e}")

    @staticmethod
    def _deserialize_trade(trade_data: Dict[str, Any]) -> Optional[IntradayTrade]:
        """Rebuild an IntradayTrade from a serialized dict."""
        try:
            from .models import SignalDirection, SignalClassification

            def _dt(value):
                if value is None or isinstance(value, datetime):
                    return value
                return datetime.fromisoformat(value)

            direction = trade_data.get('direction')
            classification = trade_data.get('signal_classification')

            data = dict(trade_data)
            data['direction'] = direction if hasattr(direction, 'value') else SignalDirection(direction)
            data['signal_classification'] = (
                classification if hasattr(classification, 'value')
                else SignalClassification(classification)
            )
            data['entry_time'] = _dt(trade_data.get('entry_time'))
            data['exit_time'] = _dt(trade_data.get('exit_time'))

            return IntradayTrade(**data)
        except Exception as e:
            logger.error(f"[INTRADAY TRADE] Trade decode error: {e}")
            return None
    
    def _save_trades(self):
        """Save trades to file."""
        try:
            os.makedirs(os.path.dirname(self._data_file), exist_ok=True)
            
            trade_dicts = []
            for trade in self._trades:
                trade_dict = {
                    'id': trade.id,
                    'symbol': trade.symbol,
                    'direction': trade.direction.value,
                    'entry_price': trade.entry_price,
                    'exit_price': trade.exit_price,
                    'quantity': trade.quantity,
                    'entry_time': trade.entry_time.isoformat(),
                    'exit_time': trade.exit_time.isoformat(),
                    'exit_reason': trade.exit_reason,
                    'gross_pnl': trade.gross_pnl,
                    'charges': trade.charges,
                    'net_pnl': trade.net_pnl,
                    'pnl_pct': trade.pnl_pct,
                    'stop_loss': trade.stop_loss,
                    'target': trade.target,
                    'risk_reward': trade.risk_reward,
                    'signal_score': trade.signal_score,
                    'signal_classification': trade.signal_classification.value if hasattr(trade.signal_classification, 'value') else str(trade.signal_classification),
                    'strategy': trade.strategy,
                    'is_paper': trade.is_paper,
                    'duration_minutes': trade.duration_minutes
                }
                trade_dicts.append(trade_dict)
            
            with open(self._data_file, 'w') as f:
                json.dump(trade_dicts, f, indent=2)
            
        except Exception as e:
            logger.error(f"[INTRADAY TRADE] Save error: {e}")
    
    def add_trade(self, trade: IntradayTrade):
        """
        Add a trade to the journal.
        
        Args:
            trade: IntradayTrade instance
        """
        self._trades.append(trade)
        self._save_trades()
        logger.info(f"[INTRADAY TRADE] Trade added: {trade.symbol}")
    
    def get_trades(self, trade_date: Optional[date] = None) -> List[IntradayTrade]:
        """
        Get trades, optionally filtered by date.
        
        Args:
            trade_date: Optional date filter
            
        Returns:
            List of IntradayTrade
        """
        if trade_date:
            return [
                t for t in self._trades 
                if t.exit_time.date() == trade_date
            ]
        return self._trades.copy()
    
    def get_today_trades(self) -> List[IntradayTrade]:
        """Get today's trades."""
        return self.get_trades(date.today())
    
    def get_performance_metrics(self) -> Dict[str, Any]:
        """Get performance metrics."""
        if not self._trades:
            return {
                'total_trades': 0,
                'winning_trades': 0,
                'losing_trades': 0,
                'win_rate': 0.0,
                'gross_profit': 0.0,
                'gross_loss': 0.0,
                'net_pnl': 0.0,
                'profit_factor': 0.0
            }
        
        winning = [t for t in self._trades if t.net_pnl > 0]
        losing = [t for t in self._trades if t.net_pnl < 0]
        
        gross_profit = sum(t.net_pnl for t in winning)
        gross_loss = abs(sum(t.net_pnl for t in losing))
        net_pnl = sum(t.net_pnl for t in self._trades)
        
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else 0.0
        
        return {
            'total_trades': len(self._trades),
            'winning_trades': len(winning),
            'losing_trades': len(losing),
            'win_rate': len(winning) / len(self._trades) * 100 if self._trades else 0.0,
            'gross_profit': gross_profit,
            'gross_loss': gross_loss,
            'net_pnl': net_pnl,
            'profit_factor': profit_factor
        }
