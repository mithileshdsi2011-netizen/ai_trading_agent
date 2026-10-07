"""
Portfolio Rotation Engine

Replaces and extends the OpportunityCostEngine. Every 15 minutes it evaluates
whether the weakest holding should be sold to free capital for a materially
better, uncorrelated new opportunity — after accounting for costs, taxes, and
risk-adjusted opportunity scores.
"""
import logging
from datetime import datetime
from typing import Dict, List, Optional

from config import config
from risk_manager import PositionStatus, _total_charges

logger = logging.getLogger(__name__)


class PortfolioRotationEngine:
    """
    Compares open holdings against today's ranked BUY signals and recommends a
    single rotation when the *risk-adjusted, net-of-costs* improvement is large
    enough.
    """

    def __init__(self):
        # Thresholds — intentionally conservative to avoid churn.
        # Override via .env by adding them to the Config class in config.py.
        self.min_hold_hours = float(getattr(config, 'ROTATION_MIN_HOLD_HOURS', 24.0))
        self.min_score_delta = float(getattr(config, 'ROTATION_MIN_SCORE_DELTA', 0.20))   # new must be 20% better
        self.min_net_improvement = float(getattr(config, 'ROTATION_MIN_NET_IMPROVEMENT', 0.03))  # 3% after costs
        self.min_loss_rotation_delta = float(getattr(config, 'ROTATION_MIN_LOSS_ROTATION_DELTA', 0.10))  # 10% for a losing holding
        self.max_per_day = int(getattr(config, 'ROTATION_MAX_PER_DAY', 1))
        self.same_sector_penalty = float(getattr(config, 'ROTATION_SAME_SECTOR_PENALTY', 0.70))
        self.new_sector_bonus = float(getattr(config, 'ROTATION_NEW_SECTOR_BONUS', 1.20))
        self.stcg_rate = float(getattr(config, 'ROTATION_STCG_RATE', 0.15))  # 15% short-term capital gains
        self.slippage_pct = float(getattr(config, 'ROTATION_SLIPPAGE_PCT', 0.0005))  # 0.05% market impact

        self._rotations_today = 0
        self._last_date: Optional[str] = None

    @staticmethod
    def _holding_score(position, current_price: float) -> float:
        """
        Risk-adjusted opportunity score for an existing position.
        score = expected_return / (downside_risk + 0.001)
        """
        if current_price <= 0 or position.target <= 0 or position.entry_price <= 0:
            return 0.0

        expected_return = (position.target - current_price) / current_price
        # Downside risk from current level to stop loss
        downside = (current_price - position.stop_loss) / current_price if position.stop_loss > 0 else 0.05
        return expected_return / max(downside + 0.001, 0.001)

    def _new_score(self, signal: Dict, weakest_sector: str, open_sectors: set) -> float:
        """Fetch or compute risk-adjusted opportunity score for a new idea."""
        score = signal.get('opportunity_score')
        if score is None:
            price = signal.get('current_price', 0.0) or 0.0
            target = signal.get('target', 0.0) or 0.0
            atr = signal.get('atr', 0.0) or 0.0
            confidence = signal.get('confidence', 0.0) or 0.0
            expected_return = (target - price) / price if price > 0 and target > price else 0.0
            risk = (atr / price) if price > 0 and atr > 0 else 0.05
            score = (expected_return * confidence) / (risk + 0.001)

        sector = signal.get('sector') or (signal.get('_research') or {}).get('sector', 'Unknown')

        # Sector rotation guard: avoid swapping within the same sector
        if sector and sector == weakest_sector:
            score *= self.same_sector_penalty

        # Prefer rotating into a sector that is not currently held
        if sector and sector not in open_sectors:
            score *= self.new_sector_bonus

        return float(score)

    def _selling_cost_pct(
        self,
        sell_value: float,
        buy_value: float,
        unrealized_profit: float,
    ) -> float:
        """
        Estimated total cost of rotating: sell one leg + buy another.
        Includes brokerage, STT, exchange/SEBI, GST, slippage and STCG if profitable.
        """
        if sell_value <= 0:
            return 1.0  # block the rotation

        base_cost = _total_charges(buy_value, sell_value)
        slippage = (buy_value + sell_value) * self.slippage_pct
        stcg = max(0.0, unrealized_profit) * self.stcg_rate

        total_cost = base_cost + slippage + stcg
        return total_cost / sell_value

    def evaluate(
        self,
        buy_signals: List[Dict],
        open_positions,
        open_symbols: set,
        market_data=None,
        date_str: Optional[str] = None,
        portfolio_value: float = 0.0,
    ) -> Optional[Dict]:
        """
        Returns a rotation dict if the weakest holding should be sold for a
        better new opportunity, otherwise None.

        Args:
            buy_signals: Today's BUY signals, already ranked best-first.
            open_positions: Current open Position objects.
            open_symbols: Set of currently held symbols.
            market_data: MarketDataFetcher for live prices.
            date_str: 'YYYY-MM-DD' for daily rotation cap.
            portfolio_value: Total portfolio value for context.
        """
        # Reset daily rotation counter
        if date_str and date_str != self._last_date:
            self._rotations_today = 0
            self._last_date = date_str

        if not buy_signals or not open_positions:
            return None

        if market_data is None:
            return None

        if self._rotations_today >= self.max_per_day:
            logger.info(f"PortfolioRotation: max rotations today ({self.max_per_day}) reached")
            return None

        now = datetime.now()
        open_sectors = {p.sector for p in open_positions if p.status in {PositionStatus.OPEN, PositionStatus.PARTIAL} and p.sector}

        # ── 1. Score all open positions, find the weakest ───────────────────────
        holding_candidates = []
        for p in open_positions:
            if p.status not in {PositionStatus.OPEN, PositionStatus.PARTIAL}:
                continue

            hold_hours = (now - p.entry_time).total_seconds() / 3600.0
            if hold_hours < self.min_hold_hours:
                continue

            current_price = market_data.get_realtime_price(p.symbol)
            if not current_price or current_price <= 0:
                current_price = p.average_price or p.entry_price
            if not current_price or current_price <= 0:
                continue

            if p.target <= 0 or p.entry_price <= 0:
                continue

            expected_return = (p.target - current_price) / current_price
            unrealized_pct = (current_price - p.entry_price) / p.entry_price
            unrealized_pnl = (current_price - p.entry_price) * p.quantity
            score = self._holding_score(p, current_price)

            holding_candidates.append({
                'symbol': p.symbol,
                'position': p,
                'current_price': current_price,
                'quantity': p.quantity,
                'entry_price': p.entry_price,
                'target': p.target,
                'stop_loss': p.stop_loss,
                'expected_return': expected_return,
                'unrealized_pct': unrealized_pct,
                'unrealized_pnl': unrealized_pnl,
                'score': score,
                'hold_hours': hold_hours,
                'sector': p.sector,
            })

        if not holding_candidates:
            return None

        # Weakest = lowest risk-adjusted opportunity score
        weakest = min(holding_candidates, key=lambda x: x['score'])

        # ── 2. Pick the best new opportunity that is not already held ───────────
        best_signal = None
        for sig in buy_signals:
            if sig.get('symbol') not in open_symbols:
                best_signal = sig
                break
        if best_signal is None:
            return None

        # Skip if the best new idea is weaker or not materially better
        best_score = self._new_score(best_signal, weakest['sector'], open_sectors)
        if best_score <= 0:
            return None

        score_ratio = best_score / (weakest['score'] + 0.0001)
        if score_ratio < (1.0 + self.min_score_delta):
            logger.info(
                f"PortfolioRotation: best new {best_signal['symbol']} score={best_score:.2f} "
                f"not strong enough vs weakest {weakest['symbol']} score={weakest['score']:.2f} "
                f"(ratio={score_ratio:.2f})"
            )
            return None

        # ── 3. Selling cost / net improvement check ─────────────────────────────
        sell_value = weakest['quantity'] * weakest['current_price']
        buy_price = best_signal.get('current_price', 0.0) or 0.0
        buy_value = sell_value  # assume rotating the same capital
        cost_pct = self._selling_cost_pct(sell_value, buy_value, weakest['unrealized_pnl'])

        best_expected_return = best_signal.get('expected_return') or ((best_signal.get('target', 0.0) - buy_price) / buy_price if buy_price > 0 else 0.0)
        gross_improvement = best_expected_return - weakest['expected_return']
        net_improvement = gross_improvement - cost_pct

        # ── 4. Loss protection ──────────────────────────────────────────────────
        if weakest['unrealized_pnl'] < 0:
            # Never sell a losing holding to chase a marginal improvement
            if net_improvement < self.min_loss_rotation_delta:
                logger.info(
                    f"PortfolioRotation: blocked {weakest['symbol']} (unrealized={weakest['unrealized_pct']:.1%}) "
                    f"→ {best_signal['symbol']} — net improvement {net_improvement:.1%} "
                    f"< loss-rotation threshold {self.min_loss_rotation_delta:.1%}"
                )
                return None

        # ── 5. Profitable holding extra threshold ───────────────────────────────
        else:
            if net_improvement < self.min_net_improvement:
                logger.info(
                    f"PortfolioRotation: blocked {weakest['symbol']} (green={weakest['unrealized_pct']:.1%}) "
                    f"→ {best_signal['symbol']} — net improvement {net_improvement:.1%} "
                    f"< threshold {self.min_net_improvement:.1%}"
                )
                return None

        # ── 6. Final approval ───────────────────────────────────────────────────
        self._rotations_today += 1

        logger.warning(
            f"PortfolioRotation: rotate {weakest['symbol']} "
            f"(score={weakest['score']:.2f}, exp={weakest['expected_return']:.1%}, "
            f"pnl={weakest['unrealized_pct']:.1%}) -> "
            f"{best_signal['symbol']} "
            f"(score={best_score:.2f}, exp={best_expected_return:.1%}) "
            f"gross={gross_improvement:.1%} cost={cost_pct:.2%} "
            f"net={net_improvement:.1%}"
        )

        return {
            'sell_symbol': weakest['symbol'],
            'buy_signal': best_signal,
            'weakest_score': weakest['score'],
            'best_score': best_score,
            'weakest_expected_return': weakest['expected_return'],
            'best_expected_return': best_expected_return,
            'gross_improvement': gross_improvement,
            'cost_pct': cost_pct,
            'net_improvement': net_improvement,
            'sell_current_price': weakest['current_price'],
            'sell_quantity': weakest['quantity'],
            'sell_entry_price': weakest['entry_price'],
            'sell_investment_amount': weakest['quantity'] * weakest['entry_price'],
            'reason': (
                f"Portfolio rotation: sell {weakest['symbol']} "
                f"(score={weakest['score']:.2f}, exp={weakest['expected_return']:.1%}, "
                f"pnl={weakest['unrealized_pct']:.1%}) "
                f"for {best_signal['symbol']} "
                f"(score={best_score:.2f}, exp={best_expected_return:.1%}) "
                f"net improvement={net_improvement:.1%}"
            ),
        }
