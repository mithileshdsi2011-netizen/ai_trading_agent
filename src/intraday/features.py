"""
Intraday Technical Features Module

Calculates technical indicators for intraday trading.
Completely separate from Swing Trading technical analysis.
"""
import logging
from typing import Dict, Any
import pandas as pd

logger = logging.getLogger(__name__)


class IntradayFeatures:
    """Calculates intraday technical features."""
    
    @staticmethod
    def calculate_ema(series: pd.Series, period: int) -> pd.Series:
        """
        Calculate Exponential Moving Average.
        
        Args:
            series: Price series
            period: EMA period
            
        Returns:
            EMA series
        """
        return series.ewm(span=period, adjust=False).mean()
    
    @staticmethod
    def calculate_rsi(series: pd.Series, period: int = 14) -> pd.Series:
        """
        Calculate Relative Strength Index.
        
        Args:
            series: Price series
            period: RSI period
            
        Returns:
            RSI series
        """
        delta = series.diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
        
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        
        return rsi
    
    @staticmethod
    def calculate_vwap(df: pd.DataFrame) -> float:
        """
        Calculate Volume Weighted Average Price.
        
        Args:
            df: DataFrame with OHLCV data
            
        Returns:
            VWAP value
        """
        if df.empty:
            return 0.0
        
        typical_price = (df['high'] + df['low'] + df['close']) / 3
        vwap = (typical_price * df['volume']).sum() / df['volume'].sum()
        
        return vwap
    
    @staticmethod
    def calculate_atr(df: pd.DataFrame, period: int = 14) -> float:
        """
        Calculate Average True Range.
        
        Args:
            df: DataFrame with OHLCV data
            period: ATR period
            
        Returns:
            ATR value
        """
        if len(df) < period:
            return 0.0
        
        high = df['high']
        low = df['low']
        close = df['close']
        
        tr1 = high - low
        tr2 = abs(high - close.shift())
        tr3 = abs(low - close.shift())
        
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr = tr.rolling(window=period).mean()
        
        return atr.iloc[-1]
    
    @staticmethod
    def calculate_opening_range(df: pd.DataFrame, minutes: int = 15) -> Dict[str, float]:
        """
        Calculate opening range high and low.
        
        Args:
            df: DataFrame with intraday data
            minutes: Opening range duration in minutes
            
        Returns:
            Dict with OR high and low
        """
        if df.empty:
            return {'or_high': 0.0, 'or_low': 0.0}
        
        # Get first N minutes
        or_data = df.head(minutes)
        
        return {
            'or_high': or_data['high'].max(),
            'or_low': or_data['low'].min()
        }
    
    @staticmethod
    def calculate_relative_volume(current_volume: int, avg_volume: int) -> float:
        """
        Calculate relative volume.
        
        Args:
            current_volume: Current volume
            avg_volume: Average volume
            
        Returns:
            Relative volume ratio
        """
        if avg_volume == 0:
            return 0.0
        
        return current_volume / avg_volume
    
    @staticmethod
    def calculate_momentum(df: pd.DataFrame, period: int = 5) -> float:
        """
        Calculate price momentum.
        
        Args:
            df: DataFrame with close prices
            period: Momentum period
            
        Returns:
            Momentum value
        """
        if len(df) < period:
            return 0.0
        
        current_close = df['close'].iloc[-1]
        past_close = df['close'].iloc[-period]
        
        momentum = ((current_close - past_close) / past_close) * 100
        
        return momentum
    
    @staticmethod
    def calculate_ema_alignment(ema_short: float, ema_long: float) -> str:
        """
        Determine EMA alignment.
        
        Args:
            ema_short: Short EMA value
            ema_long: Long EMA value
            
        Returns:
            Alignment: BULLISH, BEARISH, or NEUTRAL
        """
        if ema_short > ema_long:
            return "BULLISH"
        elif ema_short < ema_long:
            return "BEARISH"
        else:
            return "NEUTRAL"
    
    @staticmethod
    def calculate_vwap_structure(price: float, vwap: float) -> str:
        """
        Determine VWAP structure.
        
        Args:
            price: Current price
            vwap: VWAP value
            
        Returns:
            Structure: ABOVE_VWAP, BELOW_VWAP, or AT_VWAP
        """
        threshold = 0.002  # 0.2% threshold
        
        if price > vwap * (1 + threshold):
            return "ABOVE_VWAP"
        elif price < vwap * (1 - threshold):
            return "BELOW_VWAP"
        else:
            return "AT_VWAP"
    
    @staticmethod
    def calculate_all_features(df: pd.DataFrame, config) -> Dict[str, Any]:
        """
        Calculate all technical features for a symbol.
        
        Args:
            df: DataFrame with OHLCV data
            config: IntradayConfig instance
            
        Returns:
            Dict with all features
        """
        if df.empty or len(df) < config.ema_long_period:
            return {}
        
        current_price = df['close'].iloc[-1]
        current_volume = df['volume'].iloc[-1]
        
        # EMAs
        ema_9 = IntradayFeatures.calculate_ema(df['close'], config.ema_short_period).iloc[-1]
        ema_20 = IntradayFeatures.calculate_ema(df['close'], config.ema_long_period).iloc[-1]
        
        # RSI
        rsi = IntradayFeatures.calculate_rsi(df['close'], config.rsi_period).iloc[-1]
        
        # VWAP
        vwap = IntradayFeatures.calculate_vwap(df)
        
        # ATR
        atr = IntradayFeatures.calculate_atr(df, config.atr_period)
        
        # Opening range
        or_data = IntradayFeatures.calculate_opening_range(df, config.or_minutes)
        
        # Momentum
        momentum = IntradayFeatures.calculate_momentum(df)
        
        # Relative volume (using average of last 20 candles)
        avg_volume = df['volume'].tail(20).mean()
        relative_volume = IntradayFeatures.calculate_relative_volume(current_volume, avg_volume)
        
        # EMA alignment
        ema_alignment = IntradayFeatures.calculate_ema_alignment(ema_9, ema_20)
        
        # VWAP structure
        vwap_structure = IntradayFeatures.calculate_vwap_structure(current_price, vwap)
        
        return {
            'current_price': current_price,
            'vwap': vwap,
            'ema_9': ema_9,
            'ema_20': ema_20,
            'rsi': rsi,
            'atr': atr,
            'or_high': or_data['or_high'],
            'or_low': or_data['or_low'],
            'momentum': momentum,
            'volume': current_volume,
            'relative_volume': relative_volume,
            'ema_alignment': ema_alignment,
            'vwap_structure': vwap_structure
        }
