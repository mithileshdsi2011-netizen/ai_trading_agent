"""
IPO Data Models

Defines data structures for IPO information, financials, subscriptions, and analysis results.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, List, Dict, Any
from enum import Enum


class IPOStatus(Enum):
    """IPO status enumeration."""
    UPCOMING = "upcoming"
    OPEN = "open"
    CLOSED = "closed"
    LISTED = "listed"


class RiskLevel(Enum):
    """Risk level enumeration."""
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    NOT_ASSESSED = "NOT ASSESSED"  # Insufficient data — risk cannot be evaluated


class Recommendation(Enum):
    """Recommendation enumeration."""
    STRONG_CANDIDATE = "STRONG CANDIDATE"
    CONSIDER = "CONSIDER"
    WATCH = "WATCH"
    HIGH_RISK = "HIGH RISK"
    AVOID = "AVOID"
    INSUFFICIENT_DATA = "INSUFFICIENT DATA"  # Data too limited for a verdict


@dataclass
class IPOFinancials:
    """Financial metrics for an IPO."""
    revenue: Optional[float] = None  # Revenue in crores
    profit: Optional[float] = None  # Profit in crores
    revenue_growth: Optional[float] = None  # Revenue growth percentage
    profit_growth: Optional[float] = None  # Profit growth percentage
    debt: Optional[float] = None  # Debt in crores
    cash_flow: Optional[float] = None  # Cash flow in crores
    profitability: Optional[float] = None  # Profit margin percentage
    pe_ratio: Optional[float] = None  # P/E ratio
    pb_ratio: Optional[float] = None  # Price-to-book ratio
    peer_pe: Optional[float] = None  # Peer P/E ratio for comparison


@dataclass
class IPOSubscription:
    """Subscription data for an IPO."""
    qib: Optional[float] = None  # QIB subscription multiple
    nii: Optional[float] = None  # NII subscription multiple
    retail: Optional[float] = None  # Retail subscription multiple
    overall: Optional[float] = None  # Overall subscription multiple


@dataclass
class IPO:
    """Main IPO data model."""
    name: str
    symbol: Optional[str] = None
    sector: Optional[str] = None
    status: IPOStatus = IPOStatus.UPCOMING
    issue_type: Optional[str] = None  # EQUITY, SME, DEBT — from NSE series/securityType
    
    # Issue details
    price_band_min: Optional[float] = None
    price_band_max: Optional[float] = None
    issue_size: Optional[float] = None  # Issue size in crores
    fresh_issue: Optional[float] = None  # Fresh issue in crores
    offer_for_sale: Optional[float] = None  # OFS in crores
    
    # Dates
    open_date: Optional[str] = None  # ISO format date string
    close_date: Optional[str] = None  # ISO format date string
    listing_date: Optional[str] = None  # ISO format date string
    
    # Financials
    financials: Optional[IPOFinancials] = None
    
    # Subscription
    subscription: Optional[IPOSubscription] = None
    
    # Additional info
    promoter_holding: Optional[float] = None  # Promoter holding percentage post-IPO
    use_of_proceeds: Optional[str] = None  # Brief description of use of proceeds
    business_description: Optional[str] = None  # Brief business description
    
    # Metadata
    is_demo_data: bool = False  # Flag to indicate demo data
    data_source: Optional[str] = None  # Source of data
    
    @property
    def is_equity(self) -> bool:
        """True for equity/SME IPOs; False for DEBT/NCD/ZCZP offerings.
        Unknown issue types are treated as equity (do not hide data)."""
        return (self.issue_type or 'EQUITY').upper() != 'DEBT'

    def get_price_display(self) -> str:
        """Get formatted price band display."""
        if self.price_band_min and self.price_band_max:
            if self.price_band_min == self.price_band_max:
                return f"₹{self.price_band_min:.0f}"
            return f"₹{self.price_band_min:.0f}–₹{self.price_band_max:.0f}"
        if self.price_band_min:
            return f"₹{self.price_band_min:.0f}"
        if self.price_band_max:
            return f"₹{self.price_band_max:.0f}"
        return "N/A"
    
    def get_issue_price(self) -> float:
        """Get the issue price (average of band or single price)."""
        if self.price_band_min and self.price_band_max:
            return (self.price_band_min + self.price_band_max) / 2
        if self.price_band_min:
            return self.price_band_min
        if self.price_band_max:
            return self.price_band_max
        return 0.0


@dataclass
class IPOAnalysisResult:
    """Result of IPO analysis."""
    ipo: IPO
    score: float  # 0-100
    risk_level: RiskLevel
    recommendation: Recommendation
    
    # Breakdown
    financial_score: float = 0.0
    valuation_score: float = 0.0
    structure_score: float = 0.0
    subscription_score: float = 0.0
    business_score: float = 0.0
    market_score: float = 0.0
    
    # Explainable factors
    positive_factors: List[str] = field(default_factory=list)
    risk_factors: List[str] = field(default_factory=list)
    
    # Risk explanation
    risk_explanation: Optional[str] = None
    
    # Analysis metadata
    analyzed_at: str = field(default_factory=lambda: datetime.now().isoformat())
    data_quality: str = "FULL"  # FULL, PARTIAL, LIMITED


@dataclass
class PostListingWatchlist:
    """Post-listing watchlist entry for IPO analysis."""
    symbol: str
    ipo_name: str
    listing_price: float
    current_price: float
    listing_date: str  # ISO format
    
    # Performance metrics
    change_from_issue: float  # Percentage change from issue price
    change_from_listing: float  # Percentage change from listing price
    days_since_listing: int
    
    # Technical indicators (optional, READ-ONLY)
    rsi: Optional[float] = None
    ema_short: Optional[float] = None
    ema_long: Optional[float] = None
    volume_trend: Optional[str] = None  # UP, DOWN, STABLE
    breakout_indication: Optional[str] = None  # BULLISH, BEARISH, NEUTRAL
    
    # Recommendation (READ-ONLY)
    post_listing_action: Optional[str] = None  # WAIT FOR STABILIZATION, WATCH, POTENTIAL SWING SETUP
    
    # Metadata
    last_updated: str = field(default_factory=lambda: datetime.now().isoformat())
