"""
Enterprise AI Decision Engine (Score Engine v2)
Replaces the single rule-based score with a weighted, explainable,
regime-aware, adaptive multi-factor decision engine.
"""
import logging
from datetime import datetime
from typing import Dict, List, Optional
import numpy as np

from trade_journal import TradeJournal
from market_intelligence import MarketBreadthEngine
from sector_rotation import SectorRotationEngine
from fii_dii import FII_DII_Engine
from options_intelligence import OptionsIntelligenceEngine
from config import config

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class EnterpriseAIDecisionEngine:
    """
    Master AI decision engine.

    Architecture
    ------------
    Scanner -> Technical AI -> Fundamental AI -> Sector AI -> Market AI
    -> Liquidity AI -> Position AI -> Master Decision Engine -> Risk Engine
    -> Lifecycle

    Outputs:
        - per-factor scores (0-100)
        - weighted final score (0-100)
        - dynamic BUY threshold by market regime
        - confidence (%)
        - AI explain
        - adaptive weights updated from trade journal
    """

    # Base weights per user specification
    DEFAULT_WEIGHTS = {
        'technical': 0.25,
        'sector': 0.20,
        'market': 0.15,
        'liquidity': 0.10,
        'relative_strength': 0.15,
        'volume_profile': 0.10,
        'historical_success': 0.05,
    }

    # Dynamic threshold defaults by regime
    DEFAULT_THRESHOLDS = {
        'BULL': 65,
        'SIDEWAYS': 75,
        'BEAR': 90,
    }

    # Sub-technical factors that can be learned
    TECHNICAL_FACTORS = ['rsi', 'macd', 'ema', 'vwap', 'support', 'resistance']

    def __init__(self, market_data=None, trade_journal: Optional[TradeJournal] = None):
        self.market_data = market_data
        self.trade_journal = trade_journal or TradeJournal()
        self.breadth_engine = MarketBreadthEngine(market_data=self.market_data)
        self.sector_rotation = SectorRotationEngine(market_data=self.market_data)
        self.fii_dii_engine = FII_DII_Engine(market_data=self.market_data)
        self.options_intelligence = OptionsIntelligenceEngine(market_data=self.market_data)
        self.weights = dict(self.DEFAULT_WEIGHTS)
        self.feature_success = {}
        self._load_adaptive_weights()

    # ── Public API ───────────────────────────────────────────────────────────

    def compute_scores(
        self,
        symbol: str,
        current_price: float,
        hist,
        research: Dict,
    ) -> Dict:
        """
        Compute the complete Enterprise AI Score for a symbol.

        Returns dict with:
            sub_scores, confidences, final_score, final_confidence,
            threshold, recommendation, explain, explain_text, weights_used,
            score_components, technical_factors
        """
        market_regime = research.get('market_regime', 'UNKNOWN')

        # 1. Sub-scores (each 0-100)
        sub_scores = {}
        confidences = {}

        sub_scores['technical'], confidences['technical'], tech_factors = \
            self._technical_score(symbol, current_price, hist, research)
        sub_scores['market'], confidences['market'] = \
            self._market_score(research, market_regime)
        sub_scores['sector'], confidences['sector'] = \
            self._sector_score(research)
        sub_scores['liquidity'], confidences['liquidity'] = \
            self._liquidity_score(symbol, current_price, hist)
        sub_scores['relative_strength'], confidences['relative_strength'] = \
            self._relative_strength_score(symbol, current_price, hist)
        sub_scores['volume_profile'], confidences['volume_profile'] = \
            self._volume_profile_score(symbol, current_price, hist)
        sub_scores['historical_success'], confidences['historical_success'] = \
            self._historical_success_score(symbol)

        # 2. Final weighted score
        final_score = 0.0
        for k, v in sub_scores.items():
            final_score += v * self.weights.get(k, self.DEFAULT_WEIGHTS.get(k, 0.0))
        final_score = round(max(0.0, min(100.0, final_score)), 2)

        # 3. Market breadth adjustment
        breadth = self.breadth_engine.adjust_ai_score(final_score)
        final_score = round(breadth['adjusted_score'], 2)

        # 4. Sector rotation adjustment
        sector = research.get('sector', 'Other')
        sector_adj = self.sector_rotation.adjust_ai_score(final_score, sector)
        final_score = round(sector_adj['adjusted_score'], 2)

        # 5. FII/DII institutional flow adjustment
        fii_dii = self.fii_dii_engine.adjust_ai_score(final_score)
        final_score = round(fii_dii['adjusted_score'], 2)

        # 6. Confidence
        final_confidence = self._compute_confidence(sub_scores, confidences)

        # 6a. Options-chain confidence boost
        options_adj = self.options_intelligence.adjust_confidence(final_confidence)
        final_confidence = round(options_adj['adjusted_confidence'], 2)

        # 7. Dynamic threshold
        threshold = self._dynamic_threshold(market_regime)

        # 8. Decision
        action = self._determine_action(final_score, threshold, final_confidence)

        # 9. Explain
        explain = self._build_explain(
            symbol, sub_scores, confidences, final_score, final_confidence,
            threshold, action, self.weights, tech_factors, breadth, sector_adj, fii_dii, options_adj
        )

        score_components = dict(sub_scores)
        score_components.update(tech_factors)
        score_components['breadth_score'] = breadth['breadth_score']
        score_components['breadth_adjustment'] = breadth['adjustment']
        score_components['market_strength'] = breadth['market_strength']
        score_components['sector_momentum_score'] = sector_adj['momentum_score']
        score_components['sector_rank'] = sector_adj['rank']
        score_components['sector_adjustment'] = sector_adj['adjustment']
        score_components['fii_dii_net_flow'] = fii_dii['net_flow']
        score_components['fii_dii_sentiment'] = fii_dii['sentiment']
        score_components['fii_dii_adjustment'] = fii_dii['adjustment']
        score_components['options_pcr'] = options_adj['pcr']
        score_components['options_max_pain'] = options_adj['max_pain']
        score_components['options_long_buildup'] = options_adj['long_buildup']
        score_components['options_short_buildup'] = options_adj['short_buildup']
        score_components['options_strong_oi_support'] = options_adj['strong_oi_support']
        score_components['options_confidence_boost'] = options_adj['confidence_boost']

        return {
            'symbol': symbol,
            'sub_scores': sub_scores,
            'confidences': confidences,
            'technical_factors': tech_factors,
            'final_score': final_score,
            'final_confidence': round(final_confidence, 2),
            'threshold': threshold,
            'regime_threshold': threshold,
            'recommendation': action,
            'action': action,
            'explain': explain,
            'explain_text': explain['text'],
            'weights_used': dict(self.weights),
            'score_components': score_components,
        }

    def update_weights_from_journal(self):
        """Re-compute adaptive weights from completed trade history."""
        try:
            trades = self.trade_journal._load()
            component_stats = {}

            for t in trades:
                if t.get('action') != 'BUY' or t.get('status') != 'CLOSED':
                    continue
                pnl = float(t.get('net_pnl') or 0)
                win = pnl > 0
                components = t.get('score_components', {}) or {}
                for feature, value in components.items():
                    # Track trades where this feature contributed positively
                    feature_key = str(feature).lower().replace(' ', '_')
                    if feature_key not in component_stats:
                        component_stats[feature_key] = {'wins': 0, 'total': 0, 'score_sum': 0.0}
                    component_stats[feature_key]['total'] += 1
                    component_stats[feature_key]['score_sum'] += float(value)
                    if win:
                        component_stats[feature_key]['wins'] += 1

            if not component_stats:
                return

            # Success rate per tracked component
            self.feature_success = {
                k: round(v['wins'] / v['total'] * 100, 1) if v['total'] else 0
                for k, v in component_stats.items()
            }

            # Adjust main category weights
            category_names = set(self.DEFAULT_WEIGHTS.keys())
            category_success = {k: [] for k in category_names}

            # Map components to main categories
            mapping = {
                'technical': ['technical', 'rsi', 'macd', 'ema', 'vwap', 'support', 'resistance'],
                'sector': ['sector', 'sector_momentum'],
                'market': ['market', 'market_regime'],
                'liquidity': ['liquidity'],
                'relative_strength': ['relative_strength', 'momentum', 'rs'],
                'volume_profile': ['volume', 'volume_profile', 'volume_ratio', 'obv'],
                'historical_success': ['historical', 'historical_success'],
            }

            for cat, keys in mapping.items():
                for comp, stats in component_stats.items():
                    if comp in keys:
                        success = stats['wins'] / stats['total'] if stats['total'] else 0.5
                        category_success[cat].append(success)

            new_weights = {}
            raw = []
            for cat in category_names:
                avg_success = sum(category_success[cat]) / len(category_success[cat]) if category_success[cat] else 0.5
                # Base weight scaled by performance; 0.5 neutral => no change
                adj = self.DEFAULT_WEIGHTS[cat] * (0.5 + avg_success)
                new_weights[cat] = adj
                raw.append(adj)

            # Normalize to sum 1.0
            total = sum(raw)
            if total > 0:
                self.weights = {k: round(v / total, 3) for k, v in new_weights.items()}
            logger.info(f"Adaptive weights updated: {self.weights}")
        except Exception as e:
            logger.warning(f"Adaptive weight update failed: {e}")

    # ── Sub-scoring engines ──────────────────────────────────────────────────

    def _technical_score(self, symbol, current_price, hist, research) -> tuple:
        """
        Technical AI: uses research technical score when available,
        otherwise computes a fallback from price data.
        """
        tech_score = float(research.get('technical_score', 0))
        if not tech_score and hist is not None and not hist.empty:
            try:
                close = hist['Close']
                # EMA alignment
                ema20 = close.ewm(span=20).mean().iloc[-1]
                ema50 = close.ewm(span=50).mean().iloc[-1]
                above_ema = float(close.iloc[-1] > ema20 > ema50)
                # Momentum
                ret_5d = float(close.iloc[-1] / close.iloc[-6] - 1) if len(close) >= 6 else 0.0
                # Volatility filter
                atr = self._atr(hist)
                atr_pct = atr / current_price if current_price else 0
                # Score
                score = 50.0
                score += 20 * above_ema
                score += min(20, max(-20, ret_5d * 200))
                score -= max(0, (atr_pct - 0.04) * 200)
                tech_score = round(max(0, min(100, score)), 2)
            except Exception as e:
                logger.debug(f"Technical score fallback failed for {symbol}: {e}")
                tech_score = 50.0

        # Sub-factor map for learning + explain
        ta = research.get('technical_analysis', {})
        factors = {
            'rsi': float(ta.get('rsi', 0) or 50),
            'macd': float(ta.get('macd', 0) or 0),
            'ema': 1.0 if ta.get('above_ema', False) else 0.0,
            'vwap': 1.0 if ta.get('above_vwap', False) else 0.0,
            'support': float(ta.get('support', 0)),
            'resistance': float(ta.get('resistance', 0)),
        }

        confidence = 0.85 if 'technical_score' in research else 0.65
        return float(tech_score or 50.0), confidence, factors

    def _market_score(self, research, market_regime) -> tuple:
        """Market AI: translate regime into a directional score."""
        regime = (market_regime or 'UNKNOWN').upper()
        if regime == 'BULL':
            score = 85.0
        elif regime == 'SIDEWAYS':
            score = 60.0
        elif regime == 'BEAR':
            score = 40.0
        else:
            score = 55.0

        # Adjust if research gives explicit market_score
        explicit = float(research.get('market_score', 0))
        if explicit:
            score = 0.6 * score + 0.4 * explicit
        return round(score, 2), 0.80

    def _sector_score(self, research) -> tuple:
        """Sector AI: based on sector momentum / relative strength."""
        score = float(research.get('sector_momentum', 0))
        if not score:
            # fallback
            score = 55.0
        else:
            score = float(score) * 100  # if stored as 0-1
        return round(max(0, min(100, score)), 2), 0.75

    def _liquidity_score(self, symbol, current_price, hist) -> tuple:
        """Liquidity AI: average daily turnover and spread proxy."""
        try:
            if hist is None or hist.empty or 'Volume' not in hist.columns:
                return 70.0, 0.70
            avg_volume = float(hist['Volume'].tail(20).mean())
            turnover = avg_volume * current_price
            if turnover > 50_00_00_000:  # > 50 Cr
                score = 95.0
            elif turnover > 10_00_00_000:
                score = 85.0
            elif turnover > 2_00_00_000:
                score = 70.0
            else:
                score = 50.0
            return round(score, 2), 0.80
        except Exception as e:
            logger.debug(f"Liquidity score failed for {symbol}: {e}")
            return 70.0, 0.70

    def _relative_strength_score(self, symbol, current_price, hist) -> tuple:
        """Relative Strength AI: 20-day momentum proxy."""
        try:
            if hist is None or hist.empty or len(hist) < 21:
                return 50.0, 0.65
            close = hist['Close']
            ret = float(close.iloc[-1] / close.iloc[-21] - 1)
            score = 50.0 + (ret * 400)  # 10% move = +40 points
            score = max(0, min(100, score))
            return round(score, 2), 0.75
        except Exception as e:
            logger.debug(f"RS score failed for {symbol}: {e}")
            return 50.0, 0.65

    def _volume_profile_score(self, symbol, current_price, hist) -> tuple:
        """Volume Profile AI: volume trend and OBV direction."""
        try:
            if hist is None or hist.empty or 'Volume' not in hist.columns:
                return 50.0, 0.65
            vol = hist['Volume']
            close = hist['Close']
            avg_vol = vol.tail(20).mean()
            today_vol = vol.iloc[-1]
            vol_ratio = today_vol / avg_vol if avg_vol else 1.0

            # OBV
            obv = [0]
            for i in range(1, len(close)):
                if close.iloc[i] > close.iloc[i - 1]:
                    obv.append(obv[-1] + vol.iloc[i])
                elif close.iloc[i] < close.iloc[i - 1]:
                    obv.append(obv[-1] - vol.iloc[i])
                else:
                    obv.append(obv[-1])
            obv_trend = 1 if obv[-1] > obv[-5] else -1 if obv[-1] < obv[-5] else 0

            score = 50.0
            score += min(25, max(-25, (vol_ratio - 1.0) * 25))
            score += obv_trend * 15
            score = max(0, min(100, score))
            return round(score, 2), 0.75
        except Exception as e:
            logger.debug(f"Volume profile score failed for {symbol}: {e}")
            return 50.0, 0.65

    def _historical_success_score(self, symbol) -> tuple:
        """Historical AI Success: symbol-specific win rate."""
        try:
            trades = self.trade_journal._load()
            sym_trades = [t for t in trades if t.get('symbol') == symbol and t.get('action') == 'BUY' and t.get('status') == 'CLOSED']
            if not sym_trades:
                # Engine-wide win rate
                all_buys = [t for t in trades if t.get('action') == 'BUY' and t.get('status') == 'CLOSED']
                if not all_buys:
                    return 50.0, 0.60
                wins = sum(1 for t in all_buys if float(t.get('net_pnl') or 0) > 0)
                return round(wins / len(all_buys) * 100, 2), 0.75

            wins = sum(1 for t in sym_trades if float(t.get('net_pnl') or 0) > 0)
            return round(wins / len(sym_trades) * 100, 2), 0.85
        except Exception as e:
            logger.debug(f"Historical score failed for {symbol}: {e}")
            return 50.0, 0.60

    # ── Confidence + Threshold + Decision ────────────────────────────────────

    def _compute_confidence(self, sub_scores: Dict, confidences: Dict) -> float:
        """
        Confidence is a blend of:
            - agreement between sub-scores (low std = higher confidence)
            - per-factor data quality confidences
        """
        values = np.array(list(sub_scores.values()), dtype=float)
        mean = float(np.mean(values))
        std = float(np.std(values))

        # Agreement: perfect agreement when all equal => 1.0
        agreement = max(0.0, 1.0 - std / 50.0) if mean > 0 else 0.0

        # Data quality
        data_conf = float(np.mean(list(confidences.values())))

        # Final: 60% agreement, 40% data quality
        final = 0.6 * agreement + 0.4 * data_conf
        return round(max(0.0, min(1.0, final)) * 100, 2)

    def _dynamic_threshold(self, market_regime: str) -> float:
        """Regime-aware BUY threshold."""
        regime = (market_regime or 'UNKNOWN').upper()
        key = f"SCORE_{regime}_THRESHOLD"
        if hasattr(config, key):
            return float(getattr(config, key))
        return float(self.DEFAULT_THRESHOLDS.get(regime, 70))

    def _determine_action(self, final_score: float, threshold: float, confidence: float) -> str:
        if final_score >= threshold and confidence >= config.MIN_CONFIDENCE_BULL * 100:
            return 'BUY'
        elif final_score >= threshold * 0.95 and confidence >= config.MIN_CONFIDENCE_BULL * 100:
            return 'STRONG_HOLD'
        return 'HOLD'

    # ── Explain ──────────────────────────────────────────────────────────────

    def _build_explain(
        self,
        symbol: str,
        sub_scores: Dict,
        confidences: Dict,
        final_score: float,
        final_confidence: float,
        threshold: float,
        action: str,
        weights: Dict,
        tech_factors: Dict,
        breadth: Optional[Dict] = None,
        sector: Optional[Dict] = None,
        fii_dii: Optional[Dict] = None,
        options: Optional[Dict] = None,
    ) -> Dict:
        """Build a human-readable AI explain."""
        lines = [f"{symbol}: {action} | Final {final_score} (threshold {threshold}) | Confidence {final_confidence}%"]
        for k, v in sub_scores.items():
            w = round(weights.get(k, 0) * 100, 1)
            c = confidences.get(k, 0) * 100
            lines.append(f"  {k.replace('_', ' ').title():20s} {v:6.2f}  (weight {w:5.1f}%, confidence {c:5.1f}%)")

        if breadth:
            b = breadth
            sign = '+' if b.get('adjustment', 0) >= 0 else ''
            lines.append(
                f"  {'Market Breadth':20s} {b['breadth_score']:6.2f}  ("
                f"{b['market_strength']}, {sign}{b['adjustment']} score)"
            )

        if sector:
            s = sector
            sign = '+' if s.get('adjustment', 0) >= 0 else ''
            lines.append(
                f"  {'Sector Rotation':20s} {s['momentum_score']:6.2f}  ("
                f"rank {s['rank']}, {s['sector']}, {sign}{s['adjustment']} score)"
            )

        if fii_dii:
            f = fii_dii
            sign = '+' if f.get('adjustment', 0) >= 0 else ''
            lines.append(
                f"  {'FII/DII Flow':20s} {f['net_flow']:6.2f}  ("
                f"{f['sentiment']}, {sign}{f['adjustment']} score)"
            )

        if options:
            o = options
            flags = []
            if o.get('pcr', 1.0) > 1.0:
                flags.append('PCR>1')
            if o.get('long_buildup'):
                flags.append('Long Build-up')
            if o.get('strong_oi_support'):
                flags.append('OI Support')
            flag_str = ', '.join(flags) if flags else 'neutral'
            lines.append(
                f"  {'Options':20s} {o['pcr']:6.2f}  ("
                f"max pain {o['max_pain']}, {flag_str}, +{o['confidence_boost']}% confidence)"
            )

        if tech_factors:
            lines.append("  Technical factors:")
            for fk, fv in tech_factors.items():
                lines.append(f"    - {fk}: {fv}")

        text = '\n'.join(lines)
        logger.info(text)
        return {
            'text': text,
            'summary': lines[0],
            'details': {k: {'score': v, 'weight': weights.get(k, 0), 'confidence': confidences.get(k, 0)}
                        for k, v in sub_scores.items()},
            'technical_factors': tech_factors,
            'breadth': breadth or {},
        }

    # ── Helpers ──────────────────────────────────────────────────────────────

    def _load_adaptive_weights(self):
        """Initialise adaptive weights from journal at startup."""
        try:
            self.update_weights_from_journal()
        except Exception:
            pass

    @staticmethod
    def _atr(hist) -> float:
        try:
            high = hist['High']
            low = hist['Low']
            close = hist['Close']
            tr1 = high - low
            tr2 = (high - close.shift(1)).abs()
            tr3 = (low - close.shift(1)).abs()
            tr = np.maximum(np.maximum(tr1, tr2), tr3)
            return float(tr.tail(14).mean())
        except Exception:
            return 0.0
