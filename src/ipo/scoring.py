"""
IPO Scoring Engine

Calculates IPO scores based on multiple factors including financial quality,
valuation, issue structure, subscription, business quality, and market conditions.
"""

import logging
from typing import Optional, Dict, Any
from .models import IPO, IPOFinancials, IPOSubscription

logger = logging.getLogger(__name__)


class IPOScoringEngine:
    """
    IPO scoring engine that evaluates IPOs on multiple dimensions.
    
    Score ranges from 0-100, with higher scores indicating better IPOs.
    Missing data is handled safely by adjusting weights rather than assuming values.
    """
    
    def __init__(self):
        # Weight distribution for different factors (sum = 1.0)
        self.weights = {
            'financial': 0.25,      # Financial quality
            'valuation': 0.20,       # Valuation metrics
            'structure': 0.15,      # Issue structure
            'subscription': 0.20,    # Subscription demand
            'business': 0.10,       # Business quality
            'market': 0.10          # Market conditions
        }
    
    def calculate_score(self, ipo: IPO) -> Dict[str, Any]:
        """
        Calculate comprehensive IPO score.
        
        Args:
            ipo: IPO object with financials and subscription data.
            
        Returns:
            Dictionary with:
            - total_score: Overall score (0-100)
            - breakdown: Individual component scores
            - data_quality: Assessment of data availability
        """
        financials = ipo.financials or IPOFinancials()
        subscription = ipo.subscription or IPOSubscription()
        
        # Calculate individual component scores
        financial_score = self._calculate_financial_score(financials)
        valuation_score = self._calculate_valuation_score(financials)
        structure_score = self._calculate_structure_score(ipo)
        subscription_score = self._calculate_subscription_score(subscription)
        business_score = self._calculate_business_score(ipo)
        market_score = self._calculate_market_score(ipo)
        
        # Calculate weighted total score
        total_score = (
            financial_score * self.weights['financial'] +
            valuation_score * self.weights['valuation'] +
            structure_score * self.weights['structure'] +
            subscription_score * self.weights['subscription'] +
            business_score * self.weights['business'] +
            market_score * self.weights['market']
        )
        
        # Determine data quality
        data_quality = self._assess_data_quality(ipo, financials, subscription)
        
        return {
            'total_score': round(total_score, 1),
            'breakdown': {
                'financial_score': round(financial_score, 1),
                'valuation_score': round(valuation_score, 1),
                'structure_score': round(structure_score, 1),
                'subscription_score': round(subscription_score, 1),
                'business_score': round(business_score, 1),
                'market_score': round(market_score, 1)
            },
            'data_quality': data_quality
        }
    
    def _calculate_financial_score(self, financials: IPOFinancials) -> float:
        """
        Calculate financial quality score (0-100).
        
        Factors: revenue growth, profit growth, profitability, debt levels.
        """
        score = 50.0  # Base score
        
        # Revenue growth (weight: 30%)
        if financials.revenue_growth is not None:
            if financials.revenue_growth >= 30:
                score += 15
            elif financials.revenue_growth >= 20:
                score += 12
            elif financials.revenue_growth >= 10:
                score += 8
            elif financials.revenue_growth >= 5:
                score += 4
            elif financials.revenue_growth < 0:
                score -= 10
        
        # Profit growth (weight: 25%)
        if financials.profit_growth is not None:
            if financials.profit_growth >= 30:
                score += 12
            elif financials.profit_growth >= 20:
                score += 10
            elif financials.profit_growth >= 10:
                score += 6
            elif financials.profit_growth >= 5:
                score += 3
            elif financials.profit_growth < 0:
                score -= 8
        
        # Profitability (weight: 25%)
        if financials.profitability is not None:
            if financials.profitability >= 15:
                score += 12
            elif financials.profitability >= 10:
                score += 10
            elif financials.profitability >= 5:
                score += 6
            elif financials.profitability < 0:
                score -= 10
        
        # Debt levels (weight: 20%)
        if financials.debt is not None and financials.revenue is not None:
            debt_to_revenue = financials.debt / financials.revenue if financials.revenue > 0 else 999
            if debt_to_revenue <= 0.5:
                score += 10
            elif debt_to_revenue <= 1.0:
                score += 6
            elif debt_to_revenue <= 2.0:
                score += 2
            elif debt_to_revenue > 3.0:
                score -= 10
        
        return max(0, min(100, score))
    
    def _calculate_valuation_score(self, financials: IPOFinancials) -> float:
        """
        Calculate valuation score (0-100).
        
        Factors: P/E ratio, P/B ratio, comparison with peers.
        """
        score = 50.0  # Base score
        
        # P/E ratio comparison with peers (weight: 50%)
        if financials.pe_ratio is not None and financials.peer_pe is not None:
            if financials.peer_pe > 0:
                pe_discount = (financials.peer_pe - financials.pe_ratio) / financials.peer_pe
                if pe_discount >= 0.2:  # 20% discount to peers
                    score += 20
                elif pe_discount >= 0.1:
                    score += 15
                elif pe_discount >= 0:
                    score += 10
                elif pe_discount >= -0.1:
                    score += 5
                elif pe_discount < -0.2:  # 20% premium to peers
                    score -= 15
        
        # P/B ratio (weight: 25%)
        if financials.pb_ratio is not None:
            if financials.pb_ratio <= 2.0:
                score += 12
            elif financials.pb_ratio <= 3.0:
                score += 8
            elif financials.pb_ratio <= 4.0:
                score += 4
            elif financials.pb_ratio > 6.0:
                score -= 10
        
        # Absolute P/E reasonableness (weight: 25%)
        if financials.pe_ratio is not None:
            if financials.pe_ratio <= 20:
                score += 12
            elif financials.pe_ratio <= 30:
                score += 8
            elif financials.pe_ratio <= 40:
                score += 4
            elif financials.pe_ratio > 50:
                score -= 10
        
        return max(0, min(100, score))
    
    def _calculate_structure_score(self, ipo: IPO) -> float:
        """
        Calculate issue structure score (0-100).
        
        Factors: fresh issue vs OFS, promoter holding, use of proceeds.
        """
        score = 50.0  # Base score
        
        # Fresh issue vs OFS (weight: 40%)
        if ipo.fresh_issue is not None and ipo.issue_size is not None and ipo.issue_size > 0:
            fresh_issue_ratio = ipo.fresh_issue / ipo.issue_size
            if fresh_issue_ratio >= 0.75:  # Mostly fresh issue
                score += 20
            elif fresh_issue_ratio >= 0.5:
                score += 15
            elif fresh_issue_ratio >= 0.25:
                score += 10
            elif fresh_issue_ratio < 0.25:  # Mostly OFS
                score -= 10
        
        # Promoter holding post-IPO (weight: 35%)
        if ipo.promoter_holding is not None:
            if ipo.promoter_holding >= 75:
                score += 17
            elif ipo.promoter_holding >= 60:
                score += 12
            elif ipo.promoter_holding >= 50:
                score += 8
            elif ipo.promoter_holding < 25:
                score -= 15
        
        # Use of proceeds (weight: 25%)
        if ipo.use_of_proceeds:
            # Check for positive use of proceeds keywords
            positive_keywords = ['expansion', 'capacity', 'manufacturing', 'r&d', 'research', 'infrastructure']
            negative_keywords = ['repayment', 'offer for sale', 'ofc', 'exiting investor']
            
            proceeds_lower = ipo.use_of_proceeds.lower()
            positive_count = sum(1 for kw in positive_keywords if kw in proceeds_lower)
            negative_count = sum(1 for kw in negative_keywords if kw in proceeds_lower)
            
            if positive_count >= 2 and negative_count == 0:
                score += 12
            elif positive_count >= 1 and negative_count == 0:
                score += 8
            elif negative_count >= 2:
                score -= 10
        
        return max(0, min(100, score))
    
    def _calculate_subscription_score(self, subscription: IPOSubscription) -> float:
        """
        Calculate subscription score (0-100).
        
        Factors: QIB, NII, retail, and overall subscription levels.
        """
        score = 50.0  # Base score
        
        # QIB subscription (weight: 35%)
        if subscription.qib is not None:
            if subscription.qib >= 50:
                score += 17
            elif subscription.qib >= 30:
                score += 12
            elif subscription.qib >= 15:
                score += 8
            elif subscription.qib >= 5:
                score += 4
            elif subscription.qib < 2:
                score -= 15
        
        # NII subscription (weight: 25%)
        if subscription.nii is not None:
            if subscription.nii >= 30:
                score += 12
            elif subscription.nii >= 20:
                score += 8
            elif subscription.nii >= 10:
                score += 5
            elif subscription.nii < 3:
                score -= 10
        
        # Retail subscription (weight: 20%)
        if subscription.retail is not None:
            if subscription.retail >= 20:
                score += 10
            elif subscription.retail >= 10:
                score += 6
            elif subscription.retail >= 5:
                score += 3
            elif subscription.retail < 2:
                score -= 8
        
        # Overall subscription (weight: 20%)
        if subscription.overall is not None:
            if subscription.overall >= 40:
                score += 10
            elif subscription.overall >= 25:
                score += 6
            elif subscription.overall >= 15:
                score += 3
            elif subscription.overall < 5:
                score -= 10
        
        return max(0, min(100, score))
    
    def _calculate_business_score(self, ipo: IPO) -> float:
        """
        Calculate business quality score (0-100).
        
        Factors: sector, business description quality, competitive position.
        """
        score = 50.0  # Base score
        
        # Sector quality (weight: 50%)
        preferred_sectors = ['technology', 'pharmaceuticals', 'healthcare', 'financial services', 'consumer goods']
        neutral_sectors = ['manufacturing', 'automotive', 'infrastructure', 'renewable energy']
        cautious_sectors = ['real estate', 'textiles', 'commodities']
        
        if ipo.sector:
            sector_lower = ipo.sector.lower()
            if any(ps in sector_lower for ps in preferred_sectors):
                score += 25
            elif any(ns in sector_lower for ns in neutral_sectors):
                score += 15
            elif any(cs in sector_lower for cs in cautious_sectors):
                score -= 10
        
        # Business description quality (weight: 50%)
        if ipo.business_description:
            desc_lower = ipo.business_description.lower()
            
            # Positive indicators
            positive_indicators = ['leading', 'market leader', 'proprietary', 'patented', 'innovative', 'technology', 'r&d']
            positive_count = sum(1 for ind in positive_indicators if ind in desc_lower)
            
            if positive_count >= 2:
                score += 25
            elif positive_count >= 1:
                score += 15
            elif len(ipo.business_description) < 50:
                score -= 10  # Too brief
        
        return max(0, min(100, score))
    
    def _calculate_market_score(self, ipo: IPO) -> float:
        """
        Calculate market conditions score (0-100).
        
        Factors: broad market conditions, sector conditions.
        
        Note: In V1, this uses simplified logic. In future versions,
        this could integrate with market_regime module.
        """
        # Base score - assumes neutral market conditions
        # In V1, we use a conservative baseline
        score = 50.0
        
        # Sector-specific adjustments (simplified for V1)
        if ipo.sector:
            sector_lower = ipo.sector.lower()
            
            # Sectors that tend to perform well in various conditions
            resilient_sectors = ['pharmaceuticals', 'healthcare', 'consumer goods', 'technology']
            if any(rs in sector_lower for rs in resilient_sectors):
                score += 10
        
        return max(0, min(100, score))
    
    def _assess_data_quality(self, ipo: IPO, financials: IPOFinancials, subscription: IPOSubscription) -> str:
        """
        Assess the quality of available data.
        
        Returns:
            'FULL', 'PARTIAL', or 'LIMITED'
        """
        data_points = 0
        total_points = 15  # Total expected data points
        
        # Check financials
        if financials.revenue is not None:
            data_points += 1
        if financials.profit is not None:
            data_points += 1
        if financials.revenue_growth is not None:
            data_points += 1
        if financials.profit_growth is not None:
            data_points += 1
        if financials.profitability is not None:
            data_points += 1
        if financials.pe_ratio is not None:
            data_points += 1
        if financials.peer_pe is not None:
            data_points += 1
        
        # Check subscription
        if subscription.qib is not None:
            data_points += 1
        if subscription.nii is not None:
            data_points += 1
        if subscription.retail is not None:
            data_points += 1
        if subscription.overall is not None:
            data_points += 1
        
        # Check structure
        if ipo.fresh_issue is not None:
            data_points += 1
        if ipo.offer_for_sale is not None:
            data_points += 1
        if ipo.promoter_holding is not None:
            data_points += 1
        
        # Assess quality
        if data_points >= 12:
            return 'FULL'
        elif data_points >= 7:
            return 'PARTIAL'
        else:
            return 'LIMITED'
