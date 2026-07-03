"""
Technical Analysis Module
Performs technical analysis on stock data
"""
import pandas as pd
import numpy as np
from typing import Dict, List, Optional
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class TechnicalAnalyzer:
    """Performs technical analysis on stock data"""
    
    def __init__(self):
        pass
    
    def calculate_indicators(self, data: pd.DataFrame) -> pd.DataFrame:
        """
        Calculate all technical indicators
        
        Args:
            data: DataFrame with OHLCV data
        
        Returns:
            DataFrame with added indicators
        """
        df = data.copy()
        
        if df.empty or len(df) < 20:
            return df
        
        try:
            # Moving Averages
            df['SMA_20'] = df['Close'].rolling(window=20).mean()
            df['SMA_50'] = df['Close'].rolling(window=50).mean()
            df['EMA_12'] = df['Close'].ewm(span=12, adjust=False).mean()
            df['EMA_26'] = df['Close'].ewm(span=26, adjust=False).mean()
            
            # RSI
            delta = df['Close'].diff()
            gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
            rs = gain / loss
            df['RSI'] = 100 - (100 / (1 + rs))
            
            # MACD
            df['MACD'] = df['EMA_12'] - df['EMA_26']
            df['MACD_Signal'] = df['MACD'].ewm(span=9, adjust=False).mean()
            df['MACD_Hist'] = df['MACD'] - df['MACD_Signal']
            
            # Bollinger Bands
            df['BB_Middle'] = df['Close'].rolling(window=20).mean()
            bb_std = df['Close'].rolling(window=20).std()
            df['BB_Upper'] = df['BB_Middle'] + (bb_std * 2)
            df['BB_Lower'] = df['BB_Middle'] - (bb_std * 2)
            
            # ADX (Trend Strength) - Simplified version
            high_diff = df['High'].diff()
            low_diff = -df['Low'].diff()
            tr = pd.concat([high_diff, low_diff, df['Close'].diff()], axis=1).max(axis=1)
            atr = tr.rolling(window=14).mean()
            df['ADX'] = atr  # Simplified ADX using ATR
            
            # Stochastic Oscillator
            low_min = df['Low'].rolling(window=14).min()
            high_max = df['High'].rolling(window=14).max()
            df['SlowK'] = 100 * ((df['Close'] - low_min) / (high_max - low_min))
            df['SlowD'] = df['SlowK'].rolling(window=3).mean()
            
            # Volume indicators
            df['Volume_SMA'] = df['Volume'].rolling(window=20).mean()
            
            # Price changes
            df['Price_Change'] = df['Close'].pct_change()
            df['Price_Change_5'] = df['Close'].pct_change(5)
            
            logger.info("Calculated technical indicators")
            return df
        
        except Exception as e:
            logger.error(f"Error calculating indicators: {e}")
            return df
    
    def get_trend(self, data: pd.DataFrame) -> str:
        """
        Determine the overall trend
        
        Args:
            data: DataFrame with indicators
        
        Returns:
            'BULLISH', 'BEARISH', or 'NEUTRAL'
        """
        if data.empty or len(data) < 50:
            return 'NEUTRAL'
        
        latest = data.iloc[-1]
        
        bullish_signals = 0
        bearish_signals = 0
        
        # SMA crossover
        if pd.notna(latest['SMA_20']) and pd.notna(latest['SMA_50']):
            if latest['SMA_20'] > latest['SMA_50']:
                bullish_signals += 1
            else:
                bearish_signals += 1
        
        # Price vs SMA
        if pd.notna(latest['SMA_20']):
            if latest['Close'] > latest['SMA_20']:
                bullish_signals += 1
            else:
                bearish_signals += 1
        
        # RSI
        if pd.notna(latest['RSI']):
            if latest['RSI'] > 50:
                bullish_signals += 1
            else:
                bearish_signals += 1
        
        # MACD
        if pd.notna(latest['MACD']) and pd.notna(latest['MACD_Signal']):
            if latest['MACD'] > latest['MACD_Signal']:
                bullish_signals += 1
            else:
                bearish_signals += 1
        
        # ADX for trend strength
        if pd.notna(latest['ADX']):
            if latest['ADX'] < 20:
                return 'NEUTRAL'  # Weak trend
        
        if bullish_signals > bearish_signals:
            return 'BULLISH'
        elif bearish_signals > bullish_signals:
            return 'BEARISH'
        else:
            return 'NEUTRAL'
    
    def get_support_resistance(self, data: pd.DataFrame) -> Dict[str, float]:
        """
        Calculate support and resistance levels
        
        Args:
            data: DataFrame with OHLCV data
        
        Returns:
            Dictionary with support and resistance levels
        """
        if data.empty or len(data) < 20:
            return {'support': 0, 'resistance': 0}
        
        recent_data = data.tail(20)
        
        # Simple support/resistance based on recent lows/highs
        support = recent_data['Low'].min()
        resistance = recent_data['High'].max()
        
        # Pivot points
        latest = data.iloc[-1]
        pivot = (latest['High'] + latest['Low'] + latest['Close']) / 3
        
        r1 = 2 * pivot - latest['Low']
        s1 = 2 * pivot - latest['High']
        
        return {
            'support': support,
            'resistance': resistance,
            'pivot': pivot,
            'r1': r1,
            's1': s1
        }
    
    def get_technical_score(self, data: pd.DataFrame) -> float:
        """
        Calculate overall technical score (-1 to 1)
        
        Args:
            data: DataFrame with indicators
        
        Returns:
            Score between -1 (strong sell) and 1 (strong buy)
        """
        if data.empty or len(data) < 20:
            return 0.0
        
        latest = data.iloc[-1]
        score = 0.0
        signals = 0
        
        # RSI score (weight: 0.25)
        if pd.notna(latest['RSI']):
            signals += 1
            rsi = latest['RSI']
            if rsi < 30:
                score += 0.25   # Oversold - strong buy
            elif rsi < 40:
                score += 0.15   # Approaching oversold - mild buy
            elif rsi > 70:
                score -= 0.25   # Overbought - strong sell
            elif rsi > 60:
                score -= 0.10   # Approaching overbought
            elif rsi > 50:
                score += 0.08   # Mild bullish
            else:
                score -= 0.08
        
        # MACD score (weight: 0.20)
        if pd.notna(latest['MACD']) and pd.notna(latest['MACD_Signal']):
            signals += 1
            macd_diff = latest['MACD'] - latest['MACD_Signal']
            if macd_diff > 0:
                # Positive histogram increasing = stronger signal
                if len(data) >= 2:
                    prev_diff = data.iloc[-2]['MACD'] - data.iloc[-2]['MACD_Signal']
                    if macd_diff > prev_diff:
                        score += 0.20  # MACD diverging upward
                    else:
                        score += 0.10
                else:
                    score += 0.15
            else:
                if len(data) >= 2:
                    prev_diff = data.iloc[-2]['MACD'] - data.iloc[-2]['MACD_Signal']
                    if macd_diff < prev_diff:
                        score -= 0.20
                    else:
                        score -= 0.10
                else:
                    score -= 0.15
        
        # Moving average trend (weight: 0.20)
        if pd.notna(latest['SMA_20']) and pd.notna(latest['SMA_50']):
            signals += 1
            if latest['SMA_20'] > latest['SMA_50']:
                score += 0.12   # Golden cross condition
            else:
                score -= 0.12
        # Price above/below SMA20 (weight: 0.10)
        if pd.notna(latest['SMA_20']):
            if latest['Close'] > latest['SMA_20']:
                score += 0.10
            else:
                score -= 0.10
        
        # Bollinger Band position (weight: 0.15)
        if pd.notna(latest['BB_Upper']) and pd.notna(latest['BB_Lower']) and pd.notna(latest['BB_Middle']):
            signals += 1
            bb_width = latest['BB_Upper'] - latest['BB_Lower']
            if bb_width > 0:
                bb_pos = (latest['Close'] - latest['BB_Lower']) / bb_width
                if bb_pos < 0.15:        # Near lower band - oversold bounce opportunity
                    score += 0.20
                elif bb_pos < 0.35:      # Lower half but not extreme
                    score += 0.08
                elif bb_pos > 0.85:      # Near upper band - overbought
                    score -= 0.15
                elif bb_pos > 0.65:
                    score -= 0.05
        
        # Stochastic score (weight: 0.10)
        if pd.notna(latest['SlowK']) and pd.notna(latest['SlowD']):
            signals += 1
            k, d = latest['SlowK'], latest['SlowD']
            if k < 20 and d < 20:
                score += 0.10   # Oversold
            elif k > 80 and d > 80:
                score -= 0.10   # Overbought
            elif k > d and k < 50:
                score += 0.05   # Bullish crossover in lower zone
            elif k < d and k > 50:
                score -= 0.05
        
        # Volume surge (weight: 0.10)
        if pd.notna(latest['Volume_SMA']) and latest['Volume_SMA'] > 0:
            signals += 1
            vol_ratio = latest['Volume'] / latest['Volume_SMA']
            if vol_ratio > 2.0 and latest['Close'] > latest.get('SMA_20', latest['Close']):
                score += 0.10   # High volume breakout
            elif vol_ratio > 1.5:
                score += 0.05
            elif vol_ratio < 0.5:
                score -= 0.05   # Low volume = weak move
        
        # Recent momentum - last 5 days (weight: 0.10)
        if len(data) >= 6:
            momentum_5 = latest.get('Price_Change_5', 0)
            if pd.notna(momentum_5):
                if momentum_5 > 0.03:
                    score += 0.10
                elif momentum_5 > 0.01:
                    score += 0.05
                elif momentum_5 < -0.03:
                    score -= 0.10
                elif momentum_5 < -0.01:
                    score -= 0.05
        
        # Clamp score between -1 and 1
        return max(-1.0, min(1.0, score))
    
    def generate_signals(self, data: pd.DataFrame) -> Dict:
        """
        Generate trading signals based on technical analysis
        
        Args:
            data: DataFrame with OHLCV data
        
        Returns:
            Dictionary with signals and analysis
        """
        if data.empty or len(data) < 20:
            return {
                'signal': 'HOLD',
                'confidence': 0.0,
                'trend': 'NEUTRAL',
                'technical_score': 0.0,
                'reason': 'Insufficient data'
            }
        
        df = self.calculate_indicators(data)
        trend = self.get_trend(df)
        technical_score = self.get_technical_score(df)
        support_resistance = self.get_support_resistance(df)
        
        # Generate signal based on score
        # Confidence is scaled so score of 0.3 -> ~0.60 confidence
        if technical_score > 0.3:
            signal = 'BUY'
            confidence = min(0.45 + technical_score * 0.8, 0.92)
        elif technical_score < -0.3:
            signal = 'SELL'
            confidence = min(0.45 + abs(technical_score) * 0.8, 0.92)
        else:
            signal = 'HOLD'
            confidence = max(0.30, 0.45 + technical_score * 0.5)
        
        latest = df.iloc[-1]
        prev   = df.iloc[-2] if len(df) >= 2 else latest

        # Extra fields consumed by TradeScorer and Smart Exit AI
        rsi_val    = float(latest['RSI'])    if pd.notna(latest.get('RSI'))    else 50.0
        macd_h     = float(latest['MACD'] - latest['MACD_Signal']) \
                     if pd.notna(latest.get('MACD')) and pd.notna(latest.get('MACD_Signal')) else None
        macd_h_prev= float(prev['MACD'] - prev['MACD_Signal']) \
                     if pd.notna(prev.get('MACD')) and pd.notna(prev.get('MACD_Signal')) else None
        vol_ratio  = float(latest['Volume'] / latest['Volume_SMA']) \
                     if pd.notna(latest.get('Volume_SMA')) and latest.get('Volume_SMA', 0) > 0 else 1.0

        reason = f"Trend: {trend}, Technical Score: {technical_score:.2f}"
        
        return {
            'signal': signal,
            'confidence': confidence,
            'trend': trend,
            'technical_score': technical_score,
            'support': support_resistance['support'],
            'resistance': support_resistance['resistance'],
            'reason': reason,
            # Extra fields for scoring and smart exit
            'rsi':                  rsi_val,
            'macd_histogram':       macd_h,
            'macd_histogram_prev':  macd_h_prev,
            'volume_ratio':         vol_ratio,
        }
