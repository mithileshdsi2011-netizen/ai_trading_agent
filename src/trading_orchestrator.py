"""
Main Trading Orchestrator Module
Coordinates all components for automated trading
"""
import logging
import os
import schedule
import threading
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
from sell_decision_ai import SellDecisionAI
from risk_manager import PositionStatus
from decision_explainer import DecisionExplainer

import os as _os
_log_dir = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), 'logs')
_os.makedirs(_log_dir, exist_ok=True)
_file_handler    = logging.FileHandler(_os.path.join(_log_dir, 'trading.log'))
_file_handler.setLevel(logging.INFO)
_file_handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(name)s: %(message)s'))

_console_handler = logging.StreamHandler()
_console_handler.setLevel(logging.WARNING)   # only WARN/ERROR/CRITICAL to terminal
_console_handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s: %(message)s'))

logging.basicConfig(level=logging.INFO, handlers=[_file_handler, _console_handler], force=True)

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
        self.explainer = DecisionExplainer()
        self.mtf = MultiTimeframeConfirmer(market_data=self.market_data)
        self.smart_exit = SmartExitAI(market_data=self.market_data)
        self.sell_decision_ai = SellDecisionAI()
        broker = self.order_executor.broker
        mode_str = 'PAPER' if broker.paper_trading else ('LIVE' if broker.live_ready else 'UNKNOWN')
        logger.info(f"TradingOrchestrator initialized — broker mode: {mode_str}, startup: {broker.startup_timestamp}")
        self.is_running = False
        self.trade_log: List[Dict] = []   # capped at 500 entries (in-memory only)
        self._TRADE_LOG_MAX = 500
        self._last_rebalance_week: Optional[int] = None
        # Re-entry tracking: symbol -> {exit_price, exit_time, exit_reason}
        self._recently_sold: Dict[str, Dict] = {}
        # Kite health: consecutive failure counter for mid-session token expiry alert
        self._kite_fail_count: int = 0
        self._KITE_FAIL_ALERT_THRESHOLD: int = 2
        self._kite_alert_sent: bool = False   # send alert only once per outage
        self._last_alerted_ip: str = ''          # track which IP we already alerted on
        self._run_once_lock = threading.Lock()   # prevent overlapping trading cycles
        # Morning shortlist cache — built once per trading day from full 150-stock scan,
        # reused every 15-min cycle to avoid rescanning all 150 stocks repeatedly.
        self._morning_shortlist: List[str] = []   # top-40 symbols for intraday cycles
        self._morning_shortlist_date: str = ''    # date when shortlist was built
        # One-shot circuit breaker: once it fires today, do not re-fire
        self._circuit_breaker_fired_today = False
    
    def run_once(self) -> Dict:
        """
        Run a single trading cycle
        
        Returns:
            Trading cycle results
        """
        logger.info("=" * 50)
        logger.info(f"Starting trading cycle at {datetime.now()}")
        
        # Reset per-cycle market data metrics and warm the rate limiter
        self.market_data.new_cycle()
        self.market_data._get_instruments()
        
        cycle_result = {
            'timestamp': datetime.now().isoformat(),
            'market_open': self.market_data.is_market_open(),
            'signals_generated': [],
            'orders_executed': [],
            'positions_monitored': [],
            'errors': []
        }
        
        # ── Kite token health check — alert if down 2+ consecutive cycles ─────────
        kite_ok = bool(self.market_data.kite)
        if not config.PAPER_TRADING:
            try:
                # Quick probe: margins() is lightweight and confirms token is live
                if self.market_data.kite:
                    self.market_data.kite.margins()
                    kite_ok = True
                    if self._kite_fail_count > 0:
                        logger.info(f"Kite health restored after {self._kite_fail_count} failures")
                    self._kite_fail_count = 0
                    self._kite_alert_sent = False  # reset so next outage alerts again
                else:
                    kite_ok = False
            except Exception as _kite_err:
                # Try to reload token from file — bot may have started with a stale token
                try:
                    import json as _json
                    _tfile = os.path.join(os.path.dirname(__file__), '..', 'data', 'kite_token.json')
                    with open(_tfile) as _tf:
                        _td = _json.load(_tf)
                    _new_token = _td.get('access_token', '')
                    if _new_token and self.market_data.kite:
                        self.market_data.kite.set_access_token(_new_token)
                        # Also update broker's kite object
                        if hasattr(self.order_executor, 'broker') and hasattr(self.order_executor.broker, 'kite'):
                            self.order_executor.broker.kite.set_access_token(_new_token)
                        self.market_data.kite.margins()  # verify it works
                        kite_ok = True
                        self._kite_fail_count = 0
                        self._kite_alert_sent = False
                        logger.info("Kite token reloaded from file successfully")
                except Exception:
                    kite_ok = False
                    # If Zerodha is explicitly rejecting the token, mark it invalid immediately
                    _err_str = str(_kite_err).lower()
                    if 'access_token' in _err_str or 'api_key' in _err_str or 'invalid token' in _err_str:
                        try:
                            from token_manager import TokenManager
                            TokenManager().mark_token_invalid()
                        except Exception:
                            pass
                    logger.error(f"Kite health check FAILED: {_kite_err}")
            if not kite_ok:
                self._kite_fail_count += 1
                if self._kite_fail_count >= self._KITE_FAIL_ALERT_THRESHOLD and not self._kite_alert_sent:
                    logger.error(
                        f"KITE TOKEN EXPIRED/UNREACHABLE — "
                        f"{self._kite_fail_count} consecutive failures. "
                        f"Run: python get_kite_token.py"
                    )
                    if self._is_trading_day():
                        self._alert(
                            "🔴 KITE TOKEN ALERT",
                            f"🔴 KITE TOKEN ALERT\n\n"
                            f"kite_ok=False for {self._kite_fail_count} consecutive cycles.\n"
                            f"Orders are BLOCKED.\n"
                            f"Run: python get_kite_token.py to refresh token."
                        )
                    else:
                        logger.info("Kite token alert suppressed — not a trading day (weekend/holiday)")
                    self._kite_alert_sent = True  # suppress further alerts until recovery
                else:
                    logger.warning(f"Kite health check failed (cycle {self._kite_fail_count}) — alert already sent, waiting for recovery")
                cycle_result['errors'].append(f'kite_ok=False ({self._kite_fail_count} cycles)')
                self.market_data.get_cycle_metrics(); return cycle_result
        # ──────────────────────────────────────────────────────────────────

        # Check if market is open (includes holiday check)
        if not cycle_result['market_open']:
            if self.market_data.is_market_holiday():
                logger.info("NSE holiday today — bot paused, no trading")
            else:
                logger.info("Market is closed — skipping trading cycle")
            self.market_data.get_cycle_metrics(); return cycle_result

        # Check if past intraday cutoff — only monitor/close, no new BUYs
        ist = pytz.timezone('Asia/Kolkata')
        now_ist = datetime.now(ist)
        cutoff_h, cutoff_m = map(int, config.INTRADAY_CUTOFF.split(':'))
        past_cutoff = (now_ist.hour, now_ist.minute) >= (cutoff_h, cutoff_m)

        # Enforce TRADING_START buffer (default 09:30) — no new BUYs before this
        # NSE open (09:15) is illiquid and erratic; wait for price discovery.
        start_h, start_m = map(int, config.TRADING_START.split(':'))
        before_trading_start = (now_ist.hour, now_ist.minute) < (start_h, start_m)
        if before_trading_start:
            logger.info(
                f"Before TRADING_START {config.TRADING_START} IST — "
                f"monitoring positions only, no new BUYs (avoiding illiquid open)"
            )
            position_updates = self.order_executor.monitor_positions()
            cycle_result['positions_monitored'] = position_updates
            self.market_data.get_cycle_metrics(); return cycle_result

        if past_cutoff:
            if config.TRADING_MODE == "swing":
                logger.warning(f"Past intraday cutoff ({config.INTRADAY_CUTOFF}) — swing mode: monitoring only, no new BUYs")
                position_updates = self.order_executor.monitor_positions()
                cycle_result['positions_monitored'] = position_updates
                self.market_data.get_cycle_metrics(); return cycle_result
            else:
                logger.warning(f"Past intraday cutoff ({config.INTRADAY_CUTOFF}) — closing all positions, no new orders")
                self.end_of_day_close()
                self.market_data.get_cycle_metrics(); return cycle_result

        # Check if we should stop trading (daily loss limit OR consecutive loss streak)
        if self.order_executor.should_stop_trading():
            rm = self.order_executor.risk_manager
            # Determine which limit fired for a meaningful alert
            daily_loss_hit  = rm.daily_pnl < -rm.max_daily_loss
            consec_limit    = config.MAX_CONSECUTIVE_LOSSES
            stop_reason = (
                f"Daily loss limit: ₹{rm.daily_pnl:.0f} < -₹{rm.max_daily_loss:.0f}"
                if daily_loss_hit
                else f"{consec_limit} consecutive losing trades today"
            )
            logger.warning(f"Trading halted — {stop_reason}")
            cycle_result['errors'].append(f"Trading halted: {stop_reason}")
            self._alert(
                "🔴 TRADING HALTED",
                f"🔴 TRADING HALTED\n\n"
                f"Reason: {stop_reason}\n"
                f"No new BUYs for the rest of today.\n"
                f"Positions are still being monitored."
            )
            # Still monitor and exit existing positions — never abandon open trades
            position_updates = self.order_executor.monitor_positions()
            cycle_result['positions_monitored'] = position_updates
            self.market_data.get_cycle_metrics(); return cycle_result
        
        # Emergency circuit breaker: if drawdown > 15% from intraday peak, sell all positions
        # Only fires if holdings API succeeds — never close on a timeout/error
        try:
            ist = pytz.timezone('Asia/Kolkata')
            today_str = datetime.now(ist).strftime('%Y-%m-%d')
            holdings = self.order_executor.broker.get_holdings()
            total_value = holdings.get('total_value', 0)
            # Use peak value file as baseline; reset to current value on a new day
            peak_path = os.path.join(os.path.dirname(__file__), '..', 'data', 'peak_value.json')
            try:
                with open(peak_path) as _pf:
                    _pd = json.load(_pf)
                    saved_date = _pd.get('date', '')
                    saved_peak = float(_pd.get('peak_value', total_value)) if saved_date == today_str else total_value
            except Exception:
                saved_peak = total_value
            # Update peak if current value is higher
            peak_value = max(saved_peak, total_value)
            try:
                os.makedirs(os.path.dirname(peak_path), exist_ok=True)
                with open(peak_path, 'w') as _pf:
                    json.dump({'peak_value': peak_value, 'date': today_str}, _pf)
            except Exception:
                pass
            # Only trigger if we have real data (total_value > 0) and genuine drawdown > 15%
            if total_value > 0 and peak_value > 0:
                drawdown = (peak_value - total_value) / peak_value
                if drawdown > 0.15:
                    if self._circuit_breaker_fired_today:
                        logger.warning("Circuit breaker already triggered today; not closing again")
                    else:
                        self._circuit_breaker_fired_today = True
                        logger.error(f"EMERGENCY CIRCUIT BREAKER: drawdown {drawdown:.2%} from peak ₹{peak_value:.0f} — closing all positions")
                        close_results = self.order_executor.close_all_positions('Circuit breaker')
                        cycle_result['close_results'] = close_results
                        cycle_result['errors'].append(f"Circuit breaker triggered: drawdown {drawdown:.2%}")
                        self._alert(
                            "🔴 EMERGENCY CIRCUIT BREAKER",
                            f"🔴 EMERGENCY CIRCUIT BREAKER\n\nDrawdown: {drawdown:.2%} from peak ₹{peak_value:.0f}\nAll positions closed."
                        )
                        self.market_data.get_cycle_metrics(); return cycle_result
        except Exception as e:
            logger.warning(f"Circuit breaker check failed (skipping): {e}")
        
        # Daily loss limit: if today's realised P&L is worse than DAILY_MAX_LOSS_PCT × capital, halt new buys
        _daily_loss_halt = False
        try:
            _journal_path = os.path.join(os.path.dirname(__file__), '..', 'data', 'trade_journal.json')
            if os.path.exists(_journal_path):
                with open(_journal_path) as _jf:
                    _all_entries = json.load(_jf)
                _today = datetime.now().strftime('%Y-%m-%d')
                _today_pnl = sum(
                    float(e.get('net_pnl', 0) or 0)
                    for e in _all_entries
                    if (e.get('timestamp', '') or '')[:10] == _today and e.get('action') == 'SELL'
                )
                _loss_limit = -(config.TRADING_AMOUNT * config.DAILY_MAX_LOSS_PCT)
                if _today_pnl < _loss_limit:
                    logger.error(
                        f"DAILY LOSS LIMIT HIT: today P&L ₹{_today_pnl:.0f} "
                        f"< limit ₹{_loss_limit:.0f} — no new BUYs for rest of day"
                    )
                    _daily_loss_halt = True
                    self._alert(
                        "🔴 Daily Loss Limit Hit",
                        f"🔴 Daily loss limit hit\nToday P&L: ₹{_today_pnl:.0f}\n"
                        f"Limit: ₹{_loss_limit:.0f}\nNo new buys until tomorrow."
                    )
        except Exception as _dl_e:
            logger.debug(f"Daily loss check skipped: {_dl_e}")

        try:
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
                    self.market_data.get_cycle_metrics(); return cycle_result

            # ── MORNING SHORTLIST: full 150-stock scan once per day ─────────────
            # On the FIRST cycle each trading day, scan all 150 stocks and cache
            # the top-40 as the shortlist. Every subsequent cycle only scans those
            # 40 + current holdings (reduces API usage by 60-75%).
            _today_str = now_ist.strftime('%Y-%m-%d')
            _need_full_scan = (self._morning_shortlist_date != _today_str
                               or not self._morning_shortlist)

            if _need_full_scan:
                logger.info("Morning full scan: building 150-stock shortlist for today...")
                try:
                    _candidates = self.dynamic_universe.get_top_candidates(
                        display_n=50, scan_n=config.DYNAMIC_UNIVERSE_SIZE
                    )
                    _full_universe = _candidates["scan_universe"]
                    logger.info(f"Full universe: {len(_full_universe)} stocks fetched")
                except Exception as ue:
                    logger.warning(f"Dynamic universe failed ({ue}), using fallback watchlist")
                    _full_universe = config.WATCHLIST

                # ── Inject priority stocks into full universe before shortlisting ──
                priority_syms: list = list(open_symbols)  # always include held stocks

                # Morning report picks
                try:
                    _cache_path = os.path.join(
                        os.path.dirname(os.path.dirname(__file__)), 'data', 'morning_report_cache.json'
                    )
                    if os.path.exists(_cache_path):
                        with open(_cache_path) as _f:
                            _mr = json.load(_f)
                        from datetime import date as _date
                        if _mr.get("date") == str(_date.today()):
                            _rpt = _mr.get("report", {})
                            priority_syms += [p["symbol"] for p in _rpt.get("ai_top_picks", [])[:15]]
                            priority_syms += [g["symbol"] for g in _rpt.get("top_gainers", [])[:10]]
                            priority_syms += [g["symbol"] for g in _rpt.get("gap_up_stocks", [])[:8]]
                            logger.info(f"Morning picks injected: {priority_syms[:10]}")
                except Exception as _mp_e:
                    logger.debug(f"Morning picks load error: {_mp_e}")

                # Live batch-quote gainers ≥1.5%
                try:
                    from dynamic_universe import _NIFTY500_PRIORITY as _prio
                    _all_q: dict = {}
                    for _i in range(0, len(list(_prio)[:200]), 200):
                        try:
                            _q = self.market_data.kite.quote(
                                [f"NSE:{s}" for s in list(_prio)[:200][_i:_i+200]]
                            ) or {}
                            _all_q.update(_q)
                        except Exception:
                            pass
                    _live = []
                    for _k, _qv in _all_q.items():
                        _s = _k.replace("NSE:", "")
                        _lp = _qv.get("last_price", 0)
                        _pc = _qv.get("ohlc", {}).get("close", 0)
                        if _lp and _pc and (_lp - _pc) / _pc * 100 >= 1.5:
                            _live.append((_s, (_lp - _pc) / _pc * 100))
                    _live.sort(key=lambda x: x[1], reverse=True)
                    priority_syms += [s for s, _ in _live[:12]]
                    logger.info(f"Live gainers ≥1.5%: {[s for s,_ in _live[:8]]}")
                except Exception as _lg_e:
                    logger.debug(f"Live gainer fetch error: {_lg_e}")

                # Merge priority first, then full universe, dedup → take top 40 as shortlist
                _seen_u: set = set()
                merged_universe: list = []
                for _s in priority_syms + _full_universe:
                    if _s and _s not in _seen_u:
                        _seen_u.add(_s)
                        merged_universe.append(_s)
                    if len(merged_universe) >= 175:
                        break

                # Cache top-40 as morning shortlist (priority stocks guaranteed)
                self._morning_shortlist = merged_universe[:40]
                self._morning_shortlist_date = _today_str
                logger.info(
                    f"Morning shortlist built: {len(self._morning_shortlist)} stocks "
                    f"(from {len(merged_universe)} merged) — cached for all cycles today"
                )
                universe = merged_universe  # first cycle scans full merged list
            else:
                # Intraday cycle — only scan shortlist + current holdings (no redundant full scan)
                intraday_syms: list = list(self._morning_shortlist)
                for _s in open_symbols:
                    if _s and _s not in intraday_syms:
                        intraday_syms.append(_s)
                universe = intraday_syms
                logger.info(
                    f"Intraday cycle: scanning {len(universe)} stocks "
                    f"({len(self._morning_shortlist)} shortlist + {len(open_symbols)} holdings)"
                )

            # --- Prepare risk data for decision logging ---
            _open_slot_count_est = len([p for p in self.order_executor.risk_manager.positions
                                        if p.status in {PositionStatus.OPEN, PositionStatus.PARTIAL}])
            _remaining_slots_est = max(1, config.MAX_POSITIONS - _open_slot_count_est)
            per_stock_budget = budget / _remaining_slots_est
            risk_data = {
                'available_cash': available_cash,
                'open_positions': list(open_symbols),
                'holdings': [p.symbol for p in self.order_executor.risk_manager.positions if p.status == PositionStatus.OPEN],
                'cooldown_status': False,
                'portfolio_exposure': current_invested / (available_cash + current_invested) if (available_cash + current_invested) > 0 else 0,
                'max_position_size': per_stock_budget
            }

            # --- Centralized two-stage prefetch: quotes (lightweight) + daily history ---
            try:
                self.market_data.prefetch_quotes(universe)
                self.market_data.prefetch_historical(universe, '3mo', '1d')
            except Exception as _pfe:
                logger.warning(f"Cycle market data prefetch failed: {_pfe}")

            # --- Generate signals for universe ---
            logger.info(f"Generating signals for {len(universe)} stocks...")
            signals = self.signal_generator.generate_signals_for_watchlist(universe, risk_data)
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
            buy_signals = [] if _daily_loss_halt else [s for s in signals if s.get('action') == 'BUY']
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

                    # ── 2b. Sector concentration limit (max 2 per sector) ──────
                    if self._sector_concentration_exceeded(sym, open_symbols):
                        logger.warning(f"Skipping {sym}: sector concentration limit (max 2 per sector)")
                        continue

                    # ── 3. Negative news filter ────────────────────────────────
                    if self._has_negative_news(best_signal):
                        logger.warning(f"Skipping {sym}: negative news filter")
                        continue

                    # ── 3b. Earnings/result date guard — skip within 3 days ────
                    if self._near_earnings(sym):
                        logger.warning(f"Skipping {sym}: earnings/corporate action within 3 days")
                        continue

                    # ── 4. Regime deterioration guard ──────────────────────────
                    if config.VOLATILE_BLOCK_BUYS and regime == 'VOLATILE':
                        logger.warning(f"Skipping {sym}: no new BUYs in VOLATILE regime")
                        continue
                    if regime == 'BEAR':
                        logger.warning(f"Skipping {sym}: no new BUYs in BEAR regime")
                        continue

                    # ── 4b. Liquidity filter: order value < 1% of 20d ADV ──────
                    if self._order_too_large_vs_adv(best_signal):
                        logger.warning(f"Skipping {sym}: order > 1% of 20d ADV — would move price")
                        continue

                    # ── 5. Trade Scoring ───────────────────────────────────────
                    mtf_result = self.mtf.confirm(sym)
                    research = best_signal.get('_research', {})
                    score_result = self.scorer.score(
                        signal=best_signal,
                        research=research,
                        regime=regime,
                        sector_momentum=research.get('sector_momentum', 0.0),
                        mtf_aligned=mtf_result['aligned'],
                    )
                    comp = score_result['components']
                    decision = 'SKIP' if score_result['skip'] else 'BUY'
                    logger.info(
                        f"TradeScore {sym}: "
                        f"trade_score={score_result['total_score']} "
                        f"overall_score={best_signal.get('overall_score', 0.0):.2f} "
                        f"confidence={best_signal.get('confidence', 0.0):.2f} "
                        f"trend={comp.get('trend', 0)} "
                        f"rsi={comp.get('rsi', 0)} "
                        f"macd={comp.get('macd', 0)} "
                        f"volume={comp.get('volume', 0)} "
                        f"sector={comp.get('sector', 0)} "
                        f"sentiment={comp.get('sentiment', 0)} "
                        f"regime={comp.get('regime', 0)} "
                        f"mtf={mtf_result['aligned']} "
                        f"decision={decision}"
                    )
                    if score_result['skip']:
                        logger.warning(self.explainer.format_skip(
                            symbol=sym,
                            reason="Trade score below effective skip threshold",
                            score_result=score_result,
                            mtf_result=mtf_result,
                            confidence=best_signal.get('confidence', 0.0),
                            overall_score=best_signal.get('overall_score', 0.0),
                            regime=regime,
                        ))
                        continue

                    # ── 6. Minimum R:R guard (1.5:1) ──────────────────────────
                    rr = best_signal.get('risk_reward_ratio', 0)
                    if rr < config.MIN_RISK_REWARD:
                        logger.warning(self.explainer.format_skip(
                            symbol=sym,
                            reason=f"R:R {rr:.2f} below minimum {config.MIN_RISK_REWARD}:1",
                            score_result=score_result,
                            mtf_result=mtf_result,
                            confidence=best_signal.get('confidence', 0.0),
                            overall_score=best_signal.get('overall_score', 0.0),
                            rr=rr,
                            regime=regime,
                        ))
                        continue

                    # ── 6b. SIDEWAYS regime tightened filters ──────────────────
                    if regime == 'SIDEWAYS':
                        signal_conf = best_signal.get('confidence', 0.0)
                        overall_score = best_signal.get('overall_score', 0.0)
                        if (score_result['total_score'] < config.SIDEWAYS_BUY_SCORE_MIN
                                or signal_conf < config.MIN_CONFIDENCE_SIDEWAYS
                                or overall_score <= config.SIDEWAYS_BUY_OVERALL_SCORE_MIN):
                            reasons = []
                            if score_result['total_score'] < config.SIDEWAYS_BUY_SCORE_MIN:
                                reasons.append(f"score {score_result['total_score']} < {config.SIDEWAYS_BUY_SCORE_MIN}")
                            if signal_conf < config.MIN_CONFIDENCE_SIDEWAYS:
                                reasons.append(f"confidence {signal_conf:.0%} < {config.MIN_CONFIDENCE_SIDEWAYS:.0%}")
                            if overall_score <= config.SIDEWAYS_BUY_OVERALL_SCORE_MIN:
                                reasons.append(f"overall {overall_score:.2f} <= {config.SIDEWAYS_BUY_OVERALL_SCORE_MIN}")
                            logger.warning(self.explainer.format_skip(
                                symbol=sym,
                                reason="SIDEWAYS gate: " + ", ".join(reasons),
                                score_result=score_result,
                                mtf_result=mtf_result,
                                confidence=signal_conf,
                                overall_score=overall_score,
                                rr=rr,
                                regime=regime,
                            ))
                            continue

                    # ── 7. Multi-timeframe confirmation ────────────────────────
                    if not mtf_result['aligned']:
                        logger.warning(self.explainer.format_skip(
                            symbol=sym,
                            reason=f"MTF not aligned — {mtf_result['reason']}",
                            score_result=score_result,
                            mtf_result=mtf_result,
                            confidence=best_signal.get('confidence', 0.0),
                            overall_score=best_signal.get('overall_score', 0.0),
                            rr=rr,
                            regime=regime,
                        ))
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
                        logger.warning(self.explainer.format_skip(
                            symbol=sym,
                            reason=f"Re-entry not ready — {reentry_result}",
                            score_result=score_result,
                            mtf_result=mtf_result,
                            confidence=best_signal.get('confidence', 0.0),
                            overall_score=best_signal.get('overall_score', 0.0),
                            rr=rr,
                            regime=regime,
                        ))
                        continue
                    if isinstance(reentry_result, dict):  # approved re-entry — attach metadata
                        best_signal['_reentry_meta'] = reentry_result
                        best_signal['_reentry_meta']['reentry_confidence'] = best_signal.get('confidence', 0)

                    # ── Adaptive position size (score × confidence tier) ────────
                    price      = best_signal.get('current_price', 1)
                    confidence = best_signal.get('confidence', 0.5)
                    # Target allocation tier × score fraction
                    conf_mult  = self._confidence_multiplier(confidence, per_stock_budget)
                    # Reduce position size in SIDEWAYS regime
                    regime_size_factor = config.SIDEWAYS_SIZE_FACTOR if regime == 'SIDEWAYS' else 1.0
                    slot_budget = per_stock_budget * score_result['size_fraction'] * conf_mult * regime_size_factor
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
                        logger.warning(self.explainer.format_skip(
                            symbol=sym,
                            reason=f"Capital limit (₹{projected_invested:.0f} > ₹{max_allowed_invested:.0f})",
                            score_result=score_result,
                            mtf_result=mtf_result,
                            confidence=confidence,
                            overall_score=best_signal.get('overall_score', 0.0),
                            rr=rr,
                            regime=regime,
                        ))
                        continue

                    logger.info(self.explainer.format_buy(
                        symbol=sym,
                        score_result=score_result,
                        mtf_result=mtf_result,
                        confidence=confidence,
                        overall_score=best_signal.get('overall_score', 0.0),
                        rr=rr,
                        regime=regime,
                    ))
                    execution_result = self.order_executor.execute_signal(best_signal)
                    cycle_result['orders_executed'].append(execution_result)
                    if execution_result['success']:
                        logger.info(f"Order executed: {sym}")
                        self._append_trade_log(execution_result)
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
                    self._append_trade_log(execution_result)
                    # Record exit for re-entry engine
                    self._record_exit(sym, sell_signal.get('current_price', 0), 'signal')
            
            # --- Monitor existing positions (SL / target / trailing) - HIGHEST PRIORITY ---
            logger.info("Monitoring existing positions (Stop Loss, Target, Trailing)...")
            position_updates = self.order_executor.monitor_positions()
            cycle_result['positions_monitored'] = position_updates

            # --- Monitor CNC holdings (prior-day delivery positions) ---
            logger.info("Monitoring CNC holdings...")
            holding_updates = self.order_executor.monitor_holdings()
            if holding_updates:
                logger.info(f"Holdings exits: {len(holding_updates)}")
            position_updates = position_updates + holding_updates

            if position_updates:
                logger.info(f"Position updates: {len(position_updates)}")
                for update in position_updates:
                    self._append_trade_log(update)
                    # Record exit for re-entry cooldown — prevents immediate re-buy
                    if update.get('success'):
                        es = update.get('exit_signal', {})
                        if es.get('symbol') and not es.get('partial'):
                            self._record_exit(
                                es['symbol'],
                                es.get('price', 0),
                                es.get('reason', 'sl_target'),
                                pnl=es.get('pnl', 0),
                            )
                    # Alert for partial exits
                    if update.get('success') and update.get('exit_signal', {}).get('partial'):
                        es = update['exit_signal']
                        self._alert(
                            f"🟡 Partial Profit: {es['symbol']}",
                            f"🟡 PARTIAL PROFIT BOOKED\n\n"
                            f"Symbol: {es['symbol']}\n"
                            f"Qty sold: {es['quantity']}\n"
                            f"Price: ₹{es['price']:.2f}\n"
                            f"P&L: ₹{es.get('pnl',0):.2f}\n"
                            f"Net P&L: ₹{es.get('net_pnl',0):.2f}\n"
                            f"Reason: {es.get('reason','')}"
                        )

            # --- AI-Powered Sell Decision: evaluate remaining open positions ---
            # Only run AI on positions that were NOT closed by SL/Target
            open_positions = [
                p for p in self.order_executor.risk_manager.positions
                if p.status in {PositionStatus.OPEN, PositionStatus.PARTIAL}
            ]
            if open_positions:
                logger.info("Evaluating remaining positions with AI Sell Decision Engine...")
                prices_map = {}
                for p in open_positions:
                    pr = self.market_data.get_realtime_price(p.symbol)
                    if pr:
                        prices_map[p.symbol] = pr
                
                # First, run AI Sell Decision Engine
                ai_sell_decisions = []
                for position in open_positions:
                    current_price = prices_map.get(position.symbol)
                    if current_price:
                        decision = self.sell_decision_ai.evaluate_position(position, current_price)
                        if decision.should_sell:
                            ai_sell_decisions.append({
                                'position': position,
                                'decision': decision,
                                'price': current_price
                            })
                            logger.info(
                                f"AI Sell Decision: {position.symbol} - {decision.recommendation} "
                                f"(confidence: {decision.confidence:.2f}) - {decision.reason}"
                            )
                
                # Execute AI sell decisions
                for decision_data in ai_sell_decisions:
                    position = decision_data['position']
                    decision = decision_data['decision']
                    price = decision_data['price']
                    
                    # Determine quantity based on recommendation
                    if decision.recommendation == 'REDUCE_PARTIAL':
                        quantity = position.quantity // 2  # Sell half
                        reason_suffix = " (partial exit)"
                    else:
                        quantity = position.quantity
                        reason_suffix = " (full exit)"
                    
                    exec_r = self.order_executor.execute_signal({
                        'symbol': position.symbol, 'action': 'SELL',
                        'current_price': price,
                        'position_size': quantity,
                        'investment_amount': price * quantity,
                        'stop_loss': 0, 'target': 0,
                        'risk_reward_ratio': 0, 'confidence': decision.confidence,
                        'overall_score': int((1 - decision.confidence) * 100),
                        'reasoning': f"AI Sell Decision: {decision.reason}{reason_suffix}",
                        'allow_loss_exit': True,
                        'timestamp': datetime.now().isoformat(),
                        '_ai_sell_decision': True, '_exit_price': price,
                    })
                    if exec_r['success']:
                        cycle_result['orders_executed'].append(exec_r)
                        self._append_trade_log(exec_r)
                        self._record_exit(position.symbol, price, f"ai_sell_decision_{decision.recommendation}")
                        try:
                            self.telegram.exit({
                                'symbol': position.symbol,
                                'price': price,
                                'quantity': quantity,
                                'reason': decision.reason
                            }, exec_r.get('order_id', ''))
                        except Exception:
                            pass
                
                # Then, run traditional Smart Exit for remaining positions
                remaining_positions = [
                    p for p in open_positions 
                    if p.symbol not in [d['position'].symbol for d in ai_sell_decisions]
                ]
                
                if remaining_positions:
                    remaining_prices = {p.symbol: prices_map[p.symbol] for p in remaining_positions}
                    smart_exits = self.smart_exit.check_all(remaining_positions, remaining_prices, regime)
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
                            '_smart_exit': True, '_exit_price': se['price'],
                        })
                        if exec_r['success']:
                            cycle_result['orders_executed'].append(exec_r)
                            self._append_trade_log(exec_r)
                            self._record_exit(se['symbol'], se['price'], se.get('reason', 'smart_exit'))
                            try:
                                self.telegram.exit(se, exec_r.get('order_id', ''))
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
        
        # Log per-cycle market data metrics for dashboard / diagnostics
        try:
            _metrics = self.market_data.get_cycle_metrics()
            logger.info(
                f"Market data metrics: API={_metrics['api_calls']} "
                f"cache_hits={_metrics['cache_hits']} "
                f"cache_misses={_metrics['cache_misses']} "
                f"hit={_metrics['cache_hit_ratio']:.1%} "
                f"miss={_metrics['cache_miss_ratio']:.1%} "
                f"circuit_breakers={_metrics['circuit_breakers']} "
                f"scan_time={_metrics['cycle_elapsed_seconds']:.1f}s"
            )
        except Exception:
            pass

        logger.info(f"Trading cycle completed at {datetime.now()}")
        logger.info("=" * 50)
        
        self.market_data.get_cycle_metrics(); return cycle_result
    
    def _run_once_wrapper(self):
        """Non-reentrant wrapper so long cycles cannot overlap and don't schedule twice."""
        if not self._run_once_lock.acquire(blocking=False):
            logger.warning("Previous trading cycle still running — skipping scheduled cycle")
            return {}
        try:
            return self.run_once()
        finally:
            self._run_once_lock.release()
    
    def run_scheduled(self, interval_minutes: int = 15):
        """
        Run trading on a schedule
        
        Args:
            interval_minutes: Interval between trading cycles in minutes
        """
        logger.info(f"Starting scheduled trading with {interval_minutes} minute intervals")
        self.is_running = True
        
        # Schedule trading cycles (non-reentrant wrapper prevents overlaps)
        schedule.every(interval_minutes).minutes.do(self._run_once_wrapper)
        
        # Schedule end-of-day close at 14:55 IST — 5 min before cutoff
        schedule.every().day.at("14:55").do(self.end_of_day_close)
        
        # Schedule daily email report at 4:00 PM IST
        schedule.every().day.at("16:00").do(self.daily_email_report)
        
        # Schedule pre-market check (9:20 AM IST - 10 minutes before open)
        schedule.every().day.at("09:20").do(self.pre_market_check)
        
        # Schedule daily reset
        schedule.every().day.at("09:00").do(self.daily_reset)

        # Schedule IP check every 30 minutes to catch dynamic IP changes early
        schedule.every(30).minutes.do(self._check_ip_whitelist)

        # Run one cycle immediately on startup so we don't wait up to 15 min
        logger.info("Running immediate startup cycle...")
        try:
            self._run_once_wrapper()
        except Exception as startup_err:
            logger.error(f"Startup cycle error: {startup_err}")

        while self.is_running:
            try:
                schedule.run_pending()
            except Exception as e:
                logger.error(f"Scheduled job error: {e}")
            time.sleep(60)  # Check every minute
    
    def _check_ip_whitelist(self):
        """Check if public IP has changed — alert ONCE per new IP via email + Telegram."""
        if not self._is_trading_day():
            return
        try:
            import urllib.request as _ur
            # Fallback chain — same as dashboard
            current_ip = ''
            for _url in ('https://api.ipify.org', 'https://ifconfig.me/ip',
                         'https://icanhazip.com', 'https://checkip.amazonaws.com'):
                try:
                    _r = _ur.urlopen(_url, timeout=5).read().decode().strip()
                    if _r and '.' in _r and len(_r) < 20:
                        current_ip = _r
                        break
                except Exception:
                    continue
            if not current_ip:
                return  # can't determine IP — skip silently

            ip_file = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                'data', 'last_known_ip.txt'
            )
            known_ip = open(ip_file).read().strip() if os.path.exists(ip_file) else None

            if known_ip and known_ip != current_ip:
                logger.error(
                    f"\n{'='*60}\n"
                    f"  \u26a0\ufe0f  IP ADDRESS CHANGED!\n"
                    f"  Old IP: {known_ip}\n"
                    f"  New IP: {current_ip}\n"
                    f"  ACTION: Add {current_ip} to Kite whitelist NOW!\n"
                    f"  URL   : https://developers.kite.trade/profile\n"
                    f"{'='*60}"
                )
                # Alert only if this new IP hasn't been alerted on yet
                if current_ip != self._last_alerted_ip:
                    self._alert(
                        f"\u26a0\ufe0f IP Changed — Whitelist {current_ip}",
                        f"\u26a0\ufe0f IP ADDRESS CHANGED\n\n"
                        f"Old IP: {known_ip}\n"
                        f"New IP: {current_ip}\n\n"
                        f"Orders may be BLOCKED until you whitelist the new IP.\n\n"
                        f"Step 1 — Add new IP to Kite:\n"
                        f"https://developers.kite.trade/profile\n\n"
                        f"Step 2 — Re-authenticate if needed:\n"
                        f"https://kite.trade/connect/login?api_key=veq6w4lv31v27ogd&v=3"
                    )
                    self._last_alerted_ip = current_ip
            else:
                # IP unchanged — reset so next change alerts again
                self._last_alerted_ip = ''

            # Always persist current IP
            with open(ip_file, 'w') as f:
                f.write(current_ip)
        except Exception:
            pass

    def _alert(self, subject: str, body: str) -> None:
        """
        Send a critical alert via BOTH Telegram and email.
        Either channel failing does not block the other.
        `body` is plain text for Telegram; HTML is auto-wrapped for email.
        """
        # Telegram (plain text, strip HTML tags for readability)
        try:
            self.telegram._send(body)
        except Exception as _te:
            logger.warning(f"Telegram alert failed: {_te}")
        # Email (wrap in minimal HTML)
        try:
            html_body = "<html><body><pre style='font-family:Arial'>" \
                        + body.replace("&", "&amp;").replace("<b>", "<b>").replace("</b>", "</b>") \
                        + "</pre></body></html>"
            self.email.send_report(subject, html_body)
        except Exception as _ee:
            logger.warning(f"Email alert failed: {_ee}")

    def _is_trading_day(self) -> bool:
        """Return True only on NSE trading days (Mon–Fri, non-holiday)."""
        import pytz
        ist = pytz.timezone('Asia/Kolkata')
        now = datetime.now(ist)
        if now.weekday() >= 5:
            return False
        today_str = now.strftime('%Y-%m-%d')
        from market_data import MarketDataFetcher
        return today_str not in MarketDataFetcher._NSE_HOLIDAYS

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

    def _sector_concentration_exceeded(self, candidate: str, open_symbols: set,
                                        max_per_sector: int = 2) -> bool:
        """
        Return True if adding `candidate` would give more than max_per_sector positions
        in the same sector group. Prevents all-banking or all-IT concentration.
        """
        if not open_symbols:
            return False
        for group in self._SECTOR_GROUPS:
            if candidate in group:
                count = len(group & open_symbols)
                if count >= max_per_sector:
                    return True
        return False

    def _near_earnings(self, symbol: str, days: int = 3) -> bool:
        """
        Return True if the stock has a known corporate action (results/earnings)
        within `days` calendar days. Uses Kite corporate_actions API.
        Fails silently (returns False) if Kite is unavailable.
        """
        try:
            if not self.market_data.kite:
                return False
            from datetime import date, timedelta
            today = date.today()
            window_end = today + timedelta(days=days)
            actions = self.market_data.kite.corporate_actions(
                instrument_token=None,
                exchange="NSE",
                tradingsymbol=symbol,
                from_date=str(today),
                to_date=str(window_end),
            )
            if actions:
                logger.info(
                    f"Earnings guard: {symbol} has {len(actions)} corporate action(s) "
                    f"within {days} days — skipping"
                )
                return True
        except Exception as _e:
            logger.debug(f"Earnings check skipped for {symbol}: {_e}")
        return False

    def _order_too_large_vs_adv(self, signal: Dict, adv_fraction: float = 0.01) -> bool:
        """
        Return True if the proposed order value exceeds `adv_fraction` (default 1%)
        of the 20-day average daily value traded. Prevents price impact on illiquid stocks.
        """
        try:
            order_value = signal.get('investment_amount', 0)
            if order_value <= 0:
                return False
            sym = signal['symbol']
            hist = self.market_data.get_stock_data(sym, period="1mo", interval="1d")
            if hist is None or hist.empty or 'Volume' not in hist.columns:
                return False
            adv_shares = float(hist['Volume'].tail(20).mean())
            avg_price   = float(hist['Close'].tail(20).mean())
            adv_value   = adv_shares * avg_price   # 20d average daily traded value in ₹
            if adv_value <= 0:
                return False
            max_order = adv_value * adv_fraction
            if order_value > max_order:
                logger.info(
                    f"Liquidity check {sym}: order ₹{order_value:.0f} > "
                    f"1% ADV ₹{max_order:.0f} (20d ADV ₹{adv_value:.0f})"
                )
                return True
        except Exception as _e:
            logger.debug(f"ADV liquidity check skipped for {signal.get('symbol')}: {_e}")
        return False

    @staticmethod
    def _confidence_multiplier(confidence: float, per_stock_budget: float) -> float:
        """
        Map confidence to a target allocation tier, then return the multiplier
        needed to reach that target relative to the equal per-stock budget.
        """
        if confidence >= 0.95:
            target = config.CONFIDENCE_ALLOCATION_95
        elif confidence >= 0.85:
            target = config.CONFIDENCE_ALLOCATION_85
        elif confidence >= 0.70:
            target = config.CONFIDENCE_ALLOCATION_70
        else:
            target = config.CONFIDENCE_ALLOCATION_70 * 0.5
        if per_stock_budget <= 0:
            return 1.0
        return max(0.2, min(3.0, target / per_stock_budget))

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
            self._append_trade_log(exec_r)
            self._alert(
                f"🔄 Weekly Rebalance: {weakest.symbol}",
                f"🔄 WEEKLY REBALANCE\n\n"
                f"Exited: {weakest.symbol}\n"
                f"P&L: {wpct*100:.1f}%\n"
                f"Reason: Underperformer replaced by better opportunity"
            )

    def end_of_day_close(self) -> Dict:
        """
        Close all intraday (MIS) positions at end of trading day (3:00 PM IST).
        Swing (CNC) positions are held overnight and monitored by SL/target/max-hold-days.

        Returns:
            Close results
        """
        if not self._is_trading_day():
            logger.info("End-of-day close skipped — not a trading day (weekend/holiday)")
            return {}
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
        
        # Clear pending SELL notifications at end of day (retries stop until next session)
        self.order_executor.reset_pending_sells()

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
        if not self._is_trading_day():
            logger.info("Daily reset skipped — not a trading day (weekend/holiday)")
            return
        logger.info("Executing daily reset")
        self._circuit_breaker_fired_today = False
        self.order_executor.reset_daily()
    
    def _reset_peak_value(self) -> None:
        """Reset the daily peak value to the current opening portfolio value."""
        try:
            holdings = self.order_executor.broker.get_holdings()
            total_value = holdings.get('total_value', 0)
            if total_value <= 0:
                return
            peak_path = os.path.join(os.path.dirname(__file__), '..', 'data', 'peak_value.json')
            os.makedirs(os.path.dirname(peak_path), exist_ok=True)
            today_str = datetime.now(pytz.timezone('Asia/Kolkata')).strftime('%Y-%m-%d')
            with open(peak_path, 'w') as _pf:
                json.dump({'peak_value': total_value, 'date': today_str}, _pf)
            logger.info(f"Daily peak value reset to ₹{total_value:.2f}")
        except Exception as e:
            logger.warning(f"Peak value reset failed: {e}")

    def pre_market_check(self):
        """Pre-market check before trading starts"""
        if not self._is_trading_day():
            logger.info("Pre-market check skipped — not a trading day (weekend/holiday)")
            return
        logger.info("Executing pre-market check (9:20 AM IST)")
        # Reset daily circuit-breaker state and opening peak
        self._circuit_breaker_fired_today = False
        self._reset_peak_value()

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

        # Pre-market data warming: fetch 3mo/1d once before 9:15 so first cycle is cache-hot
        try:
            _prefetch = list(self.dynamic_universe.get_universe(top_n=200))
            _prefetch = list(dict.fromkeys(list(_prefetch) + ['NIFTY 50']))
            logger.info(f"Pre-market prefetch starting for {len(_prefetch)} symbols...")
            self.market_data.prefetch_historical(_prefetch, '3mo', '1d')
            self.market_data.prefetch_quotes(_prefetch)
            logger.info("Pre-market prefetch completed")
        except Exception as _pme:
            logger.warning(f"Pre-market prefetch failed: {_pme}")
    
    def _append_trade_log(self, entry: Dict) -> None:
        self.trade_log.append(entry)
        if len(self.trade_log) > self._TRADE_LOG_MAX:
            self.trade_log = self.trade_log[-self._TRADE_LOG_MAX:]

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
        """Send daily email report at 4:00 PM IST — trading days only."""
        if not self._is_trading_day():
            logger.info("Daily email report skipped — not a trading day (weekend/holiday)")
            return
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
