"""
Intraday Trading Models

Data models for intraday signals, positions, trades, and configuration.
Completely isolated from Swing Trading models.
"""
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional, Dict, Any


class MarketRegime(Enum):
    """Market regime classification for intraday trading."""
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    SIDEWAYS = "SIDEWAYS"
    HIGH_VOLATILITY = "HIGH_VOLATILITY"


class SignalDirection(Enum):
    """Signal direction."""
    LONG = "LONG"
    SHORT = "SHORT"


class SignalClassification(Enum):
    """Signal classification based on score."""
    STRONG_SETUP = "STRONG_SETUP"
    GOOD_SETUP = "GOOD_SETUP"
    WATCH = "WATCH"
    NO_TRADE = "NO_TRADE"


class PositionStatus(Enum):
    """Position status."""
    OPEN = "OPEN"
    CLOSED = "CLOSED"
    STOPPED_OUT = "STOPPED_OUT"
    TARGET_HIT = "TARGET_HIT"
    EOD_SQUARE_OFF = "EOD_SQUARE_OFF"
    KILL_SWITCH = "KILL_SWITCH"


@dataclass
class IntradaySignal:
    """Intraday trading signal."""
    symbol: str
    direction: SignalDirection
    score: float
    classification: SignalClassification
    entry_price: float
    stop_loss: float
    target: float
    risk_reward: float
    
    # Feature breakdown
    vwap_score: float
    ema_score: float
    volume_score: float
    momentum_score: float
    breakout_score: float
    market_regime_score: float
    
    # Market context
    market_regime: MarketRegime
    current_price: float
    vwap: float
    ema_9: float
    ema_20: float
    rsi: float
    volume: int
    relative_volume: float
    
    # Validation
    validation_passed: bool
    rejection_reason: Optional[str] = None
    
    # AI explanation
    ai_explanation: Optional[str] = None
    
    # Metadata
    timestamp: datetime = field(default_factory=datetime.now)
    strategy: str = "UNKNOWN"


@dataclass
class IntradayPosition:
    """Intraday position."""
    id: str
    symbol: str
    direction: SignalDirection
    entry_price: float
    quantity: int
    stop_loss: float
    target: float
    entry_time: datetime
    
    # Current state
    current_price: float
    unrealized_pnl: float
    unrealized_pnl_pct: float
    
    # Status
    status: PositionStatus = PositionStatus.OPEN
    
    # Exit info
    exit_price: Optional[float] = None
    exit_time: Optional[datetime] = None
    exit_reason: Optional[str] = None
    realized_pnl: float = 0.0
    
    # Original signal
    signal_score: float = 0.0
    signal_classification: SignalClassification = SignalClassification.NO_TRADE
    
    # Metadata
    strategy: str = "UNKNOWN"
    is_paper: bool = True


@dataclass
class IntradayTrade:
    """Completed intraday trade."""
    id: str
    symbol: str
    direction: SignalDirection
    entry_price: float
    exit_price: float
    quantity: int
    entry_time: datetime
    exit_time: datetime
    exit_reason: str
    
    # P&L
    gross_pnl: float
    charges: float
    net_pnl: float
    pnl_pct: float
    
    # Risk metrics
    stop_loss: float
    target: float
    risk_reward: float
    
    # Signal info
    signal_score: float
    signal_classification: SignalClassification
    strategy: str
    
    # Paper/Live
    is_paper: bool = True
    
    # Duration
    duration_minutes: Optional[float] = None


@dataclass
class IntradayConfig:
    """Intraday trading configuration."""
    
    # Angel One credentials
    angel_api_key: str = ""
    angel_client_code: str = ""
    angel_password_or_mpin: str = ""
    angel_totp_secret: str = ""
    angel_live_trading: bool = False
    angel_static_ip_required: bool = True
    
    # Capital and risk
    intraday_capital: float = 10000.0
    max_trades_per_day: int = 5
    max_daily_loss_pct: float = 0.02  # 2%
    risk_per_trade_pct: float = 0.01  # 1%
    min_risk_reward: float = 1.5
    max_concurrent_positions: int = 2
    
    # Trading hours
    entry_start_time: str = "09:30"
    last_entry_time: str = "14:30"
    square_off_time: str = "15:00"
    
    # Risk controls
    max_consecutive_losses: int = 3
    kill_switch: bool = False
    
    # Trading universe
    trading_universe: str = "NIFTY50"
    
    # Strategy weights (scoring)
    trend_ema_weight: float = 20.0
    vwap_structure_weight: float = 20.0
    volume_confirmation_weight: float = 20.0
    momentum_weight: float = 15.0
    breakout_quality_weight: float = 15.0
    market_regime_weight: float = 10.0
    
    # Score thresholds
    strong_setup_threshold: float = 80.0
    good_setup_threshold: float = 70.0
    watch_threshold: float = 60.0
    
    # Feature parameters
    ema_short_period: int = 9
    ema_long_period: int = 20
    rsi_period: int = 14
    atr_period: int = 14
    
    # Opening range
    or_minutes: int = 15  # First 15 minutes for opening range
    
    # Paper trading
    paper_trading: bool = True
    
    # Data freshness
    max_data_age_seconds: int = 30
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert config to dictionary."""
        return {
            'angel_api_key': (self.angel_api_key[:8] + '...') if len(self.angel_api_key) > 8 else (self.angel_api_key + '...') if self.angel_api_key else '',
            'angel_client_code': (self.angel_client_code[:8] + '...') if len(self.angel_client_code) > 8 else (self.angel_client_code + '...') if self.angel_client_code else '',
            'angel_live_trading': self.angel_live_trading,
            'angel_static_ip_required': self.angel_static_ip_required,
            'intraday_capital': self.intraday_capital,
            'max_trades_per_day': self.max_trades_per_day,
            'max_daily_loss_pct': self.max_daily_loss_pct,
            'risk_per_trade_pct': self.risk_per_trade_pct,
            'min_risk_reward': self.min_risk_reward,
            'max_concurrent_positions': self.max_concurrent_positions,
            'entry_start_time': self.entry_start_time,
            'last_entry_time': self.last_entry_time,
            'square_off_time': self.square_off_time,
            'max_consecutive_losses': self.max_consecutive_losses,
            'kill_switch': self.kill_switch,
            'trading_universe': self.trading_universe,
            'paper_trading': self.paper_trading
        }
