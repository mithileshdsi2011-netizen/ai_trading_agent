"""
Intraday Engine Orchestrator

Wires the intraday pipeline end to end:

    Angel Market Data
        -> Intraday Scanner
        -> Technical Features
        -> Intraday Scorer
        -> AI Explanation (no order authority)
        -> Hard Rule Validation
        -> Intraday Risk Manager
        -> Position Sizing
        -> Paper Trade Executor

V1 defaults to PAPER trading. Live execution is architected behind
ANGEL_LIVE_TRADING=false and stays disabled.

Every public method fails closed: any Angel-side error degrades the
intraday engine only — Swing Trading and IPO Intelligence are unaffected.
"""
import logging
import os
import threading
import time
from datetime import datetime, date
from typing import Any, Dict, List, Optional

from .models import (
    IntradayConfig, IntradaySignal, IntradayPosition,
    MarketRegime, SignalClassification, PositionStatus,
)
from .config import get_intraday_config
from .validators import IntradayValidator, signal_idempotency_key
from .state import IntradayStateStore
from .scanner import IntradayScanner
from .scorer import IntradayScorer
from .analyzer import IntradayAnalyzer
from .risk_manager import IntradayRiskManager
from .position_sizer import IntradayPositionSizer
from .paper_executor import PaperTradeExecutor
from .live_executor import LiveTradeExecutor
from .position_manager import IntradayPositionManager
from .trade_manager import IntradayTradeManager
from .journal import IntradayJournal
from .metrics import IntradayMetrics
from .angel.auth import AngelAuth
from .angel.client import AngelClient
from .angel.instruments import AngelInstruments
from .angel.market_data import AngelMarketData
from .angel.websocket import AngelWebSocket

logger = logging.getLogger(__name__)

# How often a lazy scan cycle may run when the dashboard polls
DEFAULT_SCAN_INTERVAL_SECONDS = 60

# Quote-refresh failure monitoring — after this many consecutive failed
# refresh cycles with open PAPER positions, one deduplicated incident
# notification is raised via the shared alert store.
QUOTE_FAIL_ALERT_THRESHOLD = 3
QUOTE_INCIDENT_KEY = 'intraday:angel:quote_refresh_failure'
EOD_QUOTE_INCIDENT_KEY = 'intraday:angel:eod_quote_failure'

# A scan cycle running longer than this is surfaced as a stuck-scan
# incident (deduplicated) — rate-limit storms can stretch cycles far
# beyond their normal duration even with zero open positions.
SCAN_STUCK_ALERT_SECONDS = 600
SCAN_STUCK_INCIDENT_KEY = 'intraday:angel:scan_stuck'
SCHEDULER_TICK_SECONDS = 30
# EOD duty must never starve behind a scan lock. The tick waits a bounded
# time per attempt, retries each tick, and raises a deduplicated incident
# once the delay crosses the alert threshold.
EOD_LOCK_TIMEOUT_SECONDS = 5
EOD_DELAY_ALERT_SECONDS = 300
# Once EOD is confirmed complete (or the market is closed with nothing
# open), the scheduler drops to a slow heartbeat instead of trading cycles.
IDLE_TICK_SECONDS = 120
EOD_DELAYED_INCIDENT_KEY = 'intraday:angel:eod_delayed'
OVERNIGHT_INCIDENT_KEY = 'intraday:angel:overnight_positions'


def daily_breakdown(trades: List, entries_today: int,
                    max_entries: int) -> Dict[str, Any]:
    """Split today's ledger into entry-date vs exit-date metrics.

    trades: exit-date-filtered records (e.g. get_today_trades()).
    A trade is 'carryover' when its entry date precedes its exit date.
    Pure function — kept module-level for regression tests.
    """
    same = {'exits': 0, 'wins': 0, 'losses': 0, 'pnl': 0.0}
    carry = {'exits': 0, 'wins': 0, 'losses': 0, 'pnl': 0.0}
    for t in trades or []:
        entry_dt = getattr(t, 'entry_time', None)
        exit_dt = getattr(t, 'exit_time', None)
        is_carry = (isinstance(entry_dt, datetime)
                    and isinstance(exit_dt, datetime)
                    and entry_dt.date() != exit_dt.date())
        bucket = carry if is_carry else same
        pnl = getattr(t, 'net_pnl', 0.0) or 0.0
        bucket['exits'] += 1
        bucket['pnl'] += pnl
        if pnl > 0:
            bucket['wins'] += 1
        else:
            bucket['losses'] += 1
    for b in (same, carry):
        b['pnl'] = round(b['pnl'], 2)
    return {
        'entries_today': entries_today,
        'max_entries': max_entries,
        'exits_today': same['exits'] + carry['exits'],
        'same_day': same,
        'carryover': carry,
        'realized_pnl': round(same['pnl'] + carry['pnl'], 2),
    }


def _production_incident(action: str, incident_key: str, detail: str) -> None:
    """Default incident sink — deduplicated email via the shared store.

    'notify' sends one email per incident (persistent dedup suppresses
    repeats); 'resolve' clears the incident and emits a single recovery
    note. Never called under pytest (engine gates on PYTEST_CURRENT_TEST).
    """
    from email_reports import EmailReporter
    if action == 'resolve':
        from persistence import get_store
        if get_store().resolve_alert_incident(incident_key):
            EmailReporter().send_report(
                "Angel PAPER: market-data quotes recovered",
                "Quote refresh recovered — intraday exit monitoring is live "
                "again. Fresh prices resume driving SL/target evaluation.")
        return
    EmailReporter().send_report(
        "Angel PAPER: market-data quote failure",
        f"Intraday PAPER quote refresh failure.\n\n{detail}\n\n"
        "Positions are preserved and exits deferred — SL/target "
        "evaluation requires fresh prices and will not use stale data.",
        incident_key=incident_key)


class IntradayEngine:
    """Orchestrates the isolated Angel One intraday engine."""

    def __init__(self, config: Optional[IntradayConfig] = None,
                 state_dir: Optional[str] = None,
                 scan_interval_seconds: int = DEFAULT_SCAN_INTERVAL_SECONDS,
                 alert_hook=None):
        self.config = config or get_intraday_config()
        self.state_store = IntradayStateStore(state_dir)
        self.scan_interval = scan_interval_seconds

        # Pipeline components
        self.validator = IntradayValidator(self.config)
        self.scorer = IntradayScorer(self.config)
        self.analyzer = IntradayAnalyzer()
        self.journal = IntradayJournal()
        self.metrics = IntradayMetrics()

        # Risk / execution
        self.risk_manager = IntradayRiskManager(self.config, self.state_store)
        self.position_sizer = IntradayPositionSizer(self.config)
        self.paper_executor = PaperTradeExecutor(
            self.risk_manager, self.position_sizer, self.config,
            validator=self.validator
        )
        self.position_manager = IntradayPositionManager(self.paper_executor)
        trades_file = os.path.join(self.state_store.state_dir, "trades.json")
        self.trade_manager = IntradayTradeManager(data_file=trades_file)

        # Angel connectivity — never raises
        self.auth = AngelAuth(
            api_key=self.config.angel_api_key,
            client_code=self.config.angel_client_code,
            password=self.config.angel_password_or_mpin,
            totp_secret=self.config.angel_totp_secret,
        )
        self.client = AngelClient(self.auth)
        self.instruments = AngelInstruments(cache_dir=self.state_store.state_dir)
        self.market_data = AngelMarketData(self.client, self.instruments)
        self.scanner = IntradayScanner(self.market_data, self.instruments, self.config)
        self.websocket = AngelWebSocket(self.auth)
        self.live_executor = LiveTradeExecutor(self.client, self.config)  # always disabled in V1

        # Runtime state
        self._lock = threading.Lock()
        self._alert_hook = alert_hook
        self._quote_fail_streak = 0
        self._cycle_running = False
        self._scheduler_started = False
        self._scheduler_stop = threading.Event()
        self._scheduler_thread: Optional[threading.Thread] = None
        self._cycle_started_at: Optional[datetime] = None
        self._last_cycle_duration_s: Optional[float] = None
        self._scan_stuck_notified = False
        self._auth_state = "AUTH PENDING" if self.credentials_configured() else "NOT CONFIGURED"
        self._auth_attempted = False
        self._auth_cooldown_until: Optional[datetime] = None
        self._last_scan_time: Optional[datetime] = None
        self._last_data_time: Optional[datetime] = None
        self._scheduler_last_tick: Optional[datetime] = None
        self._scheduler_last_success: Optional[datetime] = None
        self._eod_pending_since: Optional[datetime] = None
        self._eod_last_attempt: Optional[datetime] = None
        self._eod_completed_at: Optional[datetime] = None
        self._eod_last_failure: Optional[str] = None
        self._eod_delay_alerted = False
        self._overnight_alerted = False
        self._market_regime = MarketRegime.SIDEWAYS
        self._opportunities: List[IntradaySignal] = []
        self._instruments_loaded = False

        # Restore persisted kill switch / engine status
        status = self.state_store.load_engine_status()
        if status.get('kill_switch'):
            self.risk_manager.set_kill_switch(True)

        # Rehydrate open paper positions into the executor.
        # (Risk manager already restores its own copy via state_store.)
        for pos in self.state_store.load_positions():
            if pos.status == PositionStatus.OPEN:
                self.paper_executor.restore_position(pos)

        logger.info("[INTRADAY] Engine initialized (mode=%s, universe=%s)",
                    "PAPER" if self.config.paper_trading else "LIVE",
                    self.config.trading_universe)

    # ── authentication ──────────────────────────────────────────────
    def credentials_configured(self) -> bool:
        return bool(
            self.config.angel_api_key
            and self.config.angel_client_code
            and self.config.angel_password_or_mpin
            and self.config.angel_totp_secret
        )

    def authenticate(self, force: bool = False) -> bool:
        """
        Authenticate with Angel SmartAPI. Fails closed, never raises.
        Retries are rate-limited by an auth cooldown.
        """
        if not self.credentials_configured():
            self._auth_state = "NOT CONFIGURED"
            return False

        now = datetime.now()
        if self.auth.is_authenticated():
            self._auth_state = "CONNECTED"
            return True
        if self._auth_cooldown_until and now < self._auth_cooldown_until and not force:
            return self.auth.is_authenticated()

        try:
            result = self.auth.authenticate()
        except Exception as e:
            logger.error(f"[INTRADAY] Angel auth exception: {e}")
            result = {'success': False, 'error': str(e)}

        if result.get('success'):
            self._auth_state = "CONNECTED"
            logger.info("[INTRADAY] Angel authentication successful")
            self._auth_cooldown_until = None
            return True

        self._auth_state = "ANGEL AUTHENTICATION FAILED"
        self._auth_cooldown_until = now.fromtimestamp(now.timestamp() + 300)
        logger.error(f"[INTRADAY] Angel authentication failed: {result.get('error')}")
        return False

    # ── market data / websocket status ──────────────────────────────
    def market_data_status(self) -> str:
        """LIVE / STALE / DISCONNECTED — coarse "is data flowing" signal.

        The engine is REST-polled: quote freshness for ENTRIES is gated
        separately (30s per-symbol stamp in validate_entry_conditions).
        Here, any successful fetch (quotes or candles) within a
        scan-scaled window means the feed is healthy — a multi-minute
        scan with continuous candle pulls must not flicker STALE."""
        if self.websocket.is_connected():
            return "STALE" if self.websocket.is_data_stale(self.config.max_data_age_seconds) else "LIVE"
        last_fetch = self.market_data.last_fetch_time() or self._last_data_time
        if last_fetch is None:
            return "DISCONNECTED"
        window = max(self.scan_interval * 2, self.config.max_data_age_seconds)
        age = (datetime.now() - last_fetch).total_seconds()
        return "LIVE" if age <= window else "STALE"

    def _record_data_time(self):
        """
        Stamp data freshness ONLY when market data was actually fetched.

        If no fetch succeeded, the previous timestamp is preserved so the
        stale-data validator keeps rejecting entries. On startup with no
        successful fetch, _last_data_time stays None -> data unavailable.
        """
        ts = self.market_data.last_update_time()
        if ts is not None:
            self._last_data_time = ts

    # ── main pipeline ───────────────────────────────────────────────
    def run_cycle(self) -> Dict[str, Any]:
        """
        Run one scan/validate/execute cycle. Idempotent and thread-safe.

        Returns:
            Cycle summary dict
        """
        with self._lock:
            return self._run_cycle_locked()

    def _run_cycle_locked(self) -> Dict[str, Any]:
        summary = {'scanned': 0, 'signals': 0, 'entries': 0, 'exits': 0,
                   'skipped_reason': None}
        try:
            # Gate 1: credentials
            if not self.credentials_configured():
                summary['skipped_reason'] = "Angel credentials not configured"
                return summary

            # Gate 2: auth
            if not self.authenticate():
                summary['skipped_reason'] = self._auth_state
                return summary

            # Gate 3: market hours
            if not self.validator.is_market_open():
                summary['skipped_reason'] = "Outside market hours"
                # Still ensure nothing stays open past the close
                self._maybe_square_off()
                return summary

            # Instruments must resolve before any data fetch
            if not self._instruments_loaded:
                self._instruments_loaded = self.instruments.load_instruments()
                if not self._instruments_loaded:
                    summary['skipped_reason'] = "Instrument master unavailable"
                    return summary

            # Refresh open positions + exits first
            summary['exits'] = self._refresh_positions()

            # EOD square-off takes precedence over new entries
            if self.validator.is_square_off_time():
                self._maybe_square_off()
                summary['skipped_reason'] = "Square-off window"
                return summary

            # Market regime
            self._market_regime = self.scanner.detect_market_regime()

            # Scan universe -> score -> validate -> execute
            opportunities = []
            for symbol in self.instruments.get_all_symbols():
                try:
                    symbol_data = self.scanner.get_symbol_data(symbol)
                    if not symbol_data or not symbol_data.get('features'):
                        continue
                    summary['scanned'] += 1

                    signal = self.scorer.calculate_score(symbol_data, self._market_regime)
                    if signal is None:
                        continue

                    signal.ai_explanation = self.analyzer.explain_signal(signal)
                    summary['signals'] += 1

                    if signal.classification == SignalClassification.NO_TRADE:
                        continue
                    opportunities.append(signal)

                    self._try_enter(signal)
                    if self.risk_manager.get_position(signal.symbol):
                        summary['entries'] += 1
                except Exception as e:
                    logger.error(f"[INTRADAY] Symbol cycle error for {symbol}: {e}")

            self._opportunities = sorted(
                opportunities, key=lambda s: s.score, reverse=True
            )[:10]
            self._last_scan_time = datetime.now()
            self._record_data_time()
            self._persist_status()

        except Exception as e:
            logger.error(f"[INTRADAY] Cycle error: {e}")
            summary['skipped_reason'] = f"Engine error: {e}"
        return summary

    def _try_enter(self, signal: IntradaySignal):
        """Run hard validation + risk gates, then paper-execute."""
        key = signal_idempotency_key(signal)

        # Freshness gate: use this symbol's own successful-fetch stamp —
        # _last_data_time is only written at cycle end and a batch stamp
        # does not prove this symbol's quote was returned. Fall back to
        # the cycle stamp when the per-symbol stamp is unavailable.
        data_time = self._last_data_time
        try:
            sym_ts = self.market_data.symbol_last_update(signal.symbol)
            if isinstance(sym_ts, datetime):
                data_time = sym_ts
        except Exception:
            pass
        ok, reason = self.validator.validate_entry_conditions(
            signal, data_time
        )
        if not ok:
            signal.validation_passed = False
            signal.rejection_reason = reason
            self.journal.log_rejection(signal.symbol, reason)
            return

        ok, reason = self.risk_manager.can_enter_trade(signal, idempotency_key=key)
        if not ok:
            signal.validation_passed = False
            signal.rejection_reason = reason
            self.journal.log_rejection(signal.symbol, reason)
            return

        self.risk_manager.register_pending(key)
        try:
            position = self.paper_executor.execute_entry(signal)
        except Exception as e:
            # Failed entry must not hold the dedup key for the rest of the day
            self.risk_manager.release_pending(key)
            logger.error(f"[INTRADAY] Entry execution error for {signal.symbol}: {e}")
            raise
        if position:
            self.journal.log_entry({
                'symbol': position.symbol,
                'direction': position.direction.value,
                'entry_price': position.entry_price,
                'quantity': position.quantity,
                'stop_loss': position.stop_loss,
                'target': position.target,
            })
        else:
            self.risk_manager.release_pending(key)

    def _incident(self, action: str, incident_key: str, detail: str) -> None:
        """Raise or clear an operational incident. Fail-safe: notification
        errors never propagate into the trading loop. Under pytest the
        production sink is bypassed entirely (alert_hook or nothing)."""
        hook = self._alert_hook
        if hook is None:
            if os.environ.get('PYTEST_CURRENT_TEST'):
                return  # never notify from automated tests
            hook = _production_incident
        try:
            hook(action, incident_key, detail)
        except Exception as e:
            logger.warning(f"[INTRADAY] incident {action} failed: {e}")

    def _refresh_positions(self) -> int:
        """Update open positions with fresh quotes and process exits."""
        positions = self.paper_executor.get_open_positions()
        if not positions:
            return 0

        symbols = [p.symbol for p in positions]
        quotes = self.market_data.get_quotes(symbols)
        prices = {s: q['ltp'] for s, q in quotes.items() if q and q.get('ltp')}
        if not prices:
            self._quote_fail_streak += 1
            logger.warning(
                f"[INTRADAY] No fresh quotes for {len(positions)} open position(s) "
                f"— exits deferred, positions preserved "
                f"(failure {self._quote_fail_streak}/{QUOTE_FAIL_ALERT_THRESHOLD})")
            if self._quote_fail_streak >= QUOTE_FAIL_ALERT_THRESHOLD:
                self._incident(
                    'notify', QUOTE_INCIDENT_KEY,
                    f"{len(positions)} open PAPER position(s); "
                    f"{self._quote_fail_streak} consecutive quote-refresh failures.")
            return 0

        # Success — even if prices are identical to the previous values,
        # a non-empty quote response is a healthy refresh, not a failure.
        if self._quote_fail_streak:
            self._incident('resolve', QUOTE_INCIDENT_KEY, '')
            self._quote_fail_streak = 0

        self.paper_executor.update_positions(prices, quotes=quotes)
        self._record_data_time()
        for pos in positions:
            self.risk_manager.update_position(pos)

        # Exit checks only on symbols with fresh prices — a stale
        # current_price must never trigger SL/target.
        exited = self.position_manager.check_all_exits(fresh_symbols=set(prices))
        for trade in exited:
            self.trade_manager.add_trade(trade)
            self.journal.log_exit({
                'symbol': trade.symbol,
                'exit_price': trade.exit_price,
                'net_pnl': trade.net_pnl,
                'exit_reason': trade.exit_reason,
            })
        return len(exited)

    def _maybe_square_off(self):
        """EOD square-off of any remaining paper positions.

        Carryover policy: a position that survives a session is an
        operational exception — flagged via the overnight incident — but
        it is still MANAGED: SL/target monitoring resumes next session
        via _refresh_positions, and it is force-closed at the next
        square-off window. Outside exchange hours it is NEVER exited —
        a non-session quote is at best a last-traded close, and exiting
        on it would book fictitious P&L."""
        if not self.paper_executor.get_open_positions():
            return
        # Square-off only inside the configured EOD window on a trading
        # day (15:00→midnight, Mon–Fri). Outside exchange hours a broker
        # quote is at best a last-traded close — exiting on it would book
        # fictitious P&L. Positions found open outside the window are
        # flagged overnight, monitored during the next valid session, and
        # force-closed at its square-off.
        if not self.validator.is_square_off_window():
            return
        self._eod_last_attempt = datetime.now()
        # Refresh prices once more so EOD exits use live data where possible
        try:
            open_syms = [p.symbol for p in self.paper_executor.get_open_positions()]
            quotes = self.market_data.get_quotes(open_syms)
            prices = {s: q['ltp'] for s, q in quotes.items() if q and q.get('ltp')}
            if prices:
                self.paper_executor.update_positions(prices, quotes=quotes)
            else:
                logger.warning(
                    "[INTRADAY] EOD square-off: no fresh quotes — "
                    "exiting at last known prices")
                self._incident(
                    'notify', EOD_QUOTE_INCIDENT_KEY,
                    "EOD square-off executed at last-known prices — live "
                    "quotes unavailable at close.")
        except Exception as e:
            logger.warning(
                f"[INTRADAY] EOD square-off: quote refresh failed ({e}) — "
                "exiting at last known prices")
            self._incident(
                'notify', EOD_QUOTE_INCIDENT_KEY,
                f"EOD square-off executed at last-known prices — quote "
                f"refresh raised {e!r} at close.")
        trades = self.paper_executor.eod_square_off()
        for trade in trades:
            self.trade_manager.add_trade(trade)
        if trades:
            logger.warning(f"[INTRADAY] EOD SQUARE OFF executed for {len(trades)} positions")
        # Persist what ACTUALLY remains open — a failed execute_exit must
        # not wipe the position from the ledger (restart would lose it
        # and pending-EOD tracking depends on the persisted state).
        remaining = self.paper_executor.get_open_positions()
        self.state_store.save_positions(remaining)
        if not remaining:
            self._eod_completed_at = datetime.now()

    def maybe_run_cycle(self):
        """Kick off a background cycle if the scan interval has elapsed.
        Never blocks the caller — a full scan can take minutes."""
        # No new scans once square-off is due — but keep the EOD path
        # alive: a non-blocking square-off attempt preserves the redundant
        # coverage run_cycle's gate-3 used to provide via polls.
        if self.validator.is_square_off_window():
            if self._lock.acquire(blocking=False):
                try:
                    self._maybe_square_off()
                finally:
                    self._lock.release()
            return
        due = (self._last_scan_time is None
               or (datetime.now() - self._last_scan_time).total_seconds() >= self.scan_interval)
        # Check-and-set under the lock: a scheduler tick and an API poll must
        # never spawn two cycles for the same slot. The acquire MUST be
        # non-blocking — run_cycle holds this lock for the whole (multi-
        # minute, API-throttled) scan, so a blocking acquire would hang
        # every /api/intraday-data poll behind an in-flight scan.
        if not self._lock.acquire(blocking=False):
            return
        try:
            if not due or self._cycle_running:
                return
            self._cycle_running = True
            self._cycle_started_at = datetime.now()
        finally:
            self._lock.release()

        def _bg():
            try:
                self.run_cycle()
            except Exception as e:
                logger.error(f"[INTRADAY] Background cycle error: {e}")
            finally:
                self._last_cycle_duration_s = (
                    datetime.now() - self._cycle_started_at).total_seconds()
                self._cycle_started_at = None
                self._cycle_running = False
                if self._scan_stuck_notified:
                    self._scan_stuck_notified = False
                    self._incident('resolve', SCAN_STUCK_INCIDENT_KEY, '')

        threading.Thread(target=_bg, daemon=True, name="intraday-cycle").start()

    def start_scheduler(self, tick_seconds: int = SCHEDULER_TICK_SECONDS):
        """Self-schedule scan cycles during market hours.

        Without this, the engine is poll-driven: cycles only start when
        /api/intraday-data is requested, so exits (SL/target/EOD) never
        evaluate while the Intraday tab is unviewed. A 30s daemon tick
        calls maybe_run_cycle(), which no-ops unless a scan is due —
        cheap, single-flight, and gated to market hours so nothing hits
        the network overnight or on weekends."""
        if self._scheduler_started:
            return
        self._scheduler_started = True
        self._scheduler_stop.clear()
        self._scheduler_thread = None

        def _tick():
            while not self._scheduler_stop.is_set():
                try:
                    self._scheduler_tick_once()
                except Exception as e:
                    self._eod_last_failure = repr(e)
                    logger.error(f"[INTRADAY] scheduler tick error: {e}")
                try:
                    self._persist_status()
                except Exception:
                    pass
                # Idle once nothing can need the trading loop: market closed,
                # square-off window handled, no open positions.
                idle = not (self.paper_executor.get_open_positions()
                            or (self.validator.is_market_open()
                                and not self.validator.is_square_off_window()))
                self._scheduler_stop.wait(
                    IDLE_TICK_SECONDS if idle else tick_seconds)

        self._scheduler_thread = threading.Thread(
            target=_tick, daemon=True, name="intraday-scheduler")
        self._scheduler_thread.start()

    def _scheduler_tick_once(self):
        """One scheduler iteration — extracted for deterministic testing.

        Priority: EOD square-off duty > session scans > overnight recovery.
        Never blocks indefinitely; a busy scan lock defers work to the next
        tick rather than hanging the scheduler thread."""
        self._scheduler_last_tick = datetime.now()
        positions = self.paper_executor.get_open_positions()
        market_open = self.validator.is_market_open()
        square_off_due = self.validator.is_square_off_window()

        if square_off_due:
            # EOD duty — no new scans once square-off is due.
            if positions:
                if self._eod_pending_since is None:
                    self._eod_pending_since = datetime.now()
                self._eod_last_attempt = datetime.now()
                if self._lock.acquire(timeout=EOD_LOCK_TIMEOUT_SECONDS):
                    try:
                        self._maybe_square_off()
                    finally:
                        self._lock.release()
                else:
                    delay = (datetime.now()
                             - self._eod_pending_since).total_seconds()
                    logger.warning(
                        f"[INTRADAY] EOD square-off delayed — scan lock held "
                        f"({delay:.0f}s pending, retrying next tick)")
                    if (delay >= EOD_DELAY_ALERT_SECONDS
                            and not self._eod_delay_alerted):
                        self._eod_delay_alerted = True
                        self._incident(
                            'notify', EOD_DELAYED_INCIDENT_KEY,
                            f"EOD square-off delayed {delay:.0f}s by scan "
                            f"lock contention — {len(positions)} PAPER "
                            f"position(s) still open.")
            else:
                # EOD confirmed complete — zero positions inside the
                # square-off window. Stamp completion once (auditable
                # "EOD done" marker) and resolve any delay state.
                if self._eod_completed_at is None:
                    self._eod_completed_at = datetime.now()
                self._eod_pending_since = None
                if self._eod_delay_alerted:
                    self._eod_delay_alerted = False
                    self._incident('resolve', EOD_DELAYED_INCIDENT_KEY, '')
        elif market_open:
            self.maybe_run_cycle()
        # else: market closed, not in a square-off window (overnight,
        # early morning, weekend) — positions are flagged, never exited
        # on stale quotes; recovery happens inside the next session.

        # Overnight PAPER positions are an operational exception: flagged
        # once (deduped incident), exit policy unchanged.
        today = date.today()
        overnight = [
            p.symbol for p in self.paper_executor.get_open_positions()
            if isinstance(p.entry_time, datetime)
            and p.entry_time.date() < today]
        if overnight and not self._overnight_alerted:
            self._overnight_alerted = True
            self._incident(
                'notify', OVERNIGHT_INCIDENT_KEY,
                f"{len(overnight)} PAPER position(s) carried "
                f"overnight: {overnight}")
        elif not overnight and self._overnight_alerted:
            self._overnight_alerted = False
            self._incident('resolve', OVERNIGHT_INCIDENT_KEY, '')

        self._scheduler_last_success = datetime.now()

    def stop_scheduler(self):
        """Stop the background tick (test teardown / graceful shutdown)."""
        self._scheduler_stop.set()

    # ── kill switch ─────────────────────────────────────────────────
    def set_kill_switch(self, active: bool):
        """Intraday-only kill switch; does NOT affect Swing Trading."""
        self.risk_manager.set_kill_switch(active)
        status = self.state_store.load_engine_status()
        status['kill_switch'] = bool(active)
        self.state_store.save_engine_status(status)

    # ── status / serialization ──────────────────────────────────────
    def _scheduler_status(self) -> Dict[str, Any]:
        """Distinguishes a healthy idle scheduler from a dead/blocked one:
        `last_tick` advances every loop iteration while `last_success` only
        advances on clean iterations — divergence = repeated failures."""
        now = datetime.now()
        def _iso(dt): return dt.isoformat() if dt else None
        open_positions = self.paper_executor.get_open_positions()
        today = date.today()
        overnight = sum(
            1 for p in open_positions
            if isinstance(p.entry_time, datetime)
            and p.entry_time.date() < today)
        idle = not (open_positions
                    or (self.validator.is_market_open()
                        and not self.validator.is_square_off_window()))
        thread_alive = (self._scheduler_thread.is_alive()
                        if self._scheduler_thread else False)
        return {
            'running': self._scheduler_started
                       and not self._scheduler_stop.is_set()
                       and thread_alive,
            'thread_alive': thread_alive,
            'mode': 'idle' if idle else 'active',
            'last_tick': _iso(self._scheduler_last_tick),
            'last_success': _iso(self._scheduler_last_success),
            'tick_age_seconds': round(
                (now - self._scheduler_last_tick).total_seconds(), 1)
                if self._scheduler_last_tick else None,
            'expected_interval_seconds': IDLE_TICK_SECONDS if idle
                else SCHEDULER_TICK_SECONDS,
            'eod_pending_since': _iso(self._eod_pending_since),
            'eod_last_attempt': _iso(self._eod_last_attempt),
            'eod_completed_at': _iso(self._eod_completed_at),
            'eod_last_failure': self._eod_last_failure,
            'eod_delay_alerted': self._eod_delay_alerted,
            'open_positions': len(open_positions),
            'overnight_positions': overnight,
        }

    def _persist_status(self):
        # Merge into the existing file: other writers (kill switch, earlier
        # cycle) must not be clobbered; the store writes atomically.
        status = self.state_store.load_engine_status() or {}
        status.update({
            'kill_switch': self.risk_manager.is_killed(),
            'market_data_status': self.market_data_status(),
            'market_regime': self._market_regime.value,
            'last_scan_time': self._last_scan_time.isoformat() if self._last_scan_time else None,
            'last_data_time': self._last_data_time.isoformat() if self._last_data_time else None,
            'scheduler': self._scheduler_status(),
        })
        self.state_store.save_engine_status(status)

    @staticmethod
    def _signal_to_dict(signal: IntradaySignal) -> Dict[str, Any]:
        return {
            'symbol': signal.symbol,
            'direction': signal.direction.value,
            'score': signal.score,
            'entry': signal.entry_price,
            'stop_loss': signal.stop_loss,
            'target': signal.target,
            'risk_reward': signal.risk_reward,
            'classification': signal.classification.value.replace('_', ' '),
            'strategy': signal.strategy,
            'explanation': signal.ai_explanation,
            'rejection_reason': signal.rejection_reason,
        }

    @staticmethod
    def _position_to_dict(pos: IntradayPosition) -> Dict[str, Any]:
        return {
            'symbol': pos.symbol,
            'direction': pos.direction.value,
            'entry_price': pos.entry_price,
            'current_price': pos.current_price,
            'quantity': pos.quantity,
            'stop_loss': pos.stop_loss,
            'target': pos.target,
            'unrealized_pnl': pos.unrealized_pnl,
        }

    def _engine_state(self) -> str:
        """Human-readable engine state for the dashboard."""
        if self.risk_manager.is_killed():
            return "STOPPED (KILL SWITCH)"
        if self.risk_manager.get_daily_state()['blocked']:
            return "BLOCKED (RISK LIMIT)"
        if not self.credentials_configured():
            return "NOT CONFIGURED"
        if self._auth_state not in ("CONNECTED",):
            return self._auth_state
        if self._cycle_running:
            return "SCANNING"
        if not self.validator.is_market_open():
            return "MARKET CLOSED"
        return "ACTIVE"

    def _market_session(self) -> str:
        """Current NSE session label."""
        if not self.validator.is_market_open():
            return "CLOSED"
        if self.validator.is_square_off_time():
            return "SQUARE-OFF WINDOW"
        return "OPEN"

    def get_status(self) -> Dict[str, Any]:
        """Dashboard-facing engine status."""
        daily = self.risk_manager.get_daily_state()
        status = self.state_store.load_engine_status()

        # Stuck-scan detection — fires once per episode, from the caller's
        # thread, so it works even while the cycle itself is grinding.
        scan_seconds = None
        if self._cycle_running and self._cycle_started_at:
            scan_seconds = (datetime.now() - self._cycle_started_at).total_seconds()
            if scan_seconds >= SCAN_STUCK_ALERT_SECONDS and not self._scan_stuck_notified:
                self._scan_stuck_notified = True
                self._incident(
                    'notify', SCAN_STUCK_INCIDENT_KEY,
                    f"Scan cycle running {scan_seconds:.0f}s "
                    f"(threshold {SCAN_STUCK_ALERT_SECONDS}s); last completed "
                    f"scan {self._last_scan_time}. Likely rate-limit storm or "
                    "stalled API calls.")

        return {
            'auth_status': self._auth_state if self.credentials_configured() else "NOT CONFIGURED",
            # Split the conflated auth concept into its real parts.
            # `auth_configured` is kept unchanged for existing consumers —
            # it means configured AND authenticated.
            'credentials_configured': self.credentials_configured(),
            'is_authenticated': self.auth.is_authenticated(),
            'auth_configured': self.credentials_configured() and self.auth.is_authenticated(),
            'market_data_status': self.market_data_status(),
            'market_data_available': self.market_data_status() == "LIVE",
            'mode': 'PAPER' if self.config.paper_trading else 'LIVE',
            # Regime is only meaningful after a successful scan — never show
            # SIDEWAYS when no fresh regime data exists.
            'market_regime': self._market_regime.value if self._last_scan_time else 'UNKNOWN',
            'market_session': self._market_session(),
            'engine_state': self._engine_state(),
            'cycle_running': self._cycle_running,
            'trading_blocked': daily['blocked'] or self.risk_manager.is_killed(),
            'kill_switch': self.risk_manager.is_killed(),
            'static_ip_status': self._static_ip_status(),
            'daily_state': daily,
            'daily_breakdown': self._daily_breakdown(),
            'opportunities': [self._signal_to_dict(s) for s in self._opportunities],
            'positions': [self._position_to_dict(p) for p in self.paper_executor.get_open_positions()],
            'history': self._history(),
            'metrics': self.metrics.calculate_metrics(self.trade_manager.get_trades()),
            'websocket': self.websocket.get_status(),
            'last_scan_time': status.get('last_scan_time'),
            'scheduler': self._scheduler_status(),
            'scan_started': self._cycle_started_at.isoformat() if self._cycle_started_at else None,
            'scan_seconds': scan_seconds,
            'last_cycle_duration_s': self._last_cycle_duration_s,
            # Response generation stamp so the UI can age/label stale data.
            'server_time': datetime.now().isoformat(),
        }

    def _static_ip_status(self) -> str:
        """Angel static-IP readiness (independent of Kite static-IP setup)."""
        if self.config.paper_trading and not self.config.angel_live_trading:
            return "NOT REQUIRED FOR CURRENT PAPER MODE"
        configured = bool(os.getenv("ANGEL_STATIC_IP"))
        return "CONFIGURED" if configured else "NOT CONFIGURED"

    def _history(self) -> List[Dict[str, Any]]:
        history = []
        for trade in self.trade_manager.get_today_trades():
            try:
                entry_dt = trade.entry_time if isinstance(trade.entry_time, datetime) else None
                exit_dt = trade.exit_time if isinstance(trade.exit_time, datetime) else None
                history.append({
                    'symbol': trade.symbol,
                    'direction': trade.direction.value,
                    'entry_price': trade.entry_price,
                    'exit_price': trade.exit_price,
                    'quantity': trade.quantity,
                    'net_pnl': trade.net_pnl,
                    'exit_reason': trade.exit_reason,
                    'entry_time': entry_dt.strftime('%Y-%m-%d %H:%M:%S') if entry_dt else None,
                    'exit_time': exit_dt.strftime('%H:%M:%S') if exit_dt else None,
                    'carryover': bool(entry_dt and exit_dt
                                      and entry_dt.date() != exit_dt.date()),
                    # Existing ledger rows carry no quote-timestamp metadata —
                    # freshness of the exit price cannot be proven. New rows
                    # carry real provenance from execution time.
                    'exit_price_source': trade.exit_price_source or 'UNVERIFIED',
                    'exit_exchange_ts': trade.exit_exchange_ts.isoformat()
                        if isinstance(trade.exit_exchange_ts, datetime) else None,
                    'exit_quote_received_at': trade.exit_quote_received_at.isoformat()
                        if isinstance(trade.exit_quote_received_at, datetime) else None,
                    'exit_quote_age_s': trade.exit_quote_age_s,
                    'exit_quote_symbol': trade.exit_quote_symbol,
                    'exit_quote_exchange': trade.exit_quote_exchange,
                })
            except Exception as e:
                logger.error(f"[INTRADAY] History serialize error: {e}")
        return history

    def _daily_breakdown(self) -> Dict[str, Any]:
        """Entry-date vs exit-date split — trades_today counts entries,
        wins/losses/pnl count exits; carryover exits are separated so the
        two axes stop masquerading as one number."""
        return daily_breakdown(
            self.trade_manager.get_today_trades(),
            entries_today=self.risk_manager.get_daily_state().get('trades_today', 0),
            max_entries=self.config.max_trades_per_day,
        )


# ── singleton ───────────────────────────────────────────────────────
_ENGINE: Optional[IntradayEngine] = None
_ENGINE_LOCK = threading.Lock()


def get_intraday_engine(**kwargs) -> IntradayEngine:
    """Process-wide intraday engine singleton."""
    global _ENGINE
    with _ENGINE_LOCK:
        if _ENGINE is None:
            _ENGINE = IntradayEngine(**kwargs)
        _ENGINE.start_scheduler()
        return _ENGINE
