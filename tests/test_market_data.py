"""
Test cases for Market Data Fetcher
"""
import pytest
import pandas as pd
from unittest.mock import Mock, patch
from datetime import datetime

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from src.market_data import MarketDataFetcher


class TestMarketDataFetcher:
    """Test cases for MarketDataFetcher"""
    
    @pytest.fixture
    def fetcher(self):
        """Create a MarketDataFetcher instance"""
        return MarketDataFetcher()
    
    def test_initialization(self, fetcher):
        """Test fetcher initialization"""
        assert fetcher is not None
        assert hasattr(fetcher, 'cache')
        assert hasattr(fetcher, 'cache_duration')
    
    @patch('yfinance.Ticker')
    def test_get_stock_data(self, mock_ticker, fetcher):
        """Test getting stock data"""
        # Mock the yfinance Ticker
        mock_ticker_instance = Mock()
        mock_ticker.return_value = mock_ticker_instance
        
        # Create mock data
        mock_data = pd.DataFrame({
            'Open': [100, 101, 102],
            'High': [105, 106, 107],
            'Low': [99, 100, 101],
            'Close': [104, 105, 106],
            'Volume': [1000000, 1100000, 1200000]
        }, index=pd.date_range('2024-01-01', periods=3))
        
        mock_ticker_instance.history.return_value = mock_data
        
        # Test
        result = fetcher.get_stock_data('RELIANCE')
        
        assert not result.empty
        assert len(result) == 3
        assert 'Close' in result.columns
        mock_ticker_instance.history.assert_called_once()
    
    @patch('yfinance.Ticker')
    def test_get_stock_data_empty(self, mock_ticker, fetcher):
        """Test getting stock data when no data available"""
        mock_ticker_instance = Mock()
        mock_ticker.return_value = mock_ticker_instance
        mock_ticker_instance.history.return_value = pd.DataFrame()
        
        result = fetcher.get_stock_data('INVALID')
        
        assert result.empty
    
    @patch('yfinance.Ticker')
    def test_get_realtime_price(self, mock_ticker, fetcher):
        """Test getting real-time price"""
        mock_ticker_instance = Mock()
        mock_ticker.return_value = mock_ticker_instance
        mock_ticker_instance.info = {'currentPrice': 1500.0}
        
        result = fetcher.get_realtime_price('RELIANCE')
        
        assert result == 1500.0
    
    @patch('yfinance.Ticker')
    def test_get_realtime_price_fallback(self, mock_ticker, fetcher):
        """Test getting real-time price with fallback"""
        mock_ticker_instance = Mock()
        mock_ticker.return_value = mock_ticker_instance
        mock_ticker_instance.info = {'regularMarketPrice': 1500.0}
        
        result = fetcher.get_realtime_price('RELIANCE')
        
        assert result == 1500.0
    
    @patch('yfinance.Ticker')
    def test_get_stock_info(self, mock_ticker, fetcher):
        """Test getting stock information"""
        mock_ticker_instance = Mock()
        mock_ticker.return_value = mock_ticker_instance
        mock_ticker_instance.info = {
            'longName': 'Reliance Industries Limited',
            'sector': 'Energy',
            'industry': 'Oil & Gas Integrated',
            'marketCap': 20000000000000,
            'currentPrice': 2500.0,
            'previousClose': 2450.0,
            'dayHigh': 2550.0,
            'dayLow': 2440.0,
            'volume': 5000000,
            'fiftyTwoWeekHigh': 2800.0,
            'fiftyTwoWeekLow': 2200.0,
            'trailingPE': 25.0,
            'beta': 0.9
        }
        
        result = fetcher.get_stock_info('RELIANCE')
        
        assert result['symbol'] == 'RELIANCE.NS'
        assert result['name'] == 'Reliance Industries Limited'
        assert result['sector'] == 'Energy'
        assert result['current_price'] == 2500.0
    
    def test_is_market_open(self, fetcher):
        """Test market open check"""
        # This test will depend on when it's run
        # Just check that the method works
        result = fetcher.is_market_open()
        assert isinstance(result, bool)
    
    @patch('yfinance.Ticker')
    def test_get_multiple_stocks_data(self, mock_ticker, fetcher):
        """Test getting data for multiple stocks"""
        mock_ticker_instance = Mock()
        mock_ticker.return_value = mock_ticker_instance
        
        mock_data = pd.DataFrame({
            'Open': [100],
            'High': [105],
            'Low': [99],
            'Close': [104],
            'Volume': [1000000]
        }, index=pd.date_range('2024-01-01', periods=1))
        
        mock_ticker_instance.history.return_value = mock_data
        
        symbols = ['RELIANCE', 'TCS']
        result = fetcher.get_multiple_stocks_data(symbols)
        
        assert len(result) == 2
        assert 'RELIANCE' in result
        assert 'TCS' in result


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
