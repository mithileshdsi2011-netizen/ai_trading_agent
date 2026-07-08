"""
Risk Management Module
Manages trading risk and position sizing.
Features: ATR-based SL, volatility position sizing, partial profit booking,
daily blacklist, correlation guard, slippage/brokerage tracking, JSON persistence.
"""
from typing import Dict, List, Optional, Set
import logging
import json
import os
from datetime import datetime, timedelta, date
from dataclasses import dataclass, field
from enum import Enum

import pandas as pd
import numpy as np

from config import config

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
    pnl: float = 0.0
    pnl_percentage: float = 0.0
    charges: float = 0.0        # brokerage + STT + exchange
    net_pnl: float = 0.0        # pnl after charges
    slippage: float = 0.0       # actual vs expected fill difference
    planned_exit_date: Optional[datetime] = None
    product_type: str = "MIS"
    highest_price: float = 0.0
    trailing_stop: Optional[float] = None
    atr_at_entry: float = 0.0   # ATR used for SL calculation
    partial_booked: bool = False # True once 50% sold at first target
    partial_qty: int = 0         # qty of the partial exit


class RiskManager:
    """Manages trading risk and positions"""
    
    def __init__(self):
        self.positions: List[Position] = []
        self.daily_pnl = 0.0
        self.daily_trades = 0
        self.max_daily_loss = config.TRADING_AMOUNT * config.DAILY_MAX_LOSS_PCT
        self._daily_blacklist: Set[str] = set()  # symbols SL-hit today
        self._blacklist_date: date = date.today()
        self._load_positions()

    # ------------------------------------------------------------------
    # Persistence helpers
    # ------------------------------------------------------------------

    def _load_positions(self):
        """Restore positions from JSON on restart."""
        try:
            os.makedirs(os.path.dirname(PERSISTENCE_FILE), exist_ok=True)
            if not os.path.exists(PERSISTENCE_FILE):
                return
            with open(PERSISTENCE_FILE) as f:
                data = json.load(f)
            today = date.today().isoformat()
            if data.get('date') != today:
                logger.info("Persistence file is from a previous day — skipping position reload")
                return
            for p in data.get('positions', []):
                if p.get('status') == 'OPEN':
                    pos = Position(
                        symbol=p['symbol'],
                        entry_price=p['entry_price'],
                        quantity=p['quantity'],
                        stop_loss=p['stop_loss'],
                        target=p['target'],
                        entry_time=datetime.fromisoformat(p['entry_time']),
                        status=PositionStatus.OPEN,
                        planned_exit_date=datetime.fromisoformat(p['planned_exit_date']) if p.get('planned_exit_date') else None,
                        product_type=p.get('product_type', 'CNC'),
                        highest_price=p.get('highest_price', p['entry_price']),
                        trailing_stop=p.get('trailing_stop'),
                        atr_at_entry=p.get('atr_at_entry', 0.0),
                        partial_booked=p.get('partial_booked', False),
                        partial_qty=p.get('partial_qty', 0),
                    )
                    self.positions.append(pos)
                    logger.info(f"Restored position from file: {pos.symbol} {pos.quantity} @ {pos.entry_price}")
            self.daily_pnl = data.get('daily_pnl', 0.0)
            self._daily_blacklist = set(data.get('daily_blacklist', []))
            logger.info(f"Loaded {len(self.positions)} positions from persistence file")
        except Exception as e:
            logger.warning(f"Could not load positions from file: {e}")

    def save_positions(self):
        """Persist current positions + daily state to JSON."""
        try:
            os.makedirs(os.path.dirname(PERSISTENCE_FILE), exist_ok=True)
            data = {
                'date': date.today().isoformat(),
                'daily_pnl': self.daily_pnl,
                'daily_blacklist': list(self._daily_blacklist),
                'positions': [
                    {
                        'symbol': p.symbol,
                        'entry_price': p.entry_price,
                        'quantity': p.quantity,
                        'stop_loss': p.stop_loss,
                        'target': p.target,
                        'entry_time': p.entry_time.isoformat(),
                        'planned_exit_date': p.planned_exit_date.isoformat() if p.planned_exit_date else None,
                        'product_type': p.product_type,
                        'highest_price': p.highest_price,
                        'trailing_stop': p.trailing_stop,
                        'atr_at_entry': p.atr_at_entry,
                        'partial_booked': p.partial_booked,
                        'partial_qty': p.partial_qty,
                        'status': p.status.value,
                        'pnl': p.pnl,
                        'charges': p.charges,
                        'net_pnl': p.net_pnl,
                    }
                    for p in self.positions
                ]
            }
            with open(PERSISTENCE_FILE, 'w') as f:
                json.dump(data, f, indent=2)
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
        """
        symbol = signal['symbol']

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
        
        # Check daily loss limit
        if self.daily_pnl < -self.max_daily_loss:
            logger.warning(f"Daily loss limit reached: {self.daily_pnl:.2f}")
            return False
        
        # Check risk-reward ratio
        if signal['risk_reward_ratio'] < config.MIN_RISK_REWARD:
            logger.warning(f"R:R too low: {signal['risk_reward_ratio']:.2f} (min {config.MIN_RISK_REWARD})")
            return False

        # Check confidence
        if signal['confidence'] < config.MIN_CONFIDENCE:
            logger.warning(f"Confidence too low: {signal['confidence']:.0%} (min {config.MIN_CONFIDENCE:.0%})")
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

        # Keep signal target unless overriding
        target = signal['target']
        # First partial target: +5% (or ATR-based)
        partial_target = entry_price * 1.05

        # Volatility-based quantity
        per_slot = config.TRADING_AMOUNT / max(1, config.MAX_POSITIONS)
        quantity = self.volatility_position_size(per_slot, entry_price, atr)
        # Don't exceed what signal already calculated if smaller
        quantity = min(quantity, signal.get('position_size', quantity))
        quantity = max(1, quantity)

        position = Position(
            symbol=signal['symbol'],
            entry_price=entry_price,
            quantity=quantity,
            stop_loss=round(stop_loss, 2),
            target=target,
            entry_time=datetime.now(),
            planned_exit_date=planned_exit_date,
            product_type=product_type,
            highest_price=entry_price,
            trailing_stop=round(stop_loss, 2) if config.TRAILING_STOP_ENABLED else None,
            atr_at_entry=atr,
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

            # ── Partial profit booking ─────────────────────────────────
            partial_target = getattr(position, '_partial_target',
                                     position.entry_price * 1.05)
            if (not position.partial_booked
                    and position.status == PositionStatus.OPEN
                    and current_price >= partial_target
                    and position.quantity >= 2):
                half_qty = position.quantity // 2
                exit_signal = {
                    'symbol': position.symbol,
                    'action': 'SELL',
                    'price': current_price,
                    'quantity': half_qty,
                    'partial': True,
                    'pnl': (current_price - position.entry_price) * half_qty,
                    'pnl_percentage': ((current_price - position.entry_price) / position.entry_price) * 100,
                    'reason': f"Partial profit (+{((current_price/position.entry_price)-1)*100:.1f}%)",
                    'status': PositionStatus.PARTIAL.value,
                    'timestamp': datetime.now().isoformat()
                }
                charges = _total_charges(
                    position.entry_price * half_qty,
                    current_price * half_qty
                )
                exit_signal['charges'] = charges
                exit_signal['net_pnl'] = exit_signal['pnl'] - charges
                position.partial_booked = True
                position.partial_qty = half_qty
                position.quantity -= half_qty
                position.status = PositionStatus.PARTIAL
                position.pnl += exit_signal['pnl']
                position.charges += charges
                position.net_pnl += exit_signal['net_pnl']
                self.daily_pnl += exit_signal['pnl']
                # Tighten stop to break-even after partial
                position.stop_loss = max(position.stop_loss, position.entry_price)
                self.save_positions()
                exit_signals.append(exit_signal)
                logger.info(f"Partial exit {position.symbol}: {half_qty} @ ₹{current_price:.2f} P&L ₹{exit_signal['pnl']:.2f}")
                continue

            # ── Stop loss hit ──────────────────────────────────────────
            if current_price <= effective_stop:
                exit_signal = self._close_position(position, current_price, PositionStatus.STOPPED_OUT)
                if position.trailing_stop and current_price <= position.trailing_stop and current_price > position.stop_loss:
                    exit_signal['reason'] = f"Trailing stop hit (₹{position.trailing_stop:.2f})"
                self.add_to_blacklist(position.symbol)  # blacklist after SL
                exit_signals.append(exit_signal)

            # ── Full target hit ────────────────────────────────────────
            elif current_price >= position.target:
                exit_signal = self._close_position(position, current_price, PositionStatus.TARGET_HIT)
                exit_signals.append(exit_signal)
            
            # ── Swing max hold days ────────────────────────────────────
            elif (position.planned_exit_date
                  and datetime.now() >= position.planned_exit_date
                  and config.TRADING_MODE == "swing"):
                exit_signal = self._close_position(position, current_price, PositionStatus.CLOSED)
                exit_signal['reason'] = f"Max hold days reached ({config.SWING_MAX_HOLD_DAYS})"
                exit_signals.append(exit_signal)
        
        return exit_signals
    
    def _close_position(self, position: Position, exit_price: float, status: PositionStatus) -> Dict:
        """Close a position and compute charges + net P&L."""
        position.exit_price = exit_price
        position.exit_time = datetime.now()
        position.status = status

        gross_pnl = (exit_price - position.entry_price) * position.quantity
        position.pnl += gross_pnl
        position.pnl_percentage = ((exit_price - position.entry_price) / position.entry_price) * 100

        # Brokerage + charges (round-trip for remaining qty)
        charges = _total_charges(
            position.entry_price * position.quantity,
            exit_price * position.quantity
        )
        position.charges += charges
        position.net_pnl = position.pnl - position.charges

        # Slippage: difference between signal price and fill (stored from signal)
        position.slippage = exit_price - position.exit_price  # 0 here; broker fills in real mode

        self.daily_pnl += gross_pnl
        self.save_positions()
        
        exit_signal = {
            'symbol': position.symbol,
            'action': 'SELL',
            'price': exit_price,
            'quantity': position.quantity,
            'pnl': position.pnl,
            'pnl_percentage': position.pnl_percentage,
            'charges': position.charges,
            'net_pnl': position.net_pnl,
            'status': status.value,
            'reason': self._get_exit_reason(status),
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
            PositionStatus.TARGET_HIT: "Target achieved",
            PositionStatus.CLOSED: "Manual close"
        }
        return reasons.get(status, "Unknown")
    
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
        
        # Stop if too many consecutive losses (reads today's journal so it survives restarts)
        max_consec = config.MAX_CONSECUTIVE_LOSSES
        consecutive_losses = 0
        try:
            _jpath = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                'data', 'trade_journal.json'
            )
            if os.path.exists(_jpath):
                with open(_jpath) as _jf:
                    _all = json.load(_jf)
                _today = date.today().isoformat()
                _today_sells = [
                    e for e in _all
                    if e.get('action') == 'SELL'
                    and (e.get('timestamp', '') or '')[:10] == _today
                ]
                # Walk backwards through today's closed trades
                for _e in reversed(_today_sells):
                    _pnl = float(_e.get('net_pnl') or _e.get('pnl') or 0)
                    if _pnl < 0:
                        consecutive_losses += 1
                    else:
                        break
        except Exception:
            # Fallback: check in-memory positions
            recent = [p for p in self.positions if p.status != PositionStatus.OPEN][-5:]
            for p in reversed(recent):
                if hasattr(p, 'pnl') and p.pnl < 0:
                    consecutive_losses += 1
                else:
                    break

        if consecutive_losses >= max_consec:
            logger.warning(
                f"{consecutive_losses} consecutive losing trades today — halting new entries"
            )
            return True
        
        return False
    
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
