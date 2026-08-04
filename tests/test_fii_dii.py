"""
Unit tests for src/fii_dii.py
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from fii_dii import FII_DII_Engine


class TestFII_DII_Engine(unittest.TestCase):

    def test_ingest_computes_net_flow(self):
        engine = FII_DII_Engine(store=None)
        snap = engine.ingest(fii_buy=1000, fii_sell=800, dii_buy=600, dii_sell=500)
        self.assertEqual(snap['fii_net'], 200)
        self.assertEqual(snap['dii_net'], 100)
        self.assertEqual(snap['net_flow'], 300)
        self.assertEqual(snap['sentiment'], 'POSITIVE')

    def test_neutral_sentiment(self):
        engine = FII_DII_Engine(store=None)
        snap = engine.ingest(fii_buy=1000, fii_sell=1000, dii_buy=500, dii_sell=500)
        self.assertEqual(snap['net_flow'], 0)
        self.assertEqual(snap['sentiment'], 'NEUTRAL')

    def test_negative_sentiment(self):
        engine = FII_DII_Engine(store=None)
        snap = engine.ingest(fii_buy=800, fii_sell=1000, dii_buy=500, dii_sell=700)
        self.assertEqual(snap['net_flow'], -400)
        self.assertEqual(snap['sentiment'], 'NEGATIVE')

    def test_adjust_positive(self):
        engine = FII_DII_Engine(store=None)
        engine.ingest(fii_buy=1000, fii_sell=800, dii_buy=600, dii_sell=500)
        adj = engine.adjust_ai_score(60.0)
        self.assertEqual(adj['adjustment'], 3)
        self.assertEqual(adj['adjusted_score'], 63.0)

    def test_adjust_negative(self):
        engine = FII_DII_Engine(store=None)
        engine.ingest(fii_buy=800, fii_sell=1000, dii_buy=500, dii_sell=700)
        adj = engine.adjust_ai_score(60.0)
        self.assertEqual(adj['adjustment'], -3)
        self.assertEqual(adj['adjusted_score'], 57.0)


if __name__ == '__main__':
    unittest.main()
