"""
Intraday Live Trading Executor Module

Executes real Angel One orders (DISABLED for V1).
This executor is completely disabled by default and will reject all orders.
"""
import logging
from typing import Optional

from .models import IntradaySignal, IntradayPosition

logger = logging.getLogger(__name__)


class LiveTradeExecutor:
    """Live trading executor for Angel One (DISABLED for V1)."""
    
    def __init__(self, angel_client, config):
        """
        Initialize live executor.
        
        Args:
            angel_client: AngelClient instance
            config: IntradayConfig instance
        """
        self.angel_client = angel_client
        self.config = config
        
        # V1: Always disabled
        self._disabled = True
        logger.warning("[INTRADAY LIVE] Live executor DISABLED for V1")
    
    def execute_entry(self, signal: IntradaySignal) -> Optional[IntradayPosition]:
        """
        Execute a live trade entry (DISABLED for V1).
        
        Args:
            signal: IntradaySignal instance
            
        Returns:
            None (always rejected)
        """
        logger.error("[INTRADAY LIVE] Live order entry DISABLED for V1")
        logger.error("[INTRADAY LIVE] Use paper trading mode instead")
        return None
    
    def execute_exit(self, position: IntradayPosition, 
                    exit_price: float, exit_reason: str) -> bool:
        """
        Execute a live trade exit (DISABLED for V1).
        
        Args:
            position: IntradayPosition instance
            exit_price: Exit price
            exit_reason: Reason for exit
            
        Returns:
            False (always rejected)
        """
        logger.error("[INTRADAY LIVE] Live order exit DISABLED for V1")
        logger.error("[INTRADAY LIVE] Use paper trading mode instead")
        return False
    
    def modify_order(self, order_id: str, new_price: float) -> bool:
        """
        Modify a live order (DISABLED for V1).
        
        Args:
            order_id: Order ID
            new_price: New price
            
        Returns:
            False (always rejected)
        """
        logger.error("[INTRADAY LIVE] Live order modification DISABLED for V1")
        return False
    
    def cancel_order(self, order_id: str) -> bool:
        """
        Cancel a live order (DISABLED for V1).
        
        Args:
            order_id: Order ID
            
        Returns:
            False (always rejected)
        """
        logger.error("[INTRADAY LIVE] Live order cancellation DISABLED for V1")
        return False
    
    def is_enabled(self) -> bool:
        """Check if live executor is enabled."""
        return False
    
    def get_status(self) -> dict:
        """Get executor status."""
        return {
            'enabled': False,
            'mode': 'DISABLED',
            'reason': 'V1 paper trading only - live trading disabled'
        }
