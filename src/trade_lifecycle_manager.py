"""
Enterprise Trade Lifecycle Manager
Continuous position management after entry:
- ATR / Highest High / Swing Low / EMA trailing stops
- 30 / 30 / 40 scale-out partial profit booking
- Break-even stops, time exits, volatility exits, gap exits
- Scale-in detection
- Dynamic target updates
"""
import logging
from datetime import datetime
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from config import config
from market_data import MarketDataFetcher
from risk_manager import PositionStatus, RiskManager

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Scale-out fractions and ATR multiples for each partial
_SCALE_OUT_FRACTIONS = [0.30, 0.30, 0.40]
_SCALE_OUT_ATR_MULTIPLES = [1.5, 3.0, 4.5]
_TRAIL_ATR_MULTIPLIER = 2.0
_TRAIL_PCT = 0.05
_EMA_PERIOD = 20
_SWING_LOW_WINDOW = 5


class TradeLifecycleManager:
    """Manages open positions continuously after entry."""

    def __init__(self, market_data: Optional[MarketDataFetcher] = None):
        self._market_data = market_data

    @property
    def market_data(self) -> MarketDataFetcher:
        if self._market_data is None:
            self._market_data = MarketDataFetcher()
        return self._market_data

    @staticmethod
    def _calculate_atr(df: pd.DataFrame, period: int = 14) -> float:
        """Average True Range for a DataFrame with OHLC."""
        if df.empty or 'High' not in df or 'Low' not in df or 'Close' not in df or len(df) < period + 1:
            return 0.0
        try:
            return RiskManager.calculate_atr(df, period=period)
        except Exception:
            return 0.0

    def _get_indicators(self, symbol: str) -> Dict:
        """Fetch ATR, EMA, swing low, volume, prev close for a symbol."""
        default = {
            'atr': 0.0, 'ema20': 0.0, 'swing_low': 0.0,
            'vol_5': 0.0, 'vol_20': 0.0, 'prev_close': 0.0,
            'hist': pd.DataFrame()
        }
        try:
            hist = self.market_data.get_stock_data(symbol, period='1mo', interval='1d')
            if hist.empty or len(hist) < _EMA_PERIOD:
                return default
            atr = self._calculate_atr(hist)
            ema20 = float(hist['Close'].ewm(span=_EMA_PERIOD, adjust=False).mean().iloc[-1])
            swing_low = float(hist['Low'].tail(_SWING_LOW_WINDOW).min())
            vol_5 = float(hist['Volume'].tail(5).mean()) if 'Volume' in hist else 0.0
            vol_20 = float(hist['Volume'].tail(20).mean()) if 'Volume' in hist else 0.0
            prev_close = float(hist['Close'].iloc[-2]) if len(hist) >= 2 else float(hist['Close'].iloc[-1])
            return {
                'atr': atr, 'ema20': ema20, 'swing_low': swing_low,
                'vol_5': vol_5, 'vol_20': vol_20, 'prev_close': prev_close,
                'hist': hist
            }
        except Exception as e:
            logger.warning(f"Lifecycle indicators for {symbol} failed: {e}")
            return default

    # ── trailing / stop logic ─────────────────────────────────────────

    def _update_trailing_stop(self, position, current_price: float, ind: Dict) -> None:
        """Smart trailing stop: ATR, highest high %, EMA, swing low."""
        if current_price > position.highest_price:
            position.highest_price = current_price

        highest = position.highest_price
        atr = max(position.atr_at_entry, ind.get('atr', 0))
        ema20 = ind.get('ema20', 0)
        swing_low = ind.get('swing_low', 0)

        if atr <= 0 and highest <= 0:
            return

        candidates = []
        if atr > 0:
            candidates.append(highest - _TRAIL_ATR_MULTIPLIER * atr)
        candidates.append(highest * (1 - _TRAIL_PCT))
        if ema20 > 0:
            candidates.append(ema20)
        if swing_low > 0:
            candidates.append(swing_low * 0.99)

        if not candidates:
            return

        new_trail = max(candidates)
        # Never lower the trailing stop, never below original SL
        current_trail = position.trailing_stop or 0
        new_trail = max(new_trail, current_trail, position.stop_loss)
        position.trailing_stop = round(new_trail, 2)

    def _break_even_check(self, position, current_price: float) -> None:
        """Once RR > 1 (price covers the original risk), move SL to cost."""
        if position.stop_loss >= position.entry_price:
            return
        rr1_price = 2 * position.entry_price - position.stop_loss
        if current_price >= rr1_price:
            position.stop_loss = max(position.stop_loss, position.entry_price)
            logger.info(f"Break-even stop set for {position.symbol} @ ₹{position.entry_price:.2f}")

    def _dynamic_target(self, position, current_price: float, ind: Dict) -> None:
        """Raise target based on ATR and EMA."""
        atr = max(position.atr_at_entry, ind.get('atr', 0))
        if atr > 0:
            candidate = current_price + _TRAIL_ATR_MULTIPLIER * atr
            position.target = round(max(position.target, candidate), 2)

    # ── scale in / out ────────────────────────────────────────────────

    def _scale_in_ready(self, position, current_price: float, ind: Dict) -> Optional[Dict]:
        """Return a SCALE_IN action if trend/volume confirms a breakout."""
        if not config.TRADING_MODE == 'swing':
            return None
        if position.partial_count > 0:
            return None  # don't scale-in after partial booked
        if current_price <= position.entry_price * 1.03:
            return None
        ema20 = ind.get('ema20', 0)
        if ema20 > 0 and current_price < ema20:
            return None
        if ind.get('vol_20', 0) > 0 and ind.get('vol_5', 0) < ind.get('vol_20', 0) * 1.2:
            return None
        atr = max(position.atr_at_entry, ind.get('atr', 0))
        if atr <= 0:
            return None

        # Conservative risk amount for the add-on trade
        risk_amount = float(config.TRADING_AMOUNT) * float(config.RISK_PER_TRADE)
        qty = int(risk_amount / (atr * 1.5))
        qty = max(1, min(qty, 50))  # safety cap
        price = current_price
        stop_loss = round(price - 1.5 * atr, 2)
        target = round(price + 2.0 * atr, 2)

        return {
            'symbol': position.symbol,
            'action': 'SCALE_IN',
            'price': current_price,
            'quantity': qty,
            'position_size': qty,
            'stop_loss': stop_loss,
            'target': target,
            'atr': atr,
            'reason': 'Scale-in on confirmed breakout',
            'timestamp': datetime.now().isoformat()
        }

    def _scale_out(self, position, current_price: float) -> Optional[Dict]:
        """30/30/40 scale-out levels based on ATR from entry."""
        if position.partial_count >= 3 or position.quantity <= 0:
            return None
        atr = position.atr_at_entry
        if atr <= 0:
            return None

        level = position.entry_price + atr * _SCALE_OUT_ATR_MULTIPLES[position.partial_count]
        if current_price < level:
            return None

        fraction = _SCALE_OUT_FRACTIONS[position.partial_count]
        qty = max(1, int(position.initial_quantity * fraction))
        if position.partial_count == 2:
            qty = position.quantity  # final leg: sell rest
        qty = min(qty, position.quantity)

        reason = f"Partial profit (+{((current_price / position.entry_price) - 1) * 100:.1f}%)"
        logger.info(
            f"SELL_PIPELINE | origin=trade_lifecycle_manager.scale_out "
            f"| symbol={position.symbol} | current_price={current_price} | quantity={qty} "
            f"| reason='{reason}'"
        )
        return {
            'symbol': position.symbol,
            'action': 'SELL',
            'price': current_price,
            'quantity': qty,
            'position_size': qty,
            'partial': True,
            'reason': reason,
            'status': PositionStatus.PARTIAL.value,
            'timestamp': datetime.now().isoformat()
        }

    # ── exits ─────────────────────────────────────────────────────────

    def _gap_exit(self, position, current_price: float, ind: Dict) -> Optional[Dict]:
        prev_close = ind.get('prev_close', 0)
        if prev_close <= 0:
            return None
        gap = (prev_close - current_price) / prev_close
        if gap > _TRAIL_PCT:
            logger.info(
                f"SELL_PIPELINE | origin=trade_lifecycle_manager.gap_exit "
                f"| symbol={position.symbol} | current_price={current_price} "
                f"| gap={gap:.3%} | reason='Gap down {gap:.1%} below prior close'"
            )
            return {
                'symbol': position.symbol,
                'action': 'SELL',
                'price': current_price,
                'quantity': position.quantity,
                'position_size': position.quantity,
                'reason': f"Gap down {gap:.1%} below prior close",
                'status': PositionStatus.CLOSED.value,
                'timestamp': datetime.now().isoformat()
            }
        return None

    def _volatility_exit(self, position, current_price: float, ind: Dict) -> Optional[Dict]:
        vol_5 = ind.get('vol_5', 0)
        vol_20 = ind.get('vol_20', 0)
        ema20 = ind.get('ema20', 0)
        if vol_20 > 0 and vol_5 < vol_20 * 0.6 and current_price < ema20:
            logger.info(
                f"SELL_PIPELINE | origin=trade_lifecycle_manager.volatility_exit "
                f"| symbol={position.symbol} | current_price={current_price} "
                f"| ema20={ind.get('ema20')} | vol_5={ind.get('vol_5')} | vol_20={ind.get('vol_20')} "
                f"| reason='Volatility collapse + below EMA'"
            )
            return {
                'symbol': position.symbol,
                'action': 'SELL',
                'price': current_price,
                'quantity': position.quantity,
                'position_size': position.quantity,
                'reason': 'Volatility collapse + below EMA',
                'status': PositionStatus.CLOSED.value,
                'timestamp': datetime.now().isoformat()
            }
        return None

    def _time_exit(self, position, current_price: float) -> Optional[Dict]:
        if not position.planned_exit_date:
            return None
        if datetime.now() < position.planned_exit_date:
            return None
        # Only exit if momentum has vanished (no longer near high)
        if current_price >= position.highest_price * 0.97:
            return None  # still running, let trail take over
        logger.info(
            f"SELL_PIPELINE | origin=trade_lifecycle_manager.time_exit "
            f"| symbol={position.symbol} | current_price={current_price} "
            f"| planned_exit_date={position.planned_exit_date} "
            f"| reason='Time exit (max hold {config.SWING_MAX_HOLD_DAYS}d)'"
        )
        return {
            'symbol': position.symbol,
            'action': 'SELL',
            'price': current_price,
            'quantity': position.quantity,
            'position_size': position.quantity,
            'reason': f"Time exit (max hold {config.SWING_MAX_HOLD_DAYS}d)",
            'status': PositionStatus.CLOSED.value,
            'timestamp': datetime.now().isoformat()
        }

    def _stop_loss_exit(self, position, current_price: float) -> Optional[Dict]:
        effective_stop = max(position.stop_loss, position.trailing_stop or 0)
        if current_price <= effective_stop:
            reason = 'Stop loss hit'
            if position.trailing_stop and current_price <= position.trailing_stop and current_price > position.stop_loss:
                reason = f"ATR trailing stop (₹{position.trailing_stop:.2f})"
            logger.info(
                f"SELL_PIPELINE | origin=trade_lifecycle_manager.stop_loss_exit "
                f"| symbol={position.symbol} | current_price={current_price} "
                f"| stop_loss={position.stop_loss} | trailing_stop={position.trailing_stop} "
                f"| reason='{reason}'"
            )
            return {
                'symbol': position.symbol,
                'action': 'SELL',
                'price': current_price,
                'quantity': position.quantity,
                'position_size': position.quantity,
                'reason': reason,
                'status': PositionStatus.CLOSED.value,
                'timestamp': datetime.now().isoformat()
            }
        return None

    # ── main public entry ─────────────────────────────────────────────

    def process_positions(self, positions, current_prices: Dict[str, float]) -> List[Dict]:
        """
        Evaluate all open/partial positions and return a list of actions.
        Mutates positions with updated trailing stops / SL / targets.
        """
        actions: List[Dict] = []

        for position in positions:
            if position.status not in (PositionStatus.OPEN, PositionStatus.PARTIAL):
                continue

            current_price = current_prices.get(position.symbol)
            if not current_price or current_price <= 0:
                # No live price — emergency stale-price signal
                actions.append({
                    'symbol': position.symbol,
                    'action': 'HOLD',
                    'reason': 'No live price for lifecycle evaluation',
                    'timestamp': datetime.now().isoformat(),
                    'lifecycle_state': self._lifecycle_state(position, 0.0)
                })
                continue

            ind = self._get_indicators(position.symbol)

            # Update stops and targets
            self._update_trailing_stop(position, current_price, ind)
            self._break_even_check(position, current_price)
            self._dynamic_target(position, current_price, ind)

            # Exit checks in priority order
            action = (self._gap_exit(position, current_price, ind)
                      or self._stop_loss_exit(position, current_price)
                      or self._volatility_exit(position, current_price, ind)
                      or self._time_exit(position, current_price)
                      or self._scale_out(position, current_price)
                      or self._scale_in_ready(position, current_price, ind))

            if action:
                action['lifecycle_state'] = self._lifecycle_state(position, current_price)
                actions.append(action)
                if action.get('action') != 'HOLD':
                    logger.info(
                        f"{position.symbol}: lifecycle {action['action']} "
                        f"@ ₹{current_price:.2f} - {action.get('reason', 'unknown reason')}"
                    )
            else:
                actions.append({
                    'symbol': position.symbol,
                    'action': 'HOLD',
                    'reason': 'No lifecycle action',
                    'timestamp': datetime.now().isoformat(),
                    'lifecycle_state': self._lifecycle_state(position, current_price)
                })

        return actions

    def _lifecycle_state(self, position, current_price: float, ind: Dict = None) -> Dict:
        """Expose current lifecycle metrics + active triggers for one position."""
        ind = ind or {}
        entry = position.entry_price
        first = position.first_entry_price or entry
        sl_initial = position.stop_loss
        sl_trail = position.trailing_stop or 0
        effective_sl = max(sl_initial, sl_trail)
        risk = max(entry - sl_initial, 0.001)
        rr = (current_price - entry) / risk if current_price > 0 else 0.0
        days = (datetime.now() - position.entry_time).days
        highest = position.highest_price or current_price
        drawdown = (highest - current_price) / highest * 100 if highest > 0 else 0.0

        # Recommendations in priority order
        recommendation = 'HOLD'
        reason = f'Price ₹{current_price:.2f} is between effective SL ₹{effective_sl:.2f} and target ₹{position.target:.2f}.'
        if current_price <= effective_sl:
            recommendation = 'SELL'
            reason = f'Stop-loss hit/approached at ₹{effective_sl:.2f}. Exit the position.'
        elif sl_initial < entry and current_price >= (2 * entry - sl_initial):
            if not (position.stop_loss >= entry):
                recommendation = 'MOVE SL TO BREAK-EVEN'
                reason = f'Price reached ₹{current_price:.2f}. Move SL from ₹{sl_initial:.2f} up to cost ₹{entry:.2f}.'
        elif (position.partial_count < 3 and position.atr_at_entry > 0
              and (current_price - entry) >= _SCALE_OUT_ATR_MULTIPLES[position.partial_count] * position.atr_at_entry):
            recommendation = 'PARTIAL EXIT'
            pidx = position.partial_count
            reason = f'Scale-out {pidx + 1} triggered at ₹{current_price:.2f} (+{_SCALE_OUT_ATR_MULTIPLES[pidx]}x ATR).'

        # Time / planned exit
        planned_days = None
        if position.planned_exit_date:
            planned_days = max(0, (position.planned_exit_date - datetime.now()).days)

        # Active triggers grid
        atr_now = ind.get('atr', position.atr_at_entry or 0)
        ema20 = ind.get('ema20', 0)
        swing_low = ind.get('swing_low', 0)
        prev_close = ind.get('prev_close', 0)

        triggers = []
        # Stop loss
        triggers.append({'name': 'STOP', 'level': effective_sl,
                         'status': 'TRIGGERED' if current_price <= effective_sl else 'monitoring'})
        # Break-even
        be_level = entry
        if sl_initial >= entry:
            be_status = 'locked'
        elif current_price >= (2 * entry - sl_initial):
            be_status = 'ready'
        else:
            be_status = 'pending'
        triggers.append({'name': 'BREAK-EVEN', 'level': be_level, 'status': be_status})
        # ATR trail
        if atr_now > 0:
            atr_level = round(highest - _TRAIL_ATR_MULTIPLIER * atr_now, 2)
            triggers.append({'name': 'ATR', 'level': atr_level,
                             'status': 'TRIGGERED' if current_price <= atr_level else 'monitoring'})
        # 5% trail
        pct_level = round(highest * (1 - _TRAIL_PCT), 2)
        triggers.append({'name': 'PCT', 'level': pct_level,
                         'status': 'TRIGGERED' if current_price <= pct_level else 'monitoring'})
        # EMA20
        if ema20 > 0:
            triggers.append({'name': 'EMA', 'level': round(ema20, 2),
                             'status': 'TRIGGERED' if current_price <= ema20 else 'monitoring'})
        # Swing low
        if swing_low > 0:
            sl_level = round(swing_low * 0.99, 2)
            triggers.append({'name': 'SWING LOW', 'level': sl_level,
                             'status': 'TRIGGERED' if current_price <= sl_level else 'monitoring'})
        # Gap
        if prev_close > 0:
            gap_pct = (current_price - prev_close) / prev_close * 100
            gap_status = 'TRIGGERED' if gap_pct < -1.5 else ('watch' if gap_pct < -1.0 else 'monitoring')
            triggers.append({'name': 'GAP', 'level': round(prev_close, 2), 'status': gap_status})
        # Target
        if position.target > 0:
            triggers.append({'name': 'TARGET', 'level': round(position.target, 2),
                             'status': 'REACHED' if current_price >= position.target * 0.98 else 'pending'})
        # Time
        if planned_days is not None:
            t_status = 'due' if planned_days <= 1 else 'pending'
            triggers.append({'name': 'TIME EXIT', 'level': planned_days, 'status': t_status})

        # Lifecycle progress: Break-even, Partial 1, Partial 2, Final Exit
        partial_progress = [
            bool(sl_initial >= entry),
            bool(position.partial_count >= 1),
            bool(position.partial_count >= 2),
            bool(position.partial_count >= 3 or (position.target > 0 and current_price >= position.target))
        ]

        return {
            'symbol': position.symbol,
            'first_entry': round(first, 2),
            'average_price': round(entry, 2),
            'current_price': round(current_price, 2),
            'quantity': position.quantity,
            'initial_quantity': position.initial_quantity,
            'highest_price': round(highest, 2),
            'drawdown_pct': round(drawdown, 2),
            'current_rr': round(rr, 2),
            'atr_at_entry': round(position.atr_at_entry, 2),
            'initial_sl': round(sl_initial, 2),
            'trailing_sl': round(sl_trail, 2) if sl_trail > 0 else None,
            'effective_sl': round(effective_sl, 2),
            'target': round(position.target, 2),
            'next_target_level': round(position.target, 2),
            'break_even_hit': bool(sl_initial >= entry),
            'partial_count': position.partial_count,
            'partial_progress': partial_progress,
            'scale_in_qty': position.scale_in_qty,
            'days_held': days,
            'planned_exit_days': planned_days,
            'recommendation': recommendation,
            'reason': reason,
            'active_triggers': triggers,
        }

    def get_lifecycle_summary(self, positions, current_prices: Dict[str, float]) -> List[Dict]:
        """Return non-mutating lifecycle summary for every open position."""
        summary = []
        for p in positions:
            if p.status in (PositionStatus.OPEN, PositionStatus.PARTIAL):
                current = current_prices.get(p.symbol, p.entry_price)
                ind = self._get_indicators(p.symbol)
                summary.append(self._lifecycle_state(p, current, ind))
        return summary
