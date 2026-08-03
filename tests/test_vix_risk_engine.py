"""
Unit tests for src/vix_risk_engine.py
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from vix_risk_engine import IndiaVIXRiskEngine


class FakeMarketData:
    def __init__(self, vix):
        self._vix = vix

    def get_realtime_price(self, symbol):
        return self._vix

    def get_stock_info(self, symbol):
        return {'current_price': self._vix}


class TestIndiaVIXRiskEngine(unittest.TestCase):

    def test_risk_factor_low(self):
        self.assertEqual(IndiaVIXRiskEngine.risk_factor_from_vix(14.5), 1.0)

    def test_risk_factor_moderate(self):
        self.assertEqual(IndiaVIXRiskEngine.risk_factor_from_vix(17.0), 0.8)

    def test_risk_factor_high(self):
        self.assertEqual(IndiaVIXRiskEngine.risk_factor_from_vix(22.5), 0.6)

    def test_risk_factor_extreme(self):
        self.assertEqual(IndiaVIXRiskEngine.risk_factor_from_vix(26.0), 0.3)

    def test_compute_and_persist(self):
        engine = IndiaVIXRiskEngine(market_data=FakeMarketData(18.0))
        result = engine.compute()
        self.assertIn('vix', result)
        self.assertIn('volatility_score', result)
        self.assertIn('risk_factor', result)
        self.assertIn('risk_level', result)
        self.assertEqual(result['vix'], 18.0)
        self.assertEqual(result['risk_factor'], 0.8)


if __name__ == '__main__':
    unittest.main()
