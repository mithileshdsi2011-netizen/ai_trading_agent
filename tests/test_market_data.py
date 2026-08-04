"""
Test cases for the optimized Market Data Fetcher.

These tests exercise the public API of MarketDataFetcher using mocked
Kite responses so the suite stays fast and does not need network access.
"""
import pytest
import pandas as pd
from unittest.mock import Mock
from datetime import datetime

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from src.market_data import MarketDataFetcher


def _reset_market_data_caches():
    MarketDataFetcher._instruments_cache = None
    MarketDataFetcher._instruments_cache_time = None
    MarketDataFetcher._symbol_to_token = {}
    MarketDataFetcher._quote_cache = {}
    MarketDataFetcher._quote_cache_time = {}
    MarketDataFetcher._ltp_cache = {}
    MarketDataFetcher._ltp_cache_time = {}
    MarketDataFetcher._hist_cache = {}
    MarketDataFetcher._hist_cache_time = {}


class TestMarketDataFetcher:
    """Test cases for MarketDataFetcher"""

    @pytest.fixture
    def fetcher(self):
        """Create a MarketDataFetcher instance with a mock Kite object."""
        _reset_market_data_caches()
        kite = Mock()
        return MarketDataFetcher(kite=kite)

    def test_initialization(self, fetcher):
        """Test fetcher initialization"""
        assert fetcher is not None
        assert fetcher.kite is not None
        assert hasattr(fetcher, 'get_stock_data')
        assert hasattr(fetcher, 'get_realtime_price')
        assert hasattr(fetcher, 'get_stock_info')

    def test_get_stock_data(self, fetcher, monkeypatch):
        """Test getting stock data via batch wrapper"""
        mock_df = pd.DataFrame({
            'Open': [100, 101, 102],
            'High': [105, 106, 107],
            'Low': [99, 100, 101],
            'Close': [104, 105, 106],
            'Volume': [1000000, 1100000, 1200000]
        }, index=pd.date_range('2024-01-01', periods=3))

        def fake_batch(symbols, period, interval):
            return {s: mock_df.copy() for s in symbols}

        monkeypatch.setattr(fetcher, 'get_batch_stock_data', fake_batch)

        result = fetcher.get_stock_data('RELIANCE')

        assert not result.empty
        assert len(result) == 3
        assert 'Close' in result.columns

    def test_get_stock_data_empty(self, fetcher, monkeypatch):
        """Test getting stock data when no data available"""
        monkeypatch.setattr(fetcher, 'get_batch_stock_data', lambda *a, **kw: {})

        result = fetcher.get_stock_data('INVALID')

        assert result.empty

    def test_get_realtime_price(self, fetcher, monkeypatch):
        """Test getting real-time price"""
        monkeypatch.setattr(
            fetcher, 'get_batch_realtime_prices',
            lambda symbols: {s: 1500.0 for s in symbols}
        )

        result = fetcher.get_realtime_price('RELIANCE')

        assert result == 1500.0

    def test_get_realtime_price_fallback(self, fetcher, monkeypatch):
        """Test getting real-time price falls back to batch quote"""
        monkeypatch.setattr(
            fetcher, 'get_batch_realtime_prices',
            lambda symbols: {}
        )
        monkeypatch.setattr(
            fetcher, 'get_batch_stock_info',
            lambda symbols: {s: {'current_price': 1500.0} for s in symbols}
        )

        result = fetcher.get_realtime_price('RELIANCE')

        assert result == 1500.0

    def test_get_stock_info(self, fetcher, monkeypatch):
        """Test getting stock information"""
        monkeypatch.setattr(
            fetcher, 'get_batch_stock_info',
            lambda symbols: {
                s: {
                    'symbol': s,
                    'current_price': 2500.0,
                    'day_open': 2450.0,
                    'day_high': 2550.0,
                    'day_low': 2440.0,
                    'day_close': 2490.0,
                    'volume': 5000000,
                    'change': 50.0,
                    'change_percent': 2.0,
                    'instrument_token': 123,
                    'exchange': 'NSE',
                }
                for s in symbols
            }
        )

        result = fetcher.get_stock_info('RELIANCE')

        assert result['symbol'] == 'RELIANCE'
        assert result['current_price'] == 2500.0
        assert result['volume'] == 5000000

    def test_is_market_open(self, fetcher):
        """Test market open check returns a boolean"""
        result = fetcher.is_market_open()
        assert isinstance(result, bool)

    def test_get_multiple_stocks_data(self, fetcher, monkeypatch):
        """Test getting data for multiple stocks"""
        mock_data = pd.DataFrame({
            'Open': [100, 101, 102],
            'High': [105, 106, 107],
            'Low': [99, 100, 101],
            'Close': [104, 105, 106],
            'Volume': [1000000, 1100000, 1200000]
        }, index=pd.date_range('2024-01-01', periods=3))

        monkeypatch.setattr(
            fetcher, 'get_batch_stock_data',
            lambda symbols, period='1mo', interval='1d': {
                s: mock_data.copy() for s in symbols
            }
        )

        result = fetcher.get_multiple_stocks_data(['RELIANCE', 'TCS'])

        assert len(result) == 2
        assert 'RELIANCE' in result
        assert 'TCS' in result


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
