"""
Unit tests for src/enterprise_risk_engine.py
Run from project root: PYTHONPATH=src venv/bin/python -m unittest tests.test_enterprise_risk
"""
import os
import sys
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import pandas as pd
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from enterprise_risk_engine import EnterpriseRiskEngine, _REGIME_LIMITS


class FakePosition:
    def __init__(self, symbol, qty, price, sector="Unknown", status="OPEN"):
        self.symbol = symbol
        self.quantity = qty
        self.entry_price = price
        self.sector = sector
        self.status = SimpleNamespace(value=status)


class FakeRiskManager:
    def __init__(self, positions=None, daily_pnl=0.0):
        self.positions = positions or []
        self.daily_pnl = daily_pnl
        self.daily_trades = 0

    def should_stop_trading(self):
        return self.daily_pnl < -5000


class TestEnterpriseRiskEngine(unittest.TestCase):

    def setUp(self):
        self.rm = FakeRiskManager()
        self.engine = EnterpriseRiskEngine(self.rm)

    # 1. regime limits
    def test_regime_bull_allows_trade(self):
        signal = {"symbol": "RELIANCE", "market_regime": "BULL"}
        ok, msg = self.engine._regime_allow(signal)
        self.assertTrue(ok)

    def test_regime_sideways_blocks_new_trade_after_limit(self):
        self.rm.positions = [FakePosition("A", 1, 100) for _ in range(2)]
        signal = {"symbol": "RELIANCE", "market_regime": "SIDEWAYS"}
        ok, msg = self.engine._regime_allow(signal)
        self.assertFalse(ok)
        self.assertIn("SIDEWAYS limit", msg)

    # 2. sector exposure
    def test_sector_exposure_blocks_over_concentration(self):
        # Portfolio: ₹100,000 in Banking, total value ₹150,000 cash + positions
        self.rm.positions = [FakePosition("HDFCBANK", 100, 1000, "Banking")]
        signal = {
            "symbol": "ICICIBANK",
            "current_price": 500,
            "position_size": 20,  # ₹10,000 more in banking
            "_research": {"sector": "Banking"}
        }
        ok, msg = self.engine._sector_exposure_allow(signal)
        self.assertFalse(ok)
        self.assertIn("Banking exposure", msg)

    def test_sector_exposure_allows_within_limit(self):
        self.rm.positions = [FakePosition("INFY", 1, 1000, "IT")]
        signal = {
            "symbol": "TCS",
            "current_price": 1000,
            "position_size": 1,
            "_research": {"sector": "IT"}
        }
        ok, msg = self.engine._sector_exposure_allow(signal)
        self.assertTrue(ok)

    # 3. drawdown governor
    def test_drawdown_governor_halt(self):
        self.rm.daily_pnl = -6000
        ok, msg = self.engine._drawdown_governor()
        self.assertFalse(ok)
        self.assertIn("drawdown", msg)

    # 4. correlation guard
    def test_correlation_guard_rejects_highly_correlated_stock(self):
        dates = pd.date_range("2026-07-01", periods=20)
        # Two perfectly correlated return series
        base = np.linspace(100, 120, 20) + np.random.normal(0, 0.01, 20)
        candidate = base * 1.05
        position = base * 0.98

        md = MagicMock()
        md.get_stock_data.side_effect = lambda sym, **kwargs: (
            pd.DataFrame({"Close": candidate}, index=dates) if sym == "AXISBANK"
            else pd.DataFrame({"Close": position}, index=dates)
        )
        self.engine._market_data = md

        self.rm.positions = [FakePosition("HDFCBANK", 10, 1000, "Banking")]
        ok, msg = self.engine._correlation_guard({"symbol": "AXISBANK"})
        self.assertFalse(ok)
        self.assertIn("HDFCBANK", msg)

    # 5. Kelly sizing
    def test_kelly_size_factor(self):
        self.assertEqual(self.engine._kelly_size_factor(0.95), 1.0)
        self.assertEqual(self.engine._kelly_size_factor(0.80), 0.5)
        self.assertEqual(self.engine._kelly_size_factor(0.70), 0.5)
        self.assertEqual(self.engine._kelly_size_factor(0.65), 0.25)
        self.assertEqual(self.engine._kelly_size_factor(0.50), 0.0)

    # 6. dynamic position size
    def test_dynamic_position_size_atr_based(self):
        signal = {
            "current_price": 100,
            "atr": 2.0,
            "confidence": 0.95,
            "market_regime": "BULL"
        }
        qty = self.engine._dynamic_position_size(signal, vol_factor=1.0)
        # Risk amount ≈ TRADING_AMOUNT * 0.02 * 1 * 1 * 1 = ₹300
        # qty = 300 / 2 = 150, but capped by per-slot capital
        self.assertGreater(qty, 0)
        self.assertLessEqual(qty, 150)

    # 7. portfolio heat
    def test_portfolio_heat_shape(self):
        self.rm.positions = [
            FakePosition("HDFCBANK", 10, 1000, "Banking"),
            FakePosition("INFY", 10, 1000, "IT"),
        ]
        heat = self.engine.portfolio_heat()
        self.assertIn("exposure_by_sector", heat)
        self.assertIn("Banking", heat["exposure_by_sector"])
        self.assertIn("IT", heat["exposure_by_sector"])
        self.assertEqual(heat["open_positions"], 2)


if __name__ == '__main__':
    unittest.main()
