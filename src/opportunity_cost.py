"""
Opportunity Cost Engine
Prevents capital from being trapped in mediocre holdings when a materially
better opportunity is available.
"""
import logging
from datetime import datetime
from typing import Dict, List, Optional

from config import config
from risk_manager import PositionStatus

logger = logging.getLogger(__name__)


class OpportunityCostEngine:
    """
    Compares open positions against today's best BUY signal and recommends
    a single rotation when the expected return gap is large enough.
    """

    def __init__(self):
        # Thresholds are intentionally conservative to avoid churn.
        # Override via .env by adding them to the Config class in config.py.
        self.min_return_delta = float(getattr(config, 'OPPORTUNITY_COST_MIN_RETURN_DELTA', 0.05))     # 5% expected-return edge
        self.min_confidence = float(getattr(config, 'OPPORTUNITY_COST_MIN_CONFIDENCE', 0.80))         # 80% confidence in the new idea
        self.min_hold_hours = float(getattr(config, 'OPPORTUNITY_COST_MIN_HOLD_HOURS', 12.0))         # avoid STCG/noise
        self.max_per_day = int(getattr(config, 'OPPORTUNITY_COST_MAX_PER_DAY', 2))                    # 2 rotations/day max
        self.profitable_extra_delta = float(getattr(config, 'OPPORTUNITY_COST_PROFITABLE_EXTRA_DELTA', 0.03))  # extra 3% if holding is green

        self._rotations_today = 0
        self._last_date: Optional[str] = None

    def evaluate(
        self,
        buy_signals: List[Dict],
        open_positions,
        open_symbols: set,
        market_data=None,
        date_str: Optional[str] = None,
    ) -> Optional[Dict]:
        """
        Returns a rotation dict if a weak holding should be sold to buy a
        materially better opportunity, otherwise None.

        Args:
            buy_signals: Today's BUY signals, sorted best-first.
            open_positions: Current open Position objects (from RiskManager).
            open_symbols: Set of currently held symbols.
            market_data: MarketDataFetcher for live prices.
            date_str: 'YYYY-MM-DD' for daily rotation cap.
        """
        # Reset daily rotation counter
        if date_str and date_str != self._last_date:
            self._rotations_today = 0
            self._last_date = date_str

        if not buy_signals or not open_positions:
            return None

        if market_data is None:
            return None

        # Pick the best *new* (not already held) BUY signal
        best_signal = None
        for sig in buy_signals:
            if sig.get('symbol') not in open_symbols:
                best_signal = sig
                break
        if best_signal is None:
            return None

        best_conf = best_signal.get('confidence', 0.0)
        if best_conf < self.min_confidence:
            return None

        best_price = best_signal.get('current_price', 0.0) or 0.0
        best_target = best_signal.get('target', 0.0) or 0.0
        if best_price <= 0 or best_target <= 0 or best_target <= best_price:
            return None

        best_return = (best_target - best_price) / best_price
        if best_return <= 0:
            return None

        now = datetime.now()
        candidates = []
        for p in open_positions:
            if p.status not in {PositionStatus.OPEN, PositionStatus.PARTIAL}:
                continue

            hold_hours = (now - p.entry_time).total_seconds() / 3600.0
            if hold_hours < self.min_hold_hours:
                continue

            current_price = market_data.get_realtime_price(p.symbol)
            if not current_price or current_price <= 0:
                continue

            if p.target <= 0 or p.entry_price <= 0:
                continue

            expected_return = (p.target - current_price) / current_price
            unrealized_pct = (current_price - p.entry_price) / p.entry_price

            # Composite score: expected gain × conviction. Avoids rotating into a
            # high-return but low-conviction "hope" trade.
            score = expected_return * best_conf

            candidates.append({
                'symbol': p.symbol,
                'position': p,
                'current_price': current_price,
                'quantity': p.quantity,
                'entry_price': p.entry_price,
                'target': p.target,
                'expected_return': expected_return,
                'unrealized_pct': unrealized_pct,
                'score': score,
                'hold_hours': hold_hours,
            })

        if not candidates:
            return None

        # Identify the weakest current use of capital
        weakest = min(candidates, key=lambda x: x['expected_return'])

        improvement = best_return - weakest['expected_return']
        threshold = self.min_return_delta
        if weakest['unrealized_pct'] > 0:
            # If the weak holding is already profitable, require a larger edge
            # to justify realised gains / tax / churn.
            threshold += self.profitable_extra_delta

        if improvement <= threshold:
            return None

        # Daily cap to prevent runaway churn
        if self._rotations_today >= self.max_per_day:
            logger.info(f"OpportunityCost: max rotations today ({self.max_per_day}) reached")
            return None

        self._rotations_today += 1

        logger.info(
            f"OpportunityCost: rotate {weakest['symbol']} "
            f"(exp_ret={weakest['expected_return']:.1%}, pnl={weakest['unrealized_pct']:.1%}) -> "
            f"{best_signal['symbol']} (exp_ret={best_return:.1%}, conf={best_conf:.0%})"
        )

        return {
            'sell_symbol': weakest['symbol'],
            'buy_signal': best_signal,
            'weakest_expected_return': weakest['expected_return'],
            'best_expected_return': best_return,
            'expected_improvement': improvement,
            'sell_current_price': weakest['current_price'],
            'sell_quantity': weakest['quantity'],
            'sell_entry_price': weakest['entry_price'],
            'sell_investment_amount': weakest['quantity'] * weakest['entry_price'],
            'reason': (
                f"Opportunity cost: sell {weakest['symbol']} "
                f"(expected {weakest['expected_return']:.1%}) "
                f"for {best_signal['symbol']} "
                f"(expected {best_return:.1%}, conf {best_conf:.0%})"
            ),
        }
