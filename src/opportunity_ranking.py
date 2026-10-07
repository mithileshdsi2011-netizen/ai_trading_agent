"""
Opportunity Ranking Engine

Ranks today's BUY signals by a composite score that combines:
  expected return, confidence, market regime, sector concentration, and correlation.
The orchestrator, rotation engine, and dashboard all use this ranked queue.
"""
import logging
from typing import Dict, List, Optional

from config import config
from risk_manager import PositionStatus

logger = logging.getLogger(__name__)


class OpportunityRankingEngine:
    """
    Ranks BUY signals so the bot always knows the next best candidate.
    """

    def __init__(self):
        self.max_sector_exposure = float(getattr(config, 'RANKING_MAX_SECTOR_EXPOSURE', 0.25))
        self.min_confidence = float(getattr(config, 'MIN_CONFIDENCE_BULL', 0.55))
        self.regime_factors = {
            'BULL':     1.00,
            'SIDEWAYS': float(getattr(config, 'SIDEWAYS_SIZE_FACTOR', 0.75)),
            'BEAR':     float(getattr(config, 'POSITION_SIZING_BEAR_FACTOR', 0.60)),
            'VOLATILE': float(getattr(config, 'POSITION_SIZING_VOLATILE_FACTOR', 0.20)),
        }

    def _regime_factor(self, regime: str) -> float:
        return self.regime_factors.get(str(regime).upper(), 0.50)

    def _portfolio_value(self, open_positions, cash: float) -> float:
        invested = 0.0
        for p in open_positions:
            if p.status in {PositionStatus.OPEN, PositionStatus.PARTIAL}:
                invested += p.quantity * (p.average_price or p.entry_price)
        return max(cash + invested, cash)

    def _sector_exposure(self, open_positions, portfolio_value: float, candidate_sector: str) -> float:
        if portfolio_value <= 0 or not candidate_sector or candidate_sector == 'Unknown':
            return 0.0
        exposure = 0.0
        for p in open_positions:
            if p.status in {PositionStatus.OPEN, PositionStatus.PARTIAL} and p.sector == candidate_sector:
                exposure += p.quantity * (p.average_price or p.entry_price)
        return exposure / portfolio_value

    @staticmethod
    def _opportunity_score(signal: Dict, expected_return: float,
                           sector_factor: float, regime_factor: float) -> float:
        """
        Risk-adjusted Opportunity Score:

        score = (expected_return × confidence × trend_score × regime_factor × sector_factor)
                ─────────────────────────────────────────────────────────────────────────────
                                        (risk + 0.001)

        Risk is proxied by ATR / current_price. Higher ATR = higher risk = lower score.
        """
        confidence = signal.get('confidence', 0.0) or 0.0

        # Trend score: overall_score is 0-100 or 0-1 depending on source
        overall = signal.get('overall_score', 0.0) or 0.0
        if overall > 1.0:
            trend_score = min(1.0, max(0.0, overall / 100.0))
        else:
            trend_score = min(1.0, max(0.0, overall))

        # Risk proxy: ATR as a fraction of price
        price = signal.get('current_price', 0.0) or 0.0
        atr = signal.get('atr', 0.0) or 0.0
        risk = atr / price if price > 0 and atr > 0 else 0.05

        return (expected_return * confidence * trend_score * regime_factor * sector_factor) / (risk + 0.001)

    def rank(
        self,
        buy_signals: List[Dict],
        open_positions,
        regime: str,
        cash: float = 0.0,
    ) -> List[Dict]:
        """
        Rank BUY signals best-first and attach a rank_score to each.

        Args:
            buy_signals: raw BUY signals from the signal generator.
            open_positions: current open Position objects.
            regime: current market regime.
            cash: available cash for exposure calculation.

        Returns:
            A new list of signal dicts sorted by opportunity_score descending.
        """
        portfolio_value = self._portfolio_value(open_positions, cash)
        regime_factor = self._regime_factor(regime)
        scored = []

        for signal in buy_signals:
            symbol = signal.get('symbol', '')
            confidence = signal.get('confidence', 0.0) or 0.0
            # self.min_confidence is 0-1; signal confidence is 0-100
            if confidence < self.min_confidence * 100:
                continue

            price = signal.get('current_price', 0.0) or 0.0
            target = signal.get('target', 0.0) or 0.0
            if price <= 0 or target <= 0 or target <= price:
                continue

            expected_return = (target - price) / price
            if expected_return <= 0:
                continue

            # Sector concentration penalty
            research = signal.get('_research') or {}
            sector = signal.get('sector') or research.get('sector', 'Unknown')
            sector_exposure = self._sector_exposure(open_positions, portfolio_value, sector)
            sector_factor = max(0.5, 1.0 - (sector_exposure / self.max_sector_exposure))

            # Risk-adjusted opportunity score
            opp_score = self._opportunity_score(signal, expected_return, sector_factor, regime_factor)

            ranked_signal = dict(signal)
            ranked_signal['opportunity_score'] = opp_score
            ranked_signal['expected_return'] = expected_return
            ranked_signal['rank_sector'] = sector
            ranked_signal['rank_sector_exposure'] = sector_exposure
            ranked_signal['rank_reason'] = (
                f"exp={expected_return:.1%} conf={confidence:.0f}% "
                f"trend={signal.get('overall_score', 0)} "
                f"regime={regime}×{regime_factor:.2f} "
                f"sector={sector}({sector_exposure:.1%})×{sector_factor:.2f} "
                f"opp_score={opp_score:.3f}"
            )
            scored.append(ranked_signal)

        scored.sort(key=lambda s: s['opportunity_score'], reverse=True)
        for i, s in enumerate(scored, start=1):
            s['rank'] = i

        return scored
