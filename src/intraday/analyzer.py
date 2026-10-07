"""
Intraday AI Analyzer Module

Provides AI explanation for intraday signals.
AI has NO order authority - only explains signals.
"""
import logging

from .models import IntradaySignal

logger = logging.getLogger(__name__)


class IntradayAnalyzer:
    """AI analyzer for intraday signal explanation."""
    
    def __init__(self):
        """Initialize analyzer."""
        pass
    
    def explain_signal(self, signal: IntradaySignal) -> str:
        """
        Generate human-readable explanation for a signal.
        
        Args:
            signal: IntradaySignal instance
            
        Returns:
            Human-readable explanation
        """
        try:
            direction = signal.direction.value
            score = signal.score
            classification = signal.classification.value
            
            explanation_parts = [
                f"{signal.symbol} {direction} signal detected.",
                f"Score: {score}/100 ({classification})."
            ]
            
            # VWAP explanation
            if signal.vwap_score >= 70:
                explanation_parts.append(
                    f"Price ({signal.current_price:.2f}) is above VWAP ({signal.vwap:.2f}), "
                    f"indicating bullish structure."
                )
            elif signal.vwap_score <= 30:
                explanation_parts.append(
                    f"Price ({signal.current_price:.2f}) is below VWAP ({signal.vwap:.2f}), "
                    f"indicating bearish structure."
                )
            
            # EMA explanation
            if signal.ema_score >= 70:
                explanation_parts.append(
                    f"EMA alignment is bullish (EMA9: {signal.ema_9:.2f} > EMA20: {signal.ema_20:.2f})."
                )
            elif signal.ema_score <= 30:
                explanation_parts.append(
                    f"EMA alignment is bearish (EMA9: {signal.ema_9:.2f} < EMA20: {signal.ema_20:.2f})."
                )
            
            # Volume explanation
            if signal.volume_score >= 75:
                explanation_parts.append(
                    f"Volume is strong ({signal.relative_volume:.1f}x average), "
                    f"confirming the move."
                )
            
            # Momentum explanation
            if signal.momentum_score >= 70:
                explanation_parts.append(
                    f"Momentum is strong (score {signal.momentum_score:.0f}/100), "
                    f"supporting the direction."
                )
            
            # Market regime explanation
            explanation_parts.append(
                f"Market regime: {signal.market_regime.value}."
            )
            
            # Risk:Reward explanation
            explanation_parts.append(
                f"Risk:Reward is {signal.risk_reward}:1 "
                f"(Entry: {signal.entry_price:.2f}, SL: {signal.stop_loss:.2f}, Target: {signal.target:.2f})."
            )
            
            return " ".join(explanation_parts)
            
        except Exception as e:
            logger.error(f"[INTRADAY ANALYZER] Explanation error: {e}")
            return "Unable to generate explanation."
    
    def identify_conflicts(self, signal: IntradaySignal) -> list:
        """
        Identify conflicting factors in a signal.
        
        Args:
            signal: IntradaySignal instance
            
        Returns:
            List of conflicting factors
        """
        conflicts = []
        
        # VWAP vs EMA conflict
        if signal.vwap_score >= 70 and signal.ema_score <= 30:
            conflicts.append("Price above VWAP but EMA bearish - conflicting trend signals")
        elif signal.vwap_score <= 30 and signal.ema_score >= 70:
            conflicts.append("Price below VWAP but EMA bullish - conflicting trend signals")
        
        # Volume weakness
        if signal.volume_score <= 40:
            conflicts.append("Volume is weak - lack of confirmation")
        
        # Momentum divergence
        if signal.momentum_score <= 40 and signal.score >= 70:
            conflicts.append("Momentum is weak despite high score - potential divergence")
        
        # RSI extremes
        if signal.rsi >= 70:
            conflicts.append("RSI is overbought - potential reversal risk")
        elif signal.rsi <= 30:
            conflicts.append("RSI is oversold - potential reversal risk")
        
        return conflicts
    
    def describe_risk(self, signal: IntradaySignal) -> str:
        """
        Describe risk factors for a signal.
        
        Args:
            signal: IntradaySignal instance
            
        Returns:
            Risk description
        """
        risk_parts = []
        
        # Risk:Reward assessment
        if signal.risk_reward < 1.0:
            risk_parts.append("Risk:Reward is unfavorable (< 1:1).")
        elif signal.risk_reward < 1.5:
            risk_parts.append("Risk:Reward is moderate (< 1.5:1).")
        else:
            risk_parts.append("Risk:Reward is favorable (>= 1.5:1).")
        
        # Market regime risk
        if signal.market_regime.value in ["BEARISH", "HIGH_VOLATILITY"]:
            risk_parts.append(
                f"Market regime ({signal.market_regime.value}) adds risk."
            )
        
        # Score risk
        if signal.score < 60:
            risk_parts.append("Low score indicates weak setup quality.")
        
        return " ".join(risk_parts) if risk_parts else "No significant risk factors identified."
