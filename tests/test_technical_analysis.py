"""
Test cases for Technical Analysis
"""
import pytest
import pandas as pd
import numpy as np
from unittest.mock import Mock

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from src.technical_analysis import TechnicalAnalyzer


class TestTechnicalAnalyzer:
    """Test cases for TechnicalAnalyzer"""
    
    @pytest.fixture
    def analyzer(self):
        """Create a TechnicalAnalyzer instance"""
        return TechnicalAnalyzer()
    
    @pytest.fixture
    def sample_data(self):
        """Create sample OHLCV data"""
        dates = pd.date_range('2024-01-01', periods=100)
        np.random.seed(42)
        
        data = pd.DataFrame({
            'Open': np.random.uniform(100, 200, 100),
            'High': np.random.uniform(100, 200, 100),
            'Low': np.random.uniform(100, 200, 100),
            'Close': np.random.uniform(100, 200, 100),
            'Volume': np.random.randint(1000000, 10000000, 100)
        }, index=dates)
        
        # Ensure High >= Low and High/Low >= Open/Close
        data['High'] = data[['Open', 'Close']].max(axis=1) + np.random.uniform(0, 5, 100)
        data['Low'] = data[['Open', 'Close']].min(axis=1) - np.random.uniform(0, 5, 100)
        
        return data
    
    def test_initialization(self, analyzer):
        """Test analyzer initialization"""
        assert analyzer is not None
    
    def test_calculate_indicators(self, analyzer, sample_data):
        """Test indicator calculation"""
        result = analyzer.calculate_indicators(sample_data)
        
        assert not result.empty
        assert 'SMA_20' in result.columns
        assert 'SMA_50' in result.columns
        assert 'RSI' in result.columns
        assert 'MACD' in result.columns
        assert 'BB_Upper' in result.columns
        assert 'ADX' in result.columns
    
    def test_calculate_indicators_insufficient_data(self, analyzer):
        """Test indicator calculation with insufficient data"""
        short_data = pd.DataFrame({
            'Open': [100],
            'High': [105],
            'Low': [99],
            'Close': [104],
            'Volume': [1000000]
        }, index=pd.date_range('2024-01-01', periods=1))
        
        result = analyzer.calculate_indicators(short_data)
        
        # Should return data even if indicators can't be calculated
        assert not result.empty
    
    def test_get_trend(self, analyzer, sample_data):
        """Test trend detection"""
        result = analyzer.get_trend(sample_data)
        
        assert result in ['BULLISH', 'BEARISH', 'NEUTRAL']
    
    def test_get_trend_insufficient_data(self, analyzer):
        """Test trend detection with insufficient data"""
        short_data = pd.DataFrame({
            'Open': [100],
            'High': [105],
            'Low': [99],
            'Close': [104],
            'Volume': [1000000]
        }, index=pd.date_range('2024-01-01', periods=1))
        
        result = analyzer.get_trend(short_data)
        
        assert result == 'NEUTRAL'
    
    def test_get_support_resistance(self, analyzer, sample_data):
        """Test support and resistance calculation"""
        result = analyzer.get_support_resistance(sample_data)
        
        assert 'support' in result
        assert 'resistance' in result
        assert 'pivot' in result
        assert result['support'] > 0
        assert result['resistance'] > 0
        assert result['support'] < result['resistance']
    
    def test_get_technical_score(self, analyzer, sample_data):
        """Test technical score calculation"""
        result = analyzer.get_technical_score(sample_data)
        
        assert isinstance(result, float)
        assert -1.0 <= result <= 1.0
    
    def test_get_technical_score_insufficient_data(self, analyzer):
        """Test technical score with insufficient data"""
        short_data = pd.DataFrame({
            'Open': [100],
            'High': [105],
            'Low': [99],
            'Close': [104],
            'Volume': [1000000]
        }, index=pd.date_range('2024-01-01', periods=1))
        
        result = analyzer.get_technical_score(short_data)
        
        assert result == 0.0
    
    def test_generate_signals(self, analyzer, sample_data):
        """Test signal generation"""
        result = analyzer.generate_signals(sample_data)
        
        assert 'signal' in result
        assert 'confidence' in result
        assert 'trend' in result
        assert 'technical_score' in result
        assert result['signal'] in ['BUY', 'SELL', 'HOLD']
        assert 0.0 <= result['confidence'] <= 1.0
        assert -1.0 <= result['technical_score'] <= 1.0
    
    def test_generate_signals_insufficient_data(self, analyzer):
        """Test signal generation with insufficient data"""
        short_data = pd.DataFrame({
            'Open': [100],
            'High': [105],
            'Low': [99],
            'Close': [104],
            'Volume': [1000000]
        }, index=pd.date_range('2024-01-01', periods=1))
        
        result = analyzer.generate_signals(short_data)
        
        assert result['signal'] == 'HOLD'
        assert result['confidence'] == 0.0


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
