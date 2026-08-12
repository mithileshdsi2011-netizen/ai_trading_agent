"""
Exit Decision Engine

Layered, modular exit architecture for swing-mode positions.

Responsibility: decide whether each open position should be
STRONG_HOLD | HOLD | TRAIL | PARTIAL_EXIT | FULL_EXIT.

Layers:
  1. Safety Engine       — mandatory hard exits (SL, trailing SL, max hold, gap, time)
  2. Profit Protection   — overbought/weakening/volume signals after meaningful profit
  3. Trend Validation    — EMA/MACD/Higher-Lows/ADX-style health
  4. Opportunity         — "Would I buy at this price today?"
  5. Target Management   — dynamic target raise or full exit based on trend/opportunity
  6. Partial Exit Engine — institutional-style scale-out rules

Output is one of:
  STRONG_HOLD, HOLD, TRAIL, PARTIAL_EXIT, FULL_EXIT
"""
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from config import config
from risk_manager import Position, PositionStatus
from market_data import MarketDataFetcher

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Layer-score thresholds
_PROFIT_ACTIVATION_PCT = 0.06
_PARTIAL_EXIT_PROFIT_PCTS = [0.08, 0.12, 0.18]
_PARTIAL_EXIT_FRACTIONS = [0.25, 0.25, 0.50]  # total 100% across 3 tranches
_MAX_PARTIAL_TRANCHES = 3

_OPPORTUNITY_BEARISH_RSI = 70
_OPPORTUNITY_BULLISH_RSI = 45

@dataclass
class ExitDecision:
    """Structured exit decision for one position."""
    symbol: str
    decision: str
    quantity: int = 0
    reason: str = ""
    scores: Dict = None
    move_sl_to_cost: bool = False
    new_target: Optional[float] = None
    new_trailing_stop: Optional[float] = None
    source: str = "exit_decision_engine"


class ExitDecisionEngine:
    """Layered scoring engine that produces hold / trail / partial / full exit decisions."""

    def __init__(self, market_data: Optional[MarketDataFetcher] = None):
        self._market_data = market_data or MarketDataFetcher()

    @property
    def market_data(self) -> MarketDataFetcher:
        return self._market_data

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def decide_all(
        self,
        positions: List[Position],
        current_prices: Dict[str, float],
        market_regime: str = 'SIDEWAYS',
    ) -> List[ExitDecision]:
        """Evaluate every open/partial position and return a list of decisions."""
        decisions: List[ExitDecision] = []
        for pos in positions:
            if pos.status not in {PositionStatus.OPEN, PositionStatus.PARTIAL}:
                continue
            price = current_prices.get(pos.symbol)
            if not price or price <= 0:
                decisions.append(ExitDecision(
                    symbol=pos.symbol, decision='HOLD',
                    reason='No live price available'
                ))
                continue
            decisions.append(self.decide(pos, price, market_regime))
        return decisions

    def decide(self, position: Position, current_price: float,
               market_regime: str = 'SIDEWAYS') -> ExitDecision:
        """Single-position layered decision."""
        df = self._get_data(position.symbol)
        ind = self._compute_indicators(df)

        # Update highest price and trailing stop every cycle (like lifecycle manager)
        if current_price > (position.highest_price or 0):
            position.highest_price = current_price
        new_trail = self._suggested_trailing_stop(position, current_price, ind)
        if (position.trailing_stop is None
                or new_trail > position.trailing_stop):
            position.trailing_stop = round(new_trail, 2)

        # ── Layer 1: Safety Engine (hard exits) ──────────────────────────
        safety_score, safety_reason, safety_decision = self._safety_layer(
            position, current_price, ind
        )
        if safety_decision == 'FULL_EXIT':
            return self._make_decision(
                position, 'FULL_EXIT', reason=safety_reason,
                quantity=position.quantity,
                scores={'safety': safety_score}
            )

        # ── Layer 2: Profit Protection Engine ────────────────────────────
        cost_basis = position.average_price or position.first_entry_price or position.entry_price
        if not cost_basis:
            cost_basis = position.entry_price
        profit_pct = (current_price - cost_basis) / cost_basis
        pp_score, pp_flags = self._profit_protection_score(
            position, current_price, profit_pct, ind, market_regime
        )

        # ── Layer 3: Trend Validation ────────────────────────────────────
        trend_score, trend_flags = self._trend_score(position, current_price, ind)

        # ── Layer 4: Opportunity Re-evaluation ───────────────────────────
        opp_score, opp_flags = self._opportunity_score(position, current_price, ind)

        # ── Layer 5: Market / Regime ─────────────────────────────────────
        market_score = self._market_score(market_regime, ind)

        scores = {
            'safety': safety_score,
            'profit_protection': pp_score,
            'trend': trend_score,
            'opportunity': opp_score,
            'market': market_score,
            'profit_pct': round(profit_pct * 100, 2),
            'flags': pp_flags + trend_flags + opp_flags,
        }

        # ── Layer 5: Target Management ───────────────────────────────────
        if position.target and current_price >= position.target:
            return self._target_management(
                position, current_price, profit_pct, trend_score, opp_score,
                market_score, scores
            )

        # ── Layer 6: Partial Exit Engine ─────────────────────────────────
        partial = self._partial_exit_layer(position, profit_pct)
        if partial:
            return self._make_decision(
                position, 'PARTIAL_EXIT', reason=partial['reason'],
                quantity=partial['quantity'], scores=scores
            )

        # ── Final Decision Engine ────────────────────────────────────────
        if pp_score >= 85:
            return self._make_decision(
                position, 'FULL_EXIT',
                reason=f"Profit protection score {pp_score}: {', '.join(pp_flags)}",
                quantity=position.quantity, scores=scores
            )

        if pp_score >= 65 and (trend_score < 50 or opp_score < 40):
            return self._make_decision(
                position, 'PARTIAL_EXIT',
                reason=f"Profit protection {pp_score} + weakening trend/opportunity",
                quantity=max(1, int(position.quantity * 0.25)),
                scores=scores
            )

        if trend_score < 25 and profit_pct > 0:
            return self._make_decision(
                position, 'FULL_EXIT',
                reason=f"Trend broken (score {trend_score})",
                quantity=position.quantity, scores=scores
            )

        if trend_score < 25:
            return self._make_decision(
                position, 'HOLD',
                reason=f"Trend weak but no profit yet (score {trend_score})",
                scores=scores
            )

        if opp_score < 25 and profit_pct > 0.03:
            return self._make_decision(
                position, 'FULL_EXIT',
                reason=f"Would not buy today (opportunity score {opp_score})",
                quantity=position.quantity, scores=scores
            )

        if opp_score >= 75 and trend_score >= 70 and market_score >= 60 and profit_pct > 0:
            # Raise target and trail if the opportunity is still strong
            new_target = round(position.target * 1.05, 2) if position.target else None
            new_trail = self._suggested_trailing_stop(position, current_price, ind)
            return self._make_decision(
                position, 'TRAIL',
                reason=f"Strong opportunity (opp {opp_score}) — raise target / trail",
                scores=scores, new_target=new_target, new_trailing_stop=new_trail
            )

        if trend_score >= 80 and opp_score >= 60 and market_score >= 50:
            return self._make_decision(
                position, 'STRONG_HOLD',
                reason=f"Healthy trend, hold (trend {trend_score}, opp {opp_score})",
                scores=scores
            )

        return self._make_decision(
            position, 'HOLD',
            reason=f"No exit signal (safety={safety_score}, pp={pp_score}, trend={trend_score}, opp={opp_score})",
            scores=scores
        )

    # ------------------------------------------------------------------
    # Data / indicators
    # ------------------------------------------------------------------

    def _get_data(self, symbol: str) -> pd.DataFrame:
        """Fetch 1-month daily OHLCV."""
        try:
            df = self._market_data.get_stock_data(symbol, period='1mo', interval='1d')
            if not df.empty and len(df) >= 20:
                return df.copy()
        except Exception as e:
            logger.warning(f"ExitDecisionEngine: data fetch failed for {symbol}: {e}")
        return pd.DataFrame()

    def _compute_indicators(self, df: pd.DataFrame) -> Dict:
        """Compute a common set of technicals from a price DataFrame."""
        ind = {
            'rsi': 50.0, 'macd_hist': 0.0, 'macd_prev': 0.0,
            'ema20': 0.0, 'ema50': 0.0, 'atr': 0.0,
            'vol_ratio': 1.0, 'adx_like': 30.0,
            'hh_ll_trend': 0, 'swing_low': 0.0, 'prev_close': 0.0,
            'empty': True,
        }
        if df.empty or 'Close' not in df or len(df) < 20:
            return ind

        try:
            close = df['Close']
            high = df.get('High', close)
            low = df.get('Low', close)

            # EMAs
            ind['ema20'] = float(close.ewm(span=20, adjust=False).mean().iloc[-1])
            ind['ema50'] = float(close.ewm(span=50, adjust=False).mean().iloc[-1]) if len(close) >= 50 else ind['ema20']

            # RSI
            delta = close.diff()
            gain = delta.clip(lower=0)
            loss = (-delta).clip(lower=0)
            avg_g = gain.rolling(14).mean()
            avg_l = loss.rolling(14).mean()
            rs = avg_g / avg_l.replace(0, 1e-9)
            ind['rsi'] = float(100 - (100 / (1 + rs)).iloc[-1])

            # MACD histogram
            ema12 = close.ewm(span=12, adjust=False).mean()
            ema26 = close.ewm(span=26, adjust=False).mean()
            macd = ema12 - ema26
            signal_line = macd.ewm(span=9, adjust=False).mean()
            hist = macd - signal_line
            ind['macd_hist'] = float(hist.iloc[-1])
            ind['macd_prev'] = float(hist.iloc[-2]) if len(hist) >= 2 else ind['macd_hist']

            # Volume
            if 'Volume' in df:
                vol_sma20 = df['Volume'].rolling(20).mean().iloc[-1]
                if pd.notna(vol_sma20) and vol_sma20 > 0:
                    ind['vol_ratio'] = float(df['Volume'].iloc[-1]) / float(vol_sma20)

            # ATR
            tr1 = high - low
            tr2 = abs(high - close.shift(1))
            tr3 = abs(low - close.shift(1))
            tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
            atr = tr.rolling(14).mean().iloc[-1]
            ind['atr'] = float(atr) if pd.notna(atr) else 0.0

            # ADX-like (simplified using 14-period directional movement)
            plus_dm = (high - high.shift(1)).clip(lower=0)
            minus_dm = (low.shift(1) - low).clip(lower=0)
            plus_dm = plus_dm.where(plus_dm > minus_dm, 0)
            minus_dm = minus_dm.where(minus_dm > plus_dm, 0)
            atr14 = tr.rolling(14).mean()
            plus_di = 100 * plus_dm.rolling(14).mean() / atr14
            minus_di = 100 * minus_dm.rolling(14).mean() / atr14
            dx = (abs(plus_di - minus_di) / (plus_di + minus_di).replace(0, 1e-9)) * 100
            adx = dx.rolling(14).mean().iloc[-1]
            ind['adx_like'] = float(adx) if pd.notna(adx) else 30.0

            # Higher highs / higher lows over last 5 bars
            highs = high.tail(5)
            lows = low.tail(5)
            hh = (highs.diff() > 0).sum() - (highs.diff() < 0).sum()
            ll = (lows.diff() > 0).sum() - (lows.diff() < 0).sum()
            ind['hh_ll_trend'] = int(hh + ll)

            ind['swing_low'] = float(low.tail(5).min())
            ind['prev_close'] = float(close.iloc[-2]) if len(close) >= 2 else float(close.iloc[-1])
            ind['empty'] = False
        except Exception as e:
            logger.warning(f"ExitDecisionEngine: indicator calc error: {e}")

        return ind

    # ------------------------------------------------------------------
    # Layer 1 — Safety
    # ------------------------------------------------------------------

    def _safety_layer(self, position: Position, current_price: float,
                      ind: Dict) -> Tuple[int, str, str]:
        """Return (score, reason, decision). decision='FULL_EXIT' or 'NONE'."""
        # Stop loss / trailing stop
        effective_stop = max(position.stop_loss or 0, position.trailing_stop or 0)
        if current_price <= effective_stop:
            if position.trailing_stop and current_price <= position.trailing_stop and current_price > (position.stop_loss or 0):
                return 100, f"Trailing stop hit (₹{position.trailing_stop:.2f})", 'FULL_EXIT'
            return 100, f"Stop loss hit (₹{position.stop_loss:.2f})", 'FULL_EXIT'

        # Max hold days
        if (position.planned_exit_date
                and datetime.now() >= position.planned_exit_date
                and current_price >= position.entry_price):
            return 100, f"Max hold {config.SWING_MAX_HOLD_DAYS} days reached", 'FULL_EXIT'

        # Gap down
        prev_close = ind.get('prev_close', 0)
        if prev_close > 0:
            gap = (prev_close - current_price) / prev_close
            if gap > 0.05:
                return 100, f"Gap down {gap:.1%}", 'FULL_EXIT'

        return 0, "No safety trigger", 'NONE'

    # ------------------------------------------------------------------
    # Layer 2 — Profit Protection
    # ------------------------------------------------------------------

    def _profit_protection_score(self, position: Position, current_price: float,
                                 profit_pct: float, ind: Dict,
                                 market_regime: str) -> Tuple[int, List[str]]:
        """Return score 0-100 and list of flags. Active only after profit > 6%."""
        if profit_pct < _PROFIT_ACTIVATION_PCT:
            return 0, []

        score = 0
        flags = []

        # RSI overbought
        rsi = ind.get('rsi', 50)
        if rsi > 80:
            score += 35
            flags.append(f"RSI overbought ({rsi:.1f})")
        elif rsi > 70:
            score += 15
            flags.append(f"RSI elevated ({rsi:.1f})")

        # MACD weakening
        macd_hist = ind.get('macd_hist', 0)
        macd_prev = ind.get('macd_prev', 0)
        if macd_prev >= 0 and macd_hist < 0:
            score += 20
            flags.append("MACD bearish crossover")
        elif macd_hist < macd_prev:
            score += 10
            flags.append("MACD weakening")

        # Volume declining
        vol_ratio = ind.get('vol_ratio', 1.0)
        if vol_ratio < 0.5:
            score += 15
            flags.append(f"Volume collapse ({vol_ratio:.2f}x)")
        elif vol_ratio < 0.8:
            score += 5
            flags.append(f"Volume declining ({vol_ratio:.2f}x)")

        # Bearish candlestick: last close < last open and large body
        # We don't have full OHLC here, so use EMA rejection as proxy
        ema20 = ind.get('ema20', 0)
        if ema20 > 0 and current_price < ema20 and profit_pct > 0.10:
            score += 10
            flags.append("Price below EMA20")

        # Market regime bear
        if str(market_regime).upper() == 'BEAR':
            score += 15
            flags.append("Market regime BEAR")

        # ATR contraction (trend losing power)
        atr = ind.get('atr', 0)
        if atr > 0 and position.atr_at_entry and atr < position.atr_at_entry * 0.7:
            score += 10
            flags.append("ATR contraction")

        return min(score, 100), flags

    # ------------------------------------------------------------------
    # Layer 3 — Trend Validation
    # ------------------------------------------------------------------

    def _trend_score(self, position: Position, current_price: float,
                     ind: Dict) -> Tuple[int, List[str]]:
        """Score trend health 0-100 and flags."""
        if ind.get('empty'):
            return 50, ['No data']

        score = 50
        flags = []

        # EMA alignment
        ema20 = ind.get('ema20', 0)
        ema50 = ind.get('ema50', 0)
        if ema20 > 0 and ema50 > 0:
            if ema20 > ema50 and current_price > ema20:
                score += 15
                flags.append('EMA bullish')
            elif ema20 < ema50 or current_price < ema20:
                score -= 20
                flags.append('EMA bearish')

        # ADX-like strength
        adx = ind.get('adx_like', 30)
        if adx > 30:
            score += 10
            flags.append(f"ADX-like {adx:.1f}")

        # Higher highs / higher lows
        hh_ll = ind.get('hh_ll_trend', 0)
        if hh_ll >= 2:
            score += 10
            flags.append('Higher highs/lows')
        elif hh_ll <= -2:
            score -= 15
            flags.append('Lower highs/lows')

        # MACD positive/negative
        if ind.get('macd_hist', 0) > 0:
            score += 10
        else:
            score -= 10
            flags.append('MACD negative')

        # Relative to highest price
        if position.highest_price and position.highest_price > 0:
            drawdown = (position.highest_price - current_price) / position.highest_price
            if drawdown > 0.10:
                score -= 15
                flags.append(f"Drawdown {drawdown:.1%} from high")

        return max(0, min(100, score)), flags

    # ------------------------------------------------------------------
    # Layer 4 — Opportunity Re-evaluation
    # ------------------------------------------------------------------

    def _opportunity_score(self, position: Position, current_price: float,
                           ind: Dict) -> Tuple[int, List[str]]:
        """Would I buy this at the current price today?"""
        if ind.get('empty'):
            return 50, ['No data']

        score = 50
        flags = []

        # Value vs recent swing low and EMA
        swing_low = ind.get('swing_low', 0)
        ema20 = ind.get('ema20', 0)

        if swing_low > 0 and current_price > swing_low * 1.05:
            score += 10
            flags.append('Above swing low')
        elif swing_low > 0 and current_price < swing_low:
            score -= 20
            flags.append('Below swing low')

        if ema20 > 0 and current_price > ema20:
            score += 10
        elif ema20 > 0 and current_price < ema20:
            score -= 15
            flags.append('Below EMA20')

        # RSI context
        rsi = ind.get('rsi', 50)
        if rsi > _OPPORTUNITY_BEARISH_RSI:
            score -= 20
            flags.append('Price overbought')
        elif rsi < _OPPORTUNITY_BULLISH_RSI:
            score += 10
            flags.append('Not overbought')

        # Volume context
        vol_ratio = ind.get('vol_ratio', 1.0)
        if vol_ratio > 1.2:
            score += 10
            flags.append('Healthy volume')
        elif vol_ratio < 0.5:
            score -= 15
            flags.append('Weak volume')

        return max(0, min(100, score)), flags

    # ------------------------------------------------------------------
    # Layer 5 — Market / Regime
    # ------------------------------------------------------------------

    def _market_score(self, market_regime: str, ind: Dict) -> int:
        """Score the broader market context 0-100."""
        regime = str(market_regime).upper()
        if regime == 'BULL':
            return 80
        if regime == 'BEAR':
            return 20
        return 50

    # ------------------------------------------------------------------
    # Target / Partial helpers
    # ------------------------------------------------------------------

    def _target_management(self, position: Position, current_price: float,
                           profit_pct: float, trend_score: int, opp_score: int,
                           market_score: int, scores: Dict) -> ExitDecision:
        """Handle target hit: raise target / trail OR exit based on health."""
        new_target = round(position.target * 1.05, 2) if position.target else None
        new_trail = self._suggested_trailing_stop(position, current_price,
                                                  {'atr': scores.get('profit_pct', 0), 'empty': True})

        if trend_score >= 80 and opp_score >= 70 and market_score >= 70:
            return self._make_decision(
                position, 'TRAIL',
                reason=f"Target {position.target} hit but trend strong (trend {trend_score}, opp {opp_score}) — raise target",
                new_target=new_target, new_trailing_stop=new_trail, scores=scores
            )

        if trend_score >= 60 and market_score >= 50 and profit_pct > 0.10:
            # Move stop to cost / raise trail but keep holding
            new_trail = max(new_trail or 0, position.entry_price)
            return self._make_decision(
                position, 'TRAIL',
                reason=f"Target {position.target} hit, move SL to cost and trail",
                new_trailing_stop=new_trail, move_sl_to_cost=True, scores=scores
            )

        return self._make_decision(
            position, 'FULL_EXIT',
            reason=f"Target {position.target} hit and trend/opportunity weak (trend {trend_score}, opp {opp_score})",
            quantity=position.quantity, scores=scores
        )

    def _partial_exit_layer(self, position: Position, profit_pct: float) -> Optional[Dict]:
        """Return {quantity, reason} if a tranche should be scaled out."""
        if position.partial_count >= _MAX_PARTIAL_TRANCHES:
            return None

        for i, threshold in enumerate(_PARTIAL_EXIT_PROFIT_PCTS):
            if position.partial_count == i and profit_pct >= threshold:
                fraction = _PARTIAL_EXIT_FRACTIONS[i]
                qty = max(1, int(position.quantity * fraction))
                if qty >= position.quantity:
                    qty = position.quantity
                return {
                    'quantity': qty,
                    'reason': f"Partial tranche {i+1} at +{profit_pct*100:.1f}%"
                }
        return None

    def _suggested_trailing_stop(self, position: Position, current_price: float,
                                 ind: Dict) -> float:
        """Suggest a trailing stop based on ATR or percentage."""
        highest = position.highest_price or current_price
        atr = ind.get('atr', 0) if not ind.get('empty') else (position.atr_at_entry or 0)
        candidates = []
        if atr and atr > 0:
            candidates.append(highest - 2.0 * atr)
        candidates.append(highest * 0.95)
        return round(max(candidates), 2) if candidates else highest * 0.95

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _is_hard_exit(reason: str) -> bool:
        """Hard swing exits that bypass MIN_HOLD_HOURS."""
        if not reason:
            return False
        return any(k in reason.lower() for k in (
            'stop', 'trailing', 'target', 'max hold', 'gap', 'circuit', 'eod'
        ))

    def _make_decision(self, position: Position, decision: str, reason: str,
                       quantity: int = 0, scores: Optional[Dict] = None,
                       move_sl_to_cost: bool = False,
                       new_target: Optional[float] = None,
                       new_trailing_stop: Optional[float] = None) -> ExitDecision:
        """Build and log a decision. Mutates position for TRAIL decisions.

        Enforces MIN_HOLD_HOURS for non-hard full/partial exits.
        """
        # Apply TRAIL mutations to the position object
        if move_sl_to_cost:
            position.stop_loss = round(max(position.stop_loss or 0, position.entry_price), 2)
        if new_trailing_stop:
            position.trailing_stop = round(new_trailing_stop, 2)
        if new_target:
            position.target = round(new_target, 2)

        # Minimum holding period gate for profit/trend/opportunity exits
        if decision in ('FULL_EXIT', 'PARTIAL_EXIT') and not self._is_hard_exit(reason):
            hours_held = (
                (datetime.now() - position.entry_time).total_seconds() / 3600
                if position.entry_time else 0.0
            )
            if hours_held < config.MIN_HOLD_HOURS:
                decision = 'HOLD'
                quantity = 0
                reason = (
                    f"{reason} — MIN_HOLD_HOURS gate "
                    f"({hours_held:.1f}h < {config.MIN_HOLD_HOURS}h)"
                )

        logger.info(
            f"EXIT_DECISION | symbol={position.symbol} | decision={decision} "
            f"| quantity={quantity} | reason='{reason}' | "
            f"scores={scores or {}}"
        )

        return ExitDecision(
            symbol=position.symbol,
            decision=decision,
            quantity=quantity,
            reason=reason,
            scores=scores,
            move_sl_to_cost=move_sl_to_cost,
            new_target=new_target,
            new_trailing_stop=new_trailing_stop,
        )
