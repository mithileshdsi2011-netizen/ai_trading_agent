"""
Intraday Paper Trading Executor Module

Simulates intraday trades without real orders.
V1 default mode - no real Angel orders.
"""
import logging
import uuid
from typing import Dict, Optional
from datetime import datetime

from .models import (
    IntradaySignal, IntradayPosition, IntradayTrade,
    PositionStatus, SignalDirection
)
from .risk_manager import IntradayRiskManager
from .position_sizer import IntradayPositionSizer

logger = logging.getLogger(__name__)

# Max age of the broker quote that produced an exit price for the exit
# to be labelled VERIFIED — matches the per-symbol entry freshness gate.
EXIT_QUOTE_MAX_AGE_S = 30.0


class PaperTradeExecutor:
    """Paper trading executor for intraday."""
    
    def __init__(self, risk_manager: IntradayRiskManager,
                 position_sizer: IntradayPositionSizer, config,
                 validator=None):
        """
        Initialize paper executor.

        Args:
            risk_manager: IntradayRiskManager instance
            position_sizer: IntradayPositionSizer instance
            config: IntradayConfig instance
            validator: IntradayValidator — used for the commit-time
                entry-window gate (optional; None keeps legacy behavior)
        """
        self.risk_manager = risk_manager
        self.position_sizer = position_sizer
        self.config = config
        self.validator = validator
        self._paper_positions: Dict[str, IntradayPosition] = {}
        self._paper_trades: list = []
    
    def execute_entry(self, signal: IntradaySignal) -> Optional[IntradayPosition]:
        """
        Execute a paper trade entry.
        
        Args:
            signal: IntradaySignal instance
            
        Returns:
            IntradayPosition or None
        """
        try:
            # Check risk manager
            can_enter, reason = self.risk_manager.can_enter_trade(signal)
            if not can_enter:
                logger.info(f"[INTRADAY PAPER] Entry rejected: {reason}")
                signal.validation_passed = False
                signal.rejection_reason = reason
                return None
            
            # Calculate position size
            quantity, capital_required = self.position_sizer.calculate_quantity(
                signal, self.config.intraday_capital
            )
            
            if quantity == 0:
                logger.warning(f"[INTRADAY PAPER] Invalid quantity for {signal.symbol}")
                return None

            # Final gate at commit time: a scan that started inside the
            # entry window may reach here after the cutoff (or after the
            # EOD square-off deadline). Re-check the session/window at the
            # last moment — validation upstream ran earlier in the cycle.
            if self.validator is not None and not (
                    self.validator.is_market_open()
                    and self.validator.is_entry_window_open()
                    and not self.validator.is_square_off_time()):
                signal.validation_passed = False
                signal.rejection_reason = "Entry window closed at commit"
                logger.info(
                    f"[INTRADAY PAPER] Entry rejected at commit: "
                    f"{signal.symbol} — window/session closed")
                return None

            # Create paper position
            position_id = str(uuid.uuid4())
            position = IntradayPosition(
                id=position_id,
                symbol=signal.symbol,
                direction=signal.direction,
                entry_price=signal.entry_price,
                quantity=quantity,
                stop_loss=signal.stop_loss,
                target=signal.target,
                entry_time=datetime.now(),
                current_price=signal.current_price,
                unrealized_pnl=0.0,
                unrealized_pnl_pct=0.0,
                signal_score=signal.score,
                signal_classification=signal.classification,
                strategy=signal.strategy,
                is_paper=True
            )
            
            # Add to risk manager
            self.risk_manager.add_position(position)
            self.risk_manager.record_trade_entry(signal)
            
            # Store locally
            self._paper_positions[position_id] = position
            
            logger.info(
                f"[INTRADAY PAPER] Entry executed: {signal.symbol} {signal.direction.value} "
                f"@ ₹{signal.entry_price:.2f}, Qty: {quantity}"
            )
            
            return position
            
        except Exception as e:
            logger.error(f"[INTRADAY PAPER] Entry execution error: {e}")
            return None
    
    def execute_exit(self, position: IntradayPosition, 
                    exit_price: float, exit_reason: str) -> Optional[IntradayTrade]:
        """
        Execute a paper trade exit.
        
        Args:
            position: IntradayPosition instance
            exit_price: Exit price
            exit_reason: Reason for exit
            
        Returns:
            IntradayTrade or None
        """
        try:
            # Calculate P&L
            if position.direction == SignalDirection.LONG:
                gross_pnl = (exit_price - position.entry_price) * position.quantity
            else:
                gross_pnl = (position.entry_price - exit_price) * position.quantity
            
            # Estimate charges (simplified) — always a cost, never a credit
            charges = abs(gross_pnl) * 0.001  # 0.1% estimate
            net_pnl = gross_pnl - charges
            pnl_pct = (net_pnl / (position.entry_price * position.quantity)) * 100
            
            # Update position
            position.exit_price = exit_price
            position.exit_time = datetime.now()
            position.exit_reason = exit_reason
            position.realized_pnl = net_pnl
            position.status = PositionStatus.CLOSED
            
            # Exit-price provenance — VERIFIED only when every link in the
            # chain is proven: a real Angel broker quote, for this symbol,
            # on NSE, whose price is exactly the recorded exit price, with a
            # local receipt timestamp 0–30s old at decision time. Any missing,
            # mismatched, stale or future-dated link stays UNVERIFIED.
            received_at = position.current_price_updated_at
            exchange_ts = position.current_price_exchange_ts
            quote_symbol = position.current_price_quote_symbol
            quote_exchange = position.current_price_quote_exchange
            age_s = ((position.exit_time - received_at).total_seconds()
                     if received_at else None)
            symbol_ok = (
                quote_symbol is not None
                and quote_symbol.split('-')[0] == position.symbol
            )
            verified = (
                received_at is not None
                and age_s is not None
                and 0 <= age_s <= EXIT_QUOTE_MAX_AGE_S   # future ts → age<0 → reject
                and exit_price == position.current_price  # exit derives from that quote
                and symbol_ok
                and quote_exchange == 'NSE'
            )
            exit_price_source = 'VERIFIED' if verified else 'UNVERIFIED'

            # Create trade record
            trade = IntradayTrade(
                id=str(uuid.uuid4()),
                symbol=position.symbol,
                direction=position.direction,
                entry_price=position.entry_price,
                exit_price=exit_price,
                quantity=position.quantity,
                entry_time=position.entry_time,
                exit_time=position.exit_time,
                exit_reason=exit_reason,
                gross_pnl=gross_pnl,
                charges=charges,
                net_pnl=net_pnl,
                pnl_pct=pnl_pct,
                stop_loss=position.stop_loss,
                target=position.target,
                risk_reward=position.stop_loss / position.target if position.target else 0,
                signal_score=position.signal_score,
                signal_classification=position.signal_classification,
                strategy=position.strategy,
                is_paper=True,
                duration_minutes=(position.exit_time - position.entry_time).total_seconds() / 60,
                exit_price_source=exit_price_source,
                exit_exchange_ts=exchange_ts,
                exit_quote_received_at=received_at,
                exit_quote_age_s=age_s,
                exit_quote_symbol=quote_symbol,
                exit_quote_exchange=quote_exchange,
            )
            
            # Update risk manager
            self.risk_manager.record_trade_exit(position)
            self.risk_manager.remove_position(position.symbol)
            
            # Remove from local storage
            if position.id in self._paper_positions:
                del self._paper_positions[position.id]
            
            # Add to trades
            self._paper_trades.append(trade)
            
            logger.info(
                f"[INTRADAY PAPER] Exit executed: {position.symbol} @ ₹{exit_price:.2f}, "
                f"P&L: ₹{net_pnl:.2f} ({pnl_pct:.2f}%), Reason: {exit_reason}"
            )
            
            return trade
            
        except Exception as e:
            logger.error(f"[INTRADAY PAPER] Exit execution error: {e}")
            return None
    
    def update_positions(self, market_data: Dict[str, float],
                         quotes: Optional[Dict[str, Dict]] = None):
        """
        Update all paper positions with current market data.

        Args:
            market_data: Dict mapping symbols to current prices
            quotes: Optional dict mapping symbols to the raw quote objects the
                prices came from — used to stamp quote provenance (local
                receipt time and genuine exchange timestamp when present)
        """
        now = datetime.now()
        for position in self._paper_positions.values():
            if position.symbol in market_data:
                current_price = market_data[position.symbol]
                position.current_price = current_price
                position.current_price_updated_at = now
                quote = quotes.get(position.symbol) if quotes else None
                position.current_price_exchange_ts = (
                    quote.get('exchange_ts') if isinstance(quote, dict) else None
                )
                position.current_price_quote_symbol = (
                    (quote.get('trading_symbol') or position.symbol)
                    if isinstance(quote, dict) else None
                )
                position.current_price_quote_exchange = (
                    quote.get('exchange') if isinstance(quote, dict) else None
                )
                
                # Calculate unrealized P&L
                if position.direction == SignalDirection.LONG:
                    position.unrealized_pnl = (current_price - position.entry_price) * position.quantity
                else:
                    position.unrealized_pnl = (position.entry_price - current_price) * position.quantity
                
                position.unrealized_pnl_pct = (
                    position.unrealized_pnl / (position.entry_price * position.quantity)
                ) * 100
    
    def check_exit_conditions(self, position: IntradayPosition) -> Optional[tuple]:
        """
        Check if position should be exited.
        
        Args:
            position: IntradayPosition instance
            
        Returns:
            Tuple of (should_exit, exit_price, exit_reason) or None
        """
        current_price = position.current_price
        
        # Check stop loss
        if position.direction == SignalDirection.LONG:
            if current_price <= position.stop_loss:
                return True, current_price, "STOP_LOSS"
            elif current_price >= position.target:
                return True, current_price, "TARGET_HIT"
        else:
            if current_price >= position.stop_loss:
                return True, current_price, "STOP_LOSS"
            elif current_price <= position.target:
                return True, current_price, "TARGET_HIT"
        
        return None
    
    def restore_position(self, position: IntradayPosition):
        """Rehydrate a persisted open paper position at engine startup."""
        if position.status == PositionStatus.OPEN:
            self._paper_positions[position.id] = position

    def get_open_positions(self) -> list:
        """Get all open paper positions."""
        return list(self._paper_positions.values())
    
    def get_closed_trades(self) -> list:
        """Get all closed paper trades."""
        return self._paper_trades.copy()
    
    def eod_square_off(self) -> list:
        """
        Square off all positions at end of day.
        
        Returns:
            List of IntradayTrade for squared off positions
        """
        squared_off = []
        
        for position in list(self._paper_positions.values()):
            trade = self.execute_exit(position, position.current_price, "EOD_SQUARE_OFF")
            if trade:
                squared_off.append(trade)
        
        logger.info(f"[INTRADAY PAPER] EOD square off: {len(squared_off)} positions")
        
        return squared_off
