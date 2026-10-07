"""
Intraday Configuration Loader

Loads Intraday-specific configuration from environment variables.
Completely separate from Swing Trading configuration.
"""
import os
import logging
from typing import Optional
from dotenv import load_dotenv
from .models import IntradayConfig

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _require(key: str, default: str) -> str:
    """Read an env var; warn if absent so the operator knows a default was used."""
    val = os.getenv(key)
    if val is None:
        logger.warning(f"[INTRADAY CONFIG] {key} not set in .env — using default: {default}")
        return default
    return val


class IntradayConfigLoader:
    """Loads and validates Intraday configuration from environment variables."""
    
    @staticmethod
    def load() -> IntradayConfig:
        """Load Intraday configuration from environment variables."""
        load_dotenv()
        
        config = IntradayConfig(
            # Angel One credentials
            angel_api_key=os.getenv("ANGEL_API_KEY", ""),
            angel_client_code=os.getenv("ANGEL_CLIENT_CODE", ""),
            angel_password_or_mpin=os.getenv("ANGEL_PASSWORD_OR_MPIN", ""),
            angel_totp_secret=os.getenv("ANGEL_TOTP_SECRET", ""),
            angel_live_trading=_require("ANGEL_LIVE_TRADING", "false").lower() == "true",
            angel_static_ip_required=_require("ANGEL_STATIC_IP_REQUIRED", "true").lower() == "true",
            
            # Capital and risk
            intraday_capital=float(_require("ANGEL_INTRADAY_CAPITAL", "10000")),
            max_trades_per_day=int(_require("ANGEL_MAX_TRADES_PER_DAY", "5")),
            max_daily_loss_pct=float(_require("ANGEL_MAX_DAILY_LOSS_PCT", "0.02")),
            risk_per_trade_pct=float(_require("ANGEL_RISK_PER_TRADE_PCT", "0.01")),
            min_risk_reward=float(_require("ANGEL_MIN_RISK_REWARD", "1.5")),
            max_concurrent_positions=int(_require("ANGEL_MAX_CONCURRENT_POSITIONS", "2")),
            
            # Trading hours
            entry_start_time=_require("ANGEL_ENTRY_START_TIME", "09:30"),
            last_entry_time=_require("ANGEL_LAST_ENTRY_TIME", "14:30"),
            square_off_time=_require("ANGEL_SQUARE_OFF_TIME", "15:00"),
            
            # Risk controls
            max_consecutive_losses=int(_require("ANGEL_MAX_CONSECUTIVE_LOSSES", "3")),
            kill_switch=_require("ANGEL_KILL_SWITCH", "false").lower() == "true",
            
            # Trading universe
            trading_universe=_require("ANGEL_TRADING_UNIVERSE", "NIFTY50"),
            
            # Paper trading (MUST default to true)
            paper_trading=_require("ANGEL_PAPER_TRADING", "true").lower() == "true"
        )
        
        # Safety check: paper trading must be true for V1
        if not config.paper_trading:
            logger.warning("[INTRADAY CONFIG] ANGEL_PAPER_TRADING is false. Forcing paper trading mode for V1.")
            config.paper_trading = True
        
        # Safety check: live trading must be false for V1
        if config.angel_live_trading:
            logger.warning("[INTRADAY CONFIG] ANGEL_LIVE_TRADING is true. Forcing to false for V1.")
            config.angel_live_trading = False
        
        logger.info(f"[INTRADAY CONFIG] Loaded: Paper={config.paper_trading}, Live={config.angel_live_trading}, Capital={config.intraday_capital}")
        
        return config


# Global config instance
_intraday_config: Optional[IntradayConfig] = None


def get_intraday_config() -> IntradayConfig:
    """Get the global Intraday configuration instance."""
    global _intraday_config
    if _intraday_config is None:
        _intraday_config = IntradayConfigLoader.load()
    return _intraday_config
