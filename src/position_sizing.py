"""
Dynamic position sizing engine.

Replaces fixed ₹-per-trade allocation with a size computed from:
  confidence × market regime × ATR risk × sector exposure × correlation × cash.
"""
import logging
from typing import Dict, List, Optional

import pandas as pd

from config import config
from risk_manager import Position, PositionStatus

logger = logging.getLogger(__name__)


class PositionSizingEngine:
    """
    Calculate the appropriate quantity and budget for a BUY signal.

    Inputs:
      - confidence from AI/scoring
      - ATR (volatility) and current price
      - open positions (for sector exposure & correlation)
      - portfolio value & available cash
      - market regime (BULL / SIDEWAYS / BEAR / VOLATILE)

    Returns a dict with qty, investment_amount, budget and a human-readable reason.
    """

    def __init__(self):
        self.confidence_exponent = float(getattr(config, 'POSITION_SIZING_CONFIDENCE_EXPONENT', 1.5))
        self.regime_factors = {
            'BULL':     1.00,
            'SIDEWAYS': float(getattr(config, 'SIDEWAYS_SIZE_FACTOR', 0.75)),
            'BEAR':     float(getattr(config, 'POSITION_SIZING_BEAR_FACTOR', 0.60)),
            'VOLATILE': float(getattr(config, 'POSITION_SIZING_VOLATILE_FACTOR', 0.20)),
        }
        self.max_sector_exposure = float(getattr(config, 'POSITION_SIZING_MAX_SECTOR_EXPOSURE', 0.25))
        self.corr_threshold = float(getattr(config, 'POSITION_SIZING_CORR_THRESHOLD', 0.80))
        self.risk_per_trade = float(getattr(config, 'RISK_PER_TRADE', 0.02))
        self.atr_sl_multiplier = float(getattr(config, 'ATR_SL_MULTIPLIER', 2.0))

    @staticmethod
    def _confidence_factor(confidence: float, exponent: float) -> float:
        # Non-linear: high confidence is rewarded, low confidence is sharply reduced
        return max(0.0, confidence ** exponent)

    def _regime_factor(self, regime: str) -> float:
        return self.regime_factors.get(str(regime).upper(), 0.50)

    def _sector_exposure(self, open_positions, portfolio_value: float, candidate_sector: str) -> float:
        """Return the % of portfolio already in the same sector."""
        if portfolio_value <= 0 or not candidate_sector or candidate_sector == 'Unknown':
            return 0.0
        exposure = 0.0
        for p in open_positions:
            if p.status not in {PositionStatus.OPEN, PositionStatus.PARTIAL}:
                continue
            if p.sector == candidate_sector:
                exposure += p.quantity * (p.average_price or p.entry_price)
        return exposure / portfolio_value

    def _max_correlation(self, candidate_symbol: str, open_positions, market_data) -> float:
        """
        Compute the maximum 1-month price correlation of the candidate to open positions.
        Returns 0.0 if data is unavailable; this is a safety default (no penalty).
        """
        if not market_data or not open_positions:
            return 0.0
        try:
            cand_df = market_data.get_stock_data(candidate_symbol, period='1mo', interval='1d')
            if cand_df is None or cand_df.empty or 'Close' not in cand_df:
                return 0.0
            cand_ret = cand_df['Close'].pct_change().dropna()
            max_corr = 0.0
            for p in open_positions:
                open_df = market_data.get_stock_data(p.symbol, period='1mo', interval='1d')
                if open_df is None or open_df.empty or 'Close' not in open_df:
                    continue
                open_ret = open_df['Close'].pct_change().dropna()
                common = pd.concat([cand_ret, open_ret], axis=1).dropna()
                if len(common) < 5:
                    continue
                corr = common.iloc[:, 0].corr(common.iloc[:, 1])
                if pd.notna(corr):
                    max_corr = max(max_corr, abs(float(corr)))
            return max_corr
        except Exception as e:
            logger.warning(f"Position sizing correlation check failed for {candidate_symbol}: {e}")
            return 0.0

    def calculate(
        self,
        signal: Dict,
        open_positions,
        open_symbols: set,
        base_budget: float,
        cash_for_trade: float,
        portfolio_value: float,
        regime: str,
        market_data=None,
    ) -> Dict:
        """
        Return the recommended quantity and budget for this signal.

        Args:
            signal: BUY signal dict, must contain 'current_price' and 'confidence'.
            open_positions: list of open Position objects.
            open_symbols: set of currently held symbols.
            base_budget: per-slot budget before adjustments (e.g. per_stock_budget).
            cash_for_trade: hard cash cap for this trade (available - already invested).
            portfolio_value: total portfolio value for risk/exposure calc.
            regime: current market regime string.
            market_data: MarketDataFetcher for correlation lookup.
        """
        price = signal.get('current_price', 0.0) or 0.0
        if price <= 0:
            return {'qty': 0, 'investment_amount': 0.0, 'budget': 0.0, 'reason': 'Invalid price'}

        # Enterprise engine confidence is 0-100; scale to 0-1 for sizing math
        confidence = (signal.get('confidence', 0.0) or 0.0) / 100.0
        atr = signal.get('atr', 0.0) or 0.0

        # ── 1. Confidence and regime sizing ─────────────────────────────────────
        confidence_factor = self._confidence_factor(confidence, self.confidence_exponent)
        regime_factor = self._regime_factor(regime)

        # ── 2. Sector exposure penalty ──────────────────────────────────────────
        research = signal.get('_research') or {}
        sector = signal.get('sector') or research.get('sector', 'Unknown')
        sector_exposure = self._sector_exposure(open_positions, portfolio_value, sector)
        # Linear penalty from 1.0 at 0% down to ~0.5 at the max-exposure threshold
        sector_factor = max(0.5, 1.0 - (sector_exposure / self.max_sector_exposure))

        # ── 3. Correlation penalty ──────────────────────────────────────────────
        max_corr = 0.0
        if open_symbols:
            max_corr = self._max_correlation(signal['symbol'], open_positions, market_data)
        corr_factor = 0.5 if max_corr > self.corr_threshold else 1.0

        # ── 4. ATR risk cap ─────────────────────────────────────────────────────
        # Cap notional so a 2xATR stop move does not lose more than RISK_PER_TRADE % of portfolio
        atr_risk_cap = float('inf')
        if atr > 0 and price > 0:
            position_risk_pct = (atr * self.atr_sl_multiplier) / price
            if position_risk_pct > 0:
                atr_risk_cap = (portfolio_value * self.risk_per_trade) / position_risk_pct

        # ── 5. Final budget ─────────────────────────────────────────────────────
        target_budget = base_budget * confidence_factor * regime_factor * sector_factor * corr_factor
        budget = min(target_budget, atr_risk_cap, cash_for_trade)

        # ── 6. Quantity ─────────────────────────────────────────────────────────
        qty = max(1, int(budget / price)) if budget >= price else 0
        investment = qty * price

        reason = (
            f"Dynamic sizing: base=₹{base_budget:.0f} "
            f"conf={confidence:.0%}×{confidence_factor:.2f} "
            f"regime={regime}×{regime_factor:.2f} "
            f"sector={sector}({sector_exposure:.1%})×{sector_factor:.2f} "
            f"corr={max_corr:.2f}×{corr_factor:.2f} "
            f"atr={atr:.2f} "
            f"final=₹{investment:.0f}"
        )

        return {
            'qty': qty,
            'position_size': qty,
            'investment_amount': investment,
            'budget': budget,
            'reason': reason,
        }
