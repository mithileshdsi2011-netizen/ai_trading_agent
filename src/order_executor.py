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
        # Tracks CDSL-auth alerts already sent today to avoid spamming per cycle
        self._cdsl_alert_sent: set = set()
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
                if qty <= 0:  # skip zero AND negative (settlement offsets from sold CNC)
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

        # ── Load CNC holdings (prior-day delivery positions) ───────────────
        # kite.positions() only shows intraday net; kite.holdings() has CNC
        # stocks bought on previous days that have settled into the demat account.
        # NOTE: T+1 stocks appear in BOTH kite.positions() net AND kite.holdings()
        # (with t1_quantity > 0, settled quantity = 0). We only load here if the
        # settled quantity > 0 to avoid duplicates with positions() already loaded above.
        try:
            holdings_data = self.broker.kite.holdings()
            already_loaded = {p.symbol for p in self.risk_manager.positions}
            for h in holdings_data:
                settled_qty = h.get('quantity', 0)   # only fully settled shares
                t1_qty      = h.get('t1_quantity', 0) # T+1 pending — also in positions()
                qty = settled_qty  # only use settled; T+1 already handled by positions()
                if qty <= 0:
                    continue
                symbol = h.get('tradingsymbol')
                if not symbol or symbol in already_loaded:
                    continue
                avg_price = h.get('average_price', 0) or h.get('last_price', 0)
                if not avg_price:
                    continue
                # Recalculate SL/target from config percentages (ATR not stored for old holdings)
                if config.TRADING_MODE == "swing":
                    sl     = avg_price * (1 - config.SWING_STOP_LOSS_PERCENTAGE)
                    target = avg_price * (1 + config.SWING_TARGET_PERCENTAGE)
                    max_days = config.SWING_MAX_HOLD_DAYS
                else:
                    sl     = avg_price * (1 - config.STOP_LOSS_PERCENTAGE)
                    target = avg_price * (1 + config.TARGET_PERCENTAGE)
                    max_days = None
                entry_time = _journal_entry_times.get(symbol, datetime.now())
                position = Position(
                    symbol=symbol,
                    entry_price=avg_price,
                    quantity=qty,
                    stop_loss=round(sl, 2),
                    target=round(target, 2),
                    entry_time=entry_time,
                    status=PositionStatus.OPEN,
                    planned_exit_date=entry_time + timedelta(days=max_days) if max_days else None,
                    product_type="CNC",
                    highest_price=avg_price,
                    trailing_stop=round(sl, 2) if config.TRAILING_STOP_ENABLED else None,
                )
                self.risk_manager.positions.append(position)
                already_loaded.add(symbol)
                logger.info(
                    f"Loaded CNC holding: {symbol} {qty} @ {avg_price} "
                    f"SL:{sl:.2f} Target:{target:.2f} "
                    f"entry={'from journal' if symbol in _journal_entry_times else 'fallback now()'}"
                )
        except Exception as e:
            logger.warning(f"Could not load CNC holdings: {e}")

    def monitor_holdings(self) -> List[Dict]:
        """
        Monitor CNC holdings (prior-day delivery) for SL / target exits.
        Uses existing risk_manager positions that were loaded from kite.holdings().
        Called every trading cycle alongside monitor_positions().
        """
        # Only monitor positions tagged as CNC that came from holdings
        # (monitor_positions handles the in-session positions already)
        holdings_positions = [
            p for p in self.risk_manager.positions
            if p.product_type == 'CNC'
            and p.status in {PositionStatus.OPEN, PositionStatus.PARTIAL}
        ]
        if not holdings_positions:
            return []

        # Batch LTP for all holdings symbols
        symbols = [p.symbol for p in holdings_positions]
        current_prices: Dict[str, float] = {}
        if self.market_data.kite:
            try:
                keys = [f'NSE:{s}' for s in symbols]
                ltp_data = self.market_data._kite_call_with_retry(
                    self.market_data.kite.ltp, keys
                ) or {}
                for s in symbols:
                    val = ltp_data.get(f'NSE:{s}', {}).get('last_price', 0)
                    if val and val > 0:
                        current_prices[s] = val
            except Exception as _e:
                logger.warning(f'Holdings batch LTP failed: {_e}')
                for s in symbols:
                    p = self.market_data.get_realtime_price(s)
                    if p:
                        current_prices[s] = p

        executed_exits = []
        for position in holdings_positions:
            ltp = current_prices.get(position.symbol)
            if not ltp:
                continue

            # Update trailing stop
            if ltp > position.highest_price:
                position.highest_price = ltp
            if (config.TRAILING_STOP_ENABLED
                    and position.highest_price > position.entry_price * (1 + config.TRAILING_STOP_ACTIVATION_PCT)):
                new_trail = (position.highest_price - 1.5 * position.atr_at_entry
                             if position.atr_at_entry > 0
                             else position.highest_price * (1 - config.TRAILING_STOP_TRAIL_PCT))
                if position.trailing_stop is None or new_trail > position.trailing_stop:
                    position.trailing_stop = round(new_trail, 2)

            effective_stop = max(position.stop_loss, position.trailing_stop or 0)
            pnl_pct = (ltp - position.entry_price) / position.entry_price * 100

            # Determine exit reason
            exit_reason = None
            if ltp <= effective_stop:
                exit_reason = (f'Trailing stop hit (₹{position.trailing_stop:.2f})'
                               if position.trailing_stop and ltp <= position.trailing_stop
                                  and ltp > position.stop_loss
                               else f'Stop loss hit ({pnl_pct:.1f}%)')
            elif ltp >= position.target:
                exit_reason = f'Target hit (+{pnl_pct:.1f}%)'
            elif (position.planned_exit_date
                  and datetime.now() >= position.planned_exit_date
                  and config.TRADING_MODE == 'swing'):
                exit_reason = f'Max hold days reached ({config.SWING_MAX_HOLD_DAYS}d)'

            if not exit_reason:
                logger.info(
                    f'Holding {position.symbol}: LTP ₹{ltp:.2f} '
                    f'entry ₹{position.entry_price:.2f} ({pnl_pct:+.1f}%) '
                    f'SL ₹{effective_stop:.2f} Target ₹{position.target:.2f}'
                )
                continue

            logger.info(f'Holdings exit triggered — {position.symbol}: {exit_reason}')
            sell_signal = {
                'symbol':            position.symbol,
                'action':            'SELL',
                'current_price':     ltp,
                'position_size':     position.quantity,
                'investment_amount': ltp * position.quantity,
                'stop_loss':         0,
                'target':            0,
                'risk_reward_ratio': 0,
                'confidence':        1.0,
                'overall_score':     0,
                'reasoning':         exit_reason,
                'timestamp':         datetime.now().isoformat(),
            }
            order_result = self.broker.place_order(sell_signal)
            gross_pnl = (ltp - position.entry_price) * position.quantity

            if order_result['success']:
                position.status = PositionStatus.CLOSED
                position.exit_price = ltp
                position.exit_time = datetime.now()
                logger.info(
                    f'Holding SOLD {position.symbol} ×{position.quantity} '
                    f'@ ₹{ltp:.2f} | P&L ₹{gross_pnl:+.2f} | {exit_reason}'
                )
                exit_signal_out = {
                    'symbol':      position.symbol,
                    'action':      'SELL',
                    'price':       ltp,
                    'quantity':    position.quantity,
                    'pnl':         gross_pnl,
                    'pnl_percentage': pnl_pct,
                    'reason':      exit_reason,
                    'partial':     False,
                    'timestamp':   datetime.now().isoformat(),
                }
                executed_exits.append({
                    'success':     True,
                    'order_id':    order_result['order_id'],
                    'exit_signal': exit_signal_out,
                    'timestamp':   datetime.now().isoformat(),
                })
                try:
                    self.telegram.exit(exit_signal_out, order_result.get('order_id', ''))
                except Exception:
                    pass
                try:
                    self.journal.log_entry(
                        symbol=position.symbol, action='SELL',
                        price=ltp, quantity=position.quantity,
                        exit_reason=exit_reason,
                        entry_price=position.entry_price,
                        entry_date=position.entry_time.isoformat(),
                        gross_pnl=gross_pnl,
                        net_pnl=gross_pnl,
                        charges=0,
                        sector='Other',
                        trade_score=0,
                    )
                except Exception as je:
                    logger.warning(f'Journal SELL log failed for holding: {je}')
            else:
                if order_result.get('cdsl_auth_required'):
                    # Position stays OPEN — bot will retry next cycle once authorised
                    logger.error(
                        f'CDSL auth required for {position.symbol} — '
                        f'position kept OPEN, will retry after authorisation'
                    )
                    if position.symbol not in self._cdsl_alert_sent:
                        try:
                            self.telegram._send(
                                f'🔐 <b>CDSL AUTHORISATION REQUIRED</b>\n\n'
                                f'Bot wants to sell <b>{position.symbol}</b> '
                                f'({exit_reason}) but needs demat authorisation first.\n\n'
                                f'<b>Action:</b> Open Kite → Portfolio → Holdings → tap <b>Authorise</b>\n'
                                f'🔗 https://kite.zerodha.com/holdings\n\n'
                                f'Bot will auto-sell on the next cycle once authorised.'
                            )
                        except Exception:
                            pass
                        self._cdsl_alert_sent.add(position.symbol)
                else:
                    logger.error(f'Holdings SELL order FAILED for {position.symbol}: {order_result}')

        return executed_exits

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
            
            if order_result['success']:
                try:
                    open_pos = next(
                        (p for p in self.risk_manager.positions
                         if p.symbol == exit_signal['symbol']
                         and p.status in {PositionStatus.OPEN, PositionStatus.PARTIAL, PositionStatus.CLOSED}),
                        None
                    )
                    entry_price = open_pos.entry_price if open_pos else exit_signal['price']
                    entry_date = open_pos.entry_time.isoformat() if open_pos else datetime.now().isoformat()
                    _SECTOR_MAP2 = {
                        'HDFCBANK': 'Banking', 'ICICIBANK': 'Banking', 'KOTAKBANK': 'Banking',
                        'AXISBANK': 'Banking', 'SBIN': 'Banking', 'TCS': 'IT', 'INFY': 'IT',
                        'WIPRO': 'IT', 'HCLTECH': 'IT', 'TECHM': 'IT', 'RELIANCE': 'Energy',
                        'SUNPHARMA': 'Pharma', 'DRREDDY': 'Pharma', 'MARUTI': 'Auto',
                        'TATAMOTORS': 'Auto', 'BHARTIARTL': 'Telecom',
                    }
                    charges = getattr(open_pos, 'charges', 0) or 0
                    gross_pnl = exit_signal.get('pnl', 0)
                    net_pnl = gross_pnl - charges
                    self.journal.log_entry(
                        symbol=exit_signal['symbol'],
                        action='SELL',
                        price=exit_signal['price'],
                        quantity=exit_signal['quantity'],
                        exit_reason='End of day close',
                        entry_price=entry_price,
                        entry_date=entry_date,
                        gross_pnl=gross_pnl,
                        net_pnl=net_pnl,
                        charges=charges,
                        sector=_SECTOR_MAP2.get(exit_signal['symbol'], 'Other'),
                        trade_score=getattr(open_pos, '_trade_score', 0) if open_pos else 0,
                    )
                except Exception as je:
                    logger.warning(f"Journal SELL log failed for EOD close: {je}")

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
