"""
Intraday Position Manager Module

Manages intraday position lifecycle.
Completely separate from Swing position management.
"""
import logging
from typing import Dict, List, Optional

from .models import IntradayPosition
from .paper_executor import PaperTradeExecutor

logger = logging.getLogger(__name__)


class IntradayPositionManager:
    """Manages intraday positions."""
    
    def __init__(self, paper_executor: PaperTradeExecutor):
        """
        Initialize position manager.
        
        Args:
            paper_executor: PaperTradeExecutor instance
        """
        self.paper_executor = paper_executor
    
    def get_open_positions(self) -> List[IntradayPosition]:
        """Get all open positions."""
        return self.paper_executor.get_open_positions()
    
    def get_position(self, symbol: str) -> Optional[IntradayPosition]:
        """Get position by symbol."""
        for position in self.get_open_positions():
            if position.symbol == symbol:
                return position
        return None
    
    def update_positions(self, market_data: Dict[str, float]):
        """
        Update all positions with current market data.
        
        Args:
            market_data: Dict mapping symbols to current prices
        """
        self.paper_executor.update_positions(market_data)
    
    def check_all_exits(self) -> List:
        """
        Check exit conditions for all positions.
        
        Returns:
            List of trades that were exited
        """
        exited_trades = []
        
        for position in self.get_open_positions():
            exit_result = self.paper_executor.check_exit_conditions(position)
            if exit_result:
                should_exit, exit_price, exit_reason = exit_result
                if should_exit:
                    trade = self.paper_executor.execute_exit(position, exit_price, exit_reason)
                    if trade:
                        exited_trades.append(trade)
        
        return exited_trades
    
    def eod_square_off(self) -> List:
        """
        Square off all positions at end of day.
        
        Returns:
            List of trades that were squared off
        """
        return self.paper_executor.eod_square_off()
