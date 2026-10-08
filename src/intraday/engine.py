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
from datetime import datetime
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


class IntradayEngine:
    """Orchestrates the isolated Angel One intraday engine."""

    def __init__(self, config: Optional[IntradayConfig] = None,
                 state_dir: Optional[str] = None,
                 scan_interval_seconds: int = DEFAULT_SCAN_INTERVAL_SECONDS):
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
            self.risk_manager, self.position_sizer, self.config
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
        self._cycle_running = False
        self._auth_state = "NOT CONFIGURED"
        self._auth_attempted = False
        self._auth_cooldown_until: Optional[datetime] = None
        self._last_scan_time: Optional[datetime] = None
        self._last_data_time: Optional[datetime] = None
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
        """LIVE / STALE / DISCONNECTED."""
        if self.websocket.is_connected():
            return "STALE" if self.websocket.is_data_stale(self.config.max_data_age_seconds) else "LIVE"
        last_fetch = self.market_data.last_update_time() or self._last_data_time
        if last_fetch is None:
            return "DISCONNECTED"
        if self.validator.is_data_fresh(last_fetch):
            return "LIVE"
        return "STALE"

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

        ok, reason = self.validator.validate_entry_conditions(
            signal, self._last_data_time
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

    def _refresh_positions(self) -> int:
        """Update open positions with fresh quotes and process exits."""
        positions = self.paper_executor.get_open_positions()
        if not positions:
            return 0

        symbols = [p.symbol for p in positions]
        quotes = self.market_data.get_quotes(symbols)
        prices = {s: q['ltp'] for s, q in quotes.items() if q and q.get('ltp')}
        if prices:
            self.paper_executor.update_positions(prices)
            self._record_data_time()
            for pos in positions:
                self.risk_manager.update_position(pos)

        exited = self.position_manager.check_all_exits()
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
        """EOD square-off of any remaining paper positions."""
        if not self.paper_executor.get_open_positions():
            return
        if not self.validator.is_square_off_time() and self.validator.is_market_open():
            return
        trades = self.paper_executor.eod_square_off()
        for trade in trades:
            self.trade_manager.add_trade(trade)
        if trades:
            logger.warning(f"[INTRADAY] EOD SQUARE OFF executed for {len(trades)} positions")
        self.state_store.save_positions([])

    def maybe_run_cycle(self):
        """Kick off a background cycle if the scan interval has elapsed.
        Never blocks the caller — a full scan can take minutes."""
        due = (self._last_scan_time is None
               or (datetime.now() - self._last_scan_time).total_seconds() >= self.scan_interval)
        if not due or self._cycle_running:
            return

        def _bg():
            self._cycle_running = True
            try:
                self.run_cycle()
            except Exception as e:
                logger.error(f"[INTRADAY] Background cycle error: {e}")
            finally:
                self._cycle_running = False

        threading.Thread(target=_bg, daemon=True, name="intraday-cycle").start()

    # ── kill switch ─────────────────────────────────────────────────
    def set_kill_switch(self, active: bool):
        """Intraday-only kill switch; does NOT affect Swing Trading."""
        self.risk_manager.set_kill_switch(active)
        status = self.state_store.load_engine_status()
        status['kill_switch'] = bool(active)
        self.state_store.save_engine_status(status)

    # ── status / serialization ──────────────────────────────────────
    def _persist_status(self):
        self.state_store.save_engine_status({
            'kill_switch': self.risk_manager.is_killed(),
            'market_data_status': self.market_data_status(),
            'market_regime': self._market_regime.value,
            'last_scan_time': self._last_scan_time.isoformat() if self._last_scan_time else None,
            'last_data_time': self._last_data_time.isoformat() if self._last_data_time else None,
        })

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
        return {
            'auth_status': self._auth_state if self.credentials_configured() else "NOT CONFIGURED",
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
            'opportunities': [self._signal_to_dict(s) for s in self._opportunities],
            'positions': [self._position_to_dict(p) for p in self.paper_executor.get_open_positions()],
            'history': self._history(),
            'metrics': self.metrics.calculate_metrics(self.trade_manager.get_trades()),
            'websocket': self.websocket.get_status(),
            'last_scan_time': status.get('last_scan_time'),
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
                history.append({
                    'symbol': trade.symbol,
                    'direction': trade.direction.value,
                    'entry_price': trade.entry_price,
                    'exit_price': trade.exit_price,
                    'net_pnl': trade.net_pnl,
                    'exit_reason': trade.exit_reason,
                    'exit_time': trade.exit_time.strftime('%H:%M:%S'),
                })
            except Exception as e:
                logger.error(f"[INTRADAY] History serialize error: {e}")
        return history


# ── singleton ───────────────────────────────────────────────────────
_ENGINE: Optional[IntradayEngine] = None
_ENGINE_LOCK = threading.Lock()


def get_intraday_engine(**kwargs) -> IntradayEngine:
    """Process-wide intraday engine singleton."""
    global _ENGINE
    with _ENGINE_LOCK:
        if _ENGINE is None:
            _ENGINE = IntradayEngine(**kwargs)
        return _ENGINE
