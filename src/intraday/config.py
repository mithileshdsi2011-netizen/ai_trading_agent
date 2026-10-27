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
        
        # Safety check: live trading stays disabled unless the explicit
        # arm token is ALSO present — ANGEL_LIVE_TRADING alone does
        # nothing. Arming requires both config intent and a separate
        # environment-level CONFIRM, so no single misconfiguration can
        # reach the order path.
        if config.angel_live_trading and os.environ.get("ANGEL_LIVE_ARMED") != "CONFIRM":
            logger.warning("[INTRADAY CONFIG] ANGEL_LIVE_TRADING=true without "
                           "ANGEL_LIVE_ARMED=CONFIRM — forcing LIVE off.")
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


# ── LIVE risk configuration ─────────────────────────────────────────
# Independent of PAPER limits. Every default fails closed: enabled=False
# and all limits zero means nothing is permitted until explicitly
# configured. ARMING additionally requires BOTH ANGEL_LIVE_TRADING=true
# AND ANGEL_LIVE_ARMED=CONFIRM in the environment — there is no code
# path that arms LIVE by itself.
_live_config = None


def load_live_config():
    """Load independent LIVE risk controls from the environment."""
    from .live_risk import LiveRiskConfig
    load_dotenv()
    enabled = _require("ANGEL_LIVE_RISK_ENABLED", "false").lower() == "true"
    cfg = LiveRiskConfig(
        enabled=enabled,
        capital=float(_require("ANGEL_LIVE_CAPITAL", "0")),
        max_order_value=float(_require("ANGEL_LIVE_MAX_ORDER_VALUE", "0")),
        max_order_qty=int(_require("ANGEL_LIVE_MAX_ORDER_QTY", "0")),
        max_per_trade_loss=float(_require("ANGEL_LIVE_MAX_PER_TRADE_LOSS", "0")),
        max_daily_loss=float(_require("ANGEL_LIVE_MAX_DAILY_LOSS", "0")),
        max_entries_per_day=int(_require("ANGEL_LIVE_MAX_ENTRIES", "0")),
        max_concurrent_positions=int(_require("ANGEL_LIVE_MAX_POSITIONS", "0")),
        max_gross_exposure=float(_require("ANGEL_LIVE_MAX_EXPOSURE", "0")),
        allow_leverage=_require("ANGEL_LIVE_ALLOW_LEVERAGE", "false").lower() == "true",
        max_slippage_pct=float(_require("ANGEL_LIVE_MAX_SLIPPAGE_PCT", "0")),
        entry_cutoff=_require("ANGEL_LIVE_ENTRY_CUTOFF", "14:30"),
        force_exit_by=_require("ANGEL_LIVE_FORCE_EXIT_BY", "15:05"),
        max_broker_errors=int(_require("ANGEL_LIVE_ERROR_BREAKER", "3")),
        order_timeout_s=float(_require("ANGEL_LIVE_ORDER_TIMEOUT_S", "30")),
        poll_interval_s=float(_require("ANGEL_LIVE_POLL_INTERVAL_S", "5")),
    )
    if enabled:
        logger.warning("[INTRADAY CONFIG] LIVE risk controls ENABLED "
                       "(order submission still requires arming)")
    return cfg


def get_live_config():
    global _live_config
    if _live_config is None:
        _live_config = load_live_config()
    return _live_config


def live_armed(config: IntradayConfig) -> bool:
    """The only arm check in the codebase: config flag AND env token."""
    return bool(
        config.angel_live_trading
        and os.environ.get("ANGEL_LIVE_ARMED") == "CONFIRM"
    )
