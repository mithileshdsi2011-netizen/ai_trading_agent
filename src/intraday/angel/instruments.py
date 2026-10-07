"""
Angel One Instruments Module

Manages Angel One instrument master (symbol tokens, exchange info).
Completely separate from Kite instruments.

Fetches Angel's public OpenAPI scrip master (no auth required) and caches
it for the day under data/intraday/. Falls back gracefully when the file
cannot be downloaded — the engine degrades instead of crashing.
"""
import json
import logging
import os
from typing import Dict, List, Optional
from dataclasses import dataclass
from datetime import date, datetime

import requests

logger = logging.getLogger(__name__)

# Angel's public instrument master (documented in SmartAPI docs)
SCRIP_MASTER_URL = (
    "https://margincalculator.angelone.in/OpenAPI_File/files/OpenAPIScripMaster.json"
)

# NSE index tokens from the Angel scrip master (indices carry 99926xxx tokens)
INDEX_TOKENS = {
    "NIFTY 50": "99926000",
    "NIFTY BANK": "99926009",
    "BANKNIFTY": "99926009",
    "INDIA VIX": "99926017",
}

# Default V1 universe: liquid NIFTY 50 equities (Angel tradingsymbol suffix -EQ)
NIFTY50_SYMBOLS = [
    "RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK",
    "SBIN", "BHARTIARTL", "ITC", "KOTAKBANK", "LT",
    "HINDUNILVR", "AXISBANK", "BAJFINANCE", "MARUTI",
    "HCLTECH", "ASIANPAINT", "SUNPHARMA", "TITAN",
    "DMART", "WIPRO", "ULTRACEMCO", "NTPC", "POWERGRID",
    "TATAMOTORS", "TATASTEEL", "ONGC", "COALINDIA",
    "GRASIM", "JSWSTEEL", "HINDALCO", "BAJAJFINSV",
    "M&M", "NESTLEIND", "DRREDDY", "CIPLA", "BRITANNIA",
    "ADANIENT", "ADANIPORTS", "APOLLOHOSP", "BAJAJ-AUTO",
    "BAJAJFINSV", "BEL", "BPCL", "EICHERMOT", "HEROMOTOCO",
    "HINDZINC", "INDUSINDBK", "LTIM", "SBILIFE", "SHRIRAMFIN",
    "TATACONSUM", "TECHM", "TRENT", "VEDL",
]


@dataclass
class Instrument:
    """Angel One instrument."""
    symbol: str
    symbol_token: str
    name: str
    exchange: str
    segment: str
    instrument_type: str
    lot_size: int = 1
    tick_size: float = 0.05


class AngelInstruments:
    """Angel One instrument master manager."""

    def __init__(self, cache_dir: Optional[str] = None):
        """Initialize instrument manager."""
        self._instruments: Dict[str, Instrument] = {}
        self._loaded = False
        self._cache_dir = cache_dir
        self._cache_path = None
        if cache_dir:
            self._cache_path = os.path.join(cache_dir, "scrip_master.json")

    def _default_cache_path(self) -> str:
        if self._cache_path:
            return self._cache_path
        root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
        return os.path.join(root, "data", "intraday", "scrip_master.json")

    def _load_cached_master(self) -> Optional[list]:
        """Load today's cached scrip master."""
        try:
            path = self._default_cache_path()
            meta_path = path + ".meta"
            if not (os.path.exists(path) and os.path.exists(meta_path)):
                return None
            with open(meta_path) as f:
                meta = json.load(f)
            if meta.get("date") != date.today().isoformat():
                return None
            with open(path) as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"[ANGEL INSTRUMENTS] Cache read failed: {e}")
            return None

    def _save_cached_master(self, rows: list):
        """Cache scrip master for today."""
        try:
            path = self._default_cache_path()
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as f:
                json.dump(rows, f)
            with open(path + ".meta", "w") as f:
                json.dump({"date": date.today().isoformat(),
                           "fetched_at": datetime.now().isoformat()}, f)
        except Exception as e:
            logger.warning(f"[ANGEL INSTRUMENTS] Cache write failed: {e}")

    def _fetch_scrip_master(self) -> Optional[list]:
        """Download Angel's public scrip master JSON."""
        cached = self._load_cached_master()
        if cached:
            logger.info(f"[ANGEL INSTRUMENTS] Using cached scrip master ({len(cached)} rows)")
            return cached
        try:
            logger.info("[ANGEL INSTRUMENTS] Downloading scrip master...")
            resp = requests.get(SCRIP_MASTER_URL, timeout=60)
            resp.raise_for_status()
            rows = resp.json()
            if isinstance(rows, list) and rows:
                self._save_cached_master(rows)
                logger.info(f"[ANGEL INSTRUMENTS] Downloaded {len(rows)} instruments")
                return rows
        except Exception as e:
            logger.error(f"[ANGEL INSTRUMENTS] Scrip master download failed: {e}")
        return None

    def load_instruments(self, client=None, symbols: Optional[List[str]] = None) -> bool:
        """
        Load instruments for the trading universe from the scrip master.

        Args:
            client: Unused (kept for interface compatibility)
            symbols: Universe symbols; defaults to NIFTY 50

        Returns:
            True if at least the universe was resolved
        """
        try:
            universe = symbols or self.get_nifty50_symbols()
            logger.info(f"[ANGEL INSTRUMENTS] Loading instruments for {len(universe)} symbols...")

            rows = self._fetch_scrip_master()
            if rows:
                wanted = {f"{s}-EQ" for s in universe}
                for row in rows:
                    ts = row.get("symbol", "")
                    if row.get("exch_seg") == "NSE" and ts in wanted:
                        base = ts[:-3]
                        self._instruments[base] = Instrument(
                            symbol=base,
                            symbol_token=row.get("token", ""),
                            name=row.get("name", base),
                            exchange="NSE",
                            segment="EQ",
                            instrument_type=row.get("instrumenttype", "EQUITY"),
                            lot_size=int(float(row.get("lotsize", 1) or 1)),
                            tick_size=float(row.get("tick_size", 0.05) or 0.05),
                        )

            missing = [s for s in universe if s not in self._instruments]
            if missing:
                logger.warning(f"[ANGEL INSTRUMENTS] Unresolved symbols: {missing}")

            self._loaded = bool(self._instruments)
            logger.info(f"[ANGEL INSTRUMENTS] Loaded {len(self._instruments)}/{len(universe)} instruments")
            return self._loaded

        except Exception as e:
            logger.error(f"[ANGEL INSTRUMENTS] Load error: {e}")
            return False

    def get_instrument(self, symbol: str) -> Optional[Instrument]:
        """Get instrument by base symbol (e.g. 'RELIANCE')."""
        return self._instruments.get(symbol)

    def get_symbol_token(self, symbol: str) -> Optional[str]:
        """Get Angel symbol token for a base symbol."""
        instrument = self.get_instrument(symbol)
        return instrument.symbol_token if instrument else None

    def get_tradingsymbol(self, symbol: str) -> Optional[str]:
        """Get Angel trading symbol (e.g. 'RELIANCE-EQ')."""
        if symbol in self._instruments:
            return f"{symbol}-EQ"
        return None

    def get_index_token(self, index_symbol: str) -> Optional[str]:
        """Get token for an index like 'NIFTY 50' or 'BANKNIFTY'."""
        return INDEX_TOKENS.get(index_symbol.upper())

    def get_all_symbols(self) -> List[str]:
        """Get all loaded symbols."""
        return list(self._instruments.keys())

    def is_loaded(self) -> bool:
        """Check if instruments are loaded."""
        return self._loaded

    def get_nifty50_symbols(self) -> List[str]:
        """Get configured NIFTY 50 universe."""
        return list(dict.fromkeys(NIFTY50_SYMBOLS))
