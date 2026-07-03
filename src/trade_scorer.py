"""
Trade Scoring Engine
Scores every trade candidate from 0–100 across 7 weighted dimensions.
Score determines position sizing:
  ≥80  → full position (100%)
  75–79 → 75%
  70–74 → 50%
  65–69 → 25%
  <65  → skip
"""
from typing import Dict, Optional
import logging
import pandas as pd

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ── Component weights (must sum to 100) ───────────────────────────────────────
WEIGHTS = {
    'trend':        20,   # Daily trend direction
    'rsi':          15,   # RSI position
    'macd':         15,   # MACD histogram direction
    'volume':       20,   # Volume surge vs 20-day avg
    'sentiment':    10,   # News sentiment score
    'regime':       10,   # Market regime
    'sector':       10,   # Sector relative strength (proxy: momentum vs Nifty)
}

# ── Score thresholds ───────────────────────────────────────────────────────────
SCORE_FULL     = 80   # 100% position
SCORE_75PCT    = 75   # 75% position
SCORE_50PCT    = 70   # 50% position
SCORE_25PCT    = 65   # 25% position
SCORE_SKIP     = 65   # Below this → skip


class TradeScorer:
    """
    Scores a trade signal from 0–100 and returns position size fraction.
    All inputs come from the existing research/signal pipeline — no new API calls.
    """

    def score(
        self,
        signal: Dict,
        research: Dict,
        regime: str = "SIDEWAYS",
        sector_momentum: float = 0.0,   # positive = sector outperforming
    ) -> Dict:
        """
        Score a trade signal.

        Args:
            signal:           Output of SignalGenerator.generate_signal()
            research:         Output of AIResearchAgent.research_stock()
            regime:           Market regime string: BULL / BEAR / SIDEWAYS
            sector_momentum:  Float −1..1; positive = sector is stronger than index

        Returns:
            Dict with keys: total_score, components, size_fraction, skip
        """
        tech   = research.get('technical_analysis', {})
        senti  = research.get('sentiment_analysis', {})
        hist   = research.get('_historical_df')   # optional, set by agent if passed

        components: Dict[str, float] = {}

        # ── 1. TREND (20 pts) ──────────────────────────────────────────────────
        trend = tech.get('trend', 'NEUTRAL')
        if trend == 'STRONG_UPTREND':
            components['trend'] = 20
        elif trend == 'UPTREND':
            components['trend'] = 15
        elif trend == 'NEUTRAL':
            components['trend'] = 8
        elif trend == 'DOWNTREND':
            components['trend'] = 3
        else:
            components['trend'] = 0

        # ── 2. RSI (15 pts) ────────────────────────────────────────────────────
        rsi = tech.get('rsi', 50)
        if isinstance(rsi, float) and pd.notna(rsi):
            if 40 <= rsi <= 60:          # neutral zone — trending
                components['rsi'] = 12
            elif 30 <= rsi < 40:         # recovering from oversold
                components['rsi'] = 15
            elif rsi < 30:               # oversold bounce
                components['rsi'] = 13
            elif 60 < rsi <= 70:         # mild overbought but momentum
                components['rsi'] = 8
            else:                        # >70 overbought
                components['rsi'] = 2
        else:
            components['rsi'] = 8        # no data → neutral

        # ── 3. MACD (15 pts) ───────────────────────────────────────────────────
        macd_hist = tech.get('macd_histogram', None)
        macd_prev = tech.get('macd_histogram_prev', None)
        if macd_hist is not None and pd.notna(macd_hist):
            if macd_hist > 0:
                if macd_prev is not None and macd_hist > macd_prev:
                    components['macd'] = 15  # diverging upward
                else:
                    components['macd'] = 10  # positive but converging
            else:
                if macd_prev is not None and macd_hist > macd_prev:
                    components['macd'] = 5   # negative but recovering
                else:
                    components['macd'] = 0
        else:
            components['macd'] = 7           # no data → neutral

        # ── 4. VOLUME (20 pts) ─────────────────────────────────────────────────
        vol_ratio = tech.get('volume_ratio', 1.0)   # current / 20d avg
        if vol_ratio >= 3.0:
            components['volume'] = 20
        elif vol_ratio >= 2.0:
            components['volume'] = 16
        elif vol_ratio >= 1.5:
            components['volume'] = 12
        elif vol_ratio >= 1.0:
            components['volume'] = 8
        elif vol_ratio >= 0.7:
            components['volume'] = 4
        else:
            components['volume'] = 0    # volume collapsing

        # ── 5. SENTIMENT (10 pts) ──────────────────────────────────────────────
        sent_score = senti.get('score', 0.0)   # −1 to 1
        news_count = senti.get('news_count', 0)
        if news_count == 0:
            components['sentiment'] = 5     # no news → neutral
        else:
            # map −1..1 → 0..10
            components['sentiment'] = max(0, min(10, int((sent_score + 1) / 2 * 10)))

        # ── 6. MARKET REGIME (10 pts) ──────────────────────────────────────────
        regime_upper = str(regime).upper()
        if regime_upper == 'BULL':
            components['regime'] = 10
        elif regime_upper == 'SIDEWAYS':
            components['regime'] = 6
        elif regime_upper == 'BEAR':
            components['regime'] = 0
        else:
            components['regime'] = 5

        # ── 7. SECTOR STRENGTH (10 pts) ────────────────────────────────────────
        # sector_momentum: +1 = sector 10%+ outperforming index; −1 = underperforming
        pts = max(0, min(10, int((sector_momentum + 1) / 2 * 10)))
        components['sector'] = pts

        # ── Total score ────────────────────────────────────────────────────────
        total = sum(components.values())
        total = max(0, min(100, total))

        # ── Size fraction from score ────────────────────────────────────────────
        if total >= SCORE_FULL:
            size_fraction = 1.00        # ≥80 → full position
        elif total >= SCORE_75PCT:
            size_fraction = 0.75        # 75–79 → 75%
        elif total >= SCORE_50PCT:
            size_fraction = 0.50        # 70–74 → 50%
        elif total >= SCORE_25PCT:
            size_fraction = 0.25        # 65–69 → 25%
        else:
            size_fraction = 0.00        # <65 → skip

        skip = size_fraction == 0.0

        result = {
            'total_score':    total,
            'components':     components,
            'size_fraction':  size_fraction,
            'skip':           skip,
            'grade':          self._grade(total),
        }

        logger.info(
            f"TradeScore {signal.get('symbol','?')}: {total}/100 "
            f"({self._grade(total)}) → size {size_fraction*100:.0f}% "
            f"| {components}"
        )
        return result

    @staticmethod
    def _grade(score: float) -> str:
        if score >= 90: return 'A+'
        if score >= 80: return 'A'
        if score >= 70: return 'B'
        if score >= 60: return 'C'
        return 'D'

    @staticmethod
    def extract_tech_extras(technical_analysis: Dict) -> Dict:
        """
        Pull extra fields that TechnicalAnalyzer.generate_signals() returns so
        TradeScorer can use them. Call this after generate_signals().
        These keys are added to the research['technical_analysis'] dict.
        """
        return {
            'rsi':                   technical_analysis.get('rsi', 50),
            'macd_histogram':        technical_analysis.get('macd_histogram', None),
            'macd_histogram_prev':   technical_analysis.get('macd_histogram_prev', None),
            'volume_ratio':          technical_analysis.get('volume_ratio', 1.0),
            'trend':                 technical_analysis.get('trend', 'NEUTRAL'),
        }
