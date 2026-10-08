"""
IPO Analyzer

Main IPO analysis engine that combines scoring, risk assessment, and recommendation generation.
"""

import logging
from typing import List, Optional, Dict, Any
from .models import IPO, IPOAnalysisResult, RiskLevel, Recommendation, IPOFinancials, IPOSubscription
from .scoring import IPOScoringEngine
from .providers.base import IPODataProvider

logger = logging.getLogger(__name__)


class IPOAnalyzer:
    """
    IPO analyzer that provides comprehensive analysis including scoring,
    risk assessment, and recommendations.
    """
    
    def __init__(self, provider: IPODataProvider):
        """
        Initialize IPO analyzer with a data provider.
        
        Args:
            provider: IPO data provider (mock or live).
        """
        self.provider = provider
        self.scoring_engine = IPOScoringEngine()
    
    def analyze_ipo(self, ipo: IPO) -> IPOAnalysisResult:
        """
        Perform comprehensive analysis of an IPO.
        
        Args:
            ipo: IPO object to analyze.
            
        Returns:
            IPOAnalysisResult with score, risk, recommendation, and factors.
        """
        # Calculate score
        score_result = self.scoring_engine.calculate_score(ipo)
        total_score = score_result['total_score']
        breakdown = score_result['breakdown']
        data_quality = score_result['data_quality']
        
        # Assess risk level and recommendation.
        # LIMITED data means risk cannot be evaluated reliably — report
        # NOT ASSESSED / INSUFFICIENT DATA rather than a misleading verdict.
        if data_quality == 'LIMITED':
            risk_level = RiskLevel.NOT_ASSESSED
            recommendation = Recommendation.INSUFFICIENT_DATA
        else:
            risk_level = self._assess_risk_level(ipo, total_score, breakdown)
            recommendation = self._generate_recommendation(
                total_score, risk_level, data_quality)

        # Generate explainable factors
        positive_factors = self._generate_positive_factors(ipo, breakdown)
        risk_factors = self._generate_risk_factors(ipo, breakdown)

        # Generate risk explanation
        if risk_level == RiskLevel.NOT_ASSESSED:
            risk_explanation = ("Insufficient data to assess risk — "
                                "fundamental and subscription inputs unavailable.")
        else:
            risk_explanation = self._generate_risk_explanation(ipo, risk_factors)
        
        return IPOAnalysisResult(
            ipo=ipo,
            score=total_score,
            risk_level=risk_level,
            recommendation=recommendation,
            financial_score=breakdown['financial_score'],
            valuation_score=breakdown['valuation_score'],
            structure_score=breakdown['structure_score'],
            subscription_score=breakdown['subscription_score'],
            business_score=breakdown['business_score'],
            market_score=breakdown['market_score'],
            positive_factors=positive_factors,
            risk_factors=risk_factors,
            risk_explanation=risk_explanation,
            data_quality=data_quality
        )
    
    def analyze_all_ipos(self) -> List[IPOAnalysisResult]:
        """
        Analyze all available IPOs (open, upcoming, recent).
        
        Returns:
            List of IPOAnalysisResult objects.
        """
        all_ipos = []
        
        try:
            all_ipos.extend(self.provider.get_open_ipos())
        except Exception as e:
            logger.error(f"Error fetching open IPOs: {e}")
        
        try:
            all_ipos.extend(self.provider.get_upcoming_ipos())
        except Exception as e:
            logger.error(f"Error fetching upcoming IPOs: {e}")
        
        try:
            all_ipos.extend(self.provider.get_recent_ipos())
        except Exception as e:
            logger.error(f"Error fetching recent IPOs: {e}")
        
        results = []
        for ipo in all_ipos:
            try:
                result = self.analyze_ipo(ipo)
                results.append(result)
            except Exception as e:
                logger.error(f"Error analyzing IPO {ipo.name}: {e}")
        
        return results
    
    def get_open_ipos_analysis(self) -> List[IPOAnalysisResult]:
        """Get analysis for currently open IPOs."""
        try:
            open_ipos = self.provider.get_open_ipos()
            return [self.analyze_ipo(ipo) for ipo in open_ipos]
        except Exception as e:
            logger.error(f"Error analyzing open IPOs: {e}")
            return []
    
    def get_upcoming_ipos_analysis(self) -> List[IPOAnalysisResult]:
        """Get analysis for upcoming IPOs."""
        try:
            upcoming_ipos = self.provider.get_upcoming_ipos()
            return [self.analyze_ipo(ipo) for ipo in upcoming_ipos]
        except Exception as e:
            logger.error(f"Error analyzing upcoming IPOs: {e}")
            return []
    
    def get_recent_ipos_analysis(self) -> List[IPOAnalysisResult]:
        """Get analysis for recently closed/listed IPOs."""
        try:
            recent_ipos = self.provider.get_recent_ipos()
            return [self.analyze_ipo(ipo) for ipo in recent_ipos]
        except Exception as e:
            logger.error(f"Error analyzing recent IPOs: {e}")
            return []
    
    def _assess_risk_level(self, ipo: IPO, score: float, breakdown: Dict[str, float]) -> RiskLevel:
        """
        Assess risk level based on multiple factors.
        
        Args:
            ipo: IPO object.
            score: Total IPO score.
            breakdown: Score breakdown by component.
            
        Returns:
            RiskLevel enum value.
        """
        risk_points = 0
        
        financials = ipo.financials or IPOFinancials()
        
        # High valuation risk
        if financials.pe_ratio and financials.pe_ratio > 40:
            risk_points += 2
        elif financials.pe_ratio and financials.pe_ratio > 30:
            risk_points += 1
        
        # Valuation premium to peers
        if financials.pe_ratio and financials.peer_pe and financials.peer_pe > 0:
            if financials.pe_ratio > financials.peer_pe * 1.3:
                risk_points += 2
            elif financials.pe_ratio > financials.peer_pe * 1.1:
                risk_points += 1
        
        # Weak profitability
        if financials.profitability is not None:
            if financials.profitability < 5:
                risk_points += 2
            elif financials.profitability < 10:
                risk_points += 1
        
        # High debt
        if financials.debt and financials.revenue and financials.revenue > 0:
            debt_to_revenue = financials.debt / financials.revenue
            if debt_to_revenue > 2.0:
                risk_points += 2
            elif debt_to_revenue > 1.0:
                risk_points += 1
        
        # Large OFS component
        if ipo.offer_for_sale and ipo.issue_size and ipo.issue_size > 0:
            ofs_ratio = ipo.offer_for_sale / ipo.issue_size
            if ofs_ratio > 0.5:
                risk_points += 2
            elif ofs_ratio > 0.3:
                risk_points += 1
        
        # Low institutional demand
        subscription = ipo.subscription or IPOSubscription()
        if subscription.qib is not None:
            if subscription.qib < 5:
                risk_points += 2
            elif subscription.qib < 10:
                risk_points += 1
        
        # Low promoter holding
        if ipo.promoter_holding is not None:
            if ipo.promoter_holding < 25:
                risk_points += 2
            elif ipo.promoter_holding < 50:
                risk_points += 1
        
        # Low score
        if score < 40:
            risk_points += 2
        elif score < 55:
            risk_points += 1
        
        # Determine risk level
        if risk_points >= 6:
            return RiskLevel.HIGH
        elif risk_points >= 3:
            return RiskLevel.MEDIUM
        else:
            return RiskLevel.LOW
    
    def _generate_recommendation(self, score: float, risk_level: RiskLevel, data_quality: str) -> Recommendation:
        """
        Generate recommendation based on score and risk.
        
        Args:
            score: Total IPO score.
            risk_level: Assessed risk level.
            data_quality: Quality of available data.
            
        Returns:
            Recommendation enum value.
        """
        # Adjust score based on data quality
        adjusted_score = score
        if data_quality == 'PARTIAL':
            adjusted_score -= 5
        elif data_quality == 'LIMITED':
            adjusted_score -= 10
        
        # Adjust based on risk
        if risk_level == RiskLevel.HIGH:
            adjusted_score -= 10
        elif risk_level == RiskLevel.MEDIUM:
            adjusted_score -= 5
        
        # Generate recommendation
        if adjusted_score >= 80:
            return Recommendation.STRONG_CANDIDATE
        elif adjusted_score >= 65:
            return Recommendation.CONSIDER
        elif adjusted_score >= 50:
            return Recommendation.WATCH
        elif adjusted_score >= 35:
            return Recommendation.HIGH_RISK
        else:
            return Recommendation.AVOID
    
    def _generate_positive_factors(self, ipo: IPO, breakdown: Dict[str, float]) -> List[str]:
        """Generate list of positive factors for the IPO."""
        factors = []
        financials = ipo.financials or IPOFinancials()
        subscription = ipo.subscription or IPOSubscription()
        
        # Financial strength
        if breakdown['financial_score'] >= 70:
            factors.append("Strong financial performance")
        elif financials.revenue_growth and financials.revenue_growth >= 20:
            factors.append(f"Strong revenue growth ({financials.revenue_growth:.1f}%)")
        elif financials.profit_growth and financials.profit_growth >= 20:
            factors.append(f"Strong profit growth ({financials.profit_growth:.1f}%)")
        
        # Valuation
        if breakdown['valuation_score'] >= 70:
            factors.append("Attractive valuation")
        elif financials.pe_ratio and financials.peer_pe and financials.peer_pe > 0:
            if financials.pe_ratio < financials.peer_pe:
                factors.append("Valuation discount to peers")
        
        # Structure
        if breakdown['structure_score'] >= 70:
            factors.append("Favorable issue structure")
        elif ipo.fresh_issue and ipo.issue_size and ipo.issue_size > 0:
            fresh_ratio = ipo.fresh_issue / ipo.issue_size
            if fresh_ratio >= 0.5:
                factors.append("Majority fresh issue (capital for growth)")
        
        # Subscription
        if breakdown['subscription_score'] >= 70:
            factors.append("Strong institutional demand")
        elif subscription.qib and subscription.qib >= 30:
            factors.append(f"Strong QIB subscription ({subscription.qib:.1f}x)")
        elif subscription.overall and subscription.overall >= 25:
            factors.append(f"Good overall subscription ({subscription.overall:.1f}x)")
        
        # Business
        if breakdown['business_score'] >= 70:
            factors.append("Strong business fundamentals")
        
        # Promoter holding
        if ipo.promoter_holding and ipo.promoter_holding >= 70:
            factors.append(f"High promoter holding ({ipo.promoter_holding:.0f}%)")
        
        # Limit to top 5 factors
        return factors[:5]
    
    def _generate_risk_factors(self, ipo: IPO, breakdown: Dict[str, float]) -> List[str]:
        """Generate list of risk factors for the IPO."""
        factors = []
        financials = ipo.financials or IPOFinancials()
        subscription = ipo.subscription or IPOSubscription()
        
        # Valuation risk
        if breakdown['valuation_score'] <= 40:
            factors.append("High valuation")
        elif financials.pe_ratio and financials.peer_pe and financials.peer_pe > 0:
            if financials.pe_ratio > financials.peer_pe * 1.2:
                factors.append("Valuation premium to peers")
        
        # Financial risk
        if breakdown['financial_score'] <= 40:
            factors.append("Weak financial performance")
        elif financials.profitability is not None and financials.profitability < 10:
            factors.append("Low profitability")
        elif financials.revenue_growth is not None and financials.revenue_growth < 5:
            factors.append("Slow revenue growth")
        
        # Debt risk
        if financials.debt and financials.revenue and financials.revenue > 0:
            debt_to_revenue = financials.debt / financials.revenue
            if debt_to_revenue > 1.5:
                factors.append("High debt levels")
        
        # Structure risk
        if ipo.offer_for_sale and ipo.issue_size and ipo.issue_size > 0:
            ofs_ratio = ipo.offer_for_sale / ipo.issue_size
            if ofs_ratio > 0.5:
                factors.append("Large OFS component")
        
        # Subscription risk
        if subscription.qib is not None and subscription.qib < 10:
            factors.append("Low institutional demand")
        
        # Promoter risk
        if ipo.promoter_holding is not None and ipo.promoter_holding < 50:
            factors.append("Low promoter holding post-IPO")
        
        # Limit to top 5 factors
        return factors[:5]
    
    def _generate_risk_explanation(self, ipo: IPO, risk_factors: List[str]) -> str:
        """Generate a concise risk explanation."""
        if not risk_factors:
            return "Low risk profile with strong fundamentals."
        
        explanation = "Risk factors: " + ", ".join(risk_factors[:3])
        if len(risk_factors) > 3:
            explanation += f" (and {len(risk_factors) - 3} more)"
        
        return explanation
