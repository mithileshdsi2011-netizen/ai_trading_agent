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
        # Tracks symbols whose orders are in-flight (placed but not yet confirmed filled).
        # Prevents duplicate orders when the next cycle runs before Kite confirms a fill.
        self._pending_order_symbols: set = set()
        self._load_existing_positions()
    
    def _load_existing_positions(self):
        """
        Load existing open positions from broker into RiskManager.
        Entry time is recovered from the trade journal (persisted on BUY execution)
        so that SWING_MAX_HOLD_DAYS countdown survives bot restarts.
        """
        if not self.broker.kite:
            return

        # Build a symbol -> entry_time map from the trade journal
        _journal_entry_times: dict = {}
        try:
            import json as _json, os as _os
            _jpath = _os.path.join(
                _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))),
                'data', 'trade_journal.json'
            )
            if _os.path.exists(_jpath):
                with open(_jpath) as _jf:
                    _entries = _json.load(_jf)
                # Walk in reverse so we pick the MOST RECENT open BUY per symbol
                for _e in reversed(_entries):
                    if _e.get('action') == 'BUY' and _e.get('symbol'):
                        sym = _e['symbol']
                        if sym not in _journal_entry_times:
                            try:
                                _journal_entry_times[sym] = datetime.fromisoformat(
                                    _e['timestamp'][:19]  # strip tz suffix if present
                                )
                            except Exception:
                                pass
        except Exception as _je:
            logger.warning(f"Could not load journal entry times: {_je}")

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
                # Use journal entry time; fall back to now() only if journal has no record
                entry_time = _journal_entry_times.get(symbol, datetime.now())
                if symbol not in _journal_entry_times:
                    logger.warning(
                        f"No journal record for {symbol} — using now() as entry time. "
                        f"SWING_MAX_HOLD_DAYS timer may be inaccurate."
                    )
                position = Position(
                    symbol=symbol,
                    entry_price=avg_price,
                    quantity=qty,
                    stop_loss=round(sl, 2),
                    target=round(target, 2),
                    entry_time=entry_time,
                    status=PositionStatus.OPEN,
                    planned_exit_date=entry_time + timedelta(days=max_days) if max_days else None,
                    product_type="CNC" if config.TRADING_MODE == "swing" else "MIS",
                    highest_price=avg_price,
                    trailing_stop=round(sl, 2) if config.TRAILING_STOP_ENABLED else None
                )
                self.risk_manager.positions.append(position)
                logger.info(
                    f"Loaded existing position: {symbol} {qty} @ {avg_price} "
                    f"entry={entry_time.date()} "
                    f"({'from journal' if symbol in _journal_entry_times else 'fallback now()'})")
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
        sym = signal['symbol']
        action = signal.get('action', 'BUY')
        logger.info(f"Executing signal for {sym}")

        # ── Hard paper-trading gate ────────────────────────────────────────────
        # BrokerIntegration.place_order() routes to _place_paper_order when
        # self.broker.paper_trading is True, so real orders are structurally
        # impossible. Log once per call so it's always auditable.
        if self.broker.paper_trading:
            logger.info(f"[PAPER] execute_signal {action} {sym} — no real order will be placed")

        # ── In-flight deduplication ───────────────────────────────────────────
        # Prevent double-orders when a BUY cycle runs before Kite confirms fill.
        if action == 'BUY' and sym in self._pending_order_symbols:
            logger.warning(f"Skipping {sym}: order already in-flight (pending fill)")
            return {'success': False, 'reason': 'Duplicate in-flight order', 'signal': signal}

        # Check if we can open position based on risk parameters
        if not self.risk_manager.can_open_position(signal):
            return {
                'success': False,
                'reason': 'Risk parameters not met',
                'signal': signal
            }

        # Mark as in-flight before placing so concurrent cycles can't duplicate
        if action == 'BUY':
            self._pending_order_symbols.add(sym)

        # Place order
        order_result = self.broker.place_order(signal)
        
        if order_result['success']:
            # Order confirmed — remove from pending (fill received or paper)
            self._pending_order_symbols.discard(sym)

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

            # Auto-log BUY to trade journal (only if order_id confirmed)
            if not order_result.get('order_id'):
                return execution_result
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
                _rm = signal.get('_reentry_meta') or {}
                self.journal.log_entry(
                    symbol=signal['symbol'],
                    action='BUY',
                    price=float(signal.get('current_price') or signal.get('entry_price') or 0),
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
                    is_reentry=bool(_rm),
                    prev_exit_reason=_rm.get('prev_exit_reason', ''),
                    prev_pnl=_rm.get('prev_pnl', 0.0),
                    time_since_exit_hours=_rm.get('time_since_exit_hours', 0.0),
                    reentry_score=_rm.get('reentry_score', 0.0),
                    reentry_confidence=_rm.get('reentry_confidence', 0.0),
                )
            except Exception as je:
                logger.warning(f"Journal BUY log failed: {je}")

            return execution_result
        else:
            # Order failed — clear pending so it can be retried next cycle
            self._pending_order_symbols.discard(sym)
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
        
        # Get current prices — batch LTP (1 API call) instead of N sequential calls
        symbols = [p.symbol for p in positions if p.status in {PositionStatus.OPEN, PositionStatus.PARTIAL}]
        current_prices = {}

        if symbols and self.market_data.kite:
            try:
                keys = [f"NSE:{s}" for s in symbols]
                ltp_data = self.market_data._kite_call_with_retry(
                    self.market_data.kite.ltp, keys
                ) or {}
                for s in symbols:
                    val = ltp_data.get(f"NSE:{s}", {}).get("last_price", 0)
                    if val and val > 0:
                        current_prices[s] = val
            except Exception as _e:
                logger.warning(f"Batch LTP failed, falling back to sequential: {_e}")
                for symbol in symbols:
                    price = self.market_data.get_realtime_price(symbol)
                    if price:
                        current_prices[symbol] = price
        else:
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
        
        # Get current prices — batch LTP (1 API call)
        symbols = [p.symbol for p in positions if p.status in {PositionStatus.OPEN, PositionStatus.PARTIAL}]
        current_prices = {}

        if symbols and self.market_data.kite:
            try:
                keys = [f"NSE:{s}" for s in symbols]
                ltp_data = self.market_data._kite_call_with_retry(
                    self.market_data.kite.ltp, keys
                ) or {}
                for s in symbols:
                    val = ltp_data.get(f"NSE:{s}", {}).get("last_price", 0)
                    if val and val > 0:
                        current_prices[s] = val
            except Exception:
                pass
        # Fall back to entry price for any symbol not priced
        for p in positions:
            if p.symbol not in current_prices:
                price = self.market_data.get_realtime_price(p.symbol)
                current_prices[p.symbol] = price if price else p.entry_price
        
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
