"""
Order Execution Engine Module
Executes orders and manages order lifecycle
"""
from typing import Dict, List, Optional, Tuple
import logging
import json
import os
from datetime import datetime, timedelta
import time

from broker_integration import BrokerIntegration, get_error_policy
from email_reports import EmailReporter
from risk_manager import RiskManager, Position, PositionStatus
from trade_lifecycle_manager import TradeLifecycleManager
from market_data import MarketDataFetcher
from telegram_alerts import TelegramAlerter
from trade_journal import TradeJournal
from persistence import get_store
from reconciliation_engine import ReconciliationEngine
from config import config

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class OrderExecutor:
    """Executes trading orders and manages order lifecycle"""
    
    def __init__(self):
        self.broker = BrokerIntegration()
        self.risk_manager = RiskManager()
        self.market_data = MarketDataFetcher()
        self.lifecycle_manager = TradeLifecycleManager(self.market_data)
        self.telegram = TelegramAlerter()
        self.journal = TradeJournal()
        self._store = get_store()
        self.reconciliation_engine = ReconciliationEngine(broker=self.broker)
        self.executed_orders = []
        # Tracks symbols whose orders are in-flight (placed but not yet confirmed filled).
        # Prevents duplicate orders when the next cycle runs before Kite confirms a fill.
        self._pending_order_symbols: set = set()
        # Tracks SELL attempts that failed at the broker so they can be retried and surfaced in the dashboard.
        self._pending_sells: Dict[str, Dict] = self._load_pending_sells()
        self.email = EmailReporter()
        self._load_existing_positions()

    def _load_pending_sells(self) -> Dict[str, Dict]:
        """Load persisted pending SELL state from SQLite so the dashboard survives restarts."""
        try:
            state = self._store.load_daily_state()
            _items = state.get('pending_sells', {})
            if isinstance(_items, dict):
                for _v in _items.values():
                    if isinstance(_v.get('next_retry'), str):
                        try:
                            _v['next_retry'] = datetime.fromisoformat(_v['next_retry'])
                        except Exception:
                            _v['next_retry'] = None
                    if isinstance(_v.get('sell_time'), str):
                        try:
                            _v['sell_time'] = datetime.fromisoformat(_v['sell_time'])
                        except Exception:
                            pass
                return _items
            if isinstance(_items, list):
                return {p['symbol']: p for p in _items if p.get('symbol')}
        except Exception as _e:
            logger.warning(f"Could not load pending sells: {_e}")
        return {}

    def _save_pending_sells(self):
        try:
            today = datetime.now().strftime('%Y-%m-%d')
            state = self._store.load_daily_state(today)
            self._store.save_daily_state(
                state_date=today,
                daily_pnl=state.get('daily_pnl', 0.0),
                daily_blacklist=state.get('daily_blacklist', []),
                last_exit_by_symbol=state.get('last_exit_by_symbol', {}),
                daily_trades=state.get('daily_trades', 0),
                pending_sells=dict(self._pending_sells)
            )
        except Exception as _e:
            logger.warning(f"Could not save pending sells: {_e}")

    def _send_error_notification(self, symbol: str, category: str, error: str, exit_reason: str,
                                    retry_count: int, recovery_path: str) -> bool:
        """Send one actionable alert via Telegram and email. Returns True if any channel succeeds."""
        try:
            subject = f"SELL failed for {symbol}: {category}"
            body = (
                f"<b>SELL order failed for {symbol}</b><br><br>"
                f"Exit reason: {exit_reason}<br>"
                f"Broker error: {error}<br>"
                f"Failure category: {category}<br>"
                f"Retry count: {retry_count}<br>"
                f"Recovery: {recovery_path}<br><br>"
                f"The bot will retry automatically when possible."
            )
            ok_email = False
            try:
                ok_email = self.email.send_report(subject, body)
            except Exception as _email_err:
                logger.warning(f"Email notification failed for {symbol}: {_email_err}")

            ok_telegram = False
            try:
                self.telegram._send(body)
                ok_telegram = True
            except Exception as _tg_err:
                logger.warning(f"Telegram notification failed for {symbol}: {_tg_err}")

            return ok_email or ok_telegram
        except Exception as _e:
            logger.warning(f"Could not send error notification for {symbol}: {_e}")
        return False

    def _next_market_open(self, from_dt: Optional[datetime] = None) -> datetime:
        """Return the next NSE market open timestamp (naive local time)."""
        now = from_dt or datetime.now()
        market_time = datetime.strptime(config.MARKET_OPEN, "%H:%M").time()
        candidate = datetime.combine(now.date(), market_time)
        if candidate <= now:
            candidate += timedelta(days=1)
        # Skip weekends (5=Saturday, 6=Sunday)
        while candidate.weekday() >= 5:
            candidate += timedelta(days=1)
        return candidate

    def _compute_next_retry(self, category: str, retry_count: int) -> Optional[datetime]:
        """Calculate the next allowed retry time for a failed SELL based on category policy."""
        policy = get_error_policy(category)
        retry_policy = policy.get('retry_policy')
        now = datetime.now()
        if not policy.get('retry', False) or retry_policy == 'manual':
            return None
        if retry_policy == 'next_session':
            return self._next_market_open(now)
        if retry_policy == 'exponential_backoff':
            # 60s * 2^(retry-1), capped at 30 minutes
            seconds = min(60 * (2 ** (max(retry_count, 1) - 1)), 1800)
            return now + timedelta(seconds=seconds)
        # every_cycle: default to the 15-minute trading cycle used by the orchestrator
        return now + timedelta(minutes=15)

    def _should_attempt_sell(self, symbol: str) -> bool:
        """Respect the retry queue: do not re-attempt a SELL before its scheduled next retry."""
        pending = self._pending_sells.get(symbol)
        if not pending:
            return True
        next_retry = pending.get('next_retry')
        if next_retry is None:
            return False
        return datetime.now() >= datetime.fromisoformat(next_retry)

    def _record_sell_failure(self, symbol: str, order_result: Dict, exit_reason: str):
        """Track a failed SELL, schedule the next retry, and send at most one notification per category per symbol."""
        category = order_result.get('error_category', 'other')
        policy = get_error_policy(category)
        previous = self._pending_sells.get(symbol, {})
        retry_count = previous.get('retry_count', 0) + 1
        notifications_sent = previous.get('notifications_sent', [])
        if not isinstance(notifications_sent, list):
            notifications_sent = []
        if category not in notifications_sent and policy.get('notify'):
            if self._send_error_notification(
                symbol, category,
                order_result.get('error', 'Unknown'),
                exit_reason, retry_count,
                policy['recovery_path']
            ):
                notifications_sent = list(notifications_sent)
                notifications_sent.append(category)

        notification_sent = category in notifications_sent
        next_retry = self._compute_next_retry(category, retry_count)

        self._pending_sells[symbol] = {
            'symbol': symbol,
            'exit_reason': exit_reason,
            'broker_status': order_result.get('error', 'Unknown'),
            'failure_category': category,
            'retry': policy['retry'],
            'retry_policy': policy['retry_policy'],
            'continue_trading': policy['continue_trading'],
            'notify': policy['notify'],
            'recovery_path': policy['recovery_path'],
            'retry_count': retry_count,
            'last_attempt': datetime.now().isoformat(),
            'next_retry': next_retry.isoformat() if next_retry else None,
            'last_error': order_result.get('error', 'Unknown'),
            'notifications_sent': notifications_sent,
            'notification_sent': notification_sent,
        }
        self._save_pending_sells()
        self._log_sell_failure(
            symbol, order_result, category, retry_count,
            notification_sent, exit_reason, policy['recovery_path'], next_retry
        )

    def _log_sell_failure(self, symbol: str, order_result: Dict, category: str, retry_count: int,
                          notification_sent: bool, exit_reason: str, recovery_path: str,
                          next_retry: Optional[datetime]):
        next_retry_str = next_retry.isoformat() if next_retry else 'manual / awaiting user action'
        logger.warning(
            f"SELL Attempt:\n"
            f"Symbol: {symbol}\n"
            f"Broker Response: {order_result.get('error', 'Unknown')}\n"
            f"Failure Category: {category}\n"
            f"Action: Retry Next Cycle\n"
            f"Retry Count: {retry_count}\n"
            f"Next Retry: {next_retry_str}\n"
            f"Notification: {'Sent' if notification_sent else 'Not Sent'}\n"
            f"Recovery: {recovery_path}\n"
            f"Reason: {exit_reason}"
        )

    def _clear_sell_pending(self, symbol: str):
        if symbol in self._pending_sells:
            del self._pending_sells[symbol]
            self._save_pending_sells()

    def reset_pending_sells(self):
        """Clear all pending SELL records. Called at daily reset and end-of-day close."""
        self._pending_sells.clear()
        self._save_pending_sells()

    def get_pending_sells(self) -> List[Dict]:
        """Return current pending SELL actions for the dashboard."""
        return sorted(
            list(self._pending_sells.values()),
            key=lambda x: x.get('next_retry') or x.get('last_attempt', ''),
            reverse=False,
        )
    
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
            already_loaded = {p.symbol for p in self.risk_manager.positions}
            for p in pos_data.get('net', []):
                qty = p.get('quantity', 0)
                if qty <= 0:  # skip zero AND negative (settlement offsets from sold CNC)
                    continue
                symbol = p.get('tradingsymbol')
                if symbol in already_loaded:
                    continue
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
                already_loaded.add(symbol)
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
                    self.market_data.kite.ltp, 'ltp', *keys
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

            if not self._should_attempt_sell(position.symbol):
                queued = self._pending_sells[position.symbol].get('next_retry') or 'manual'
                logger.info(f"SELL for {position.symbol} queued until {queued}")
                continue

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
            order_result = self.execute_signal(sell_signal)

            if order_result['success']:
                exit_signal_out = order_result.get('exit_signal')
                logger.info(
                    f'Holding SOLD {position.symbol} ×{position.quantity} '
                    f'@ ₹{ltp:.2f} | P&L ₹{position.pnl:+.2f} | {exit_reason}'
                )
                executed_exits.append({
                    'success':     True,
                    'order_id':    order_result.get('order_id'),
                    'exit_signal': exit_signal_out,
                    'timestamp':   datetime.now().isoformat(),
                })
                try:
                    self.telegram.exit(exit_signal_out, order_result.get('order_id', ''))
                except Exception:
                    pass
            else:
                self._record_sell_failure(position.symbol, order_result, exit_reason)
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

        if action == 'SELL':
            return self._execute_sell(signal)

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

            # Trigger reconciliation for BUY
            self.reconciliation_engine.trigger('BUY_executed')

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
                _trade_score = float(signal.get('trade_score') or signal.get('overall_score') or 0)
                _market_regime = signal.get('market_regime') or signal.get('regime')
                if not isinstance(_market_regime, str) or not _market_regime:
                    _market_regime = 'UNKNOWN'
                _sector = _SECTOR_MAP.get(signal['symbol'], '')
                if not _sector:
                    _sector = research.get('sector', '')
                if not _sector:
                    _sector = 'Other'
                _sector_momentum = float(research.get('sector_momentum', 0) or 0)
                _sector_strength = 'Strong' if _sector_momentum > 0 else 'Weak' if _sector_momentum < 0 else 'Neutral'
                self.journal.log_entry(
                    symbol=signal['symbol'],
                    action='BUY',
                    price=float(signal.get('current_price') or signal.get('entry_price') or 0),
                    quantity=signal.get('position_size', 1),
                    order_id=order_result.get('order_id') or None,
                    buy_reason=signal.get('reasoning', ''),
                    trade_score=_trade_score,
                    score_components=signal.get('score_components', {}),
                    market_regime=_market_regime,
                    sector=f"{_sector} ({_sector_strength})",
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
    
    def _log_sell_decision(
        self,
        origin: str,
        signal: Dict,
        position: Position,
        validated: bool,
        validation_message: str,
    ) -> None:
        """Structured SELL decision audit log."""
        hours_held = (
            (datetime.now() - position.entry_time).total_seconds() / 3600
            if position.entry_time else 0.0
        )
        effective_sl = max(
            position.stop_loss or 0,
            (position.trailing_stop or 0) if position.trailing_stop else 0,
        )
        logger.info(
            f"SELL_PIPELINE | origin={origin} | symbol={signal.get('symbol')} "
            f"| action=SELL | validated={validated} | reason='{signal.get('reasoning','')}' "
            f"| validation_message='{validation_message}' "
            f"| current_price={signal.get('current_price')} "
            f"| entry={position.entry_price} | stop_loss={position.stop_loss} "
            f"| trailing_stop={position.trailing_stop} | target={position.target} "
            f"| effective_sl={effective_sl} | quantity={position.quantity} "
            f"| hours_held={hours_held:.3f}"
        )

    def _validate_swing_sell(self, signal: Dict, position: Position) -> Tuple[bool, str]:
        """Final swing-mode SELL validation: block unless a real exit trigger is met."""
        if config.TRADING_MODE != "swing":
            return True, "non-swing mode: validation skipped"

        current_price = float(signal.get('current_price', 0) or 0)
        reason = (signal.get('reasoning', '') or '').lower()
        now = datetime.now()
        hours_held = (
            (now - position.entry_time).total_seconds() / 3600
            if position.entry_time else 0.0
        )

        # 1. Manual / circuit breaker / EOD / rebalance / AI sell: trust the orchestrator tag
        if any(k in reason for k in ('manual', 'rebalance', 'end of day', 'circuit breaker')):
            return True, f"swing manual/administrative exit allowed: '{reason}'"
        if signal.get('_ai_sell_decision'):
            return True, f"swing AI sell decision allowed: '{reason}'"
        if signal.get('_circuit_breaker'):
            return True, "swing circuit breaker exit allowed"

        # 2. Target hit
        if position.target and current_price >= position.target:
            return True, f"target hit: {current_price} >= {position.target}"

        # 3. Stop loss / trailing stop
        effective_sl = max(
            position.stop_loss or 0,
            (position.trailing_stop or 0) if position.trailing_stop else 0,
        )
        if current_price <= effective_sl:
            if position.trailing_stop and current_price <= position.trailing_stop:
                return True, f"trailing stop hit: {current_price} <= {position.trailing_stop}"
            return True, f"stop loss hit: {current_price} <= {position.stop_loss}"

        # 4. Max hold days
        if position.planned_exit_date and now >= position.planned_exit_date:
            return True, f"max holding days reached: {now} >= {position.planned_exit_date}"

        # 5. Smart exit
        if signal.get('_smart_exit'):
            return True, f"smart exit allowed: '{reason}'"

        # 6. Minimum holding period for any other SELL in swing mode
        if hours_held < config.MIN_HOLD_HOURS:
            return False, (
                f"swing SELL blocked: held {hours_held:.2f}h < minimum {config.MIN_HOLD_HOURS}h "
                f"and no real exit trigger (target/SL/trail/smart/AI/manual/rebalance/EOD/circuit)"
            )

        # If it reached the minimum hold but has no valid reason, still block in swing mode
        return False, (
            f"swing SELL blocked: no valid swing exit trigger for reason='{reason}' "
            f"(price {current_price} above SL {position.stop_loss}, trail {position.trailing_stop}, "
            f"target {position.target})"
        )

    def _execute_sell(self, signal: Dict) -> Dict:
        """Execute a SELL signal for an open/partial position with minimum-profit guard."""
        sym = signal['symbol']
        current_price = float(signal.get('current_price', 0) or 0)
        reason = signal.get('reasoning', '') or 'Manual close'

        position = next(
            (p for p in self.risk_manager.positions
             if p.symbol == sym and p.status in {PositionStatus.OPEN, PositionStatus.PARTIAL}),
            None
        )
        if not position:
            logger.warning(f"SELL {sym}: no open/partial position found")
            return {'success': False, 'reason': 'No open position', 'signal': signal}

        # Ensure the signal always carries the intended quantity (defensive for downstream code)
        signal.setdefault('position_size', position.quantity)

        # Final swing-mode SELL validation: hard last-line-of-defense
        origin = signal.get('_origin', 'unknown')
        validated, validation_message = self._validate_swing_sell(signal, position)
        self._log_sell_decision(origin, signal, position, validated, validation_message)
        if not validated:
            logger.warning(f"SELL {sym} REJECTED by swing validation: {validation_message}")
            return {'success': False, 'reason': validation_message, 'signal': signal}

        # Minimum Profit Rule: sell below entry only when AI explicitly allows it or it is a stop-loss
        price_below_entry = current_price < position.entry_price
        explicit_stop = any(k in reason.lower() for k in ('stop', 'sl', 'stopped', 'loss'))
        allow_loss_exit = bool(signal.get('allow_loss_exit', False))
        if price_below_entry and not (allow_loss_exit or explicit_stop):
            logger.info(
                f"SELL {sym} blocked: price ₹{current_price:.2f} below entry ₹{position.entry_price:.2f} "
                f"and reason '{reason}' is not an allowed loss exit"
            )
            return {'success': False, 'reason': 'Price below entry without allowed loss exit', 'signal': signal}

        if not self._should_attempt_sell(sym):
            queued = self._pending_sells[sym].get('next_retry') or 'manual'
            logger.info(f"SELL for {sym} queued until {queued}")
            return {'success': False, 'error': 'Queued for retry', 'signal': signal}

        order_result = self.broker.place_order(signal)
        if not order_result['success']:
            self._record_sell_failure(sym, order_result, reason)
            logger.error(f"SELL order failed for {sym}: {order_result.get('error')}")
            return {'success': False, 'error': order_result.get('error'), 'signal': signal}

        self._clear_sell_pending(sym)
        sell_qty = int(signal.get('position_size', position.quantity) or 0)
        try:
            exit_signal = self.risk_manager.close_position(sym, current_price, reason, sell_qty)
        except Exception as ce:
            logger.exception(f"Risk manager close_position failed for {sym} (order already placed): {ce}")
            exit_signal = None
        try:
            self.journal.log_entry(
                symbol=sym,
                action='SELL',
                price=current_price,
                quantity=sell_qty,
                order_id=order_result.get('order_id') or None,
                exit_reason=reason,
                entry_price=position.entry_price,
                entry_date=position.entry_time.isoformat() if position.entry_time else '',
                gross_pnl=(current_price - position.entry_price) * sell_qty,
                net_pnl=exit_signal['net_pnl'] if exit_signal else 0,
                charges=exit_signal['charges'] if exit_signal else 0,
            )
        except Exception as je:
            logger.warning(f"Journal SELL log failed: {je}")

        logger.info(f"SELL executed: {sym} @ ₹{current_price:.2f} Reason: {reason}")

        # Trigger reconciliation for SELL
        self.reconciliation_engine.trigger('SELL_executed')

        return {
            'success': True,
            'order_id': order_result.get('order_id'),
            'exit_signal': exit_signal,
            'paper_trading': order_result.get('paper_trading', False),
        }

    def monitor_positions(self) -> List[Dict]:
        """
        Monitor open positions using the enterprise trade lifecycle manager.
        Executes trailing stops, partial scale-outs, time/volatility/gap exits
        and scale-in opportunities.
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
                    self.market_data.kite.ltp, 'ltp', *keys
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

        # Lifecycle manager produces all actions (HOLD, SELL, SCALE_IN)
        actions = self.lifecycle_manager.process_positions(positions, current_prices)

        executed = []
        for action in actions:
            if action.get('action') == 'SELL':
                sym = action['symbol']
                if not self._should_attempt_sell(sym):
                    queued = self._pending_sells.get(sym, {}).get('next_retry') or 'manual'
                    logger.info(f"SELL for {sym} queued until {queued}")
                    continue

                sell_signal = {
                    'symbol': sym,
                    'action': 'SELL',
                    'current_price': action['price'],
                    'position_size': action['quantity'],
                    'investment_amount': action['price'] * action['quantity'],
                    'stop_loss': 0,
                    'target': 0,
                    'risk_reward_ratio': 0,
                    'confidence': 1.0,
                    'overall_score': 0,
                    'reasoning': action['reason'],
                    '_lifecycle': True,
                    '_origin': 'order_executor.monitor_positions',
                    'timestamp': datetime.now().isoformat()
                }
                logger.info(
                    f"SELL_PIPELINE | origin=order_executor.monitor_positions "
                    f"| symbol={sym} | proposed_price={action['price']} | reason='{action['reason']}'"
                )
                order_result = self.execute_signal(sell_signal)
                if order_result['success']:
                    executed.append({
                        'success': True,
                        'order_id': order_result.get('order_id'),
                        'exit_signal': order_result.get('exit_signal', action),
                        'lifecycle_state': action.get('lifecycle_state'),
                        'timestamp': datetime.now().isoformat()
                    })
                    logger.info(f"Lifecycle exit executed for {sym}: {action['reason']}")
                    try:
                        self.telegram.exit(action, order_result.get('order_id', ''))
                    except Exception as te:
                        logger.error(f"Telegram exit alert error: {te}")
                else:
                    self._record_sell_failure(sym, order_result, action['reason'])
                    logger.error(f"Lifecycle exit failed for {sym}: {order_result}")
            elif action.get('action') == 'SCALE_IN':
                order_result = self.execute_scale_in(action)
                if order_result['success']:
                    executed.append({
                        'success': True,
                        'order_id': order_result.get('order_id'),
                        'scale_in': action,
                        'timestamp': datetime.now().isoformat()
                    })
                    logger.info(f"Scale-in executed for {action['symbol']}: +{action['quantity']}")
                else:
                    logger.warning(f"Scale-in failed for {action['symbol']}: {order_result}")
            else:
                # HOLD action with lifecycle metadata for dashboard
                executed.append(action)

        # Persist updated stops / targets
        self.risk_manager.save_positions()
        return executed

    def execute_scale_in(self, action: Dict) -> Dict:
        """Place a scale-in BUY for an existing position."""
        sym = action['symbol']
        qty = action.get('quantity', 0)
        price = action.get('price', 0.0)
        if qty <= 0 or price <= 0:
            return {'success': False, 'reason': 'Invalid scale-in qty/price'}

        buy_signal = {
            'symbol': sym,
            'action': 'BUY',
            'current_price': price,
            'position_size': qty,
            'investment_amount': price * qty,
            'stop_loss': action.get('stop_loss', price * 0.95),
            'target': action.get('target', price * 1.05),
            'atr': action.get('atr', 0.0),
            'risk_reward_ratio': 0,
            'confidence': 1.0,
            'overall_score': 0,
            'reasoning': action.get('reason', 'Scale-in'),
            '_scale_in': True,
            'timestamp': datetime.now().isoformat()
        }

        order_result = self.broker.place_order(buy_signal)
        if not order_result['success']:
            return {'success': False, 'error': order_result.get('error')}

        # Update position with new capital
        self.risk_manager.add_to_position(
            sym,
            qty,
            price,
            buy_signal['stop_loss'],
            buy_signal['target'],
            buy_signal['atr']
        )

        # Log in journal
        try:
            position = next(
                (p for p in self.risk_manager.positions if p.symbol == sym and p.status in {PositionStatus.OPEN, PositionStatus.PARTIAL}),
                None
            )
            self.journal.log_entry(
                symbol=sym,
                action='BUY',
                price=price,
                quantity=qty,
                order_id=order_result.get('order_id') or None,
                exit_reason='Scale-in',
                entry_price=position.entry_price if position else price,
                entry_date=position.entry_time.isoformat() if position and position.entry_time else '',
                gross_pnl=0,
                net_pnl=0,
                charges=0,
            )
        except Exception as je:
            logger.warning(f"Journal scale-in log failed: {je}")

        try:
            self.reconciliation_engine.trigger('BUY_executed')
        except Exception:
            pass

        return {
            'success': True,
            'order_id': order_result.get('order_id'),
            'paper_trading': order_result.get('paper_trading', True)
        }
    
    def close_all_positions(self, reason: str = 'End of day close') -> List[Dict]:
        """
        Close all open positions (e.g. end-of-day or emergency circuit breaker).

        Args:
            reason: Exit reason to record in position, journal, and logs.

        Returns:
            List of close results
        """
        logger.info(f"Closing all positions ({reason})")

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

        close_results = []
        for p in positions:
            if p.status not in {PositionStatus.OPEN, PositionStatus.PARTIAL}:
                continue
            current_price = current_prices.get(p.symbol, p.entry_price)
            sell_signal = {
                'symbol': p.symbol,
                'action': 'SELL',
                'current_price': current_price,
                'position_size': p.quantity,
                'investment_amount': current_price * p.quantity,
                'stop_loss': 0,
                'target': 0,
                'risk_reward_ratio': 0,
                'confidence': 1.0,
                'overall_score': 0,
                'reasoning': reason,
                'allow_loss_exit': True,
                'timestamp': datetime.now().isoformat()
            }
            # Emergency closes should not be blocked by the retry queue
            self._clear_sell_pending(p.symbol)
            order_result = self.execute_signal(sell_signal)
            close_results.append({
                'success': order_result.get('success', False),
                'order_id': order_result.get('order_id'),
                'exit_signal': order_result.get('exit_signal'),
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
        """Reset daily statistics and pending SELL notifications"""
        self.risk_manager.reset_daily()
        self.reset_pending_sells()
        logger.info("Order executor daily reset completed")
