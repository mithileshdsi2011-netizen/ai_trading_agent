"""
Mock IPO Data Provider

Provides sample/demo IPO data for testing and demonstration.
All data is clearly marked as demo data.
"""

from typing import List, Optional
from datetime import datetime, timedelta
from .base import IPODataProvider
from ..models import IPO, IPOStatus, IPOFinancials, IPOSubscription


class MockIPODataProvider(IPODataProvider):
    """
    Mock IPO data provider with sample data.
    
    This provider uses clearly labeled demo data for testing
    and demonstration purposes. It should never be used in production.
    """
    
    def __init__(self):
        self._demo_ipos = self._generate_demo_ipos()
    
    def _generate_demo_ipos(self) -> List[IPO]:
        """Generate sample IPO data for demonstration."""
        now = datetime.now()
        
        # Open IPOs
        open_ipos = [
            IPO(
                name="TechNova Solutions Ltd",
                symbol="TECHNOVA",
                sector="Technology",
                status=IPOStatus.OPEN,
                price_band_min=450,
                price_band_max=475,
                issue_size=850,
                fresh_issue=600,
                offer_for_sale=250,
                open_date=(now - timedelta(days=2)).strftime("%Y-%m-%d"),
                close_date=(now + timedelta(days=3)).strftime("%Y-%m-%d"),
                listing_date=(now + timedelta(days=10)).strftime("%Y-%m-%d"),
                financials=IPOFinancials(
                    revenue=1250,
                    profit=180,
                    revenue_growth=28.5,
                    profit_growth=35.2,
                    debt=85,
                    cash_flow=145,
                    profitability=14.4,
                    pe_ratio=28.5,
                    pb_ratio=3.2,
                    peer_pe=32.0
                ),
                subscription=IPOSubscription(
                    qib=45.2,
                    nii=32.8,
                    retail=18.5,
                    overall=35.5
                ),
                promoter_holding=72.5,
                use_of_proceeds="Expansion of manufacturing facilities, working capital, general corporate purposes",
                business_description="Leading provider of enterprise software solutions and cloud services",
                is_demo_data=True,
                data_source="Mock Demo Data"
            ),
            IPO(
                name="GreenEnergy Corp",
                symbol="GREENE",
                sector="Renewable Energy",
                status=IPOStatus.OPEN,
                price_band_min=280,
                price_band_max=295,
                issue_size=420,
                fresh_issue=350,
                offer_for_sale=70,
                open_date=(now - timedelta(days=1)).strftime("%Y-%m-%d"),
                close_date=(now + timedelta(days=4)).strftime("%Y-%m-%d"),
                listing_date=(now + timedelta(days=11)).strftime("%Y-%m-%d"),
                financials=IPOFinancials(
                    revenue=680,
                    profit=95,
                    revenue_growth=42.3,
                    profit_growth=48.7,
                    debt=120,
                    cash_flow=85,
                    profitability=13.9,
                    pe_ratio=32.0,
                    pb_ratio=2.8,
                    peer_pe=28.0
                ),
                subscription=IPOSubscription(
                    qib=52.8,
                    nii=38.5,
                    retail=22.3,
                    overall=42.5
                ),
                promoter_holding=68.0,
                use_of_proceeds="Setting up solar power plants, debt repayment, working capital",
                business_description="Renewable energy company focused on solar and wind power generation",
                is_demo_data=True,
                data_source="Mock Demo Data"
            )
        ]
        
        # Upcoming IPOs
        upcoming_ipos = [
            IPO(
                name="PharmaCare Innovations",
                symbol="PHARMA",
                sector="Pharmaceuticals",
                status=IPOStatus.UPCOMING,
                price_band_min=680,
                price_band_max=720,
                issue_size=1200,
                fresh_issue=900,
                offer_for_sale=300,
                open_date=(now + timedelta(days=5)).strftime("%Y-%m-%d"),
                close_date=(now + timedelta(days=8)).strftime("%Y-%m-%d"),
                listing_date=(now + timedelta(days=15)).strftime("%Y-%m-%d"),
                financials=IPOFinancials(
                    revenue=1850,
                    profit=265,
                    revenue_growth=18.5,
                    profit_growth=22.3,
                    debt=180,
                    cash_flow=220,
                    profitability=14.3,
                    pe_ratio=25.0,
                    pb_ratio=3.8,
                    peer_pe=30.0
                ),
                subscription=None,  # Not available yet
                promoter_holding=75.0,
                use_of_proceeds="Capacity expansion, R&D, working capital, strategic acquisitions",
                business_description="Pharmaceutical company specializing in generic drugs and specialty medicines",
                is_demo_data=True,
                data_source="Mock Demo Data"
            ),
            IPO(
                name="FinTech Solutions Ltd",
                symbol="FINTECH",
                sector="Financial Services",
                status=IPOStatus.UPCOMING,
                price_band_min=380,
                price_band_max=400,
                issue_size=550,
                fresh_issue=400,
                offer_for_sale=150,
                open_date=(now + timedelta(days=7)).strftime("%Y-%m-%d"),
                close_date=(now + timedelta(days=10)).strftime("%Y-%m-%d"),
                listing_date=(now + timedelta(days=17)).strftime("%Y-%m-%d"),
                financials=IPOFinancials(
                    revenue=520,
                    profit=78,
                    revenue_growth=35.8,
                    profit_growth=42.5,
                    debt=45,
                    cash_flow=95,
                    profitability=15.0,
                    pe_ratio=30.0,
                    pb_ratio=4.5,
                    peer_pe=35.0
                ),
                subscription=None,  # Not available yet
                promoter_holding=70.0,
                use_of_proceeds="Technology infrastructure, business expansion, working capital",
                business_description="Financial technology company providing digital payment and lending solutions",
                is_demo_data=True,
                data_source="Mock Demo Data"
            )
        ]
        
        # Recently listed IPOs
        listed_ipos = [
            IPO(
                name="AutoParts Manufacturing",
                symbol="AUTOP",
                sector="Automotive",
                status=IPOStatus.LISTED,
                price_band_min=320,
                price_band_max=340,
                issue_size=380,
                fresh_issue=280,
                offer_for_sale=100,
                open_date=(now - timedelta(days=15)).strftime("%Y-%m-%d"),
                close_date=(now - timedelta(days=12)).strftime("%Y-%m-%d"),
                listing_date=(now - timedelta(days=5)).strftime("%Y-%m-%d"),
                financials=IPOFinancials(
                    revenue=890,
                    profit=95,
                    revenue_growth=12.5,
                    profit_growth=8.3,
                    debt=145,
                    cash_flow=75,
                    profitability=10.7,
                    pe_ratio=35.0,
                    pb_ratio=2.9,
                    peer_pe=28.0
                ),
                subscription=IPOSubscription(
                    qib=28.5,
                    nii=18.2,
                    retail=12.8,
                    overall=22.5
                ),
                promoter_holding=65.0,
                use_of_proceeds="Capacity expansion, debt repayment, working capital",
                business_description="Automotive components manufacturer supplying to OEMs",
                is_demo_data=True,
                data_source="Mock Demo Data"
            ),
            IPO(
                name="ConsumerGoods Ltd",
                symbol="CONSUM",
                sector="Consumer Goods",
                status=IPOStatus.LISTED,
                price_band_min=520,
                price_band_max=550,
                issue_size=750,
                fresh_issue=500,
                offer_for_sale=250,
                open_date=(now - timedelta(days=20)).strftime("%Y-%m-%d"),
                close_date=(now - timedelta(days=17)).strftime("%Y-%m-%d"),
                listing_date=(now - timedelta(days=10)).strftime("%Y-%m-%d"),
                financials=IPOFinancials(
                    revenue=1450,
                    profit=165,
                    revenue_growth=15.2,
                    profit_growth=12.8,
                    debt=220,
                    cash_flow=135,
                    profitability=11.4,
                    pe_ratio=32.0,
                    pb_ratio=4.2,
                    peer_pe=30.0
                ),
                subscription=IPOSubscription(
                    qib=38.5,
                    nii=25.3,
                    retail=15.8,
                    overall=28.5
                ),
                promoter_holding=62.0,
                use_of_proceeds="Brand expansion, distribution network, working capital",
                business_description="FMCG company manufacturing household and personal care products",
                is_demo_data=True,
                data_source="Mock Demo Data"
            )
        ]
        
        return open_ipos + upcoming_ipos + listed_ipos
    
    def get_open_ipos(self) -> List[IPO]:
        """Get currently open IPOs."""
        return [ipo for ipo in self._demo_ipos if ipo.status == IPOStatus.OPEN]
    
    def get_upcoming_ipos(self) -> List[IPO]:
        """Get upcoming IPOs."""
        return [ipo for ipo in self._demo_ipos if ipo.status == IPOStatus.UPCOMING]
    
    def get_recent_ipos(self) -> List[IPO]:
        """Get recently closed/listed IPOs."""
        return [ipo for ipo in self._demo_ipos if ipo.status in [IPOStatus.CLOSED, IPOStatus.LISTED]]
    
    def get_ipo_details(self, ipo_id: str) -> Optional[IPO]:
        """Get detailed information for a specific IPO."""
        for ipo in self._demo_ipos:
            if ipo.symbol == ipo_id or ipo.name == ipo_id:
                return ipo
        return None
    
    def is_demo_data(self) -> bool:
        """Check if this provider is using demo/mock data."""
        return True
    
    def get_data_source_name(self) -> str:
        """Get the name of the data source."""
        return "Mock Demo Data"
