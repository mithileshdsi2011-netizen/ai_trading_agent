"""
Risk Management Module
Manages trading risk and position sizing.
Features: ATR-based SL, volatility position sizing, partial profit booking,
daily blacklist, correlation guard, slippage/brokerage tracking, JSON persistence.
"""
from typing import Dict, List, Optional, Set
import logging
import os
from datetime import datetime, timedelta, date
from dataclasses import dataclass, field
from enum import Enum

from persistence import get_store

import pandas as pd
import numpy as np

from config import config
from enterprise_risk_engine import EnterpriseRiskEngine

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# NSE brokerage constants (Zerodha)
_ZERODHA_BROKERAGE_PCT  = 0.0003   # 0.03% per leg (capped ₹20)
_ZERODHA_BROKERAGE_CAP  = 20.0     # ₹20 per order
_STT_SELL_PCT           = 0.001    # 0.1% STT on sell side (delivery)
_EXCHANGE_TXN_PCT       = 0.0000325
_SEBI_CHARGES_PCT       = 0.000001
_GST_PCT                = 0.18

PERSISTENCE_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "positions.json"
)


def _brokerage(value: float) -> float:
    """Zerodha brokerage for one leg."""
    return min(_ZERODHA_BROKERAGE_PCT * value, _ZERODHA_BROKERAGE_CAP)


def _total_charges(buy_value: float, sell_value: float) -> float:
    """Estimate total round-trip charges (brokerage + STT + exchange + SEBI + GST)."""
    brok = _brokerage(buy_value) + _brokerage(sell_value)
    stt  = _STT_SELL_PCT * sell_value
    exc  = _EXCHANGE_TXN_PCT * (buy_value + sell_value)
    sebi = _SEBI_CHARGES_PCT * (buy_value + sell_value)
    base = brok + stt + exc + sebi
    gst  = _GST_PCT * (brok + exc + sebi)
    return round(base + gst, 2)


class PositionStatus(Enum):
    """Position status enumeration"""
    OPEN = "OPEN"
    CLOSED = "CLOSED"
    STOPPED_OUT = "STOPPED_OUT"
    TARGET_HIT = "TARGET_HIT"
    PARTIAL = "PARTIAL"       # 50% sold, rest trailing


@dataclass
class Position:
    """Trading position data class"""
    symbol: str
    entry_price: float
    quantity: int
    stop_loss: float
    target: float
    entry_time: datetime
    status: PositionStatus = PositionStatus.OPEN
    exit_price: Optional[float] = None
    exit_time: Optional[datetime] = None
    exit_reason: Optional[str] = None
    pnl: float = 0.0
    pnl_percentage: float = 0.0
    charges: float = 0.0        # brokerage + STT + exchange
    net_pnl: float = 0.0        # pnl after charges
    slippage: float = 0.0       # actual vs expected fill difference
    planned_exit_date: Optional[datetime] = None
    product_type: str = "MIS"
    highest_price: float = 0.0
    first_entry_price: float = 0.0
    trailing_stop: Optional[float] = None
    atr_at_entry: float = 0.0   # ATR used for SL calculation
    partial_booked: bool = False # True once partial profit booked
    partial_qty: int = 0         # cumulative partial exited quantity
    initial_quantity: int = 0    # original entry quantity (for scale-out)
    partial_count: int = 0       # number of partial exits taken
    scale_in_qty: int = 0        # cumulative quantity added via scale-in
    sector: str = "Unknown"      # sector for correlation guard


class RiskManager:
    """Manages trading risk and positions"""
    
    def __init__(self):
        self._store = get_store()
        self.positions: List[Position] = []
        self.daily_pnl = 0.0
        self.daily_trades = 0
        self.max_daily_loss = config.TRADING_AMOUNT * config.DAILY_MAX_LOSS_PCT
        self._daily_blacklist: Set[str] = set()  # symbols SL-hit today
        self._blacklist_date: date = date.today()
        # Track last full exit time per symbol for REENTRY_COOLDOWN_HOURS
        self._last_exit_by_symbol: Dict[str, datetime] = {}
        self._load_daily_state()
        self._load_positions()
        self._enterprise = EnterpriseRiskEngine(self)

    # ------------------------------------------------------------------
    # Persistence helpers
    # ------------------------------------------------------------------

    def _load_positions(self):
        """Restore positions from SQLite store on restart."""
        loaded = []
        try:
            rows = self._store.load_positions()
            for p in rows:
                if p.get('status') not in ('OPEN', 'PARTIAL'):
                    continue
                pos = Position(
                    symbol=p.get('symbol', ''),
                    entry_price=float(p.get('entry_price', 0.0) or 0.0),
                    quantity=int(p.get('quantity', 0) or 0),
                    stop_loss=float(p.get('stop_loss', 0.0) or 0.0),
                    target=float(p.get('target', 0.0) or 0.0),
                    entry_time=datetime.fromisoformat(p['entry_time']) if p.get('entry_time') else datetime.now(),
                    status=PositionStatus(p.get('status', 'OPEN')),
                    planned_exit_date=datetime.fromisoformat(p['planned_exit_date']) if p.get('planned_exit_date') else None,
                    product_type=p.get('product_type', 'CNC'),
                    highest_price=float(p.get('highest_price', p.get('entry_price', 0.0) or 0.0)),
                    first_entry_price=float(p.get('first_entry_price', p.get('entry_price', 0.0) or 0.0)),
                    trailing_stop=float(p.get('trailing_stop')) if p.get('trailing_stop') is not None else None,
                    atr_at_entry=float(p.get('atr_at_entry', 0.0) or 0.0),
                    partial_booked=p.get('partial_booked', False),
                    partial_qty=int(p.get('partial_qty', 0) or 0),
                    partial_count=int(p.get('partial_count', 0) or 0),
                    scale_in_qty=int(p.get('scale_in_qty', 0) or 0),
                    initial_quantity=int(p.get('initial_quantity', 0) or 0),
                    exit_price=float(p.get('exit_price')) if p.get('exit_price') is not None else None,
                    exit_time=datetime.fromisoformat(p['exit_time']) if p.get('exit_time') else None,
                    exit_reason=p.get('exit_reason'),
                    pnl=float(p.get('pnl', 0.0) or 0.0),
                    pnl_percentage=float(p.get('pnl_percentage', 0.0) or 0.0),
                    charges=float(p.get('charges', 0.0) or 0.0),
                    net_pnl=float(p.get('net_pnl', 0.0) or 0.0),
                    slippage=float(p.get('slippage', 0.0) or 0.0),
                    sector=p.get('sector', 'Unknown'),
                )
                if '_partial_target' in p:
                    pos._partial_target = p['_partial_target']
                loaded.append(pos)
                logger.info(f"Restored position from SQLite: {pos.symbol} {pos.quantity} @ {pos.entry_price}")
            self.positions = loaded
            logger.info(f"Loaded {len(self.positions)} positions from SQLite store")
            return self.positions
        except Exception as e:
            logger.warning(f"Could not load positions from store: {e}")
            self.positions = []
            return self.positions

    def _load_daily_state(self):
        """Restore daily P&L, blacklist and last-exit map from SQLite."""
        try:
            state = self._store.load_daily_state()
            self.daily_pnl = float(state.get('daily_pnl', 0.0))
            self.daily_trades = int(state.get('daily_trades', 0))
            self._daily_blacklist = set(state.get('daily_blacklist', []))
            self._blacklist_date = date.fromisoformat(state.get('date'))
            last_exits = state.get('last_exit_by_symbol', {})
            self._last_exit_by_symbol = {
                k: datetime.fromisoformat(v) if isinstance(v, str) else v
                for k, v in last_exits.items()
            }
        except Exception as e:
            logger.warning(f"Could not load daily state: {e}")
            self.daily_pnl = 0.0
            self.daily_trades = 0
            self._daily_blacklist = set()
            self._blacklist_date = date.today()
            self._last_exit_by_symbol = {}

    def save_positions(self):
        """Persist current positions + daily state to SQLite."""
        try:
            pos_list = []
            for p in self.positions:
                d = {
                    'symbol': p.symbol,
                    'entry_price': p.entry_price,
                    'quantity': p.quantity,
                    'stop_loss': p.stop_loss,
                    'target': p.target,
                    'entry_time': p.entry_time.isoformat(),
                    'planned_exit_date': p.planned_exit_date.isoformat() if p.planned_exit_date else None,
                    'product_type': p.product_type,
                    'highest_price': p.highest_price,
                    'first_entry_price': p.first_entry_price,
                    'trailing_stop': p.trailing_stop,
                    'atr_at_entry': p.atr_at_entry,
                    'partial_booked': p.partial_booked,
                    'partial_qty': p.partial_qty,
                    'initial_quantity': p.initial_quantity or p.quantity,
                    'partial_count': p.partial_count,
                    'scale_in_qty': p.scale_in_qty,
                    'sector': p.sector,
                    'status': p.status.value,
                    'pnl': p.pnl,
                    'pnl_percentage': p.pnl_percentage,
                    'charges': p.charges,
                    'net_pnl': p.net_pnl,
                    'slippage': p.slippage,
                    'exit_price': p.exit_price,
                    'exit_time': p.exit_time.isoformat() if p.exit_time else None,
                }
                if hasattr(p, '_partial_target'):
                    d['_partial_target'] = p._partial_target
                pos_list.append(d)
            self._store.save_positions(pos_list, clear=True)
            self._store.save_daily_state(
                state_date=date.today().isoformat(),
                daily_pnl=self.daily_pnl,
                daily_blacklist=list(self._daily_blacklist),
                last_exit_by_symbol=self._last_exit_by_symbol,
                daily_trades=self.daily_trades
            )
        except Exception as e:
            logger.error(f"Could not save positions: {e}")

    # ------------------------------------------------------------------
    # Daily blacklist helpers
    # ------------------------------------------------------------------

    def _refresh_blacklist(self):
        """Clear blacklist if it's a new trading day."""
        today = date.today()
        if today != self._blacklist_date:
            self._daily_blacklist.clear()
            self._blacklist_date = today

    def add_to_blacklist(self, symbol: str):
        """Add a symbol to today's blacklist after SL hit."""
        self._refresh_blacklist()
        self._daily_blacklist.add(symbol)
        logger.info(f"Blacklisted for today: {symbol}")
        self.save_positions()

    def is_blacklisted(self, symbol: str) -> bool:
        self._refresh_blacklist()
        return symbol in self._daily_blacklist

    # ------------------------------------------------------------------
    # ATR & volatility helpers
    # ------------------------------------------------------------------

    @staticmethod
    def calculate_atr(historical_df: pd.DataFrame, period: int = 14) -> float:
        """
        Calculate Average True Range from a OHLCV DataFrame.
        Returns 0.0 if data insufficient.
        """
        try:
            df = historical_df.copy()
            df['prev_close'] = df['Close'].shift(1)
            df['tr'] = pd.concat([
                df['High'] - df['Low'],
                (df['High'] - df['prev_close']).abs(),
                (df['Low']  - df['prev_close']).abs(),
            ], axis=1).max(axis=1)
            atr = df['tr'].rolling(period).mean().iloc[-1]
            return float(atr) if pd.notna(atr) else 0.0
        except Exception:
            return 0.0

    @staticmethod
    def atr_stop_loss(entry_price: float, atr: float, multiplier: float = 2.0) -> float:
        """ATR-based stop-loss: entry - (multiplier × ATR)."""
        if atr <= 0:
            sl_pct = config.SWING_STOP_LOSS_PERCENTAGE if config.TRADING_MODE == "swing" else config.STOP_LOSS_PERCENTAGE
            return round(entry_price * (1 - sl_pct), 2)
        return round(entry_price - multiplier * atr, 2)

    @staticmethod
    def volatility_position_size(capital_per_trade: float, entry_price: float,
                                  atr: float, risk_pct: float = 0.01) -> int:
        """
        Size position so 1 ATR move = risk_pct of total capital.
        Falls back to equal-allocation if ATR unavailable.
        """
        risk_amount = config.TRADING_AMOUNT * risk_pct
        if atr > 0 and entry_price > 0:
            qty = int(risk_amount / atr)
            qty = max(1, min(qty, int(capital_per_trade / entry_price)))
        else:
            qty = max(1, int(capital_per_trade / entry_price))
        return qty
    
    def can_open_position(self, signal: Dict) -> bool:
        """
        Check if a new position can be opened based on risk parameters.
        Enterprise portfolio-level gate runs first.
        """
        symbol = signal['symbol']

        # Defensive DB check: positions list can drift vs source-of-truth
        open_db_symbols = {
            p.get('symbol')
            for p in self._store.load_positions()
            if p.get('status') in ('OPEN', 'PARTIAL')
        }
        if symbol in open_db_symbols:
            logger.warning(f"Duplicate position: {symbol} already open")
            return False

        # ── Enterprise master pre-BUY gate ───────────────────────────
        if not self._enterprise.pre_buy_risk_check(signal):
            return False

        # Check daily blacklist (SL-hit stocks are banned for the rest of the day)
        if self.is_blacklisted(symbol):
            logger.warning(f"Skipping {symbol}: on today's blacklist (SL hit earlier)")
            return False

        # Check maximum positions
        open_count = len([p for p in self.positions if p.status in (PositionStatus.OPEN, PositionStatus.PARTIAL)])
        if open_count >= config.MAX_POSITIONS:
            logger.warning("Maximum positions reached")
            return False
        
        # Check duplicate position
        if any(p.status in (PositionStatus.OPEN, PositionStatus.PARTIAL) and p.symbol == symbol
               for p in self.positions):
            logger.warning(f"Duplicate position: {symbol} already open")
            return False
        
        # Check re-entry cooldown (REENTRY_COOLDOWN_HOURS after a full exit)
        last_exit = self._last_exit_by_symbol.get(symbol)
        if last_exit is not None:
            hours_since = (datetime.now() - last_exit).total_seconds() / 3600
            if hours_since < config.REENTRY_COOLDOWN_HOURS:
                logger.warning(
                    f"{symbol} re-entry blocked: {hours_since:.2f}h since exit "
                    f"(cooldown {config.REENTRY_COOLDOWN_HOURS}h)"
                )
                return False
        
        # Check sector concentration
        sector = (signal.get('_research') or {}).get('sector', 'Unknown')
        if sector != 'Unknown':
            sector_count = sum(
                1 for p in self.positions
                if p.status in (PositionStatus.OPEN, PositionStatus.PARTIAL) and p.sector == sector
            )
            if sector_count >= config.MAX_SECTOR_POSITIONS:
                logger.warning(f"Sector {sector} already has {sector_count} positions (max {config.MAX_SECTOR_POSITIONS}) - skipping {symbol}")
                return False
        
        # Check daily loss limit
        if self.daily_pnl < -self.max_daily_loss:
            logger.warning(f"Daily loss limit reached: {self.daily_pnl:.2f}")
            return False
        
        # Check portfolio heat (total open unrealised risk)
        open_risk = sum(
            max(p.entry_price - p.stop_loss, 0) * p.quantity
            for p in self.positions
            if p.status in (PositionStatus.OPEN, PositionStatus.PARTIAL) and p.stop_loss
        )
        entry_price = signal.get('current_price', signal.get('price', 0.0))
        atr = signal.get('atr', 0.0)
        qty = signal.get('position_size', 0)
        if entry_price > 0 and atr > 0 and qty > 0:
            sl_multiplier = float(os.environ.get('ATR_SL_MULTIPLIER', '2.0'))
            provisional_sl = self.atr_stop_loss(entry_price, atr, sl_multiplier)
            new_risk = max(entry_price - provisional_sl, 0) * qty
            if open_risk + new_risk > config.MAX_PORTFOLIO_RISK:
                logger.warning(
                    f"Portfolio heat limit reached: open ₹{open_risk:.0f} + new ₹{new_risk:.0f} "
                    f"> max ₹{config.MAX_PORTFOLIO_RISK:.0f} — skipping {symbol}"
                )
                return False
        
        # Check risk-reward ratio
        if signal['risk_reward_ratio'] < config.MIN_RISK_REWARD:
            logger.warning(f"R:R too low: {signal['risk_reward_ratio']:.2f} (min {config.MIN_RISK_REWARD})")
            return False

        # Check confidence using rounded percentages to avoid floating-point edge cases
        conf_pct = round(signal['confidence'] * 100)
        regime = signal.get('market_regime', 'SIDEWAYS')
        if regime == 'BULL':
            threshold = config.MIN_CONFIDENCE_BULL
        elif regime == 'BEAR':
            threshold = config.MIN_CONFIDENCE_BEAR
        elif regime == 'SIDEWAYS':
            threshold = config.MIN_CONFIDENCE_SIDEWAYS
        else:
            threshold = config.MIN_CONFIDENCE
        min_pct = round(threshold * 100)
        if conf_pct < min_pct:
            logger.warning(f"Confidence too low for {regime}: {conf_pct}% (min {min_pct}%)")
            return False
        
        # Sanity cap: investment must not exceed the full trading amount
        # (orchestrator already handles per-slot sizing; this just catches runaway values)
        if signal.get('investment_amount', 0) > config.TRADING_AMOUNT * config.MAX_CAPITAL_USAGE:
            logger.warning(
                f"Investment ₹{signal['investment_amount']:.0f} exceeds "
                f"trading amount ₹{config.TRADING_AMOUNT * config.MAX_CAPITAL_USAGE:.0f}"
            )
            return False
        
        return True
    
    def open_position(self, signal: Dict) -> Position:
        """
        Open a new position using ATR-based SL and volatility position sizing.
        """
        product_type = "CNC" if config.TRADING_MODE == "swing" else "MIS"
        planned_exit_date = None
        if config.TRADING_MODE == "swing" and config.SWING_MAX_HOLD_DAYS:
            planned_exit_date = datetime.now() + timedelta(days=config.SWING_MAX_HOLD_DAYS)

        entry_price = signal['current_price']
        atr = signal.get('atr', 0.0)

        # ATR-based stop loss (overrides fixed % if ATR available)
        sl_multiplier = float(os.environ.get('ATR_SL_MULTIPLIER', '2.0'))
        stop_loss = self.atr_stop_loss(entry_price, atr, sl_multiplier)
        # Ensure SL is not worse than config's fixed % floor
        sl_floor = entry_price * (1 - (config.SWING_STOP_LOSS_PERCENTAGE
                                       if config.TRADING_MODE == "swing"
                                       else config.STOP_LOSS_PERCENTAGE))
        stop_loss = max(stop_loss, sl_floor)  # tightest SL wins
        # Respect a signal-level stop loss (e.g. based on support) if it is tighter
        signal_sl = signal.get('stop_loss', 0.0)
        if 0 < signal_sl < entry_price and signal_sl > stop_loss:
            stop_loss = signal_sl

        # Keep signal target unless overriding
        target = signal['target']
        # First partial target: max(fixed 5%, 1.5x ATR as fraction) — volatility-adaptive
        partial_profit_pct = max(
            config.PARTIAL_PROFIT_THRESHOLD,
            config.PARTIAL_PROFIT_ATR_MULTIPLIER * atr / entry_price
        )
        partial_target = entry_price * (1 + partial_profit_pct)

        # Volatility-based quantity; prefer enterprise-engine size
        per_slot = config.TRADING_AMOUNT / max(1, config.MAX_POSITIONS)
        enterprise_qty = signal.get('position_size')
        if isinstance(enterprise_qty, int) and enterprise_qty > 0:
            quantity = enterprise_qty
        else:
            quantity = self.volatility_position_size(per_slot, entry_price, atr)
        quantity = max(1, quantity)

        position = Position(
            symbol=signal['symbol'],
            entry_price=entry_price,
            first_entry_price=entry_price,
            quantity=quantity,
            stop_loss=round(stop_loss, 2),
            target=target,
            entry_time=datetime.now(),
            planned_exit_date=planned_exit_date,
            product_type=product_type,
            highest_price=entry_price,
            trailing_stop=round(stop_loss, 2) if config.TRAILING_STOP_ENABLED else None,
            atr_at_entry=atr,
            initial_quantity=quantity,
            sector=(signal.get('_research') or {}).get('sector', 'Unknown'),
        )
        # store partial target on the object for check_positions
        position._partial_target = partial_target

        self.positions.append(position)
        self.daily_trades += 1
        self.save_positions()
        
        logger.info(
            f"Position opened: {position.symbol} @ {entry_price:.2f} "
            f"Qty:{quantity} SL:{position.stop_loss:.2f} Target:{target:.2f} "
            f"ATR:{atr:.2f} PartialAt:{partial_target:.2f}"
        )
        return position

    def add_to_position(self, symbol: str, qty: int, price: float,
                        stop_loss: float, target: float, atr: float) -> Optional[Position]:
        """Scale into an existing position and update average cost / SL."""
        position = next(
            (p for p in self.positions if p.symbol == symbol and p.status in (PositionStatus.OPEN, PositionStatus.PARTIAL)),
            None
        )
        if not position or qty <= 0:
            return None
        old_qty = position.quantity
        old_entry = position.entry_price
        total_cost = (old_entry * old_qty) + (price * qty)
        new_qty = old_qty + qty
        new_entry = total_cost / new_qty if new_qty > 0 else price
        position.entry_price = round(new_entry, 2)
        position.quantity = new_qty
        position.initial_quantity = position.initial_quantity + qty
        position.scale_in_qty += qty
        position.atr_at_entry = atr if atr > 0 else position.atr_at_entry
        position.stop_loss = round(max(position.stop_loss, stop_loss), 2)
        position.target = round(max(position.target, target), 2)
        position.highest_price = max(position.highest_price, price)
        self.save_positions()
        logger.info(f"Scaled in {symbol}: +{qty} @ ₹{price:.2f} -> avg ₹{new_entry:.2f} Qty:{new_qty}")
        return position
    
    def check_positions(self, current_prices: Dict[str, float]) -> List[Dict]:
        """
        Check all open/partial positions and generate exit signals.
        Implements partial profit booking and ATR trailing stop.
        """
        exit_signals = []
        
        for position in self.positions:
            if position.status not in (PositionStatus.OPEN, PositionStatus.PARTIAL):
                continue
            
            current_price = current_prices.get(position.symbol)
            if not current_price:
                continue
            
            # Update highest price for trailing stop
            if current_price > position.highest_price:
                position.highest_price = current_price
            
            # Update trailing stop if enabled
            if config.TRAILING_STOP_ENABLED and position.highest_price > position.entry_price * (1 + config.TRAILING_STOP_ACTIVATION_PCT):
                # ATR-aware trailing: use ATR if available, else % trail
                if position.atr_at_entry > 0:
                    new_trail = position.highest_price - 1.5 * position.atr_at_entry
                else:
                    new_trail = position.highest_price * (1 - config.TRAILING_STOP_TRAIL_PCT)
                if position.trailing_stop is None or new_trail > position.trailing_stop:
                    position.trailing_stop = round(new_trail, 2)
                    logger.info(f"Trailing stop updated {position.symbol}: ₹{position.trailing_stop:.2f}")

            effective_stop = max(position.stop_loss, position.trailing_stop or 0)

            # ── Target hit ─────────────────────────────────────────────
            if current_price >= position.target:
                logger.info(
                    f"SELL_PIPELINE | origin=risk_manager.check_positions "
                    f"| symbol={position.symbol} | current_price={current_price} "
                    f"| target={position.target} | reason='Target hit achieved'"
                )
                exit_signals.append(
                    self._close_position(position, current_price, PositionStatus.TARGET_HIT, "Target hit achieved")
                )
                continue

            # ── Partial profit booking ─────────────────────────────────
            atr_pct = config.PARTIAL_PROFIT_ATR_MULTIPLIER * position.atr_at_entry / position.entry_price if position.atr_at_entry and position.entry_price else 0
            partial_target = getattr(position, '_partial_target',
                                     position.entry_price * (1 + max(config.PARTIAL_PROFIT_THRESHOLD, atr_pct)))
            if (not position.partial_booked
                    and position.status == PositionStatus.OPEN
                    and current_price >= partial_target
                    and position.quantity >= 2):
                partial_qty = max(1, int(position.quantity * config.PARTIAL_PROFIT_FRACTION))
                exit_signals.append({
                    'symbol': position.symbol,
                    'action': 'SELL',
                    'price': current_price,
                    'quantity': partial_qty,
                    'position_size': partial_qty,
                    'partial': True,
                    'reason': f"Partial profit (+{((current_price/position.entry_price)-1)*100:.1f}%)",
                    'status': PositionStatus.PARTIAL.value,
                    'timestamp': datetime.now().isoformat()
                })
                logger.info(f"Partial exit {position.symbol}: {partial_qty} @ ₹{current_price:.2f} queued")
                continue

            # ── Stop loss hit ──────────────────────────────────────────
            if current_price <= effective_stop:
                sl_reason = (f"Trailing stop hit (₹{position.trailing_stop:.2f})"
                             if position.trailing_stop and current_price <= position.trailing_stop
                                and current_price > position.stop_loss
                             else "Stop loss hit")
                self.add_to_blacklist(position.symbol)  # blacklist after SL
                logger.info(
                    f"SELL_PIPELINE | origin=risk_manager.check_positions "
                    f"| symbol={position.symbol} | current_price={current_price} "
                    f"| stop_loss={position.stop_loss} | trailing_stop={position.trailing_stop} "
                    f"| reason='{sl_reason}'"
                )
                exit_signals.append({
                    'symbol': position.symbol,
                    'action': 'SELL',
                    'price': current_price,
                    'quantity': position.quantity,
                    'position_size': position.quantity,
                    'reason': sl_reason,
                    'status': PositionStatus.STOPPED_OUT.value,
                    'timestamp': datetime.now().isoformat()
                })

            # ── Swing max hold days ────────────────────────────────────
            elif (position.planned_exit_date
                  and datetime.now() >= position.planned_exit_date
                  and config.TRADING_MODE == "swing"):
                if current_price < position.entry_price:
                    logger.info(
                        f"Max hold reached for {position.symbol} but price "
                        f"{current_price:.2f} < entry {position.entry_price:.2f} — holding"
                    )
                    continue
                exit_signals.append({
                    'symbol': position.symbol,
                    'action': 'SELL',
                    'price': current_price,
                    'quantity': position.quantity,
                    'position_size': position.quantity,
                    'reason': f"Max hold days reached ({config.SWING_MAX_HOLD_DAYS})",
                    'status': PositionStatus.CLOSED.value,
                    'timestamp': datetime.now().isoformat()
                })
        
        return exit_signals
    
    def _close_position(self, position: Position, exit_price: float, status: PositionStatus, reason_override: str = None, quantity: int = None) -> Dict:
        """Close a position and compute charges + net P&L. Supports partial exits."""
        close_qty = quantity if quantity is not None and quantity > 0 else position.quantity
        close_qty = min(close_qty, position.quantity)
        remaining = position.quantity - close_qty

        gross_pnl = (exit_price - position.entry_price) * close_qty
        position.pnl += gross_pnl
        position.pnl_percentage = ((exit_price - position.entry_price) / position.entry_price) * 100

        # Brokerage + charges (round-trip for the quantity being closed)
        charges = _total_charges(
            position.entry_price * close_qty,
            exit_price * close_qty
        )
        position.charges += charges
        position.net_pnl = position.pnl - position.charges

        # Reduce quantity; mark partial if still open, otherwise closed
        position.quantity = remaining
        if remaining > 0:
            position.status = PositionStatus.PARTIAL
            position.partial_booked = True
            position.partial_qty += close_qty
            position.partial_count += 1
            # Tighten stop to break-even after a partial exit
            position.stop_loss = max(position.stop_loss, position.entry_price)
        else:
            position.status = status
            position.exit_price = exit_price
            position.exit_time = datetime.now()
            # Slippage: difference between signal price and fill (stored from signal)
            position.slippage = exit_price - position.exit_price  # 0 here; broker fills in real mode
            self._last_exit_by_symbol[position.symbol] = position.exit_time

        position.exit_reason = reason_override or self._get_exit_reason(status)
        self.daily_pnl += gross_pnl
        self.save_positions()

        exit_signal = {
            'symbol': position.symbol,
            'action': 'SELL',
            'price': exit_price,
            'quantity': close_qty,
            'pnl': position.pnl,
            'pnl_percentage': position.pnl_percentage,
            'charges': position.charges,
            'net_pnl': position.net_pnl,
            'status': position.status.value,
            'reason': position.exit_reason,
            'timestamp': datetime.now().isoformat()
        }

        logger.info(
            f"Position closed: {position.symbol} @ ₹{exit_price:.2f} "
            f"Gross P&L: ₹{position.pnl:.2f} ({position.pnl_percentage:.2f}%) "
            f"Charges: ₹{position.charges:.2f} Net P&L: ₹{position.net_pnl:.2f} "
            f"Reason: {exit_signal['reason']}"
        )
        return exit_signal
    
    def _get_exit_reason(self, status: PositionStatus) -> str:
        """Get exit reason based on status"""
        reasons = {
            PositionStatus.STOPPED_OUT: "Stop loss hit",
            PositionStatus.TARGET_HIT: "Target hit achieved",
            PositionStatus.CLOSED: "Manual close"
        }
        return reasons.get(status, "Unknown")

    def close_position(self, symbol: str, exit_price: float, reason: str, quantity: int = None) -> Optional[Dict]:
        """Close an open/partial position by symbol for external sell signals."""
        for position in self.positions:
            if (position.symbol == symbol
                    and position.status in {PositionStatus.OPEN, PositionStatus.PARTIAL}):
                return self._close_position(position, exit_price, PositionStatus.CLOSED, reason, quantity)
        logger.warning(f"close_position: no open position for {symbol}")
        return None
    
    def close_all_positions(self, current_prices: Dict[str, float]) -> List[Dict]:
        """Close all open/partial positions (end of day or circuit breaker)."""
        exit_signals = []
        for position in self.positions:
            if position.status in (PositionStatus.OPEN, PositionStatus.PARTIAL):
                current_price = current_prices.get(position.symbol, position.entry_price)
                exit_signal = self._close_position(position, current_price, PositionStatus.CLOSED)
                exit_signals.append(exit_signal)
        return exit_signals
    
    def get_position_summary(self) -> Dict:
        """Get summary of all positions including charges and net P&L."""
        open_positions  = [p for p in self.positions if p.status in (PositionStatus.OPEN, PositionStatus.PARTIAL)]
        closed_positions = [p for p in self.positions if p.status not in (PositionStatus.OPEN, PositionStatus.PARTIAL)]

        total_gross_pnl  = sum(p.pnl for p in closed_positions)
        total_charges    = sum(p.charges for p in closed_positions)
        total_net_pnl    = sum(p.net_pnl for p in closed_positions)
        winning_trades   = len([p for p in closed_positions if p.pnl > 0])
        losing_trades    = len([p for p in closed_positions if p.pnl < 0])

        wins  = [p.pnl for p in closed_positions if p.pnl > 0]
        losses = [p.pnl for p in closed_positions if p.pnl < 0]
        avg_win  = sum(wins)  / len(wins)  if wins  else 0.0
        avg_loss = sum(losses)/ len(losses) if losses else 0.0
        profit_factor = abs(sum(wins)/sum(losses)) if losses else float('inf')

        return {
            'open_positions': len(open_positions),
            'closed_positions': len(closed_positions),
            'total_pnl': total_gross_pnl,
            'total_charges': total_charges,
            'total_net_pnl': total_net_pnl,
            'daily_pnl': self.daily_pnl,
            'daily_trades': self.daily_trades,
            'winning_trades': winning_trades,
            'losing_trades': losing_trades,
            'win_rate': winning_trades / len(closed_positions) if closed_positions else 0.0,
            'avg_win': avg_win,
            'avg_loss': avg_loss,
            'profit_factor': profit_factor,
            'daily_blacklist': list(self._daily_blacklist),
        }
    
    def should_stop_trading(self) -> bool:
        """
        Check if trading should be stopped based on risk parameters
        
        Returns:
            True if trading should be stopped
        """
        # Stop if daily loss limit exceeded
        if self.daily_pnl < -self.max_daily_loss:
            logger.warning("Daily loss limit exceeded - stopping trading")
            return True
        
        # Stop if too many consecutive losses (reads today's SQLite journal so it survives restarts)
        max_consec = config.MAX_CONSECUTIVE_LOSSES
        consecutive_losses = 0
        try:
            _today = date.today().isoformat()
            _today_sells = self._store.get_trades(action='SELL', date_from=_today)
            # Walk backwards through today's closed trades
            for _e in reversed(_today_sells):
                _pnl = float(_e.get('net_pnl') or _e.get('pnl') or 0)
                if _pnl < 0:
                    consecutive_losses += 1
                else:
                    break
        except Exception:
            pass

        # Fallback / supplement: check in-memory closed positions
        if consecutive_losses < max_consec:
            recent = [p for p in self.positions if p.status != PositionStatus.OPEN][-5:]
            for p in reversed(recent):
                if p.pnl < 0:
                    consecutive_losses += 1
                else:
                    break

        if consecutive_losses >= max_consec:
            logger.warning(
                f"{consecutive_losses} consecutive losing trades today — halting new entries"
            )
            return True
        
        return False
    
    def get_portfolio_heat(self) -> Dict:
        """Return current portfolio-level heat map data."""
        if self._enterprise is None:
            return {}
        return self._enterprise.portfolio_heat()

    def reset_daily(self):
        """Reset daily statistics and clear blacklist."""
        self.daily_pnl = 0.0
        self.daily_trades = 0
        self._daily_blacklist.clear()
        self._blacklist_date = date.today()
        # Remove closed positions older than today to keep memory clean
        today_start = datetime.combine(date.today(), datetime.min.time())
        self.positions = [
            p for p in self.positions
            if p.status in (PositionStatus.OPEN, PositionStatus.PARTIAL)
            or (p.exit_time and p.exit_time >= today_start)
        ]
        self.save_positions()
        logger.info("Daily statistics reset")
