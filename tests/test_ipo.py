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
        provider = self._provider(payloads=[
            [{'companyName': 'Acme Industries Ltd', 'symbol': 'ACME',
              'issueStartDate': '05-10-2026', 'issueEndDate': '08-10-2026',
              'priceBand': '₹95 to ₹100', 'issueSizeCr': '250.5',
              'listingDate': '15-10-2026'}],
        ])
        ipos = provider.get_open_ipos()
        assert len(ipos) == 1
        ipo = ipos[0]
        assert ipo.name == 'Acme Industries Ltd'
        assert ipo.symbol == 'ACME'
        assert ipo.price_band_min == 95.0
        assert ipo.price_band_max == 100.0
        assert ipo.issue_size == 250.5
        assert ipo.open_date == '2026-10-05'
        assert ipo.close_date == '2026-10-08'
        assert ipo.listing_date == '2026-10-15'
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
        assert first is second  # cached object returned
        assert client_calls(provider) == 2  # bootstrap + one fetch

    def test_upcoming_and_recent_endpoints(self):
        provider = self._provider(payloads=[
            [{'companyName': 'Future Co', 'symbol': 'FUT',
              'issueStartDate': '01-01-2099', 'issueEndDate': '05-01-2099'}],
            [],  # open-issues fetch made while deduping upcoming
            [{'companyName': 'Old Co', 'symbol': 'OLD',
              'issueStartDate': '01-01-2020', 'issueEndDate': '05-01-2020',
              'listingDate': '15-01-2020'}],
        ])
        upcoming = provider.get_upcoming_ipos()
        recent = provider.get_recent_ipos()
        assert upcoming[0].status in (IPOStatus.UPCOMING, IPOStatus.OPEN)
        assert recent[0].status == IPOStatus.LISTED

    def test_upcoming_excludes_already_open_symbols(self):
        """NSE's upcoming feed lists currently-open issues — dedupe by symbol."""
        provider = self._provider(payloads=[
            [{'companyName': 'Dup Co', 'symbol': 'DUP'},
             {'companyName': 'New Co', 'symbol': 'NEW'}],   # upcoming feed
            [{'companyName': 'Dup Co', 'symbol': 'DUP'}],   # open feed
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


def client_calls(provider):
    """Count session.get invocations on the stubbed session."""
    return provider._session.get.call_count


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
