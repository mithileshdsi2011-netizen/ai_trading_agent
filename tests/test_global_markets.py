"""
Unit tests for src/global_markets.py
"""
import os
import sys
import unittest

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from global_markets import GlobalMarketMonitor


class FakeGlobalData:
    def __init__(self, close_map):
        self._close = close_map

    def get_global_data(self, name, symbol):
        closes = self._close.get(name)
        if closes is None:
            return pd.DataFrame()
        idx = pd.date_range(end='2026-08-03', periods=len(closes), freq='D')
        return pd.DataFrame({'Close': closes}, index=idx)


class TestGlobalMarketMonitor(unittest.TestCase):

    def test_bullish_sentiment(self):
        # Strong US and SGX up, USDINR flat -> bullish
        close_map = {
            'NASDAQ': [100, 105, 110],
            'Dow Jones': [100, 103, 107],
            'S&P500': [100, 104, 109],
            'SGX Nifty': [100, 102, 105],
            'Brent': [100, 101, 102],
            'Gold': [100, 99, 98],
            'USDINR': [80, 80, 80],
        }
        mon = GlobalMarketMonitor(store=None, market_data=FakeGlobalData(close_map))
        snap = mon.compute()
        self.assertGreater(snap['sentiment_score'], 50)

    def test_bearish_sentiment(self):
        # US and SGX down, USDINR up -> bearish
        close_map = {
            'NASDAQ': [110, 105, 100],
            'Dow Jones': [107, 103, 100],
            'S&P500': [109, 104, 100],
            'SGX Nifty': [105, 102, 100],
            'Brent': [102, 101, 100],
            'Gold': [98, 99, 100],
            'USDINR': [80, 81, 82],
        }
        mon = GlobalMarketMonitor(store=None, market_data=FakeGlobalData(close_map))
        snap = mon.compute()
        self.assertLess(snap['sentiment_score'], 50)

    def test_confidence_boost_bullish(self):
        close_map = {
            'NASDAQ': [100, 105, 110],
            'Dow Jones': [100, 103, 107],
            'S&P500': [100, 104, 109],
            'SGX Nifty': [100, 102, 105],
            'Brent': [100, 101, 102],
            'Gold': [100, 99, 98],
            'USDINR': [80, 80, 80],
        }
        mon = GlobalMarketMonitor(store=None, market_data=FakeGlobalData(close_map))
        mon.compute()
        adj = mon.adjust_confidence(70.0)
        self.assertGreater(adj['adjusted_confidence'], 70.0)

    def test_confidence_penalty_bearish(self):
        close_map = {
            'NASDAQ': [110, 105, 100],
            'Dow Jones': [107, 103, 100],
            'S&P500': [109, 104, 100],
            'SGX Nifty': [105, 102, 100],
            'Brent': [102, 101, 100],
            'Gold': [98, 99, 100],
            'USDINR': [80, 81, 82],
        }
        mon = GlobalMarketMonitor(store=None, market_data=FakeGlobalData(close_map))
        mon.compute()
        adj = mon.adjust_confidence(70.0)
        self.assertLess(adj['adjusted_confidence'], 70.0)

    def test_sentiment_bounds(self):
        mon = GlobalMarketMonitor(store=None, market_data=FakeGlobalData({}))
        snap = mon.compute()
        self.assertTrue(0 <= snap['sentiment_score'] <= 100)


if __name__ == '__main__':
    unittest.main()
