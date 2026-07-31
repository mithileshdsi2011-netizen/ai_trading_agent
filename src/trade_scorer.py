"""
Trade Scoring Engine — Conservative Philosophy
Prioritises capital preservation, high-quality setups and lower drawdowns.
Score determines position sizing:
  ≥80  → full position (100%)
  75–79 → 75%
  70–74 → 50%
  65–69 → 22%  (reduced exposure on lower-confidence trades)
  <65  → skip
Quality over quantity: only strong trend + volume confirmation executes.
"""
from typing import Dict, Optional
import logging
import pandas as pd

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ── Component weights (must sum to 100) ───────────────────────────────────────
# Conservative: trend (25) + volume (20) are primary quality gates.
# Sentiment/sector are supplementary — lower weight to avoid noise.
WEIGHTS = {
    'trend':        25,   # Daily trend direction — primary quality gate
    'rsi':          15,   # RSI position
    'macd':         15,   # MACD histogram direction
    'volume':       20,   # Volume confirmation — must back the move
    'sentiment':     8,   # News sentiment (reduced — noisy)
    'regime':       10,   # Market regime
    'sector':        7,   # Sector strength (reduced — supplementary)
}

# Regime-dependent trend points — demand higher quality in choppy/weak markets
TREND_POINTS = {
    'BULL': {
        'STRONG_UPTREND': 25, 'BULLISH': 25, 'UPTREND': 18,
        'NEUTRAL': 0, 'DOWNTREND': 0, 'BEARISH': 0, 'STRONG_DOWNTREND': 0,
    },
    'SIDEWAYS': {
        'STRONG_UPTREND': 15, 'BULLISH': 15, 'UPTREND': 10,
        'NEUTRAL': 0, 'DOWNTREND': 0, 'BEARISH': 0, 'STRONG_DOWNTREND': 0,
    },
    'BEAR': {
        'STRONG_UPTREND': 8, 'BULLISH': 8, 'UPTREND': 5,
        'NEUTRAL': 0, 'DOWNTREND': 0, 'BEARISH': 0, 'STRONG_DOWNTREND': 0,
    },
}

# ── Score thresholds ───────────────────────────────────────────────────────────
SCORE_FULL     = 80   # 100% position
SCORE_75PCT    = 75   # 75% position
SCORE_50PCT    = 70   # 50% position
SCORE_25PCT    = 65   # 20–25% position
SCORE_SKIP     = 60   # Below 60 → skip

# Regime-adjusted skip thresholds — conservative: quality over quantity
SCORE_SKIP_BULL      = 62   # Bull: still require solid setup
SCORE_SKIP_SIDEWAYS  = 58   # Sideways: only clear breakouts
SCORE_SKIP_BEAR      = 80   # Bear: almost never buy


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
        mtf_aligned: bool = True,
    ) -> Dict:
        """
        Score a trade signal.

        Args:
            signal:           Output of SignalGenerator.generate_signal()
            research:         Output of AIResearchAgent.research_stock()
            regime:           Market regime string: BULL / BEAR / SIDEWAYS
            sector_momentum:  Float −1..1; positive = sector is stronger than index
            mtf_aligned:      Multi-timeframe alignment flag (default True)

        Returns:
            Dict with keys: total_score, components, size_fraction, skip
        """
        tech   = research.get('technical_analysis', {})
        senti  = research.get('sentiment_analysis', {})
        hist   = research.get('_historical_df')   # optional, set by agent if passed

        components: Dict[str, float] = {}

        # ── 1. TREND — regime-dependent quality gate ───────────────────────────
        regime_upper = str(regime).upper()
        regime_key = regime_upper if regime_upper in TREND_POINTS else 'SIDEWAYS'
        trend = tech.get('trend', 'NEUTRAL')
        components['trend'] = TREND_POINTS[regime_key].get(trend, 0)

        # In SIDEWAYS, a strong daily trend without MTF confirmation is suspect
        if regime_key == 'SIDEWAYS' and not mtf_aligned and components['trend'] > 0:
            components['trend'] //= 2

        # ── 2. RSI (15 pts) ────────────────────────────────────────────────────
        rsi = tech.get('rsi', 50)
        if isinstance(rsi, float) and pd.notna(rsi):
            if 40 <= rsi <= 60:          # neutral zone — trending
                components['rsi'] = 12
            elif 30 <= rsi < 40:         # recovering from oversold
                components['rsi'] = 15
            elif rsi < 30:               # oversold bounce
                components['rsi'] = 13
            elif 60 < rsi <= 65:         # mild overbought — still acceptable
                components['rsi'] = 8
            elif 65 < rsi <= 70:         # upper overbought — penalise
                components['rsi'] = 3
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

        # ── 4. VOLUME (20 pts) — hard gate: no volume = no trade ────────────
        vol_ratio = tech.get('volume_ratio', 1.0)   # current / 20d avg
        if vol_ratio >= 2.5:
            components['volume'] = 20
        elif vol_ratio >= 1.8:
            components['volume'] = 16
        elif vol_ratio >= 1.3:
            components['volume'] = 12
        elif vol_ratio >= 1.0:
            components['volume'] = 8
        elif vol_ratio >= 0.7:
            components['volume'] = 3
        else:
            components['volume'] = 0    # volume collapsing — hard skip signal below

        # ── 5. SENTIMENT (8 pts) ───────────────────────────────────────────────
        sent_score = senti.get('score', 0.0)   # −1 to 1
        news_count = senti.get('news_count', 0)
        if news_count == 0:
            components['sentiment'] = 0     # no news → no credit
        else:
            # map −1..1 → 0..8
            components['sentiment'] = max(0, min(8, int((sent_score + 1) / 2 * 8)))

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
# ── 7. SECTOR STRENGTH (7 pts) ─────────────────────────────────────────
        # sector_momentum: +1 = sector 10%+ outperforming index; −1 = underperforming
        pts = max(0, min(7, int((sector_momentum + 1) / 2 * 7)))
        components['sector'] = pts

        # ── Total score ────────────────────────────────────────────────────────
        total = sum(components.values())
        total = max(0, min(100, total))

        # ── Hard veto: no trend + low volume = always skip ─────────────────────
        if components['trend'] == 0 and components['volume'] < 8:
            total = min(total, 50)   # cap below any skip threshold

        # ── Regime-adjusted skip threshold ─────────────────────────────────────
        regime_upper = str(regime).upper()
        if regime_upper == 'BULL':
            effective_skip = SCORE_SKIP_BULL
        elif regime_upper == 'BEAR':
            effective_skip = SCORE_SKIP_BEAR
        else:  # SIDEWAYS or unknown
            effective_skip = SCORE_SKIP_SIDEWAYS

        # ── Size fraction from score ────────────────────────────────────────────
        if total >= SCORE_FULL:
            size_fraction = 1.00        # ≥80 → 100%
        elif total >= SCORE_75PCT:
            size_fraction = 0.75        # 75–79 → 75%
        elif total >= SCORE_50PCT:
            size_fraction = 0.50        # 70–74 → 50%
        elif total >= SCORE_25PCT:
            size_fraction = 0.22        # 65–69 → 20–25%
        elif total >= effective_skip:
            size_fraction = 0.15        # regime floor (55–64) → small position
        else:
            size_fraction = 0.00        # below 60 (or regime floor) → skip

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
