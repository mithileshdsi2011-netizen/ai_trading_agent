"""
Order Execution Engine Module
Executes orders and manages order lifecycle
"""
from typing import Dict, List, Optional
import logging
from datetime import datetime, timedelta
import time

from broker_integration import BrokerIntegration
from risk_manager import RiskManager, Position, PositionStatus
from market_data import MarketDataFetcher
from telegram_alerts import TelegramAlerter
from trade_journal import TradeJournal
from config import config

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class OrderExecutor:
    """Executes trading orders and manages order lifecycle"""
    
    def __init__(self):
        self.broker = BrokerIntegration()
        self.risk_manager = RiskManager()
        self.market_data = MarketDataFetcher()
        self.telegram = TelegramAlerter()
        self.journal = TradeJournal()
        self.executed_orders = []
        self._load_existing_positions()
    
    def _load_existing_positions(self):
        """Load existing open positions from broker into RiskManager"""
        if not self.broker.kite:
            return
        try:
            pos_data = self.broker.kite.positions()
            for p in pos_data.get('net', []):
                qty = p.get('quantity', 0)
                if qty == 0:
                    continue
                symbol = p.get('tradingsymbol')
                avg_price = p.get('average_price', 0)
                # Estimate SL and target from swing parameters
                if config.TRADING_MODE == "swing":
                    sl = avg_price * (1 - config.SWING_STOP_LOSS_PERCENTAGE)
                    target = avg_price * (1 + config.SWING_TARGET_PERCENTAGE)
                    max_days = config.SWING_MAX_HOLD_DAYS
                else:
                    sl = avg_price * (1 - config.STOP_LOSS_PERCENTAGE)
                    target = avg_price * (1 + config.TARGET_PERCENTAGE)
                    max_days = None
                position = Position(
                    symbol=symbol,
                    entry_price=avg_price,
                    quantity=qty,
                    stop_loss=round(sl, 2),
                    target=round(target, 2),
                    entry_time=datetime.now(),
                    status=PositionStatus.OPEN,
                    planned_exit_date=datetime.now() + timedelta(days=max_days) if max_days else None,
                    product_type="CNC" if config.TRADING_MODE == "swing" else "MIS",
                    highest_price=avg_price,
                    trailing_stop=round(sl, 2) if config.TRAILING_STOP_ENABLED else None
                )
                self.risk_manager.positions.append(position)
                logger.info(f"Loaded existing position: {symbol} {qty} @ {avg_price}")
        except Exception as e:
            logger.warning(f"Could not load existing positions: {e}")
    
    def execute_signal(self, signal: Dict) -> Dict:
        """
        Execute a trading signal
        
        Args:
            signal: Trading signal
        
        Returns:
            Execution result
        """
        logger.info(f"Executing signal for {signal['symbol']}")
        
        # Check if we can open position based on risk parameters
        if not self.risk_manager.can_open_position(signal):
            return {
                'success': False,
                'reason': 'Risk parameters not met',
                'signal': signal
            }
        
        # Place order
        order_result = self.broker.place_order(signal)
        
        if order_result['success']:
            # Update risk manager
            position = self.risk_manager.open_position(signal)
            
            execution_result = {
                'success': True,
                'order_id': order_result['order_id'],
                'position': position,
                'signal': signal,
                'timestamp': datetime.now().isoformat(),
                'paper_trading': order_result.get('paper_trading', True)
            }
            
            self.executed_orders.append(execution_result)
            logger.info(f"Signal executed successfully: {signal['symbol']}")

            # Auto-log BUY to trade journal
            try:
                research = signal.get('_research', {})
                tech = research.get('technical_analysis', {})
                senti = research.get('sentiment_analysis', {})
                from market_data import MarketDataFetcher as _MDF
                _SECTOR_MAP = {
                    'HDFCBANK':'Banking','ICICIBANK':'Banking','KOTAKBANK':'Banking',
                    'AXISBANK':'Banking','SBIN':'Banking','TCS':'IT','INFY':'IT',
                    'WIPRO':'IT','HCLTECH':'IT','TECHM':'IT','RELIANCE':'Energy',
                    'SUNPHARMA':'Pharma','DRREDDY':'Pharma','MARUTI':'Auto',
                    'TATAMOTORS':'Auto','BHARTIARTL':'Telecom',
                }
                self.journal.log_entry(
                    symbol=signal['symbol'],
                    action='BUY',
                    price=signal.get('current_price', 0),
                    quantity=signal.get('position_size', 1),
                    buy_reason=signal.get('reasoning', ''),
                    trade_score=signal.get('trade_score', 0),
                    score_components=signal.get('score_components', {}),
                    market_regime=signal.get('market_regime', 'UNKNOWN'),
                    sector=_SECTOR_MAP.get(signal['symbol'], 'Other'),
                    sentiment=senti.get('sentiment', 'NEUTRAL'),
                    sentiment_score=senti.get('score', 0.0),
                    news_count=senti.get('news_count', 0),
                    rsi=tech.get('rsi', 0),
                    macd_histogram=tech.get('macd_histogram', 0) or 0,
                    volume_ratio=tech.get('volume_ratio', 1.0),
                    trend=tech.get('trend', 'NEUTRAL'),
                    atr=signal.get('atr', 0),
                    mtf_aligned=signal.get('mtf_aligned', False),
                    confidence=signal.get('confidence', 0),
                )
            except Exception as je:
                logger.warning(f"Journal BUY log failed: {je}")

            return execution_result
        else:
            logger.error(f"Order execution failed: {order_result.get('error')}")
            return {
                'success': False,
                'error': order_result.get('error'),
                'signal': signal
            }
    
    def monitor_positions(self) -> List[Dict]:
        """
        Monitor open positions and execute stop loss / target exits
        
        Returns:
            List of exit signals executed
        """
        positions = self.risk_manager.positions
        if not positions:
            return []
        
        # Get current prices for all open/partial positions
        symbols = [p.symbol for p in positions if p.status in {PositionStatus.OPEN, PositionStatus.PARTIAL}]
        current_prices = {}
        
        for symbol in symbols:
            price = self.market_data.get_realtime_price(symbol)
            if price:
                current_prices[symbol] = price
        
        # Check positions for exit signals
        exit_signals = self.risk_manager.check_positions(current_prices)
        
        # Execute exit signals
        executed_exits = []
        for exit_signal in exit_signals:
            # Create sell signal
            sell_signal = {
                'symbol': exit_signal['symbol'],
                'action': exit_signal['action'],
                'current_price': exit_signal['price'],
                'position_size': exit_signal['quantity'],
                'investment_amount': exit_signal['price'] * exit_signal['quantity'],
                'stop_loss': 0,
                'target': 0,
                'risk_reward_ratio': 0,
                'confidence': 1.0,
                'overall_score': 0,
                'reasoning': exit_signal['reason'],
                'timestamp': datetime.now().isoformat()
            }
            
            # Execute sell order
            order_result = self.broker.place_order(sell_signal)
            
            if order_result['success']:
                executed_exits.append({
                    'success': True,
                    'order_id': order_result['order_id'],
                    'exit_signal': exit_signal,
                    'timestamp': datetime.now().isoformat()
                })
                logger.info(f"Exit executed for {exit_signal['symbol']}: {exit_signal['reason']}")
                try:
                    self.telegram.exit(exit_signal, order_result.get('order_id', ''))
                except Exception as te:
                    logger.error(f"Telegram exit alert error: {te}")

                # Auto-log exit to trade journal
                try:
                    # Find matching open position for entry context
                    open_pos = next(
                        (p for p in self.risk_manager.positions
                         if p.symbol == exit_signal['symbol']
                         and p.status in {PositionStatus.OPEN, PositionStatus.PARTIAL, PositionStatus.CLOSED}),
                        None
                    )
                    entry_price = open_pos.entry_price if open_pos else exit_signal['price']
                    entry_date  = open_pos.entry_time.isoformat() if open_pos else datetime.now().isoformat()
                    _SECTOR_MAP2 = {
                        'HDFCBANK':'Banking','ICICIBANK':'Banking','KOTAKBANK':'Banking',
                        'AXISBANK':'Banking','SBIN':'Banking','TCS':'IT','INFY':'IT',
                        'WIPRO':'IT','HCLTECH':'IT','TECHM':'IT','RELIANCE':'Energy',
                        'SUNPHARMA':'Pharma','DRREDDY':'Pharma','MARUTI':'Auto',
                        'TATAMOTORS':'Auto','BHARTIARTL':'Telecom',
                    }
                    charges = getattr(open_pos, 'charges', 0) or 0
                    gross_pnl = exit_signal.get('pnl', 0)
                    net_pnl   = gross_pnl - charges
                    self.journal.log_entry(
                        symbol=exit_signal['symbol'],
                        action='SELL',
                        price=exit_signal['price'],
                        quantity=exit_signal['quantity'],
                        exit_reason=exit_signal.get('reason', ''),
                        entry_price=entry_price,
                        entry_date=entry_date,
                        gross_pnl=gross_pnl,
                        net_pnl=net_pnl,
                        charges=charges,
                        sector=_SECTOR_MAP2.get(exit_signal['symbol'], 'Other'),
                        trade_score=getattr(open_pos, '_trade_score', 0) if open_pos else 0,
                    )
                except Exception as je:
                    logger.warning(f"Journal SELL log failed: {je}")
            else:
                logger.error(f"Exit execution failed for {exit_signal['symbol']}")
        
        return executed_exits
    
    def close_all_positions(self) -> List[Dict]:
        """
        Close all open positions (typically at end of trading day)
        
        Returns:
            List of close results
        """
        logger.info("Closing all positions")
        
        positions = self.risk_manager.positions
        if not positions:
            return []
        
        # Get current prices
        symbols = [p.symbol for p in positions if p.status in {PositionStatus.OPEN, PositionStatus.PARTIAL}]
        current_prices = {}
        
        for symbol in symbols:
            price = self.market_data.get_realtime_price(symbol)
            if price:
                current_prices[symbol] = price
            else:
                # Use entry price if current price not available
                for p in positions:
                    if p.symbol == symbol:
                        current_prices[symbol] = p.entry_price
        
        # Close all positions
        exit_signals = self.risk_manager.close_all_positions(current_prices)
        
        # Execute sell orders
        close_results = []
        for exit_signal in exit_signals:
            sell_signal = {
                'symbol': exit_signal['symbol'],
                'action': 'SELL',
                'current_price': exit_signal['price'],
                'position_size': exit_signal['quantity'],
                'investment_amount': exit_signal['price'] * exit_signal['quantity'],
                'stop_loss': 0,
                'target': 0,
                'risk_reward_ratio': 0,
                'confidence': 1.0,
                'overall_score': 0,
                'reasoning': 'End of day close',
                'timestamp': datetime.now().isoformat()
            }
            
            order_result = self.broker.place_order(sell_signal)
            
            close_results.append({
                'success': order_result['success'],
                'order_id': order_result.get('order_id'),
                'exit_signal': exit_signal,
                'timestamp': datetime.now().isoformat()
            })
        
        return close_results
    
    def get_execution_summary(self) -> Dict:
        """
        Get summary of all executions
        
        Returns:
            Execution summary
        """
        position_summary = self.risk_manager.get_position_summary()
        broker_holdings = self.broker.get_holdings()
        
        return {
            'position_summary': position_summary,
            'broker_holdings': broker_holdings,
            'total_orders': len(self.executed_orders),
            'paper_trading': self.broker.paper_trading
        }
    
    def should_stop_trading(self) -> bool:
        """
        Check if trading should be stopped based on risk parameters
        
        Returns:
            True if trading should be stopped
        """
        return self.risk_manager.should_stop_trading()
    
    def reset_daily(self):
        """Reset daily statistics"""
        self.risk_manager.reset_daily()
        logger.info("Order executor daily reset completed")
