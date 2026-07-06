"""
Configuration module for AI Trading Agent
"""
import os
import socket as _socket
from dotenv import load_dotenv
from typing import Optional

# ── IPv4-only patch (applied once, covers all modules) ───────────────────────
# Kite Connect API (api.kite.trade) is hosted on Cloudflare and resolves to
# IPv6 on modern macOS. Zerodha only whitelists IPv4 IPs in the developer
# console, so any IPv6 connection is rejected regardless of the actual network
# (WiFi, mobile hotspot, etc.). This patch forces the entire process to use
# IPv4 only — dynamically, without hardcoding any IP address.
_orig_getaddrinfo = _socket.getaddrinfo
def _force_ipv4(host, port, family=0, type=0, proto=0, flags=0):  # noqa: A002
    return _orig_getaddrinfo(host, port, _socket.AF_INET, type, proto, flags)
_socket.getaddrinfo = _force_ipv4
# ─────────────────────────────────────────────────────────────────────────────

load_dotenv()


class Config:
    """Application configuration"""
    
    # OpenAI
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    
    # Zerodha Kite Connect
    KITE_API_KEY: str = os.getenv("KITE_API_KEY", "")
    KITE_API_SECRET: str = os.getenv("KITE_API_SECRET", "")
    KITE_REQUEST_TOKEN: str = os.getenv("KITE_REQUEST_TOKEN", "")
    
    # Trading Mode: intraday | swing
    TRADING_MODE: str = os.getenv("TRADING_MODE", "swing")  # Default swing for small capital
    
    # Trading Parameters
    TRADING_AMOUNT: float = float(os.getenv("TRADING_AMOUNT", "7000"))
    MAX_POSITIONS: int = int(os.getenv("MAX_POSITIONS", "7"))
    RISK_PER_TRADE: float = float(os.getenv("RISK_PER_TRADE", "0.02"))
    
    # Intraday parameters (used when TRADING_MODE=intraday)
    STOP_LOSS_PERCENTAGE: float = float(os.getenv("STOP_LOSS_PERCENTAGE", "0.015"))  # 1.5% SL
    TARGET_PERCENTAGE: float = float(os.getenv("TARGET_PERCENTAGE", "0.06"))  # 6% target
    
    # Swing trading parameters (used when TRADING_MODE=swing)
    SWING_STOP_LOSS_PERCENTAGE: float = float(os.getenv("SWING_STOP_LOSS_PERCENTAGE", "0.05"))  # 5% SL
    SWING_TARGET_PERCENTAGE: float = float(os.getenv("SWING_TARGET_PERCENTAGE", "0.10"))  # 10% target
    SWING_MAX_HOLD_DAYS: int = int(os.getenv("SWING_MAX_HOLD_DAYS", "10"))  # Auto-exit after 10 days
    
    # Paper Trading
    PAPER_TRADING: bool = os.getenv("PAPER_TRADING", "True").lower() == "true"
    
    # Logging
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
    
    # Market Hours (IST)
    MARKET_OPEN: str = "09:15"   # Actual NSE open
    MARKET_CLOSE: str = "15:30"  # Actual NSE close
    INTRADAY_CUTOFF: str = "15:00"  # Force-exit 30 min before close

    # Signal quality thresholds
    MIN_CONFIDENCE: float = float(os.getenv("MIN_CONFIDENCE", "0.52"))  # 52% min — calibrated to new scoring
    MIN_RISK_REWARD: float = float(os.getenv("MIN_RISK_REWARD", "1.0"))  # 1.0:1 min

    # Capital utilization guard
    MAX_CAPITAL_USAGE: float = float(os.getenv("MAX_CAPITAL_USAGE", "0.95"))  # 95% max deployed

    # Re-entry cooldown: minimum hours to wait after selling before buying same stock again
    REENTRY_COOLDOWN_HOURS: float = float(os.getenv("REENTRY_COOLDOWN_HOURS", "4.0"))
    
    # Trailing stop loss
    TRAILING_STOP_ENABLED: bool = os.getenv("TRAILING_STOP_ENABLED", "True").lower() == "true"
    TRAILING_STOP_ACTIVATION_PCT: float = float(os.getenv("TRAILING_STOP_ACTIVATION_PCT", "0.05"))  # 5% profit
    TRAILING_STOP_TRAIL_PCT: float = float(os.getenv("TRAILING_STOP_TRAIL_PCT", "0.03"))  # 3% trail
    
    # Daily loss limit
    DAILY_MAX_LOSS_PCT: float = float(os.getenv("DAILY_MAX_LOSS_PCT", "0.05"))  # 5% of capital
    
    # Market regime detection
    MARKET_REGIME_ENABLED: bool = os.getenv("MARKET_REGIME_ENABLED", "True").lower() == "true"
    
    # Telegram alerts
    TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "")
    
    # Email reports
    EMAIL_ENABLED: bool = os.getenv("EMAIL_ENABLED", "False").lower() == "true"
    EMAIL_SMTP_SERVER: str = os.getenv("EMAIL_SMTP_SERVER", "smtp.gmail.com")
    EMAIL_SMTP_PORT: int = int(os.getenv("EMAIL_SMTP_PORT", "587"))
    EMAIL_USERNAME: str = os.getenv("EMAIL_USERNAME", "")
    EMAIL_PASSWORD: str = os.getenv("EMAIL_PASSWORD", "")
    EMAIL_TO: str = os.getenv("EMAIL_TO", "")
    
    # Dynamic universe – top N stocks scanned live from Kite each cycle
    DYNAMIC_UNIVERSE_SIZE: int = int(os.getenv("DYNAMIC_UNIVERSE_SIZE", "100"))

    # Fallback static watchlist (used only when Kite is unavailable)
    WATCHLIST: list = [
        "RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK",
        "HINDUNILVR", "ITC", "SBIN", "BAJFINANCE", "BHARTIARTL",
        "KOTAKBANK", "LT", "AXISBANK", "ASIANPAINT", "MARUTI",
        "WIPRO", "TITAN", "NESTLEIND", "SUNPHARMA", "BAJAJ-AUTO",
    ]
    
    @classmethod
    def validate(cls) -> bool:
        """Validate configuration"""
        if not cls.OPENAI_API_KEY:
            print("WARNING: OPENAI_API_KEY not set")
        if cls.PAPER_TRADING:
            print("Running in PAPER TRADING mode")
        else:
            if not all([cls.KITE_API_KEY, cls.KITE_API_SECRET]):
                print("ERROR: KITE_API_KEY and KITE_API_SECRET required for live trading")
                return False
        return True


config = Config()
