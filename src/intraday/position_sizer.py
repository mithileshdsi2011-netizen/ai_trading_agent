"""
Intraday Position Sizing Module

Calculates position sizes for intraday trades.
Completely separate from Swing position sizing.
"""
import logging

from .models import IntradayConfig, IntradaySignal

logger = logging.getLogger(__name__)


class IntradayPositionSizer:
    """Calculates intraday position sizes."""
    
    def __init__(self, config: IntradayConfig):
        """
        Initialize position sizer.
        
        Args:
            config: IntradayConfig instance
        """
        self.config = config
    
    def calculate_quantity(self, signal: IntradaySignal, 
                          available_capital: float) -> tuple[int, float]:
        """
        Calculate position quantity and capital required.
        
        Args:
            signal: IntradaySignal instance
            available_capital: Available capital for this trade
            
        Returns:
            Tuple of (quantity, capital_required)
        """
        try:
            # Risk amount based on risk percentage
            risk_amount = self.config.intraday_capital * self.config.risk_per_trade_pct
            
            # Risk per share
            risk_per_share = abs(signal.entry_price - signal.stop_loss)
            
            if risk_per_share == 0:
                logger.warning(f"[INTRADAY SIZER] Zero risk per share for {signal.symbol}")
                return 0, 0.0
            
            # Calculate quantity
            quantity = int(risk_amount / risk_per_share)
            
            # Ensure minimum quantity
            if quantity < 1:
                quantity = 1
            
            # Calculate capital required
            capital_required = quantity * signal.entry_price
            
            # Check if within available capital
            if capital_required > available_capital:
                # Reduce quantity to fit available capital
                quantity = int(available_capital / signal.entry_price)
                capital_required = quantity * signal.entry_price
            
            logger.info(
                f"[INTRADAY SIZER] {signal.symbol}: Qty={quantity}, "
                f"Risk=₹{risk_amount:.2f}, Risk/Share=₹{risk_per_share:.2f}, "
                f"Capital=₹{capital_required:.2f}"
            )
            
            return quantity, capital_required
            
        except Exception as e:
            logger.error(f"[INTRADAY SIZER] Sizing error: {e}")
            return 0, 0.0
    
    def calculate_max_position_size(self, symbol: str, price: float) -> int:
        """
        Calculate maximum position size based on capital allocation.
        
        Args:
            symbol: Trading symbol
            price: Current price
            
        Returns:
            Maximum quantity
        """
        max_capital_per_position = self.config.intraday_capital / self.config.max_concurrent_positions
        max_quantity = int(max_capital_per_position / price)
        
        return max_quantity if max_quantity >= 1 else 1
