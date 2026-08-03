"""
Unit tests for src/options_intelligence.py
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from options_intelligence import OptionsIntelligenceEngine


def make_option_chain(
    spot=22500.0,
    pcr_over_one=True,
    long_buildup=True,
    strong_support=True,
):
    # Create 3 strikes around spot
    base = int(spot / 100) * 100
    strikes = [base - 200, base, base + 200]

    data = []
    for strike in strikes:
        # Put OI high when PCR > 1 or at support
        put_oi = 1000
        put_oi_change = 0
        call_oi = 1000
        call_oi_change = 0

        if pcr_over_one:
            put_oi = 2000
        else:
            call_oi = 2000

        if long_buildup:
            put_oi_change = 200
        else:
            call_oi_change = 200

        if strong_support and strike == base - 200:
            put_oi = 5000  # put wall

        data.append({
            "strikePrice": strike,
            "expiryDate": "31-Aug-2026",
            "CE": {
                "openInterest": call_oi,
                "changeinOpenInterest": call_oi_change,
            },
            "PE": {
                "openInterest": put_oi,
                "changeinOpenInterest": put_oi_change,
            },
        })

    return {
        "records": {
            "timestamp": "2026-08-03T15:30:00",
            "underlyingValue": spot,
            "data": data,
        }
    }


class TestOptionsIntelligenceEngine(unittest.TestCase):

    def test_pcr_calculation(self):
        engine = OptionsIntelligenceEngine(store=None)
        raw = make_option_chain(pcr_over_one=True)
        snap = engine.ingest(raw)
        self.assertGreater(snap["pcr"], 1.0)

    def test_long_buildup_flag(self):
        engine = OptionsIntelligenceEngine(store=None)
        raw = make_option_chain(long_buildup=True)
        snap = engine.ingest(raw)
        self.assertTrue(snap["long_buildup"])

    def test_short_buildup_flag(self):
        engine = OptionsIntelligenceEngine(store=None)
        raw = make_option_chain(long_buildup=False)
        snap = engine.ingest(raw)
        self.assertTrue(snap["short_buildup"])

    def test_strong_oi_support(self):
        engine = OptionsIntelligenceEngine(store=None)
        raw = make_option_chain(strong_support=True)
        snap = engine.ingest(raw)
        self.assertTrue(snap["strong_oi_support"])

    def test_confidence_boost(self):
        engine = OptionsIntelligenceEngine(store=None)
        raw = make_option_chain(pcr_over_one=True, long_buildup=True, strong_support=True)
        engine.ingest(raw)
        adj = engine.adjust_confidence(70.0)
        self.assertEqual(adj["confidence_boost"], 15.0)
        self.assertEqual(adj["adjusted_confidence"], 85.0)


if __name__ == '__main__':
    unittest.main()
