"""
Tests for IPO Intelligence module

Tests IPO scoring, risk assessment, recommendation generation,
and provider failure handling.
"""

import pytest
import sys
import os

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from ipo.models import IPO, IPOStatus, IPOFinancials, IPOSubscription, RiskLevel, Recommendation
from ipo.scoring import IPOScoringEngine
from ipo.analyzer import IPOAnalyzer
from ipo.providers.mock_provider import MockIPODataProvider


class TestIPOScoring:
    """Test IPO scoring engine."""
    
    def test_scoring_with_full_data(self):
        """Test scoring with complete financial and subscription data."""
        ipo = IPO(
            name="Test Company",
            symbol="TEST",
            sector="Technology",
            status=IPOStatus.OPEN,
            price_band_min=100,
            price_band_max=120,
            issue_size=500,
            fresh_issue=400,
            offer_for_sale=100,
            financials=IPOFinancials(
                revenue=1000,
                profit=150,
                revenue_growth=25,
                profit_growth=30,
                debt=50,
                profitability=15,
                pe_ratio=25,
                peer_pe=30,
                pb_ratio=3.0
            ),
            subscription=IPOSubscription(
                qib=40,
                nii=25,
                retail=15,
                overall=30
            ),
            promoter_holding=70,
            business_description="Leading technology company"
        )
        
        engine = IPOScoringEngine()
        result = engine.calculate_score(ipo)
        
        assert 'total_score' in result
        assert 'breakdown' in result
        assert 'data_quality' in result
        assert 0 <= result['total_score'] <= 100
        assert result['data_quality'] == 'FULL'
    
    def test_scoring_with_missing_data(self):
        """Test scoring handles missing data gracefully."""
        ipo = IPO(
            name="Test Company",
            symbol="TEST",
            sector="Technology",
            status=IPOStatus.OPEN,
            price_band_min=100,
            price_band_max=120,
            issue_size=500,
            financials=IPOFinancials(),  # Empty financials
            subscription=None,  # No subscription data
            promoter_holding=50
        )
        
        engine = IPOScoringEngine()
        result = engine.calculate_score(ipo)
        
        assert 'total_score' in result
        assert 0 <= result['total_score'] <= 100
        assert result['data_quality'] in ['PARTIAL', 'LIMITED']
    
    def test_scoring_with_limited_data(self):
        """Test scoring with very limited data."""
        ipo = IPO(
            name="Test Company",
            symbol="TEST",
            sector="Technology",
            status=IPOStatus.OPEN,
            price_band_min=100,
            price_band_max=120
        )
        
        engine = IPOScoringEngine()
        result = engine.calculate_score(ipo)
        
        assert 'total_score' in result
        assert 0 <= result['total_score'] <= 100
        assert result['data_quality'] == 'LIMITED'
    
    def test_financial_score_calculation(self):
        """Test financial score component."""
        financials = IPOFinancials(
            revenue=1000,
            profit=150,
            revenue_growth=30,
            profit_growth=35,
            debt=50,
            profitability=15
        )
        
        engine = IPOScoringEngine()
        score = engine._calculate_financial_score(financials)
        
        assert 0 <= score <= 100
        # Strong growth and profitability should give good score
        assert score > 50
    
    def test_valuation_score_calculation(self):
        """Test valuation score component."""
        financials = IPOFinancials(
            pe_ratio=25,
            peer_pe=30,
            pb_ratio=3.0
        )
        
        engine = IPOScoringEngine()
        score = engine._calculate_valuation_score(financials)
        
        assert 0 <= score <= 100
        # Discount to peers should give good score
        assert score > 50
    
    def test_subscription_score_calculation(self):
        """Test subscription score component."""
        subscription = IPOSubscription(
            qib=40,
            nii=25,
            retail=15,
            overall=30
        )
        
        engine = IPOScoringEngine()
        score = engine._calculate_subscription_score(subscription)
        
        assert 0 <= score <= 100
        # Good subscription should give good score
        assert score > 50


class TestRiskAssessment:
    """Test risk assessment logic."""
    
    def test_low_risk_ipo(self):
        """Test low risk IPO classification."""
        ipo = IPO(
            name="Test Company",
            symbol="TEST",
            sector="Technology",
            status=IPOStatus.OPEN,
            price_band_min=100,
            price_band_max=120,
            financials=IPOFinancials(
                pe_ratio=25,
                peer_pe=30,
                profitability=15,
                debt=50
            ),
            fresh_issue=400,
            offer_for_sale=100,
            issue_size=500,
            subscription=IPOSubscription(qib=40),
            promoter_holding=75
        )
        
        provider = MockIPODataProvider()
        analyzer = IPOAnalyzer(provider)
        result = analyzer.analyze_ipo(ipo)
        
        assert result.risk_level == RiskLevel.LOW
    
    def test_high_risk_ipo(self):
        """Test high risk IPO classification."""
        ipo = IPO(
            name="Test Company",
            symbol="TEST",
            sector="Technology",
            status=IPOStatus.OPEN,
            price_band_min=100,
            price_band_max=120,
            financials=IPOFinancials(
                pe_ratio=50,
                peer_pe=30,
                profitability=5,
                debt=300
            ),
            fresh_issue=100,
            offer_for_sale=400,
            issue_size=500,
            subscription=IPOSubscription(qib=3),
            promoter_holding=20
        )
        
        provider = MockIPODataProvider()
        analyzer = IPOAnalyzer(provider)
        result = analyzer.analyze_ipo(ipo)
        
        assert result.risk_level == RiskLevel.HIGH


class TestRecommendation:
    """Test recommendation generation."""
    
    def test_strong_candidate_recommendation(self):
        """Test STRONG CANDIDATE recommendation."""
        ipo = IPO(
            name="Test Company",
            symbol="TEST",
            sector="Technology",
            status=IPOStatus.OPEN,
            price_band_min=100,
            price_band_max=120,
            financials=IPOFinancials(
                revenue=1000,
                profit=150,
                revenue_growth=30,
                profit_growth=35,
                profitability=15,
                pe_ratio=25,
                peer_pe=30
            ),
            fresh_issue=400,
            offer_for_sale=100,
            issue_size=500,
            subscription=IPOSubscription(qib=50),
            promoter_holding=75
        )
        
        provider = MockIPODataProvider()
        analyzer = IPOAnalyzer(provider)
        result = analyzer.analyze_ipo(ipo)
        
        assert result.recommendation in [Recommendation.STRONG_CANDIDATE, Recommendation.CONSIDER]
    
    def test_avoid_recommendation(self):
        """Test AVOID recommendation for poor IPO."""
        ipo = IPO(
            name="Test Company",
            symbol="TEST",
            sector="Technology",
            status=IPOStatus.OPEN,
            price_band_min=100,
            price_band_max=120,
            financials=IPOFinancials(
                revenue=100,
                profit=5,
                revenue_growth=-5,
                profit_growth=-10,
                profitability=5,
                pe_ratio=60,
                peer_pe=30
            ),
            fresh_issue=50,
            offer_for_sale=450,
            issue_size=500,
            subscription=IPOSubscription(qib=2),
            promoter_holding=20
        )
        
        provider = MockIPODataProvider()
        analyzer = IPOAnalyzer(provider)
        result = analyzer.analyze_ipo(ipo)
        
        assert result.recommendation in [Recommendation.HIGH_RISK, Recommendation.AVOID]


class TestInsufficientData:
    """LIMITED data quality must not produce a misleading verdict."""

    def test_limited_data_gives_not_assessed(self):
        """Missing fundamentals → NOT ASSESSED / INSUFFICIENT DATA, not LOW/HIGH RISK."""
        ipo = IPO(
            name="Sparse Co",
            symbol="SPARSE",
            status=IPOStatus.OPEN,
            price_band_min=100,
            price_band_max=100,
        )
        provider = MockIPODataProvider()
        analyzer = IPOAnalyzer(provider)
        result = analyzer.analyze_ipo(ipo)

        assert result.data_quality == 'LIMITED'
        assert result.risk_level == RiskLevel.NOT_ASSESSED
        assert result.recommendation == Recommendation.INSUFFICIENT_DATA
        assert 'Insufficient data' in result.risk_explanation

    def test_partial_data_still_gets_verdict(self):
        """PARTIAL data keeps the normal risk/recommendation path."""
        ipo = IPO(
            name="Partial Co",
            symbol="PART",
            status=IPOStatus.OPEN,
            price_band_min=100,
            price_band_max=120,
            financials=IPOFinancials(
                revenue=500, profit=60, pe_ratio=25, peer_pe=30),
            subscription=IPOSubscription(qib=40, nii=20, retail=10),
            promoter_holding=70,
        )
        provider = MockIPODataProvider()
        analyzer = IPOAnalyzer(provider)
        result = analyzer.analyze_ipo(ipo)

        assert result.data_quality == 'PARTIAL'
        assert result.risk_level != RiskLevel.NOT_ASSESSED
        assert result.recommendation != Recommendation.INSUFFICIENT_DATA


class TestProviderFailure:
    """Test provider failure handling."""
    
    def test_mock_provider_returns_data(self):
        """Test mock provider returns IPO data."""
        provider = MockIPODataProvider()
        
        open_ipos = provider.get_open_ipos()
        upcoming_ipos = provider.get_upcoming_ipos()
        recent_ipos = provider.get_recent_ipos()
        
        assert len(open_ipos) > 0
        assert len(upcoming_ipos) > 0
        assert len(recent_ipos) > 0
        assert provider.is_demo_data() == True
        assert provider.get_data_source_name() == "Mock Demo Data"
    
    def test_analyzer_handles_provider_errors(self):
        """Test analyzer handles provider errors gracefully."""
        # This test would use a failing provider in a real scenario
        # For now, we test that the mock provider works correctly
        provider = MockIPODataProvider()
        analyzer = IPOAnalyzer(provider)
        
        # Should not raise exceptions
        open_analysis = analyzer.get_open_ipos_analysis()
        upcoming_analysis = analyzer.get_upcoming_ipos_analysis()
        recent_analysis = analyzer.get_recent_ipos_analysis()
        
        assert len(open_analysis) > 0
        assert len(upcoming_analysis) > 0
        assert len(recent_analysis) > 0


class TestExplainableAnalysis:
    """Test explainable analysis factors."""
    
    def test_positive_factors_generation(self):
        """Test positive factors are generated."""
        ipo = IPO(
            name="Test Company",
            symbol="TEST",
            sector="Technology",
            status=IPOStatus.OPEN,
            price_band_min=100,
            price_band_max=120,
            financials=IPOFinancials(
                revenue_growth=30,
                profit_growth=35,
                pe_ratio=25,
                peer_pe=30
            ),
            fresh_issue=400,
            offer_for_sale=100,
            issue_size=500,
            subscription=IPOSubscription(qib=40),
            promoter_holding=75
        )
        
        provider = MockIPODataProvider()
        analyzer = IPOAnalyzer(provider)
        result = analyzer.analyze_ipo(ipo)
        
        assert isinstance(result.positive_factors, list)
        assert len(result.positive_factors) > 0
    
    def test_risk_factors_generation(self):
        """Test risk factors are generated."""
        ipo = IPO(
            name="Test Company",
            symbol="TEST",
            sector="Technology",
            status=IPOStatus.OPEN,
            price_band_min=100,
            price_band_max=120,
            financials=IPOFinancials(
                pe_ratio=50,
                peer_pe=30,
                profitability=5
            ),
            fresh_issue=100,
            offer_for_sale=400,
            issue_size=500,
            subscription=IPOSubscription(qib=3),
            promoter_holding=20
        )
        
        provider = MockIPODataProvider()
        analyzer = IPOAnalyzer(provider)
        result = analyzer.analyze_ipo(ipo)
        
        assert isinstance(result.risk_factors, list)
        assert len(result.risk_factors) > 0
    
    def test_risk_explanation_generation(self):
        """Test risk explanation is generated."""
        ipo = IPO(
            name="Test Company",
            symbol="TEST",
            sector="Technology",
            status=IPOStatus.OPEN,
            price_band_min=100,
            price_band_max=120
        )
        
        provider = MockIPODataProvider()
        analyzer = IPOAnalyzer(provider)
        result = analyzer.analyze_ipo(ipo)
        
        assert result.risk_explanation is not None
        assert isinstance(result.risk_explanation, str)


class TestNseIPOProvider:
    """Live NSE provider — transport/mapping tests with a stubbed session.

    No real network is used; the session double drives every branch.
    """

    def _provider(self, payloads=None, statuses=None):
        from ipo.providers.nse_provider import NseIPODataProvider
        from unittest.mock import MagicMock

        session = MagicMock()
        queue = list(statuses or [])
        payload_iter = iter(payloads or [])

        def _get(url, timeout=None):
            resp = MagicMock()
            resp.status_code = queue.pop(0) if queue else 200
            if url.endswith('/'):
                return resp  # cookie bootstrap
            try:
                resp.json.return_value = next(payload_iter)
            except StopIteration:
                resp.json.return_value = []
            return resp

        session.get.side_effect = _get
        return NseIPODataProvider(session=session, cache_ttl=60)

    def test_maps_open_ipo_fields(self):
        from datetime import date, timedelta
        start = date.today() - timedelta(days=3)
        end = date.today() + timedelta(days=3)
        listing = date.today() + timedelta(days=7)
        fmt = '%d-%m-%Y'
        provider = self._provider(payloads=[
            [{'companyName': 'Acme Industries Ltd', 'symbol': 'ACME',
              'issueStartDate': start.strftime(fmt), 'issueEndDate': end.strftime(fmt),
              'priceBand': '₹95 to ₹100', 'issueSizeCr': '250.5',
              'listingDate': listing.strftime(fmt)}],
        ])
        ipos = provider.get_open_ipos()
        assert len(ipos) == 1
        ipo = ipos[0]
        assert ipo.name == 'Acme Industries Ltd'
        assert ipo.symbol == 'ACME'
        assert ipo.price_band_min == 95.0
        assert ipo.price_band_max == 100.0
        assert ipo.issue_size == 250.5
        assert ipo.open_date == start.isoformat()
        assert ipo.close_date == end.isoformat()
        assert ipo.listing_date == listing.isoformat()
        assert ipo.is_demo_data is False
        assert provider.is_demo_data() is False
        assert 'NSE' in provider.get_data_source_name()

    def test_min_max_price_fields(self):
        provider = self._provider(payloads=[
            [{'companyName': 'Beta Corp', 'symbol': 'BETA',
              'minPrice': '68', 'maxPrice': '72'}],
        ])
        ipos = provider.get_open_ipos()
        assert ipos[0].price_band_min == 68.0
        assert ipos[0].price_band_max == 72.0

    def test_http_error_raises_provider_error(self):
        from ipo.providers.nse_provider import IPOProviderError
        provider = self._provider(statuses=[200, 403, 200, 403])
        with pytest.raises(IPOProviderError):
            provider.get_open_ipos()

    def test_cache_prevents_refetch(self):
        provider = self._provider(payloads=[
            [{'companyName': 'Acme', 'symbol': 'ACME'}],
        ])
        first = provider.get_open_ipos()
        second = provider.get_open_ipos()
        assert first == second  # cached object returned
        # bootstrap + 3 feed fetches + 1 ipo-detail enrichment for the open issue
        assert client_calls(provider) == 5

    def test_upcoming_and_recent_endpoints(self):
        provider = self._provider(payloads=[
            [],  # ipo-current-issue (fetched first inside _all_ipos)
            [{'companyName': 'Future Co', 'symbol': 'FUT',
              'issueStartDate': '01-01-2099', 'issueEndDate': '05-01-2099'}],
            [{'companyName': 'Old Co', 'symbol': 'OLD',
              'issueStartDate': '01-01-2020', 'issueEndDate': '05-01-2020',
              'listingDate': '15-01-2020'}],
        ])
        upcoming = provider.get_upcoming_ipos()
        recent = provider.get_recent_ipos()
        assert upcoming[0].status == IPOStatus.UPCOMING
        assert recent[0].status == IPOStatus.LISTED

    def test_upcoming_excludes_already_open_symbols(self):
        """NSE's upcoming feed lists currently-open issues — dedupe by symbol."""
        provider = self._provider(payloads=[
            [{'companyName': 'Dup Co', 'symbol': 'DUP'}],   # open feed (fetched first)
            [{'companyName': 'Dup Co', 'symbol': 'DUP'},
             {'companyName': 'New Co', 'symbol': 'NEW'}],   # upcoming feed
        ])
        symbols = [i.symbol for i in provider.get_upcoming_ipos()]
        assert symbols == ['NEW']

    def test_per_category_rows_deduped_to_one_per_symbol(self):
        """Per-category subscription rows (QIB/NII/Total) collapse to one IPO."""
        provider = self._provider(payloads=[
            [{'companyName': 'Cat Co', 'symbol': 'CAT', 'category': 'QIB'},
             {'companyName': 'Cat Co', 'symbol': 'CAT', 'category': 'Total',
              'noOfTime': '2.5'}],
        ])
        open_ipos = provider.get_open_ipos()
        assert len(open_ipos) == 1
        assert open_ipos[0].subscription is not None
        assert open_ipos[0].subscription.overall == 2.5

    def test_company_field_and_rs_price_parsed(self):
        """Past-issues schema: 'company' name + 'Rs.208 to Rs.220' priceRange."""
        provider = self._provider(payloads=[
            [],  # current-issue feed
            [],  # upcoming feed
            [{'company': 'Past Co', 'symbol': 'PAST', 'securityType': 'EQ',
              'ipoStartDate': '29-SEP-2026', 'ipoEndDate': '01-OCT-2026',
              'priceRange': 'Rs.208 to Rs.220', 'issuePrice': '220',
              'listingDate': '07-OCT-2026'}],
        ])
        ipo = provider.get_recent_ipos()[0]
        assert ipo.name == 'Past Co'
        assert ipo.price_band_min == 208.0 and ipo.price_band_max == 220.0
        assert ipo.listing_date == '2026-10-07'

    def test_issue_size_shares_times_price_to_crores(self):
        """issueSize is a share count — converted to crores via issue price."""
        provider = self._provider(payloads=[
            [{'companyName': 'Sized Co', 'symbol': 'SIZ',
              'issueSize': '7500000', 'issuePrice': 'Rs.100'}],
        ])
        ipo = provider.get_open_ipos()[0]
        assert ipo.issue_size == 75.0  # 7.5M shares × ₹100 = ₹75 cr

    def test_debt_issues_separated_from_equity(self):
        """DEBT/NCD/ZCZP offerings excluded from equity lists, tracked separately."""
        from datetime import date, timedelta
        start = (date.today() - timedelta(days=3)).strftime('%d-%m-%Y')
        end = (date.today() + timedelta(days=3)).strftime('%d-%m-%Y')
        provider = self._provider(payloads=[
            [{'companyName': 'Equity Co', 'symbol': 'EQCO', 'series': 'EQ',
              'issueStartDate': start, 'issueEndDate': end},
             {'companyName': 'Bond Co', 'symbol': 'BOND', 'series': 'DEBT',
              'issueStartDate': start, 'issueEndDate': end}],
            [], [],
        ])
        open_syms = [i.symbol for i in provider.get_open_ipos()]
        assert open_syms == ['EQCO']
        non_eq = provider.get_non_equity_ipos()
        assert len(non_eq) == 1
        assert non_eq[0].symbol == 'BOND'
        assert non_eq[0].issue_type == 'DEBT'

    def test_single_price_band_display(self):
        """'Rs.10' (no range) displays as ₹10, not ₹10–₹10."""
        provider = self._provider(payloads=[
            [{'companyName': 'Solo Co', 'symbol': 'SOLO', 'issuePrice': 'Rs.10'}],
            [], [],
        ])
        ipo = provider.get_open_ipos()[0]
        assert ipo.price_band_min == 10.0
        assert ipo.get_price_display() == '₹10'

    def test_fallback_contract_no_mixed_data(self):
        """Provider raises on transport failure; dashboard falls back to
        the flagged mock — demo rows never pass as live."""
        from ipo.providers.nse_provider import IPOProviderError
        provider = self._provider(statuses=[200, 500])
        with pytest.raises(IPOProviderError):
            provider.get_open_ipos()
        # Mock fallback stays flagged demo
        mock = MockIPODataProvider()
        assert mock.is_demo_data() is True
        assert all(i.is_demo_data for i in mock.get_open_ipos())


class TestNseVerificationAndOptionalFields:
    """Verified/unverified flagging + lot-size/min-investment passthrough.

    Records may only display VERIFIED when the NSE row itself supplies a
    real company name, a symbol and at least one authoritative date.
    Optional fields are passed through when present and left None (→ N/A)
    when absent — never synthesized.
    """

    _provider = TestNseIPOProvider._provider

    def _single(self, row):
        return self._provider(payloads=[row]).get_open_ipos()[0]

    def test_lot_size_and_min_investment_mapped_when_present(self):
        ipo = self._single([{'companyName': 'Lot Co', 'symbol': 'LOT',
                             'issueStartDate': '01-01-2099',
                             'issueEndDate': '05-01-2099',
                             'lotSize': '75', 'minInvestment': '15000'}])
        assert ipo.lot_size == 75
        assert ipo.min_investment == 15000.0

    def test_lot_and_min_investment_none_when_absent(self):
        ipo = self._single([{'companyName': 'NoLot Co', 'symbol': 'NOLOT',
                             'issueStartDate': '01-01-2099'}])
        assert ipo.lot_size is None
        assert ipo.min_investment is None

    def test_verified_when_name_symbol_and_date_present(self):
        ipo = self._single([{'companyName': 'Real Co', 'symbol': 'REAL',
                             'issueStartDate': '01-01-2099',
                             'issueEndDate': '05-01-2099'}])
        assert ipo.verified is True

    def test_unverified_when_no_real_name(self):
        """Symbol-fallback names cannot be VERIFIED."""
        ipo = self._single([{'symbol': 'NONAME',
                             'issueStartDate': '01-01-2099'}])
        assert ipo.verified is False

    def test_unverified_when_no_authoritative_dates(self):
        ipo = self._single([{'companyName': 'NoDates Co', 'symbol': 'NOD'}])
        assert ipo.verified is False

    def test_rows_without_symbol_are_dropped(self):
        """A row with no symbol cannot form a record at all — dedup drops it."""
        ipos = self._provider(payloads=[
            [{'companyName': 'NoSym Co', 'issueStartDate': '01-01-2099'}],
        ]).get_open_ipos()
        assert ipos == []

    def test_zero_subscription_preserved_not_nulled(self):
        """A legitimate 0.0× subscription must stay 0.0 — display turns it
        into '0.00×', never N/A."""
        ipo = self._single([{'companyName': 'ZeroSub Co', 'symbol': 'ZSUB',
                             'issueStartDate': '01-01-2099',
                             'issueEndDate': '05-01-2099',
                             'noOfTime': '0'}])
        assert ipo.subscription is not None
        assert ipo.subscription.overall == 0.0

    def test_demo_data_not_verified(self):
        """Mock/demo rows must never claim authoritative-source verification."""
        mock = MockIPODataProvider()
        assert all(i.verified is False for i in mock.get_open_ipos())


class TestIssuePriceBandAndDetailEnrichment:
    """Real-shape NSE payloads (Oct 2026 ipo-current-issue + ipo-detail).

    Root cause fixed here: the list feed carries the band as an
    'issuePrice' STRING ("Rs.1065 to Rs.1119") which was previously
    only treated as a scalar — so band, issue_size and lot/min-invest
    all silently rendered N/A.
    """

    _provider = TestNseIPOProvider._provider

    @staticmethod
    def _today_range():
        from datetime import date, timedelta
        s = (date.today() - timedelta(days=1)).strftime('%d-%b-%Y')
        e = (date.today() + timedelta(days=2)).strftime('%d-%b-%Y')
        return s, e

    def test_issueprice_band_string_parsed(self):
        s, e = self._today_range()
        provider = self._provider(payloads=[
            [{'companyName': 'Mega Corp Ltd', 'symbol': 'MEGA',
              'issueStartDate': s, 'issueEndDate': e,
              'issuePrice': 'Rs.1065 to Rs.1119',
              'issueSize': '197525934', 'noOfSharesOffered': '1.97525934E8',
              'noOfTime': '1.0485', 'series': 'EQ', 'category': 'Total'}],
        ])
        ipo = provider.get_open_ipos()[0]
        assert ipo.price_band_min == 1065.0
        assert ipo.price_band_max == 1119.0
        # issue_size = shares × cap / 1e7
        assert ipo.issue_size == round(197525934 * 1119 / 1e7, 2)
        assert ipo.subscription.overall == 1.0485

    def test_issueprice_scalar_price(self):
        provider = self._provider(payloads=[
            [{'companyName': 'Fixed Co', 'symbol': 'FIX',
              'issuePrice': 'Rs.340'}],
        ])
        ipo = provider.get_open_ipos()[0]
        assert ipo.price_band_min == 340.0
        assert ipo.price_band_max == 340.0

    def test_detail_enrichment_lot_min_investment_subscription(self):
        s, e = self._today_range()
        detail = {
            'bidDetails': [
                {'category': 'Qualified Institutional Buyers(QIBs)', 'noOfTime': '2.625'},
                {'category': 'Non Institutional Investors', 'noOfTime': '0.660'},
                {'category': 'Retail Individual Investors(RIIs)', 'noOfTime': '0.382'},
            ],
            'issueInfo': {'dataList': [
                {'title': 'Bid Lot', 'value': '12 Equity Shares and in multiples thereof'},
                {'title': 'Minimum Order Quantity', 'value': '12 Equity Shares'},
                {'title': 'Price Range', 'value': 'Rs. 1065 to Rs. 1119 per Equity Share'},
                {'title': 'Issue Size', 'value': 'Fresh Issue of up to 27,00,00,000 equity shares'},
            ]},
        }
        provider = self._provider(payloads=[
            [{'companyName': 'Big Platform Ltd', 'symbol': 'BIG',
              'issueStartDate': s, 'issueEndDate': e,
              'issuePrice': 'Rs.1065 to Rs.1119', 'series': 'EQ',
              'category': 'Total', 'noOfTime': '1.05'}],
            [],  # upcoming feed
            [],  # past feed
            detail,  # ipo-detail for BIG
        ])
        ipo = provider.get_open_ipos()[0]
        assert ipo.lot_size == 12
        assert ipo.min_investment == round(1119 * 12, 2)  # 13428
        assert ipo.fresh_issue == round(270000000 * 1119 / 1e7, 2)
        assert ipo.subscription.qib == 2.625
        assert ipo.subscription.nii == 0.660
        assert ipo.subscription.retail == 0.382
        assert ipo.subscription.overall == 1.05  # list-feed value preserved

    def test_min_investment_not_fabricated_without_lot_or_price(self):
        """No fabrication: missing lot OR price → min_investment stays N/A."""
        s, e = self._today_range()
        detail_no_lot = {'issueInfo': {'dataList': [
            {'title': 'Price Range', 'value': 'Rs. 100 to Rs. 110'}]}}
        provider = self._provider(payloads=[
            [{'companyName': 'NoLot Ltd', 'symbol': 'NL',
              'issueStartDate': s, 'issueEndDate': e, 'series': 'EQ',
              'category': 'Total'}],
            [], [], detail_no_lot,
        ])
        ipo = provider.get_open_ipos()[0]
        assert ipo.price_band_min == 100.0
        assert ipo.lot_size is None
        assert ipo.min_investment is None

    def test_detail_failure_degrades_record_not_list(self):
        """A failing ipo-detail call leaves the record intact (N/A fields)."""
        s, e = self._today_range()
        provider = self._provider(
            payloads=[
                [{'companyName': 'Fragile Ltd', 'symbol': 'FRAG',
                  'issueStartDate': s, 'issueEndDate': e,
                  'issuePrice': 'Rs.100 to Rs.110', 'series': 'EQ',
                  'category': 'Total'}],
                [], []],
            statuses=[200, 200, 200, 200, 500],  # detail call -> HTTP 500
        )
        ipos = provider.get_open_ipos()
        assert len(ipos) == 1
        assert ipos[0].price_band_max == 110.0
        assert ipos[0].lot_size is None

    def test_stale_cache_served_on_refresh_failure(self):
        """Provider outage after a successful fetch → last-known-good data
        returned and flagged stale instead of erroring out."""
        provider = self._provider(
            payloads=[
                [{'companyName': 'Stable Co', 'symbol': 'STB',
                  'issueStartDate': '01-01-2099', 'issueEndDate': '05-01-2099'}],
            ],
            statuses=[200, 200, 200, 200, 200,   # call 1: bootstrap + 3 feeds + detail
                      403, 200, 403],            # call 2: 403 → re-bootstrap → 403
        )
        provider._cache_ttl = 0  # force refetch on second call
        first = provider.get_open_ipos()
        assert provider._serving_stale is False
        second = provider.get_open_ipos()
        assert second[0] is first[0]  # same cached IPO objects returned
        assert provider._serving_stale is True
        assert provider.last_success_at is not None

    def test_cold_failure_still_raises(self):
        """No cache + fetch failure → error propagates (dashboard falls back)."""
        from ipo.providers.nse_provider import IPOProviderError
        provider = self._provider(statuses=[200, 500])
        with pytest.raises(IPOProviderError):
            provider.get_open_ipos()


def client_calls(provider):
    """Count session.get invocations on the stubbed session."""
    return provider._session.get.call_count


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
