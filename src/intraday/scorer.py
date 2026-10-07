"""
Intraday Scoring Engine Module

Calculates intraday signal scores (0-100).
Completely separate from Swing TradeScorer.
"""
import logging
from typing import Dict, Any, Optional

from .models import (
    IntradaySignal, IntradayConfig, MarketRegime,
    SignalDirection, SignalClassification
)

logger = logging.getLogger(__name__)


class IntradayScorer:
    """Calculates intraday signal scores."""
    
    def __init__(self, config: IntradayConfig):
        """
        Initialize scorer.
        
        Args:
            config: IntradayConfig instance
        """
        self.config = config
    
    def calculate_score(self, symbol_data: Dict[str, Any], 
                       market_regime: MarketRegime) -> Optional[IntradaySignal]:
        """
        Calculate intraday signal score.
        
        Args:
            symbol_data: Symbol data with features
            market_regime: Current market regime
            
        Returns:
            IntradaySignal or None
        """
        try:
            features = symbol_data.get('features', {})
            if not features:
                logger.warning(f"[INTRADAY SCORER] No features for {symbol_data.get('symbol')}")
                return None
            
            symbol = symbol_data['symbol']
            current_price = features.get('current_price', 0)
            vwap = features.get('vwap', 0)
            ema_9 = features.get('ema_9', 0)
            ema_20 = features.get('ema_20', 0)
            rsi = features.get('rsi', 50)
            atr = features.get('atr', 0)
            or_high = features.get('or_high', 0)
            or_low = features.get('or_low', 0)
            momentum = features.get('momentum', 0)
            relative_volume = features.get('relative_volume', 1.0)
            ema_alignment = features.get('ema_alignment', 'NEUTRAL')
            vwap_structure = features.get('vwap_structure', 'AT_VWAP')
            
            # Calculate component scores
            vwap_score = self._calculate_vwap_score(vwap_structure, current_price, vwap)
            ema_score = self._calculate_ema_score(ema_alignment, ema_9, ema_20)
            volume_score = self._calculate_volume_score(relative_volume)
            momentum_score = self._calculate_momentum_score(momentum, rsi)
            breakout_score = self._calculate_breakout_score(current_price, or_high, or_low)
            market_regime_score = self._calculate_market_regime_score(market_regime)
            
            # Calculate weighted total score
            total_score = (
                vwap_score * (self.config.vwap_structure_weight / 100) +
                ema_score * (self.config.trend_ema_weight / 100) +
                volume_score * (self.config.volume_confirmation_weight / 100) +
                momentum_score * (self.config.momentum_weight / 100) +
                breakout_score * (self.config.breakout_quality_weight / 100) +
                market_regime_score * (self.config.market_regime_weight / 100)
            )
            
            # Determine direction
            direction = self._determine_direction(ema_alignment, vwap_structure, momentum)
            
            # Determine classification
            classification = self._classify_signal(total_score)
            
            # Calculate entry, stop loss, target
            entry, stop_loss, target = self._calculate_levels(
                current_price, atr, direction, or_high, or_low
            )
            risk_reward = self._calculate_risk_reward(entry, stop_loss, target)
            
            # Create signal
            signal = IntradaySignal(
                symbol=symbol,
                direction=direction,
                score=round(total_score, 1),
                classification=classification,
                entry_price=entry,
                stop_loss=stop_loss,
                target=target,
                risk_reward=risk_reward,
                vwap_score=vwap_score,
                ema_score=ema_score,
                volume_score=volume_score,
                momentum_score=momentum_score,
                breakout_score=breakout_score,
                market_regime_score=market_regime_score,
                market_regime=market_regime,
                current_price=current_price,
                vwap=vwap,
                ema_9=ema_9,
                ema_20=ema_20,
                rsi=rsi,
                volume=features.get('volume', 0),
                relative_volume=relative_volume,
                validation_passed=True,
                strategy="ORB" if breakout_score > 70 else "VWAP_MOMENTUM"
            )
            
            logger.info(f"[INTRADAY SCORER] {symbol}: Score={total_score:.1f}, Direction={direction.value}")
            
            return signal
            
        except Exception as e:
            logger.error(f"[INTRADAY SCORER] Scoring error: {e}")
            return None
    
    def _calculate_vwap_score(self, vwap_structure: str, 
                             price: float, vwap: float) -> float:
        """Calculate VWAP structure score."""
        if vwap_structure == "ABOVE_VWAP":
            return 85.0
        elif vwap_structure == "BELOW_VWAP":
            return 15.0
        else:
            return 50.0
    
    def _calculate_ema_score(self, ema_alignment: str, 
                             ema_short: float, ema_long: float) -> float:
        """Calculate EMA alignment score."""
        if ema_alignment == "BULLISH":
            return 85.0
        elif ema_alignment == "BEARISH":
            return 15.0
        else:
            return 50.0
    
    def _calculate_volume_score(self, relative_volume: float) -> float:
        """Calculate volume confirmation score."""
        if relative_volume >= 2.0:
            return 90.0
        elif relative_volume >= 1.5:
            return 75.0
        elif relative_volume >= 1.0:
            return 60.0
        else:
            return 30.0
    
    def _calculate_momentum_score(self, momentum: float, rsi: float) -> float:
        """Calculate momentum score."""
        # Positive momentum with reasonable RSI
        if momentum > 0.5 and 40 < rsi < 70:
            return 85.0
        elif momentum > 0:
            return 70.0
        elif momentum < -0.5:
            return 20.0
        else:
            return 50.0
    
    def _calculate_breakout_score(self, price: float, 
                                  or_high: float, or_low: float) -> float:
        """Calculate breakout quality score."""
        if or_high == 0 or or_low == 0:
            return 50.0
        
        or_range = or_high - or_low
        if or_range == 0:
            return 50.0
        
        # Check if price broke out
        if price > or_high:
            return 85.0
        elif price < or_low:
            return 15.0
        else:
            # Price within range
            position = (price - or_low) / or_range
            return 40 + (position * 20)  # 40-60 range
    
    def _calculate_market_regime_score(self, regime: MarketRegime) -> float:
        """Calculate market regime score."""
        if regime == MarketRegime.BULLISH:
            return 80.0
        elif regime == MarketRegime.BEARISH:
            return 30.0
        elif regime == MarketRegime.HIGH_VOLATILITY:
            return 40.0
        else:  # SIDEWAYS
            return 50.0
    
    def _determine_direction(self, ema_alignment: str, 
                            vwap_structure: str, momentum: float) -> SignalDirection:
        """Determine signal direction."""
        bullish_signals = 0
        bearish_signals = 0
        
        if ema_alignment == "BULLISH":
            bullish_signals += 1
        elif ema_alignment == "BEARISH":
            bearish_signals += 1
        
        if vwap_structure == "ABOVE_VWAP":
            bullish_signals += 1
        elif vwap_structure == "BELOW_VWAP":
            bearish_signals += 1
        
        if momentum > 0:
            bullish_signals += 1
        elif momentum < 0:
            bearish_signals += 1
        
        if bullish_signals > bearish_signals:
            return SignalDirection.LONG
        elif bearish_signals > bullish_signals:
            return SignalDirection.SHORT
        else:
            return SignalDirection.LONG  # Default to LONG
    
    def _classify_signal(self, score: float) -> SignalClassification:
        """Classify signal based on score."""
        if score >= self.config.strong_setup_threshold:
            return SignalClassification.STRONG_SETUP
        elif score >= self.config.good_setup_threshold:
            return SignalClassification.GOOD_SETUP
        elif score >= self.config.watch_threshold:
            return SignalClassification.WATCH
        else:
            return SignalClassification.NO_TRADE
    
    def _calculate_levels(self, price: float, atr: float, 
                         direction: SignalDirection,
                         or_high: float, or_low: float) -> tuple:
        """Calculate entry, stop loss, target."""
        entry = price
        
        if atr == 0:
            atr = price * 0.01  # Default 1% if ATR unavailable
        
        if direction == SignalDirection.LONG:
            stop_loss = price - (atr * 0.5)  # 0.5 ATR SL
            target = price + (atr * 1.5)  # 1.5 ATR target
        else:
            stop_loss = price + (atr * 0.5)
            target = price - (atr * 1.5)
        
        return entry, stop_loss, target
    
    def _calculate_risk_reward(self, entry: float, stop_loss: float, 
                              target: float) -> float:
        """Calculate risk:reward ratio."""
        risk = abs(entry - stop_loss)
        reward = abs(target - entry)
        
        if risk == 0:
            return 0.0
        
        return round(reward / risk, 2)
