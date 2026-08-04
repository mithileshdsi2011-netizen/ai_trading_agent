"""
Configuration module for AI Trading Agent

All production values must be set in .env — this file only reads and validates them.
Missing required variables log a WARNING at startup; missing critical ones abort.
"""
import os
import logging
import socket as _socket
from dotenv import load_dotenv

# ── IPv4-only patch (applied once, covers all modules) ───────────────────────
# Kite Connect API (api.kite.trade) resolves to IPv6 on modern macOS.
# Zerodha only whitelists IPv4 IPs, so all connections must use AF_INET.
_orig_getaddrinfo = _socket.getaddrinfo
def _force_ipv4(host, port, family=0, type=0, proto=0, flags=0):  # noqa: A002
    return _orig_getaddrinfo(host, port, _socket.AF_INET, type, proto, flags)
_socket.getaddrinfo = _force_ipv4
# ─────────────────────────────────────────────────────────────────────────────

load_dotenv()

_log = logging.getLogger(__name__)


def _require(key: str, default: str) -> str:
    """Read an env var; warn if absent so the operator knows a default was used."""
    val = os.getenv(key)
    if val is None:
        _log.warning("CONFIG: %s not set in .env — using default: %s", key, default)
        return default
    return val


class Config:
    """
    Production configuration — all values driven by .env.
    Defaults are the recommended production values; a WARNING is logged
    whenever a fallback is used so misconfiguration is never silent.
    """

    # ── Credentials (no defaults — must be in .env) ───────────────────────────
    OPENAI_API_KEY:     str = os.getenv("OPENAI_API_KEY", "")
    KITE_API_KEY:       str = os.getenv("KITE_API_KEY", "")
    KITE_API_SECRET:    str = os.getenv("KITE_API_SECRET", "")
    KITE_REQUEST_TOKEN: str = os.getenv("KITE_REQUEST_TOKEN", "")

    # ── Trading mode ──────────────────────────────────────────────────────────
    TRADING_MODE: str = _require("TRADING_MODE", "swing")        # swing | intraday

    # ── Core capital & position limits ────────────────────────────────────────
    TRADING_AMOUNT:  float = float(_require("TRADING_AMOUNT",  "15000"))
    MAX_POSITIONS:   int   = int  (_require("MAX_POSITIONS",   "7"))
    RISK_PER_TRADE:  float = float(_require("RISK_PER_TRADE",  "0.02"))   # 2%

    # ── Intraday parameters ───────────────────────────────────────────────────
    STOP_LOSS_PERCENTAGE: float = float(_require("STOP_LOSS_PERCENTAGE", "0.02"))  # 2%
    TARGET_PERCENTAGE:    float = float(_require("TARGET_PERCENTAGE",    "0.05"))  # 5%

    # ── Swing parameters ──────────────────────────────────────────────────────
    SWING_STOP_LOSS_PERCENTAGE: float = float(_require("SWING_STOP_LOSS_PERCENTAGE", "0.05"))  # 5%
    SWING_TARGET_PERCENTAGE:    float = float(_require("SWING_TARGET_PERCENTAGE",    "0.10"))  # 10%
    SWING_MAX_HOLD_DAYS:        int   = int  (_require("SWING_MAX_HOLD_DAYS",        "15"))

    # ── Paper trading ─────────────────────────────────────────────────────────
    PAPER_TRADING: bool = _require("PAPER_TRADING", "True").lower() == "true"

    # ── Logging ───────────────────────────────────────────────────────────────
    LOG_LEVEL: str = _require("LOG_LEVEL", "INFO")

    # ── Market hours (IST) — fixed by NSE, not overridable ───────────────────
    MARKET_OPEN:      str = "09:15"
    MARKET_CLOSE:     str = "15:30"
    INTRADAY_CUTOFF:  str = "15:00"   # Force-exit 30 min before close
    TRADING_START:    str = _require("TRADING_START", "09:30")  # Skip illiquid open 9:15-9:30

    # ── Signal quality thresholds ─────────────────────────────────────────────
    MIN_CONFIDENCE:  float = float(_require("MIN_CONFIDENCE",  "0.60"))   # 60%
    MIN_RISK_REWARD: float = float(_require("MIN_RISK_REWARD", "1.5"))    # 1.5:1

    # ── Capital management ────────────────────────────────────────────────────
    MAX_CAPITAL_USAGE:      float = float(_require("MAX_CAPITAL_USAGE",      "0.87"))  # 87%
    REENTRY_COOLDOWN_HOURS: float = float(_require("REENTRY_COOLDOWN_HOURS", "4.0"))
    MIN_HOLD_HOURS:         float = float(_require("MIN_HOLD_HOURS",         "12.0"))

    # ── Trailing stop loss ────────────────────────────────────────────────────
    TRAILING_STOP_ENABLED:        bool  = _require("TRAILING_STOP_ENABLED",        "True").lower() == "true"
    TRAILING_STOP_ACTIVATION_PCT: float = float(_require("TRAILING_STOP_ACTIVATION_PCT", "0.05"))  # 5%
    TRAILING_STOP_TRAIL_PCT:      float = float(_require("TRAILING_STOP_TRAIL_PCT",      "0.03"))  # 3%

    # ── Partial profit & conditional loss-exit thresholds ─────────────────────
    PARTIAL_PROFIT_THRESHOLD:       float = float(_require("PARTIAL_PROFIT_THRESHOLD",       "0.05"))  # 5%
    PARTIAL_PROFIT_ATR_MULTIPLIER:  float = float(_require("PARTIAL_PROFIT_ATR_MULTIPLIER",  "1.5"))  # ATR multiple for adaptive partial target
    PARTIAL_PROFIT_FRACTION:        float = float(_require("PARTIAL_PROFIT_FRACTION",        "0.5"))   # 50%
    SMALL_LOSS_PCT:                 float = float(_require("SMALL_LOSS_PCT",                 "0.02"))  # 2%
    RECOVERY_PROBABILITY_HOLD:      float = float(_require("RECOVERY_PROBABILITY_HOLD",      "0.75"))  # 75%
    BULLISH_MARKET_THRESHOLD:       float = float(_require("BULLISH_MARKET_THRESHOLD",       "0.25"))  # market_sell below this = bullish
    STRONG_SECTOR_THRESHOLD:        float = float(_require("STRONG_SECTOR_THRESHOLD",        "0.25"))  # sector_sell below this = strong
    SUPPORT_DISTANCE_THRESHOLD:     float = float(_require("SUPPORT_DISTANCE_THRESHOLD",     "0.05"))  # within 5% of support
    AI_DECLINE_CONFIDENCE_THRESHOLD: float = float(_require("AI_DECLINE_CONFIDENCE_THRESHOLD", "0.90"))  # 90%

    # ── Smart exit thresholds ─────────────────────────────────────────────────
    SMARTEXIT_MIN_PROFIT_PCT:            float = float(_require("SMARTEXIT_MIN_PROFIT_PCT",            "0.015"))  # 1.5% minimum unrealised profit
    SMARTEXIT_VOLUME_CONFIRMATION_RATIO: float = float(_require("SMARTEXIT_VOLUME_CONFIRMATION_RATIO", "0.80"))   # volume >= 80% of 20d avg for bearish candle / MACD

    # ── Risk limits ───────────────────────────────────────────────────────────
    DAILY_MAX_LOSS_PCT:     float = float(_require("DAILY_MAX_LOSS_PCT",     "0.05"))  # 5%
    MAX_PORTFOLIO_RISK:     float = float(_require("MAX_PORTFOLIO_RISK",     "2000.0")) # total open (unrealised) risk in ₹
    MAX_SECTOR_POSITIONS:   int   = int(_require("MAX_SECTOR_POSITIONS",     "2"))     # max same-sector positions
    MAX_CONSECUTIVE_LOSSES: int   = int  (_require("MAX_CONSECUTIVE_LOSSES", "3"))     # halt after 3 straight losses

    # ── Confidence regime thresholds ──────────────────────────────────────────
    MIN_CONFIDENCE_BULL:     float = float(_require("MIN_CONFIDENCE_BULL",     "0.55"))  # 55% in bull
    MIN_CONFIDENCE_BEAR:     float = float(_require("MIN_CONFIDENCE_BEAR",     "0.70"))  # 70% in bear
    MIN_CONFIDENCE_SIDEWAYS: float = float(_require("MIN_CONFIDENCE_SIDEWAYS", "0.55"))  # 55% in sideways

    # ── Regime-based buy guards ───────────────────────────────────────────────
    SIDEWAYS_BUY_SCORE_MIN:          int   = int  (_require("SIDEWAYS_BUY_SCORE_MIN",          "58"))     # min TradeScorer total for sideways (do not add extra gate)
    SIDEWAYS_BUY_OVERALL_SCORE_MIN:  float = float(_require("SIDEWAYS_BUY_OVERALL_SCORE_MIN",  "0.30"))   # min AI overall score for sideways
    SIDEWAYS_SIZE_FACTOR:            float = float(_require("SIDEWAYS_SIZE_FACTOR",            "0.75"))   # reduce size in sideways
    VOLATILE_BLOCK_BUYS:    bool  = _require("VOLATILE_BLOCK_BUYS",    "True").lower() == "true"  # no new buys in volatile

    # ── Confidence-based capital allocation tiers ─────────────────────────────
    CONFIDENCE_ALLOCATION_95: float = float(_require("CONFIDENCE_ALLOCATION_95", "20000.0"))  # 95%+ confidence budget
    CONFIDENCE_ALLOCATION_85: float = float(_require("CONFIDENCE_ALLOCATION_85", "15000.0"))  # 85%+ confidence budget
    CONFIDENCE_ALLOCATION_70: float = float(_require("CONFIDENCE_ALLOCATION_70", "10000.0"))  # 70%+ confidence budget

    # ── Market regime ─────────────────────────────────────────────────────────
    MARKET_REGIME_ENABLED: bool = _require("MARKET_REGIME_ENABLED", "True").lower() == "true"

    # ── Telegram ──────────────────────────────────────────────────────────────
    TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    TELEGRAM_CHAT_ID:   str = os.getenv("TELEGRAM_CHAT_ID",   "")

    # ── Email ─────────────────────────────────────────────────────────────────
    EMAIL_ENABLED:    bool = _require("EMAIL_ENABLED", "False").lower() == "true"
    EMAIL_SMTP_SERVER: str = os.getenv("EMAIL_SMTP_SERVER", "smtp.gmail.com")
    EMAIL_SMTP_PORT:   int = int(os.getenv("EMAIL_SMTP_PORT", "587"))
    EMAIL_USERNAME:    str = os.getenv("EMAIL_USERNAME", "")
    EMAIL_PASSWORD:    str = os.getenv("EMAIL_PASSWORD", "")
    EMAIL_TO:          str = os.getenv("EMAIL_TO", "")

    # ── Dynamic universe ──────────────────────────────────────────────────────
    DYNAMIC_UNIVERSE_SIZE: int = int(_require("DYNAMIC_UNIVERSE_SIZE", "150"))

    # ── Fallback static watchlist (used only when Kite is unavailable) ────────
    WATCHLIST: list = [
        "RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK",
        "HINDUNILVR", "ITC", "SBIN", "BAJFINANCE", "BHARTIARTL",
        "KOTAKBANK", "LT", "AXISBANK", "ASIANPAINT", "MARUTI",
        "WIPRO", "TITAN", "NESTLEIND", "SUNPHARMA", "BAJAJ-AUTO",
    ]

    @classmethod
    def validate(cls) -> bool:
        """
        Validate configuration at startup.
        Logs warnings for optional gaps, returns False for critical missing items.
        """
        ok = True

        if not cls.OPENAI_API_KEY:
            _log.warning("CONFIG: OPENAI_API_KEY not set — AI research features disabled")

        if cls.PAPER_TRADING:
            _log.info("CONFIG: Running in PAPER TRADING mode — no real orders will be placed")
        else:
            if not cls.KITE_API_KEY:
                _log.error("CONFIG: KITE_API_KEY required for live trading")
                ok = False
            if not cls.KITE_API_SECRET:
                _log.error("CONFIG: KITE_API_SECRET required for live trading")
                ok = False

        if cls.EMAIL_ENABLED and not cls.EMAIL_USERNAME:
            _log.warning("CONFIG: EMAIL_ENABLED=True but EMAIL_USERNAME not set")

        if cls.TRADING_MODE not in ("swing", "intraday"):
            _log.error("CONFIG: TRADING_MODE must be 'swing' or 'intraday', got: %s", cls.TRADING_MODE)
            ok = False

        if cls.MAX_POSITIONS < 1 or cls.MAX_POSITIONS > 20:
            _log.warning("CONFIG: MAX_POSITIONS=%d looks unusual (expected 1–20)", cls.MAX_POSITIONS)

        if cls.TRADING_AMOUNT < 1000:
            _log.warning("CONFIG: TRADING_AMOUNT=%.0f is very low — minimum recommended ₹5,000", cls.TRADING_AMOUNT)

        _log.info(
            "CONFIG: mode=%s | capital=₹%.0f | positions=%d | confidence=%.0f%% | R:R=%.1f | paper=%s",
            cls.TRADING_MODE, cls.TRADING_AMOUNT, cls.MAX_POSITIONS,
            cls.MIN_CONFIDENCE * 100, cls.MIN_RISK_REWARD, cls.PAPER_TRADING,
        )
        return ok


config = Config()
