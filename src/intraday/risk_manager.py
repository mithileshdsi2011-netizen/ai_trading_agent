"""
Intraday Risk Manager Module

Manages intraday risk controls completely separate from Swing RiskManager.
Optionally persists daily state via IntradayStateStore so the dashboard
and engine share the same view of risk.
"""
import logging
from typing import Dict, Any, List, Optional
from datetime import date
from dataclasses import dataclass

from .models import IntradayConfig, IntradaySignal, IntradayPosition, PositionStatus
from .state import IntradayStateStore

logger = logging.getLogger(__name__)


@dataclass
class DailyRiskState:
    """Daily risk state tracking."""
    date: str
    trades_today: int = 0
    wins_today: int = 0
    losses_today: int = 0
    realized_pnl: float = 0.0
    consecutive_losses: int = 0
    blocked: bool = False
    block_reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            'date': self.date,
            'trades_today': self.trades_today,
            'wins_today': self.wins_today,
            'losses_today': self.losses_today,
            'realized_pnl': self.realized_pnl,
            'consecutive_losses': self.consecutive_losses,
            'blocked': self.blocked,
            'block_reason': self.block_reason,
        }

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> 'DailyRiskState':
        return DailyRiskState(
            date=data.get('date', date.today().isoformat()),
            trades_today=int(data.get('trades_today', 0)),
            wins_today=int(data.get('wins_today', 0)),
            losses_today=int(data.get('losses_today', 0)),
            realized_pnl=float(data.get('realized_pnl', 0.0)),
            consecutive_losses=int(data.get('consecutive_losses', 0)),
            blocked=bool(data.get('blocked', False)),
            block_reason=data.get('block_reason'),
        )


class IntradayRiskManager:
    """Intraday risk manager."""

    def __init__(self, config: IntradayConfig, state_store: Optional[IntradayStateStore] = None):
        """
        Initialize risk manager.

        Args:
            config: IntradayConfig instance
            state_store: Optional IntradayStateStore for persistence
        """
        self.config = config
        self._state_store = state_store
        self._daily_state: Optional[DailyRiskState] = None
        self._open_positions: Dict[str, IntradayPosition] = {}
        self._pending_keys: set = set()
        self._kill_switch_active = config.kill_switch
        self._load_daily_state()
        self._load_positions()

    def _load_daily_state(self):
        """Load or initialize daily risk state."""
        today = date.today().isoformat()

        if self._daily_state and self._daily_state.date == today:
            return

        # Try persisted state first
        if self._state_store:
            persisted = self._state_store.load_daily_state()
            if persisted:
                self._daily_state = DailyRiskState.from_dict(persisted)
                return

        # Initialize new daily state
        self._daily_state = DailyRiskState(date=today)
        logger.info(f"[INTRADAY RISK] Initialized daily state for {today}")

    def _save_daily_state(self):
        """Persist daily state if a store is configured."""
        if self._state_store and self._daily_state:
            self._state_store.save_daily_state(self._daily_state.to_dict())

    def _load_positions(self):
        """Load persisted open positions if a store is configured."""
        if not self._state_store:
            return
        for position in self._state_store.load_positions():
            if position.status == PositionStatus.OPEN:
                self._open_positions[position.symbol] = position

    def _save_positions(self):
        """Persist open positions if a store is configured."""
        if self._state_store:
            self._state_store.save_positions(self.get_open_positions())

    def set_kill_switch(self, active: bool):
        """Enable/disable the intraday-only kill switch at runtime."""
        self._kill_switch_active = bool(active)
        state = "ACTIVE" if active else "CLEARED"
        logger.warning(f"[INTRADAY RISK] Kill switch {state} — new intraday entries "
                       f"{'blocked' if active else 'allowed'}")

    def is_killed(self) -> bool:
        """True if the kill switch is active (config or runtime)."""
        return self._kill_switch_active or self.config.kill_switch

    def can_enter_trade(self, signal: IntradaySignal,
                        idempotency_key: Optional[str] = None) -> tuple[bool, Optional[str]]:
        """
        Check if a new trade entry is allowed.

        Args:
            signal: IntradaySignal instance
            idempotency_key: Optional dedup key for the pending entry

        Returns:
            Tuple of (allowed, rejection_reason)
        """
        self._load_daily_state()

        # Check kill switch
        if self.is_killed():
            return False, "Kill switch is active"

        # Check if daily blocked
        if self._daily_state.blocked:
            return False, self._daily_state.block_reason or "Trading blocked"

        # Check max trades per day
        if self._daily_state.trades_today >= self.config.max_trades_per_day:
            return False, f"Max trades per day reached ({self.config.max_trades_per_day})"

        # Check consecutive losses
        if self._daily_state.consecutive_losses >= self.config.max_consecutive_losses:
            return False, f"Consecutive loss limit reached ({self.config.max_consecutive_losses})"

        # Check max concurrent positions
        if len(self._open_positions) >= self.config.max_concurrent_positions:
            return False, f"Max concurrent positions reached ({self.config.max_concurrent_positions})"

        # Check daily loss limit (realized + open-position unrealized losses)
        daily_loss_limit = self.config.intraday_capital * self.config.max_daily_loss_pct
        unrealized_loss = sum(
            min(p.unrealized_pnl, 0.0) for p in self._open_positions.values()
        )
        if (self._daily_state.realized_pnl + unrealized_loss) <= -daily_loss_limit:
            self._daily_state.blocked = True
            self._daily_state.block_reason = "Daily loss limit reached"
            self._save_daily_state()
            return False, f"Daily loss limit reached (₹{daily_loss_limit:.2f})"

        # Check minimum risk:reward
        if signal.risk_reward < self.config.min_risk_reward:
            return False, f"Risk:Reward below minimum ({signal.risk_reward} < {self.config.min_risk_reward})"

        # Check signal classification
        if signal.classification.value == "NO_TRADE":
            return False, "Signal classification is NO_TRADE"

        # Check for duplicate position
        if signal.symbol in self._open_positions:
            return False, f"Position already exists for {signal.symbol}"

        # Check for duplicate pending order / already-executed signal
        if idempotency_key and idempotency_key in self._pending_keys:
            return False, f"Duplicate signal suppressed for {signal.symbol}"

        return True, None

    def register_pending(self, idempotency_key: str):
        """Register an idempotency key before executing an entry."""
        if idempotency_key:
            self._pending_keys.add(idempotency_key)

    def release_pending(self, idempotency_key: str):
        """
        Release a pending idempotency key after a FAILED entry attempt.

        Successful entries keep their key registered via
        record_trade_entry(), preserving day-long duplicate protection.
        Only call this when the entry attempt did not produce a position.
        """
        if idempotency_key:
            self._pending_keys.discard(idempotency_key)

    def record_trade_entry(self, signal: IntradaySignal,
                           idempotency_key: Optional[str] = None):
        """
        Record a trade entry.

        Args:
            signal: IntradaySignal instance
            idempotency_key: Optional dedup key to keep registered
        """
        self._daily_state.trades_today += 1
        if idempotency_key:
            self._pending_keys.add(idempotency_key)
        self._save_daily_state()
        logger.info(f"[INTRADAY RISK] Trade entry recorded: {signal.symbol}, Total trades: {self._daily_state.trades_today}")

    def record_trade_exit(self, position: IntradayPosition):
        """
        Record a trade exit and update daily stats.

        Args:
            position: IntradayPosition instance
        """
        self._daily_state.realized_pnl += position.realized_pnl

        if position.realized_pnl > 0:
            self._daily_state.wins_today += 1
            self._daily_state.consecutive_losses = 0
        else:
            self._daily_state.losses_today += 1
            self._daily_state.consecutive_losses += 1

        self._save_daily_state()
        logger.info(
            f"[INTRADAY RISK] Trade exit recorded: {position.symbol}, "
            f"P&L: ₹{position.realized_pnl:.2f}, "
            f"Daily P&L: ₹{self._daily_state.realized_pnl:.2f}"
        )

    def add_position(self, position: IntradayPosition):
        """
        Add an open position.

        Args:
            position: IntradayPosition instance
        """
        self._open_positions[position.symbol] = position
        self._save_positions()
        logger.info(f"[INTRADAY RISK] Position added: {position.symbol}, Open positions: {len(self._open_positions)}")

    def update_position(self, position: IntradayPosition):
        """Refresh stored state for an open position (price/P&L updates)."""
        if position.symbol in self._open_positions:
            self._open_positions[position.symbol] = position
            self._save_positions()

    def remove_position(self, symbol: str):
        """
        Remove an open position.

        Args:
            symbol: Symbol to remove
        """
        if symbol in self._open_positions:
            del self._open_positions[symbol]
            self._save_positions()
            logger.info(f"[INTRADAY RISK] Position removed: {symbol}, Open positions: {len(self._open_positions)}")

    def get_open_positions(self) -> List[IntradayPosition]:
        """Get all open positions."""
        return list(self._open_positions.values())

    def get_position(self, symbol: str) -> Optional[IntradayPosition]:
        """Get position by symbol."""
        return self._open_positions.get(symbol)

    def get_daily_state(self) -> Dict[str, Any]:
        """Get daily risk state."""
        self._load_daily_state()

        return {
            'date': self._daily_state.date,
            'trades_today': self._daily_state.trades_today,
            'max_trades_per_day': self.config.max_trades_per_day,
            'wins_today': self._daily_state.wins_today,
            'losses_today': self._daily_state.losses_today,
            'realized_pnl': self._daily_state.realized_pnl,
            'daily_loss_limit': self.config.intraday_capital * self.config.max_daily_loss_pct,
            'consecutive_losses': self._daily_state.consecutive_losses,
            'max_consecutive_losses': self.config.max_consecutive_losses,
            'blocked': self._daily_state.blocked,
            'block_reason': self._daily_state.block_reason,
            'open_positions': len(self._open_positions),
            'max_concurrent_positions': self.config.max_concurrent_positions,
            'kill_switch': self.is_killed()
        }

    def reset_daily_block(self):
        """Reset daily block (manual override)."""
        if self._daily_state:
            self._daily_state.blocked = False
            self._daily_state.block_reason = None
            self._save_daily_state()
            logger.warning("[INTRADAY RISK] Daily block manually reset")

    def toggle_kill_switch(self, enabled: bool):
        """
        Toggle kill switch.

        Args:
            enabled: Whether to enable kill switch
        """
        self.set_kill_switch(enabled)
        logger.warning("[INTRADAY RISK] Kill switch toggled at runtime; "
                       "ANGEL_KILL_SWITCH env var also applies")
