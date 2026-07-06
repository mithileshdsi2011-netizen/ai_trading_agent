"""
Main Trading Orchestrator Module
Coordinates all components for automated trading
"""
import logging
import os
import schedule
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import json
import pytz

from config import config
from market_data import MarketDataFetcher
from signal_generator import SignalGenerator
from order_executor import OrderExecutor
from dynamic_universe import DynamicUniverse
from market_regime import MarketRegimeDetector
from telegram_alerts import TelegramAlerter
from email_reports import EmailReporter
from trade_scorer import TradeScorer, SCORE_SKIP
from multi_timeframe import MultiTimeframeConfirmer
from smart_exit import SmartExitAI
from risk_manager import PositionStatus

import os as _os
_log_dir = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), 'logs')
_os.makedirs(_log_dir, exist_ok=True)
_file_handler    = logging.FileHandler(_os.path.join(_log_dir, 'trading.log'))
_file_handler.setLevel(logging.INFO)
_file_handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(name)s: %(message)s'))

_console_handler = logging.StreamHandler()
_console_handler.setLevel(logging.WARNING)   # only WARN/ERROR/CRITICAL to terminal
_console_handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s: %(message)s'))

logging.basicConfig(level=logging.INFO, handlers=[_file_handler, _console_handler])

# Silence very chatty sub-modules in console (they still write to file)
for _noisy in ('market_data', 'technical_analysis', 'ai_research_agent',
               'dynamic_universe', 'multi_timeframe', 'trade_scorer',
               'sentiment_analysis', 'signal_generator', 'werkzeug'):
    logging.getLogger(_noisy).setLevel(logging.WARNING)

logger = logging.getLogger(__name__)


class TradingOrchestrator:
    """Main orchestrator for AI trading system"""
    
    def __init__(self):
        self.market_data = MarketDataFetcher()
        self.signal_generator = SignalGenerator()
        self.order_executor = OrderExecutor()
        self.dynamic_universe = DynamicUniverse(kite=self.market_data.kite)
        self.market_regime = MarketRegimeDetector(kite=self.market_data.kite)
        self.telegram = TelegramAlerter()
        self.email = EmailReporter()
        self.scorer = TradeScorer()
        self.mtf = MultiTimeframeConfirmer(market_data=self.market_data)
        self.smart_exit = SmartExitAI(market_data=self.market_data)
        self.is_running = False
        self.trade_log = []
        self._last_rebalance_week: Optional[int] = None
        # Re-entry tracking: symbol -> {exit_price, exit_time, exit_reason}
        self._recently_sold: Dict[str, Dict] = {}
    
    def run_once(self) -> Dict:
        """
        Run a single trading cycle
        
        Returns:
            Trading cycle results
        """
        logger.info("=" * 50)
        logger.info(f"Starting trading cycle at {datetime.now()}")
        
        cycle_result = {
            'timestamp': datetime.now().isoformat(),
            'market_open': self.market_data.is_market_open(),
            'signals_generated': [],
            'orders_executed': [],
            'positions_monitored': [],
            'errors': []
        }
        
        # Check if market is open (includes holiday check)
        if not cycle_result['market_open']:
            if self.market_data.is_market_holiday():
                logger.info("NSE holiday today — bot paused, no trading")
            else:
                logger.info("Market is closed — skipping trading cycle")
            return cycle_result

        # Check if past intraday cutoff — only monitor/close, no new BUYs
        ist = pytz.timezone('Asia/Kolkata')
        now_ist = datetime.now(ist)
        cutoff_h, cutoff_m = map(int, config.INTRADAY_CUTOFF.split(':'))
        past_cutoff = (now_ist.hour, now_ist.minute) >= (cutoff_h, cutoff_m)
        if past_cutoff:
            if config.TRADING_MODE == "swing":
                logger.warning(f"Past intraday cutoff ({config.INTRADAY_CUTOFF}) — swing mode: monitoring only, no new BUYs")
                position_updates = self.order_executor.monitor_positions()
                cycle_result['positions_monitored'] = position_updates
                return cycle_result
            else:
                logger.warning(f"Past intraday cutoff ({config.INTRADAY_CUTOFF}) — closing all positions, no new orders")
                self.end_of_day_close()
                return cycle_result

        # Check if we should stop trading
        if self.order_executor.should_stop_trading():
            logger.warning("Trading stopped due to risk limits")
            cycle_result['errors'].append("Trading stopped due to risk limits")
            return cycle_result
        
        # Emergency circuit breaker: if drawdown > 15% from peak, sell all positions
        # Only fires if holdings API succeeds — never close on a timeout/error
        try:
            holdings = self.order_executor.broker.get_holdings()
            total_value = holdings.get('total_value', 0)
            # Use peak value file as baseline; fall back to total_value itself (no false trigger)
            peak_path = os.path.join(os.path.dirname(__file__), '..', 'data', 'peak_value.json')
            try:
                with open(peak_path) as _pf:
                    _pd = json.load(_pf)
                    peak_value = float(_pd.get('peak_value', total_value))
            except Exception:
                peak_value = total_value
            # Update peak if current value is higher
            if total_value > peak_value:
                peak_value = total_value
                try:
                    with open(peak_path, 'w') as _pf:
                        json.dump({'peak_value': peak_value}, _pf)
                except Exception:
                    pass
            # Only trigger if we have real data (total_value > 0) and genuine drawdown > 15%
            if total_value > 0 and peak_value > 0:
                drawdown = (peak_value - total_value) / peak_value
                if drawdown > 0.15:
                    logger.error(f"EMERGENCY CIRCUIT BREAKER: drawdown {drawdown:.2%} from peak ₹{peak_value:.0f} — closing all positions")
                    close_results = self.order_executor.close_all_positions()
                    cycle_result['close_results'] = close_results
                    cycle_result['errors'].append(f"Circuit breaker triggered: drawdown {drawdown:.2%}")
                    try:
                        self.telegram._send(f"🔴 EMERGENCY CIRCUIT BREAKER\n\nDrawdown: {drawdown:.2%} from peak ₹{peak_value:.0f}\nAll positions closed.")
                    except Exception:
                        pass
                    return cycle_result
        except Exception as e:
            logger.warning(f"Circuit breaker check failed (skipping): {e}")
        
        try:
            # --- Build dynamic universe from live Kite data ---
            logger.info("Building dynamic stock universe from NSE via Kite...")
            try:
                universe = self.dynamic_universe.get_intraday_candidates(
                    top_n=config.DYNAMIC_UNIVERSE_SIZE
                )
                logger.info(f"Dynamic universe: {len(universe)} stocks selected")
            except Exception as ue:
                logger.warning(f"Dynamic universe failed ({ue}), using fallback watchlist")
                universe = config.WATCHLIST

            # --- Read available cash and open positions from broker ---
            holdings = self.order_executor.broker.get_holdings()
            available_cash = holdings.get("cash", config.TRADING_AMOUNT)
            budget = min(available_cash, config.TRADING_AMOUNT)
            logger.info(f"Available budget for this cycle: ₹{budget:.2f}")

            # Get live open positions from broker to prevent duplicates and enforce capital usage
            open_symbols = set()
            current_invested = 0.0
            try:
                pos_data = self.order_executor.broker.kite.positions()
                for p in pos_data.get('net', []):
                    if p.get('quantity', 0) != 0:
                        open_symbols.add(p.get('tradingsymbol'))
                        current_invested += p.get('average_price', 0) * p.get('quantity', 0)
            except Exception as e:
                logger.warning(f"Could not read broker positions: {e}")
            # Also add delivery holdings so bot never re-buys already-held stocks
            try:
                for h in holdings.get('positions', []):
                    qty = h.get('quantity', 0) or h.get('opening_quantity', 0)
                    if qty > 0:
                        open_symbols.add(h.get('tradingsymbol'))
                        current_invested += h.get('average_price', 0) * qty
            except Exception:
                pass
            logger.info(f"Already held symbols (positions+holdings): {open_symbols}")

            # --- Market regime check ---
            regime = "SIDEWAYS"
            if config.MARKET_REGIME_ENABLED:
                regime = self.market_regime.detect_regime()
                logger.info(f"Market regime: {regime}")
                if regime == "BEAR":
                    logger.warning("Bear market detected — no new BUYs, running smart exit + monitoring")
                    # Fix 6: always protect open positions even in bear regime
                    try:
                        bear_positions = [
                            p for p in self.order_executor.risk_manager.positions
                            if p.status in {PositionStatus.OPEN, PositionStatus.PARTIAL}
                        ]
                        if bear_positions:
                            bear_prices = {}
                            for bp in bear_positions:
                                pr = self.market_data.get_realtime_price(bp.symbol)
                                if pr:
                                    bear_prices[bp.symbol] = pr
                            smart_exits = self.smart_exit.check_all(bear_positions, bear_prices, regime)
                            for se in smart_exits:
                                logger.info(f"Bear SmartExit: {se['symbol']} — {se['reason']}")
                                self.order_executor.execute_signal({
                                    'symbol': se['symbol'], 'action': 'SELL',
                                    'current_price': se['price'],
                                    'position_size': se['quantity'],
                                    'investment_amount': se['price'] * se['quantity'],
                                    'stop_loss': 0, 'target': 0,
                                    'risk_reward_ratio': 0, 'confidence': 1.0,
                                    'overall_score': 0, 'reasoning': se['reason'],
                                    'timestamp': datetime.now().isoformat(),
                                })
                    except Exception as be:
                        logger.error(f"Bear regime smart exit error: {be}")
                    position_updates = self.order_executor.monitor_positions()
                    cycle_result['positions_monitored'] = position_updates
                    return cycle_result

            # --- Generate signals for dynamic universe ---
            logger.info(f"Generating signals for {len(universe)} stocks...")
            signals = self.signal_generator.generate_signals_for_watchlist(universe)
            cycle_result['signals_generated'] = signals
            logger.info(f"Generated {len(signals)} actionable signals")

            # --- Log all generated signals for diagnostics ---
            for sig in signals[:10]:
                logger.info(
                    f"  Signal: {sig['symbol']:12s} action={sig.get('action','?'):4s} "
                    f"confidence={sig.get('confidence',0):.0%} "
                    f"score={sig.get('overall_score',0):.3f} "
                    f"rr={sig.get('risk_reward_ratio',0):.2f} "
                    f"atr={sig.get('atr',0):.2f} "
                    f"trend={sig.get('trend','?')}"
                )

            # --- Dynamic capital: invest 70% of current portfolio value ---
            try:
                holdings = self.order_executor.broker.get_holdings()
                portfolio_value = holdings.get('total_value', config.TRADING_AMOUNT)
                dynamic_budget = portfolio_value * 0.60  # conservative: deploy max 60% of portfolio
                budget = max(budget, dynamic_budget)
                logger.info(f"Dynamic budget: ₹{budget:.0f} (60% of ₹{portfolio_value:.0f} portfolio — conservative cap)")
            except Exception:
                pass

            # --- Execute BUY signals with scoring, MTF, news filter ---
            buy_signals = [s for s in signals if s.get('action') == 'BUY']
            if buy_signals:
                # Only count OPEN slots not already used
                open_slot_count = len([p for p in self.order_executor.risk_manager.positions
                                       if p.status in {PositionStatus.OPEN, PositionStatus.PARTIAL}])
                remaining_slots = max(1, config.MAX_POSITIONS - open_slot_count)
                # Conservative: max 2 new buys per cycle to avoid over-trading
                MAX_BUYS_PER_CYCLE = 2
                slots = min(len(buy_signals), remaining_slots, MAX_BUYS_PER_CYCLE)
                per_stock_budget = budget / remaining_slots  # spread budget over available slots
                logger.info(
                    f"Splitting ₹{budget:.0f} across {remaining_slots} remaining slot(s) "
                    f"(₹{per_stock_budget:.0f} each) — max {MAX_BUYS_PER_CYCLE} buys this cycle"
                )
                for best_signal in buy_signals[:slots]:
                    sym = best_signal['symbol']

                    # ── 1. Duplicate / already held ────────────────────────
                    if sym in open_symbols:
                        logger.warning(f"Skipping {sym}: already held")
                        continue

                    # ── 2. Sector correlation ──────────────────────────────────
                    if self._is_correlated_with_open(sym, open_symbols):
                        logger.warning(f"Skipping {sym}: correlated with open position")
                        continue

                    # ── 3. Negative news filter ────────────────────────────────
                    if self._has_negative_news(best_signal):
                        logger.warning(f"Skipping {sym}: negative news filter")
                        continue

                    # ── 4. Bear regime guard ───────────────────────────────────
                    if regime == 'BEAR':
                        logger.warning(f"Skipping {sym}: no new BUYs in BEAR regime")
                        continue

                    # ── 5. Trade Scoring ───────────────────────────────────────
                    research = best_signal.get('_research', {})
                    score_result = self.scorer.score(
                        signal=best_signal,
                        research=research,
                        regime=regime,
                        sector_momentum=research.get('sector_momentum', 0.0),
                    )
                    if score_result['skip']:
                        logger.info(
                            f"Skipping {sym}: score {score_result['total_score']}/100 "
                            f"({score_result['grade']}) below threshold"
                        )
                        continue

                    # ── 6. Minimum R:R guard (1.5:1) ──────────────────────────
                    rr = best_signal.get('risk_reward_ratio', 0)
                    if rr < 1.5:
                        logger.warning(f"Skipping {sym}: R:R {rr:.2f} below minimum 1.5:1")
                        continue

                    # ── 7. Multi-timeframe confirmation ────────────────────────
                    mtf_result = self.mtf.confirm(sym)
                    if not mtf_result['aligned']:
                        logger.warning(f"Skipping {sym}: MTF not aligned — {mtf_result['reason']}")
                        continue

                    # ── 8. Re-entry gate (all conditions) ─────────────────────
                    reentry_result = self._check_reentry_eligibility(
                        sym,
                        current_price=best_signal.get('current_price', 0),
                        score=score_result['total_score'],
                        mtf_aligned=mtf_result['aligned'],
                        regime=regime,
                        rr=rr,
                        has_negative_news=self._has_negative_news(best_signal),
                        projected_invested=current_invested + best_signal.get('investment_amount', 0),
                        max_allowed_invested=available_cash * config.MAX_CAPITAL_USAGE,
                    )
                    if isinstance(reentry_result, str):  # blocked — reason string
                        logger.info(f"Skipping {sym}: re-entry not ready — {reentry_result}")
                        continue
                    if isinstance(reentry_result, dict):  # approved re-entry — attach metadata
                        best_signal['_reentry_meta'] = reentry_result
                        best_signal['_reentry_meta']['reentry_confidence'] = best_signal.get('confidence', 0)

                    # ── Adaptive position size (score × confidence) ────────────
                    price      = best_signal.get('current_price', 1)
                    confidence = best_signal.get('confidence', 0.5)
                    # Combined multiplier: score fraction × confidence tier
                    conf_mult  = self._confidence_multiplier(confidence)
                    slot_budget = per_stock_budget * score_result['size_fraction'] * conf_mult
                    qty = max(1, int(slot_budget / price))
                    best_signal['position_size']     = qty
                    best_signal['investment_amount'] = qty * price
                    best_signal['trade_score']        = score_result['total_score']
                    best_signal['score_components']   = score_result['components']
                    best_signal['mtf_aligned']        = mtf_result['strict']
                    best_signal['market_regime']      = regime  # Fix 7: journal uses this

                    # Capital utilization guard — use actual available cash, not fixed config amount
                    projected_invested = current_invested + best_signal['investment_amount']
                    max_allowed_invested = available_cash * config.MAX_CAPITAL_USAGE
                    if projected_invested > max_allowed_invested:
                        logger.warning(
                            f"Skipping {sym}: capital limit (₹{projected_invested:.0f} > ₹{max_allowed_invested:.0f})"
                        )
                        continue

                    logger.info(
                        f"Executing BUY: {sym} ×{qty} @ ₹{price:.2f} = ₹{qty*price:.2f} "
                        f"| Score:{score_result['total_score']}/100 "
                        f"Conf:{confidence:.0%} SizeFrac:{score_result['size_fraction']:.0%} "
                        f"MTF:{'strict' if mtf_result['strict'] else 'relaxed'}"
                    )
                    execution_result = self.order_executor.execute_signal(best_signal)
                    cycle_result['orders_executed'].append(execution_result)
                    if execution_result['success']:
                        logger.info(f"Order executed: {sym}")
                        self.trade_log.append(execution_result)
                        open_symbols.add(sym)
                        current_invested += best_signal['investment_amount']
                        try:
                            self.telegram.buy(best_signal, execution_result.get('order_id', ''))
                        except Exception as te:
                            logger.error(f"Telegram alert error: {te}")
                    else:
                        logger.error(f"Order failed: {execution_result.get('error')}")
                        cycle_result['errors'].append(execution_result.get('error'))

            # --- Execute SELL signals — only for stocks we actually hold ---
            sell_signals = [s for s in signals if s.get('action') == 'SELL']
            for sell_signal in sell_signals:
                sym = sell_signal['symbol']
                # Fix 5: only sell if we hold the position
                if sym not in open_symbols:
                    logger.info(f"Skipping SELL {sym}: not in open positions")
                    continue
                logger.info(f"Sell signal: {sym} confidence {sell_signal.get('confidence', 0):.0%}")
                execution_result = self.order_executor.execute_signal(sell_signal)
                cycle_result['orders_executed'].append(execution_result)
                if execution_result['success']:
                    logger.info(f"Sell order executed: {sym}")
                    self.trade_log.append(execution_result)
                    # Record exit for re-entry engine
                    self._record_exit(sym, sell_signal.get('current_price', 0), 'signal')
            
            # --- Smart Exit AI: evaluate open positions ---
            open_positions = [
                p for p in self.order_executor.risk_manager.positions
                if p.status in {PositionStatus.OPEN, PositionStatus.PARTIAL}
            ]
            if open_positions:
                prices_map = {}
                for p in open_positions:
                    pr = self.market_data.get_realtime_price(p.symbol)
                    if pr:
                        prices_map[p.symbol] = pr
                smart_exits = self.smart_exit.check_all(open_positions, prices_map, regime)
                for se in smart_exits:
                    logger.info(f"SmartExit: {se['symbol']} — {se['reason']}")
                    exec_r = self.order_executor.execute_signal({
                        'symbol': se['symbol'], 'action': 'SELL',
                        'current_price': se['price'],
                        'position_size': se['quantity'],
                        'investment_amount': se['price'] * se['quantity'],
                        'stop_loss': 0, 'target': 0,
                        'risk_reward_ratio': 0, 'confidence': 1.0,
                        'overall_score': 0, 'reasoning': se['reason'],
                        'timestamp': datetime.now().isoformat(),
                        # record exit for re-entry engine (checked after execute)
                        '_smart_exit': True, '_exit_price': se['price'],
                    })
                    if exec_r['success']:
                        cycle_result['orders_executed'].append(exec_r)
                        self.trade_log.append(exec_r)
                        self._record_exit(se['symbol'], se['price'], se.get('reason', 'smart_exit'))
                        try:
                            self.telegram.exit(se, exec_r.get('order_id', ''))
                        except Exception:
                            pass

            # --- Monitor existing positions (SL / target / trailing) ---
            logger.info("Monitoring existing positions...")
            position_updates = self.order_executor.monitor_positions()
            cycle_result['positions_monitored'] = position_updates

            if position_updates:
                logger.info(f"Position updates: {len(position_updates)}")
                for update in position_updates:
                    self.trade_log.append(update)
                    # Telegram alert for partial exits
                    if update.get('success') and update.get('exit_signal', {}).get('partial'):
                        try:
                            es = update['exit_signal']
                            self.telegram._send(
                                f"🟡 <b>PARTIAL PROFIT BOOKED</b>\n\n"
                                f"Symbol: {es['symbol']}\n"
                                f"Qty sold: {es['quantity']}\n"
                                f"Price: ₹{es['price']:.2f}\n"
                                f"P&amp;L: ₹{es.get('pnl',0):.2f}\n"
                                f"Net P&amp;L: ₹{es.get('net_pnl',0):.2f}\n"
                                f"Reason: {es.get('reason','')}"
                            )
                        except Exception:
                            pass
            
            # Get execution summary
            summary = self.order_executor.get_execution_summary()
            logger.info(f"Execution summary: {json.dumps(summary, indent=2, default=str)}")

            # Weekly rebalance (Fridays only, once per week)
            try:
                self.weekly_rebalance()
            except Exception as re:
                logger.error(f"Weekly rebalance error: {re}")
            
        except Exception as e:
            logger.error(f"Error in trading cycle: {e}")
            cycle_result['errors'].append(str(e))
        
        logger.info(f"Trading cycle completed at {datetime.now()}")
        logger.info("=" * 50)
        
        return cycle_result
    
    def run_scheduled(self, interval_minutes: int = 15):
        """
        Run trading on a schedule
        
        Args:
            interval_minutes: Interval between trading cycles in minutes
        """
        logger.info(f"Starting scheduled trading with {interval_minutes} minute intervals")
        self.is_running = True
        
        # Schedule trading cycles
        schedule.every(interval_minutes).minutes.do(self.run_once)
        
        # Schedule end-of-day close at 14:55 IST — 5 min before cutoff
        schedule.every().day.at("14:55").do(self.end_of_day_close)
        
        # Schedule daily email report at 4:00 PM IST
        schedule.every().day.at("16:00").do(self.daily_email_report)
        
        # Schedule pre-market check (9:20 AM IST - 10 minutes before open)
        schedule.every().day.at("09:20").do(self.pre_market_check)
        
        # Schedule daily reset
        schedule.every().day.at("09:00").do(self.daily_reset)

        # Run one cycle immediately on startup so we don't wait up to 15 min
        logger.info("Running immediate startup cycle...")
        try:
            self.run_once()
        except Exception as startup_err:
            logger.error(f"Startup cycle error: {startup_err}")

        try:
            while self.is_running:
                schedule.run_pending()
                time.sleep(60)  # Check every minute
        except KeyboardInterrupt:
            logger.info("Trading stopped by user")
            self.is_running = False
        except Exception as e:
            logger.error(f"Error in scheduled trading: {e}")
            self.is_running = False
    
    def stop(self):
        """Stop the trading orchestrator"""
        logger.info("Stopping trading orchestrator")
        self.is_running = False
    
    # KNOWN SECTOR CORRELATION GROUPS (NSE)
    # Stocks in the same group are assumed highly correlated.
    # If one is already held, skip others in the group.
    _SECTOR_GROUPS = [
        # ── Banking & Finance ─────────────────────────────────────────────────
        {"HDFCBANK", "ICICIBANK", "KOTAKBANK", "AXISBANK", "SBIN", "INDUSINDBK",
         "BANDHANBNK", "FEDERALBNK", "IDFCFIRSTB", "AUBANK", "CANBK",
         "BANKBARODA", "PNB", "UNIONBANK", "INDIANB"},
        # ── NBFCs & Fintech ────────────────────────────────────────────────────
        {"BAJFINANCE", "BAJAJFINSV", "CHOLAFIN", "MUTHOOTFIN", "MANAPPURAM",
         "CDSL", "BSE", "MCX", "ANGELONE"},
        # ── Insurance ─────────────────────────────────────────────────────────
        {"HDFCLIFE", "SBILIFE", "ICICIGI", "NIACL", "GICRE"},
        # ── IT & Technology ───────────────────────────────────────────────────
        {"TCS", "INFY", "WIPRO", "HCLTECH", "TECHM", "LTIM", "LTTS",
         "PERSISTENT", "MPHASIS", "COFORGE", "KPITTECH"},
        # ── Pharma & Healthcare ───────────────────────────────────────────────
        {"SUNPHARMA", "DRREDDY", "CIPLA", "DIVISLAB", "APOLLOHOSP",
         "LUPIN", "AUROPHARMA", "BIOCON", "ALKEM", "GLENMARK",
         "METROPOLIS", "LALPATHLAB", "THYROCARE"},
        # ── Auto & Auto Ancillaries ───────────────────────────────────────────
        {"TATAMOTORS", "MARUTI", "M&M", "BAJAJ-AUTO", "HEROMOTOCO", "EICHERMOT",
         "MOTHERSON", "BOSCHLTD", "BHARATFORG", "APOLLOTYRE", "MRF",
         "BALKRISIND", "EXIDEIND"},
        # ── Oil, Gas & Petrochemicals ─────────────────────────────────────────
        {"RELIANCE", "ONGC", "BPCL", "HINDPETRO", "IOC", "GAIL",
         "IGL", "MGL", "PETRONET", "DEEPAKNTR"},
        # ── Metals & Mining ───────────────────────────────────────────────────
        {"HINDALCO", "VEDL", "TATASTEEL", "JSWSTEEL", "SAIL",
         "NATIONALUM", "NMDC", "COALINDIA", "HINDCOPPER"},
        # ── Power & Utilities ─────────────────────────────────────────────────
        {"NTPC", "POWERGRID", "ADANIPOWER", "TATAPOWER", "CESC",
         "TORNTPOWER", "NHPC", "SJVN", "IREDA", "PFC", "RECLTD"},
        # ── Telecom ───────────────────────────────────────────────────────────
        {"BHARTIARTL", "IDEA", "TATACOMM", "HFCL"},
        # ── FMCG & Consumer Staples ───────────────────────────────────────────
        {"HINDUNILVR", "ITC", "NESTLEIND", "BRITANNIA", "DABUR",
         "MARICO", "GODREJCP", "COLPAL", "EMAMILTD", "TATACONSUM"},
        # ── Paints & Chemicals ────────────────────────────────────────────────
        {"ASIANPAINT", "BERGERPAINTS", "KANSAINER", "AKZOINDIA",
         "PIDILITIND", "VINATIORGA", "AAVAS", "COROMANDEL"},
        # ── Cement & Construction Materials ──────────────────────────────────
        {"ULTRACEMCO", "SHREECEM", "AMBUJACEM", "ACC", "RAMCOCEM", "JKCEMENT"},
        # ── Capital Goods & Engineering ───────────────────────────────────────
        {"LT", "BHEL", "SIEMENS", "ABB", "THERMAX", "CUMMINSIND",
         "BEL", "HAL", "BEML", "COCHINSHIP"},
        # ── Infrastructure & Real Estate ──────────────────────────────────────
        {"ADANIPORTS", "ADANIENT", "GMRAIRPORT", "IRB", "ASHOKA",
         "DLF", "GODREJPROP", "OBEROIRLTY", "PRESTIGE"},
        # ── Consumer Discretionary & Retail ──────────────────────────────────
        {"TITAN", "KALYANKJIL", "MANYAVAR", "TRENT", "DMART",
         "NYKAA", "DEVYANI", "JUBLFOOD", "WESTLIFE"},
        # ── Specialty Chemicals ───────────────────────────────────────────────
        {"ATUL", "NAVINFLUOR", "CLEAN", "FINEORG", "GALAXYSURF"},
        # ── Agri, Fertilisers & Sugar ────────────────────────────────────────
        {"COROMANDEL", "CHAMPCORD", "ANDHRSUGAR", "BALRAMCHIN", "TRIVENI",
         "DHAMPUR", "EIDPARRY"},
        # ── Diagnostics / Exchange / Small-cap Fintech (standalone) ─────────
        {"IFGLEXPOR"},   # refractory — standalone niche, no correlation group
    ]

    def _record_exit(
        self, symbol: str, exit_price: float, reason: str,
        pnl: float = 0.0, confidence: float = 0.0
    ) -> None:
        """Record a sell event for the re-entry engine."""
        self._recently_sold[symbol] = {
            'exit_price':  exit_price,
            'exit_time':   datetime.now(),
            'reason':      reason,
            'pnl':         pnl,
            'confidence':  confidence,
        }
        logger.info(f"Re-entry engine: recorded exit {symbol} @ ₹{exit_price:.2f} ({reason}) P&L=₹{pnl:.2f}")

    def _check_reentry_eligibility(
        self,
        symbol: str,
        current_price: float,
        score: float,
        mtf_aligned: bool = False,
        regime: str = "SIDEWAYS",
        rr: float = 0.0,
        has_negative_news: bool = False,
        projected_invested: float = 0.0,
        max_allowed_invested: float = float('inf'),
    ) -> Optional[str]:
        """
        Returns a blocking reason string if re-entry is NOT allowed, else None (approved).

        For stocks never previously sold: returns None immediately (no restrictions).

        For stocks previously sold, ALL 9 conditions must pass:
          1. Previous position fully closed (no partial still open)
          2. No open position currently exists for this symbol
          3. AI Trade Score ≥ 75
          4. Multi-timeframe confirmation passes
          5. Market regime is not BEAR
          6. Risk/Reward ≥ 1.5:1
          7. No major negative news
          8. Daily loss limits not exceeded (capital guard)
          9. Price has retraced ≥ 2% from exit (genuine new setup, not chasing)
        """
        rec = self._recently_sold.get(symbol)
        if rec is None:
            return None  # never sold this session — no restrictions (None = unrestricted)

        exit_price = rec['exit_price']
        exit_time  = rec['exit_time']
        elapsed_h  = (datetime.now() - exit_time).total_seconds() / 3600

        # ── Condition 1 & 2: position must be fully closed (no open qty) ──────
        # (Already guaranteed by open_symbols check before this call, but log clearly)

        # ── Cooldown: configurable minimum wait (default 4 hours) ────────────
        cooldown_h = config.REENTRY_COOLDOWN_HOURS
        if elapsed_h < cooldown_h:
            remaining = cooldown_h - elapsed_h
            return (
                f"cooldown: {remaining*60:.0f}min remaining "
                f"(need {cooldown_h:.0f}h since exit @ ₹{exit_price:.2f})"
            )

        # ── Condition 3: AI score ≥ 75 for re-entry (stricter than new trade) ─
        MIN_REENTRY_SCORE = 75
        if score < MIN_REENTRY_SCORE:
            return f"re-entry score {score:.0f}/100 below minimum {MIN_REENTRY_SCORE} (stricter than new trades)"

        # ── Condition 4: MTF must be aligned ──────────────────────────────────
        if not mtf_aligned:
            return "re-entry requires MTF alignment"

        # ── Condition 5: No re-entry in BEAR market ───────────────────────────
        if str(regime).upper() == 'BEAR':
            return "re-entry blocked: market regime is BEAR"

        # ── Condition 6: R:R ≥ 1.5:1 ─────────────────────────────────────────
        if rr < 1.5:
            return f"re-entry R:R {rr:.2f} below minimum 1.5:1"

        # ── Condition 7: No negative news ────────────────────────────────────
        if has_negative_news:
            return "re-entry blocked: negative news present"

        # ── Condition 8: Capital allocation allows new position ───────────────
        if projected_invested > max_allowed_invested:
            return (
                f"re-entry blocked: capital limit "
                f"(₹{projected_invested:.0f} > ₹{max_allowed_invested:.0f})"
            )

        # ── Condition 9: Price retraced ≥ 2% from exit price ─────────────────
        MIN_RETRACE_PCT = 0.02
        if exit_price > 0 and current_price > 0:
            retrace = (exit_price - current_price) / exit_price
            if retrace < MIN_RETRACE_PCT:
                return (
                    f"insufficient retrace {retrace*100:.1f}% "
                    f"(need ≥2% pullback from exit ₹{exit_price:.2f}, current ₹{current_price:.2f})"
                )

        # ── All conditions passed — approve re-entry ─────────────────────────
        retrace_pct = ((exit_price - current_price) / exit_price * 100) if exit_price > 0 else 0
        logger.info(
            f"Re-entry engine: {symbol} APPROVED — "
            f"score {score:.0f}/100 | MTF aligned | regime {regime} | "
            f"R:R {rr:.2f} | retrace {retrace_pct:.1f}% from ₹{exit_price:.2f} | "
            f"elapsed {elapsed_h:.1f}h since exit | prev P&L=₹{rec.get('pnl',0):.2f}"
        )
        # Return metadata dict for journal (truthy = approved, but caller checks None vs dict)
        meta = {
            'is_reentry':            True,
            'prev_exit_reason':      rec.get('reason', ''),
            'prev_pnl':              rec.get('pnl', 0.0),
            'prev_confidence':       rec.get('confidence', 0.0),
            'time_since_exit_hours': round(elapsed_h, 2),
            'reentry_score':         score,
        }
        del self._recently_sold[symbol]
        return meta  # None = blocked, dict = approved (with metadata)

    def _is_correlated_with_open(self, candidate: str, open_symbols: set) -> bool:
        """
        Return True if `candidate` is in the same sector group as any already-open symbol.
        """
        if not open_symbols:
            return False
        for group in self._SECTOR_GROUPS:
            if candidate in group:
                if group & open_symbols:
                    return True
        return False

    @staticmethod
    def _confidence_multiplier(confidence: float) -> float:
        """
        Map confidence to a position size multiplier.
        ≥0.90 → 1.00 (full)
        0.80–0.89 → 0.80
        0.70–0.79 → 0.65
        <0.70 → 0.50
        """
        if confidence >= 0.90:
            return 1.00
        elif confidence >= 0.80:
            return 0.80
        elif confidence >= 0.70:
            return 0.65
        else:
            return 0.50

    @staticmethod
    def _has_negative_news(signal: Dict) -> bool:
        """
        Screen for significant negative keywords in the signal's reasoning or
        sentiment news items. Returns True if the stock should be skipped.
        """
        NEGATIVE_KEYWORDS = [
            'sebi order', 'sebi ban', 'promoter selling', 'promoter sold',
            'block deal', 'court case', 'court order', 'management change',
            'ceo resigned', 'fraud', 'default', 'insolvency', 'bankruptcy',
            'quarterly loss', 'net loss', 'revenue decline', 'profit warning',
        ]
        # Check signal reasoning
        reasoning = str(signal.get('reasoning', '')).lower()
        for kw in NEGATIVE_KEYWORDS:
            if kw in reasoning:
                logger.info(f"News filter hit on '{kw}' for {signal.get('symbol')}")
                return True

        # Check sentiment news items if carried in research
        research = signal.get('_research', {})
        senti = research.get('sentiment_analysis', {})
        for item in senti.get('news_items', []):
            headline = str(item.get('headline', '') + item.get('summary', '')).lower()
            for kw in NEGATIVE_KEYWORDS:
                if kw in headline:
                    logger.info(f"News filter hit on '{kw}' in headline for {signal.get('symbol')}")
                    return True

        return False

    def weekly_rebalance(self):
        """
        Friday rebalancing: identify the weakest open position (lowest unrealised P&L %)
        and exit it if a better signal exists in the current universe.
        Only runs once per calendar week.
        """
        import pytz
        ist = pytz.timezone('Asia/Kolkata')
        now_ist = datetime.now(ist)
        week_num = now_ist.isocalendar()[1]

        # Only on Fridays, and only once per week
        if now_ist.weekday() != 4:  # 4 = Friday
            return
        if self._last_rebalance_week == week_num:
            return

        self._last_rebalance_week = week_num
        logger.info("Weekly rebalance: checking positions...")

        now_dt = datetime.now()
        open_pos = [
            p for p in self.order_executor.risk_manager.positions
            if p.status in {PositionStatus.OPEN, PositionStatus.PARTIAL}
            and (now_dt - p.entry_time).total_seconds() > 86400  # held at least 1 day
        ]
        if not open_pos:
            return

        # Find weakest position (worst unrealised % gain)
        prices = {}
        for p in open_pos:
            pr = self.market_data.get_realtime_price(p.symbol)
            if pr:
                prices[p.symbol] = pr

        def pnl_pct(pos):
            pr = prices.get(pos.symbol, pos.entry_price)
            return (pr - pos.entry_price) / pos.entry_price

        weakest = min(open_pos, key=pnl_pct)
        wpct = pnl_pct(weakest)

        # Only exit if it's losing money (negative P&L) or barely breaking even
        if wpct >= 0.01:
            logger.info(f"Rebalance: weakest position {weakest.symbol} is +{wpct*100:.1f}% — keeping")
            return

        logger.info(f"Rebalance: exiting weakest position {weakest.symbol} ({wpct*100:.1f}%)")
        exit_price = prices.get(weakest.symbol, weakest.entry_price)
        exec_r = self.order_executor.execute_signal({
            'symbol': weakest.symbol, 'action': 'SELL',
            'current_price': exit_price,
            'position_size': weakest.quantity,
            'investment_amount': exit_price * weakest.quantity,
            'stop_loss': 0, 'target': 0,
            'risk_reward_ratio': 0, 'confidence': 1.0,
            'overall_score': 0,
            'reasoning': f'Weekly rebalance — underperforming ({wpct*100:.1f}%)',
            'timestamp': datetime.now().isoformat(),
        })
        if exec_r['success']:
            logger.info(f"Rebalance exit executed: {weakest.symbol}")
            self.trade_log.append(exec_r)
            try:
                self.telegram._send(
                    f"🔄 <b>WEEKLY REBALANCE</b>\n\n"
                    f"Exited: {weakest.symbol}\n"
                    f"P&amp;L: {wpct*100:.1f}%\n"
                    f"Reason: Underperformer replaced by better opportunity"
                )
            except Exception:
                pass

    def end_of_day_close(self) -> Dict:
        """
        Close all intraday (MIS) positions at end of trading day (3:00 PM IST).
        Swing (CNC) positions are held overnight and monitored by SL/target/max-hold-days.

        Returns:
            Close results
        """
        logger.info("Executing end-of-day close (3:00 PM IST)")

        if config.TRADING_MODE == "swing":
            logger.info("Swing mode: holding CNC positions overnight; monitoring SL/target")
            position_updates = self.order_executor.monitor_positions()
            result = {
                'timestamp': datetime.now().isoformat(),
                'close_results': position_updates,
                'execution_summary': self.order_executor.get_execution_summary()
            }
            logger.info(f"Swing monitoring completed: {len(position_updates)} positions updated")
        else:
            close_results = self.order_executor.close_all_positions()
            result = {
                'timestamp': datetime.now().isoformat(),
                'close_results': close_results,
                'execution_summary': self.order_executor.get_execution_summary()
            }
            logger.info(f"End-of-day close completed: {len(close_results)} positions closed")
            self._send_eod_reports()
            # Reset daily statistics after close
            self.daily_reset()

        return result
    
    def _send_eod_reports(self):
        """Send end-of-day Telegram summary + email report."""
        try:
            summary = self.get_performance_report()
            self.telegram.daily_summary(summary)
        except Exception as e:
            logger.error(f"Telegram EOD summary failed: {e}")
        try:
            self.daily_email_report()
        except Exception as e:
            logger.error(f"EOD email report failed: {e}")

    def daily_reset(self):
        """Reset daily statistics"""
        logger.info("Executing daily reset")
        self.order_executor.reset_daily()
    
    def pre_market_check(self):
        """Pre-market check before trading starts"""
        logger.info("Executing pre-market check (9:20 AM IST)")

        # Check if token is valid and refresh if needed
        if not self.order_executor.broker.paper_trading:
            try:
                self.order_executor.broker.token_manager.initialize_kite()
                logger.info("Token validated successfully")
            except Exception as e:
                logger.error(f"Token validation failed: {e}")
                logger.warning("Please refresh token before market open")

        # Check system status
        logger.info("System ready for trading")
        logger.info(f"Market Hours: {config.MARKET_OPEN} - {config.MARKET_CLOSE} IST")
        logger.info(f"Trading Mode: {config.TRADING_MODE.upper()}")
        logger.info(f"Trading Amount: {config.TRADING_AMOUNT}")
        logger.info(f"Max Positions: {config.MAX_POSITIONS}")
        logger.info(f"Risk Per Trade: {config.RISK_PER_TRADE * 100}%")
        if config.TRADING_MODE == "swing":
            logger.info(f"Swing SL: {config.SWING_STOP_LOSS_PERCENTAGE*100:.1f}% | Target: {config.SWING_TARGET_PERCENTAGE*100:.1f}% | Max Hold: {config.SWING_MAX_HOLD_DAYS} days")
        else:
            logger.info(f"Intraday SL: {config.STOP_LOSS_PERCENTAGE*100:.1f}% | Target: {config.TARGET_PERCENTAGE*100:.1f}%")
        logger.info(f"Dynamic universe size: {config.DYNAMIC_UNIVERSE_SIZE} stocks (live NSE scan)")
    
    def get_trade_log(self) -> List[Dict]:
        """
        Get the trade log
        
        Returns:
            List of all trades
        """
        return self.trade_log
    
    def get_performance_report(self) -> Dict:
        """
        Generate performance report for dashboard/email
        
        Returns:
            Performance report
        """
        execution_summary = self.order_executor.get_execution_summary()
        position_summary = execution_summary.get('position_summary', {})
        broker_holdings = execution_summary.get('broker_holdings', {})
        
        total_trades = len(self.trade_log)
        successful_trades = len([t for t in self.trade_log if t.get('success')])
        total_pnl = position_summary.get('total_pnl', 0)
        daily_pnl = position_summary.get('daily_pnl', 0)
        cash = broker_holdings.get('cash', 0)
        total_value = broker_holdings.get('total_value', cash)
        
        return {
            'timestamp': datetime.now().isoformat(),
            'cash': cash,
            'invested': total_value - cash,
            'total_value': total_value,
            'total_trades': total_trades,
            'successful_trades': successful_trades,
            'success_rate': successful_trades / total_trades if total_trades > 0 else 0,
            'total_pnl': total_pnl,
            'daily_pnl': daily_pnl,
            'open_positions': position_summary.get('open_positions', 0),
            'win_rate': position_summary.get('win_rate', 0),
            'market_regime': self.market_regime.detect_regime() if config.MARKET_REGIME_ENABLED else "UNKNOWN",
            'execution_summary': execution_summary,
            'paper_trading': execution_summary['paper_trading']
        }
    
    def daily_email_report(self):
        """Send daily email report at 4:00 PM IST"""
        try:
            summary = self.get_performance_report()
            positions = self.order_executor.broker.get_positions()
            orders = self.order_executor.broker.kite.orders() if self.order_executor.broker.kite else []
            self.email.daily_report(summary, positions, orders)
        except Exception as e:
            logger.error(f"Daily email report failed: {e}")
    
    def run_paper_trading_test(self, duration_minutes: int = 60) -> Dict:
        """
        Run a paper trading test for specified duration
        
        Args:
            duration_minutes: Duration of test in minutes
        
        Returns:
            Test results
        """
        logger.info(f"Starting paper trading test for {duration_minutes} minutes")
        
        start_time = datetime.now()
        end_time = start_time + timedelta(minutes=duration_minutes)
        
        test_results = {
            'start_time': start_time.isoformat(),
            'end_time': end_time.isoformat(),
            'cycles': [],
            'final_summary': {}
        }
        
        cycle_count = 0
        while datetime.now() < end_time and self.order_executor.should_stop_trading() is False:
            cycle_result = self.run_once()
            test_results['cycles'].append(cycle_result)
            cycle_count += 1
            
            # Wait for next cycle (every 5 minutes for testing)
            time.sleep(300)
        
        # Get final summary
        test_results['final_summary'] = self.get_performance_report()
        test_results['total_cycles'] = cycle_count
        
        logger.info(f"Paper trading test completed: {cycle_count} cycles")
        
        return test_results


def main():
    """Main entry point"""
    import sys
    
    # Validate configuration
    if not config.validate():
        logger.error("Invalid configuration")
        sys.exit(1)
    
    orchestrator = TradingOrchestrator()
    
    # Check command line arguments
    if len(sys.argv) > 1:
        command = sys.argv[1]
        
        if command == "test":
            # Run paper trading test
            duration = int(sys.argv[2]) if len(sys.argv) > 2 else 60
            results = orchestrator.run_paper_trading_test(duration)
            print(json.dumps(results, indent=2, default=str))
        
        elif command == "once":
            # Run single cycle
            result = orchestrator.run_once()
            print(json.dumps(result, indent=2, default=str))
        
        elif command == "scheduled":
            # Run scheduled trading (full automation)
            interval = int(sys.argv[2]) if len(sys.argv) > 2 else 15
            logger.info("Starting full automated trading")
            logger.info("Schedule:")
            logger.info(f"  - Pre-market check: 9:20 AM IST")
            logger.info(f"  - Trading cycles: Every {interval} minutes during market hours (9:30 AM - 3:00 PM IST)")
            logger.info(f"  - End-of-day close: 3:00 PM IST")
            logger.info(f"  - Daily reset: 9:00 AM IST")
            orchestrator.run_scheduled(interval)
        
        elif command == "report":
            # Generate performance report
            report = orchestrator.get_performance_report()
            print(json.dumps(report, indent=2, default=str))
        
        elif command == "refresh-token":
            # Refresh Kite token
            from token_manager import TokenManager
            token_manager = TokenManager()
            logger.info("Token refresh required. Run: python get_kite_token.py")
        
        else:
            print("Unknown command. Available: test, once, scheduled, report, refresh-token")
    else:
        # Default: run single cycle
        result = orchestrator.run_once()
        print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
