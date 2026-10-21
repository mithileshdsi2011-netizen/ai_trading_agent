"""
Tests for the IPO Investment Decision engine (src/ipo/decision.py).

Coverage: coverage gate, two-horizon recommendations, component
exclusion when inputs are missing, stale-data downgrade, contradictory
source data, loss-making P/E handling, configurable weights, and the
subscription-trend / market-sentiment provider additions.
"""

import sys
import os
from datetime import date, timedelta
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from ipo.decision import (IPODecisionEngine, LISTING_WEIGHTS,
                          LONGTERM_WEIGHTS, MIN_COVERAGE)
from ipo.models import (IPO, IPOStatus, IPOFinancials, IPOSubscription,
                        MarketSentiment)


def make_ipo(**kw):
    base = dict(name='Test Co', symbol='TEST', status=IPOStatus.OPEN,
                issue_type='EQUITY', price_band_min=100.0,
                price_band_max=120.0, issue_size=500.0,
                fresh_issue=400.0, offer_for_sale=100.0,
                min_investment=14000.0, open_date='2026-10-21',
                close_date='2026-10-23')
    base.update(kw)
    return IPO(**base)


STRONG_FIN = IPOFinancials(revenue=1000, profit=150, revenue_growth=30,
                           profit_growth=30, roe=25, debt_to_equity=0.3,
                           cash_flow=200, pe_ratio=20, peer_pe=30)

CALM_SENT = MarketSentiment(nifty_last=24000, nifty_change_pct=1.0,
                            india_vix=12.0, as_of='2026-10-21 15:00')
BEAR_SENT = MarketSentiment(nifty_last=22000, nifty_change_pct=-1.2,
                            india_vix=27.0, as_of='2026-10-21 15:00')


class TestCoverageGate:
    """Missing inputs are excluded, never scored — and too little
    coverage means no score at all."""

    def test_subscription_alone_cannot_score(self):
        ipo = make_ipo(subscription=IPOSubscription(qib=4, overall=4,
                                                  retail=4))
        d = IPODecisionEngine().decide(ipo)
        # subscription(.30)+structure(.10)=.40 — exactly at the gate a
        # score IS possible; below it, not. With only subscription
        # present (no structure inputs), coverage is .30 → gated.
        ipo2 = make_ipo(fresh_issue=None, offer_for_sale=None,
                        min_investment=None, issue_size=None,
                        subscription=IPOSubscription(qib=4, overall=4))
        d2 = IPODecisionEngine().decide(ipo2)
        assert d2['listing_score'] is None
        assert d2['listing_recommendation'] == 'INSUFFICIENT DATA'
        assert d2['longterm_recommendation'] == 'INSUFFICIENT DATA'

    def test_missing_components_reported_not_zero(self):
        ipo = make_ipo(subscription=IPOSubscription(qib=2, overall=2))
        d = IPODecisionEngine().decide(ipo)
        assert d['components']['fundamentals'] is None
        assert d['components']['valuation'] is None
        assert d['components']['sentiment'] is None
        assert any('Revenue' in m for m in d['missing'])
        assert any('peer' in m.lower() for m in d['missing'])

    def test_no_confident_score_from_subscription_only(self):
        ipo = make_ipo(subscription=IPOSubscription(qib=4, overall=4))
        d = IPODecisionEngine().decide(ipo)
        assert d['confidence'] == 'LOW'
        assert d['listing_recommendation'] != 'CONSIDER APPLYING' or \
            d['listing_coverage'] < 0.7


class TestTwoHorizonRecommendations:
    def test_weak_subscription_strong_fundamentals_diverge(self):
        """Low demand + strong fundamentals: listing WAIT/AVOID while
        long-term can still be positive."""
        ipo = make_ipo(financials=STRONG_FIN,
                       subscription=IPOSubscription(qib=0.3, nii=0.2,
                                                    retail=0.3, overall=0.5))
        d = IPODecisionEngine().decide(ipo, sentiment=CALM_SENT)
        assert d['listing_score'] is not None
        assert d['longterm_score'] is not None
        assert d['longterm_score'] > d['listing_score']
        assert d['listing_recommendation'] in ('WAIT', 'AVOID')
        assert d['longterm_recommendation'] == 'CONSIDER APPLYING'

    def test_strong_everything_consider_applying(self):
        ipo = make_ipo(financials=STRONG_FIN,
                       subscription=IPOSubscription(qib=4, nii=3,
                                                    retail=4, overall=4))
        d = IPODecisionEngine().decide(ipo, sentiment=CALM_SENT)
        assert d['listing_recommendation'] == 'CONSIDER APPLYING'
        assert d['confidence'] == 'HIGH'
        assert 3 <= len(d['rationale']) <= 5

    def test_strong_subscription_expensive_valuation_lower_score(self):
        ipo = make_ipo(subscription=IPOSubscription(qib=4, overall=4))
        cheap = dict(financials=IPOFinancials(pe_ratio=15, peer_pe=30))
        pricey = dict(financials=IPOFinancials(pe_ratio=60, peer_pe=30))
        d_cheap = IPODecisionEngine().decide(ipo, sentiment=CALM_SENT,
                                             fundamentals=cheap['financials'])
        d_pricey = IPODecisionEngine().decide(ipo, sentiment=CALM_SENT,
                                              fundamentals=pricey['financials'])
        assert d_pricey['listing_score'] < d_cheap['listing_score']
        assert d_pricey['valuation_class'] == 'EXPENSIVE'
        assert d_cheap['valuation_class'] == 'ATTRACTIVE'


class TestValuationRules:
    def test_loss_making_pe_not_applied(self):
        fin = IPOFinancials(profit=-50, eps=-2.0)
        ipo = make_ipo(financials=fin,
                       subscription=IPOSubscription(qib=4, overall=4))
        d = IPODecisionEngine().decide(ipo)
        assert d['components']['valuation'] is None
        assert d['valuation_class'] == 'UNKNOWN'
        assert any('loss-making' in n for n in d['notes'])

    def test_missing_peer_absolute_bands_with_note(self):
        fin = IPOFinancials(pe_ratio=20)
        ipo = make_ipo(financials=fin,
                       subscription=IPOSubscription(qib=4, overall=4))
        d = IPODecisionEngine().decide(ipo)
        assert d['components']['valuation'] is not None
        assert any('peer' in n.lower() for n in d['notes'])

    def test_implied_pe_from_eps_and_cap_price(self):
        fin = IPOFinancials(eps=10.0, peer_pe=30)
        ipo = make_ipo(financials=fin)  # cap 120 → implied P/E 12 < .8*30
        d = IPODecisionEngine().decide(ipo)
        assert d['components']['valuation'] == 85.0
        assert d['valuation_class'] == 'ATTRACTIVE'


class TestStaleAndIntegrity:
    def test_stale_caps_confidence_and_downgrades(self):
        ipo = make_ipo(financials=STRONG_FIN,
                       subscription=IPOSubscription(qib=4, nii=3,
                                                    retail=4, overall=4))
        d = IPODecisionEngine().decide(ipo, sentiment=CALM_SENT,
                                       stale=True)
        assert d['confidence'] == 'LOW'
        assert d['listing_recommendation'] == 'WAIT'  # was CONSIDER
        assert any('stale' in n.lower() for n in d['notes'])

    def test_contradictory_issue_size_flagged(self):
        ipo = make_ipo(fresh_issue=30000, issue_size=22000,
                       subscription=IPOSubscription(qib=4, overall=4))
        d = IPODecisionEngine().decide(ipo)
        assert any('inconsistent' in n for n in d['notes'])

    def test_missing_financials_longterm_insufficient(self):
        ipo = make_ipo(subscription=IPOSubscription(qib=4, overall=4))
        d = IPODecisionEngine().decide(ipo, sentiment=CALM_SENT)
        # long-term needs fundamentals(.40)+valuation(.30) — both absent
        assert d['longterm_recommendation'] == 'INSUFFICIENT DATA'
        assert d['longterm_score'] is None


class TestEngineInternals:
    def test_custom_weights_respected(self):
        w = {'subscription': 0.10, 'valuation': 0.10, 'sentiment': 0.10,
             'fundamentals': 0.60, 'structure': 0.10}
        eng = IPODecisionEngine(listing_weights=w)
        ipo = make_ipo(financials=STRONG_FIN,
                       subscription=IPOSubscription(qib=0.2, overall=0.2))
        d = eng.decide(ipo, sentiment=CALM_SENT)
        assert d['weights']['listing'] == w
        # fundamentals now dominate → listing score should be high
        assert d['listing_score'] >= 65

    def test_sentiment_failure_component_absent(self):
        ipo = make_ipo(subscription=IPOSubscription(qib=4, overall=4))
        d = IPODecisionEngine().decide(ipo, sentiment=None)
        assert d['components']['sentiment'] is None
        assert any('sentiment' in m.lower() for m in d['missing'])

    def test_output_shape_complete(self):
        ipo = make_ipo(subscription=IPOSubscription(qib=2, overall=2))
        d = IPODecisionEngine().decide(ipo)
        for k in ('listing_recommendation', 'longterm_recommendation',
                  'listing_score', 'longterm_score', 'risk_level',
                  'confidence', 'rationale', 'key_risks', 'missing',
                  'components', 'weights', 'valuation_class', 'notes',
                  'analyzed_at'):
            assert k in d
        assert d['risk_level'] in ('LOW', 'MODERATE', 'HIGH', 'UNKNOWN')


def _nse_provider(payloads, statuses=None):
    """Stubbed-session provider (same pattern as test_ipo.py)."""
    from ipo.providers.nse_provider import NseIPODataProvider
    session = MagicMock()
    queue = list(statuses or [])
    it = iter(payloads)

    def _get(url, timeout=None):
        resp = MagicMock()
        resp.status_code = queue.pop(0) if queue else 200
        if url.endswith('/'):
            return resp
        try:
            resp.json.return_value = next(it)
        except StopIteration:
            resp.json.return_value = {}
        return resp

    session.get.side_effect = _get
    return NseIPODataProvider(session=session, cache_ttl=60)


class TestMarketSentimentProvider:
    def test_allindices_parsed(self):
        p = _nse_provider([{'data': [
            {'index': 'NIFTY 50', 'last': '24,350.25', 'percentChange': '0.85'},
            {'index': 'INDIA VIX', 'last': '13.42'},
        ]}])
        s = p.get_market_sentiment()
        assert s.nifty_last == 24350.25
        assert s.nifty_change_pct == 0.85
        assert s.india_vix == 13.42
        assert s.as_of is not None

    def test_sentiment_failure_returns_none_not_raise(self):
        p = _nse_provider([], statuses=[200, 500])
        assert p.get_market_sentiment() is None

    def test_sentiment_cached_five_minutes(self):
        p = _nse_provider([{'data': [
            {'index': 'NIFTY 50', 'last': '24000', 'percentChange': '0'},
        ]}])
        first = p.get_market_sentiment()
        second = p.get_market_sentiment()
        assert first is second


class TestSubscriptionTrend:
    def _open_row(self, overall):
        s = (date.today() - timedelta(days=1)).strftime('%d-%b-%Y')
        e = (date.today() + timedelta(days=2)).strftime('%d-%b-%Y')
        return [{'companyName': 'Trend Co', 'symbol': 'TRD',
                 'issueStartDate': s, 'issueEndDate': e,
                 'category': 'Total', 'noOfTime': str(overall)}]

    def test_snapshots_build_trend(self):
        p = _nse_provider([self._open_row(1.0), {}])
        ipos = p.get_open_ipos()
        assert ipos[0].subscription_as_of is not None
        assert ipos[0].subscription_trend is None  # one point only
        # Second refresh with a higher multiple → rising
        p._cache_expiry['all'] = 0  # force refetch
        p._session.get.side_effect = None
        p._session.get = MagicMock(side_effect=lambda url, timeout=None:
            self._resp(p, url))
        p._payloads = iter([self._open_row(1.5), {}])
        ipos = p.get_open_ipos()
        assert ipos[0].subscription_trend == 'rising'

    @staticmethod
    def _resp(p, url):
        resp = MagicMock()
        resp.status_code = 200
        if url.endswith('/'):
            return resp
        try:
            resp.json.return_value = next(p._payloads)
        except StopIteration:
            resp.json.return_value = {}
        return resp
