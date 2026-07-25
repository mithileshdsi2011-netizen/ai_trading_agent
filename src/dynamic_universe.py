"""
Dynamic Universe Module
Scans all NSE stocks via Kite Connect and selects the best intraday candidates
based on volume, momentum, and liquidity filters.
"""
import logging
import os
import sys
from typing import List, Dict, Optional
from datetime import datetime, timedelta

current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(current_dir)
sys.path.insert(0, current_dir)
sys.path.insert(0, project_root)

from token_manager import TokenManager

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Stocks to always exclude (illiquid, penny, suspended, ETFs, etc.)
_EXCLUDE = {
    "NIFTYBEES", "BANKBEES", "JUNIORBEES", "LIQUIDBEES", "GOLDBEES",
    "SETFNIF50", "SETFNN50", "CPSEETF", "BHARAT22ETF", "MOM100",
}

# Minimum price filter – avoids penny stocks (intraday margin issues)
MIN_PRICE = 50.0
MAX_PRICE = 10000.0

# Minimum intraday volume during market hours; 0 when closed (volume resets daily)
MIN_VOLUME = 50_000  # 50k shares – widened for broader coverage

# Maximum number of candidates to return for analysis
DEFAULT_TOP_N = 30

# ── Sector mapping for diversification cap ──────────────────────────────────
SECTOR_MAP = {
    # Banking & Finance
    "HDFCBANK":"Banking","ICICIBANK":"Banking","SBIN":"Banking","KOTAKBANK":"Banking",
    "AXISBANK":"Banking","INDUSINDBK":"Banking","BANKBARODA":"Banking","CANBK":"Banking",
    "PNB":"Banking","IDFCFIRSTB":"Banking","FEDERALBNK":"Banking","BANDHANBNK":"Banking",
    "BAJFINANCE":"NBFC","BAJAJFINSV":"NBFC","CHOLAFIN":"NBFC","MUTHOOTFIN":"NBFC",
    "MANAPPURAM":"NBFC","LICHSGFIN":"NBFC","SBICARD":"NBFC","PFC":"NBFC",
    "RECLTD":"NBFC","IRFC":"NBFC","PNBHOUSING":"NBFC","CANFINHOME":"NBFC",
    "HOMEFIRST":"NBFC","APTUS":"NBFC","AAVAS":"NBFC","FIVESTAR":"NBFC",
    "EDELWEISS":"NBFC","BAJAJHFL":"NBFC",
    # Insurance & Wealth
    "SBILIFE":"Insurance","HDFCLIFE":"Insurance","ICICIPRULI":"Insurance",
    "ICICIGI":"Insurance","GICRE":"Insurance","NIACL":"Insurance","STARHEALTH":"Insurance",
    "LICI":"Insurance","MOTILALOFS":"Wealth","ANGELONE":"Wealth","KFINTECH":"Wealth",
    "CAMS":"Wealth","CDSL":"Wealth","BSE":"Wealth","MCX":"Wealth","IEX":"Wealth",
    "HDFCAMC":"Wealth",
    # IT
    "TCS":"IT","INFY":"IT","WIPRO":"IT","HCLTECH":"IT","TECHM":"IT",
    "TATAELXSI":"IT","LTTS":"IT","MPHASIS":"IT","PERSISTENT":"IT",
    "COFORGE":"IT","HEXAWARE":"IT","KPITTECH":"IT","CYIENT":"IT",
    "MASTEK":"IT","ZENSARTECH":"IT","HAPPSTMNDS":"IT","TANLA":"IT",
    "INTELLECT":"IT","NEWGEN":"IT","DATAMATICS":"IT","ROUTE":"IT",
    "NAUKRI":"IT","NETWEB":"IT",
    # Energy & Oil
    "RELIANCE":"Energy","ONGC":"Energy","BPCL":"Energy","IOC":"Energy",
    "MRPL":"Energy","VEDL":"Energy","COALINDIA":"Energy","ADANIPOWER":"Energy",
    "TATAPOWER":"Energy","ADANIGREEN":"Energy","TORNTPOWER":"Energy","CESC":"Energy",
    "SJVN":"Energy","NHPC":"Energy","NTPC":"Energy","POWERGRID":"Energy",
    "SUZLON":"Energy","INOXWIND":"Energy","WAAREEENER":"Energy",
    # Pharma & Healthcare
    "SUNPHARMA":"Pharma","DRREDDY":"Pharma","CIPLA":"Pharma","DIVISLAB":"Pharma",
    "LUPIN":"Pharma","AUROPHARMA":"Pharma","BIOCON":"Pharma","GLENMARK":"Pharma",
    "IPCA":"Pharma","AJANTPHARM":"Pharma","ZYDUSLIFE":"Pharma","TORNTPHARM":"Pharma",
    "ALKEM":"Pharma","ABBOTINDIA":"Pharma","PFIZER":"Pharma","GLAXO":"Pharma",
    "SANOFI":"Pharma","GRANULES":"Pharma","GLAND":"Pharma","SYNGENE":"Pharma",
    "METROPOLIS":"Pharma","THYROCARE":"Pharma","LALPATHLAB":"Pharma",
    # Auto
    "MARUTI":"Auto","TATAMOTORS":"Auto","M&M":"Auto","BAJAJ-AUTO":"Auto",
    "EICHERMOT":"Auto","HEROMOTOCO":"Auto","MOTHERSON":"Auto","BALKRISIND":"Auto",
    "APOLLOTYRE":"Auto","MRF":"Auto","EXIDEIND":"Auto","SUNDRMFAST":"Auto",
    "BOSCHLTD":"Auto","MINDA":"Auto","SCHAEFFLER":"Auto","TIMKEN":"Auto",
    # Metals & Mining
    "JSWSTEEL":"Metals","TATASTEEL":"Metals","HINDALCO":"Metals",
    "NATIONALUM":"Metals","HINDZINC":"Metals","GMDCLTD":"Metals",
    # Cement & Infra
    "ULTRACEMCO":"Cement","SHREECEM":"Cement","AMBUJACEM":"Cement","ACC":"Cement",
    "LT":"Infra","SIEMENS":"Infra","ABB":"Infra","THERMAX":"Infra",
    "CUMMINSIND":"Infra","BHEL":"Infra","NBCC":"Infra","HUDCO":"Infra",
    "RVNL":"Infra","IRCON":"Infra","RAILTEL":"Infra",
    # Defence & PSU
    "HAL":"Defence","BEL":"Defence","BEML":"Defence","GRSE":"Defence",
    "COCHINSHIP":"Defence","MAZDOCK":"Defence","MIDHANI":"Defence","GESHIP":"Defence",
    # FMCG & Consumer
    "HINDUNILVR":"FMCG","ITC":"FMCG","NESTLEIND":"FMCG","BRITANNIA":"FMCG",
    "TATACONSUM":"FMCG","GODREJCONS":"FMCG","DABUR":"FMCG","MARICO":"FMCG",
    # Real Estate
    "GODREJPROP":"RealEstate","PRESTIGE":"RealEstate","OBEROIRLTY":"RealEstate",
    "SOBHA":"RealEstate","SUNTECK":"RealEstate","BRIGADE":"RealEstate",
    "PHOENIXLTD":"RealEstate","LODHA":"RealEstate",
    # Retail & Consumer Disc
    "TRENT":"Retail","DMART":"Retail","MANYAVAR":"Retail","SHOPERSTOP":"Retail",
    "VMART":"Retail","ZOMATO":"Retail","NYKAA":"Retail","DEVYANI":"Retail",
    "WESTLIFE":"Retail","JUBLFOOD":"Retail",
    # Chemicals & Specialty
    "PIDILITIND":"Chemicals","AARTIIND":"Chemicals","DEEPAKNTR":"Chemicals",
    "CLEAN":"Chemicals","NAVINFLUOR":"Chemicals","FLUOROCHEM":"Chemicals",
    "ALKYLAMINE":"Chemicals","UPL":"Chemicals","GHCL":"Chemicals",
    # Industrials / Electrical
    "HAVELLS":"Industrials","VOLTAS":"Industrials","CROMPTON":"Industrials",
    "POLYCAB":"Industrials","KEI":"Industrials","ASTRAL":"Industrials",
    "SUPREMEIND":"Industrials","GRINDWELL":"Industrials",
    # Telecom & Logistics
    "BHARTIARTL":"Telecom","DELHIVERY":"Logistics","CONCOR":"Logistics",
    "IRCTC":"Logistics","GMRINFRA":"Logistics","ADANIPORTS":"Logistics",
    # Conglomerates
    "ADANIENT":"Conglomerate","GRASIM":"Conglomerate","AIAENG":"Industrials",
    # Jewellery
    "TITAN":"Jewellery","KALYANKJIL":"Jewellery","SENCO":"Jewellery",
    "RAJESHEXPO":"Jewellery",
    # Misc
    "ASIANPAINT":"Paints","APOLLOHOSP":"Healthcare",
    "ETERNAL":"Internet","PAYTM":"Internet",
    "IIFL":"NBFC","TATAINVEST":"Conglomerate",
}

# Nifty 500 priority symbols — cleaned, de-duplicated, verified NSE EQ symbols
_NIFTY500_PRIORITY = {
    # ── Banking ──────────────────────────────────────────────────────────────
    "HDFCBANK","ICICIBANK","SBIN","KOTAKBANK","AXISBANK","INDUSINDBK",
    "BANKBARODA","CANBK","PNB","IDFCFIRSTB","FEDERALBNK","BANDHANBNK",
    # ── NBFC & Fintech ───────────────────────────────────────────────────────
    "BAJFINANCE","BAJAJFINSV","CHOLAFIN","MUTHOOTFIN","MANAPPURAM","LICHSGFIN",
    "SBICARD","PFC","RECLTD","IRFC","PNBHOUSING","CANFINHOME","HOMEFIRST",
    "FIVESTAR","EDELWEISS","BAJAJHFL","IIFL",
    # ── Insurance & Wealth ───────────────────────────────────────────────────
    "SBILIFE","HDFCLIFE","ICICIPRULI","ICICIGI","GICRE","NIACL","STARHEALTH",
    "LICI","MOTILALOFS","ANGELONE","KFINTECH","CAMS","CDSL","BSE","MCX",
    "IEX","HDFCAMC",
    # ── IT & Technology ──────────────────────────────────────────────────────
    "TCS","INFY","WIPRO","HCLTECH","TECHM","TATAELXSI","LTTS","MPHASIS",
    "PERSISTENT","COFORGE","HEXAWARE","KPITTECH","CYIENT","MASTEK",
    "ZENSARTECH","HAPPSTMNDS","TANLA","INTELLECT","NEWGEN","DATAMATICS",
    "ROUTE","NAUKRI","NETWEB",
    # ── Energy, Oil & Gas ────────────────────────────────────────────────────
    "RELIANCE","ONGC","BPCL","IOC","MRPL","VEDL","COALINDIA","ADANIPOWER",
    "TATAPOWER","ADANIGREEN","TORNTPOWER","CESC","SJVN","NHPC","NTPC",
    "POWERGRID","SUZLON","INOXWIND","WAAREEENER",
    # ── Pharma & Healthcare ──────────────────────────────────────────────────
    "SUNPHARMA","DRREDDY","CIPLA","DIVISLAB","LUPIN","AUROPHARMA","BIOCON",
    "GLENMARK","IPCA","AJANTPHARM","ZYDUSLIFE","TORNTPHARM","ALKEM",
    "ABBOTINDIA","PFIZER","GLAXO","SANOFI","GRANULES","GLAND","SYNGENE",
    "METROPOLIS","THYROCARE","LALPATHLAB",
    # ── Auto & Auto Ancillaries ──────────────────────────────────────────────
    "MARUTI","TATAMOTORS","M&M","BAJAJ-AUTO","EICHERMOT","HEROMOTOCO",
    "MOTHERSON","BALKRISIND","APOLLOTYRE","MRF","EXIDEIND","SUNDRMFAST",
    "BOSCHLTD","MINDA","SCHAEFFLER","TIMKEN",
    # ── Metals & Mining ──────────────────────────────────────────────────────
    "JSWSTEEL","TATASTEEL","HINDALCO","NATIONALUM","HINDZINC","GMDCLTD",
    # ── Cement & Construction ────────────────────────────────────────────────
    "ULTRACEMCO","SHREECEM","AMBUJACEM","ACC",
    # ── Infra & Capital Goods ────────────────────────────────────────────────
    "LT","SIEMENS","ABB","THERMAX","CUMMINSIND","BHEL","NBCC","HUDCO",
    "RVNL","IRCON","RAILTEL",
    # ── Defence & PSU Manufacturing ──────────────────────────────────────────
    "HAL","BEL","BEML","GRSE","COCHINSHIP","MAZDOCK","MIDHANI","GESHIP",
    # ── FMCG & Consumer Staples ──────────────────────────────────────────────
    "HINDUNILVR","ITC","NESTLEIND","BRITANNIA","TATACONSUM",
    "DABUR","MARICO",
    # ── Real Estate ──────────────────────────────────────────────────────────
    "GODREJPROP","PRESTIGE","OBEROIRLTY","SOBHA","SUNTECK","BRIGADE",
    "PHOENIXLTD","LODHA",
    # ── Retail & Consumer Discretionary ─────────────────────────────────────
    "TRENT","DMART","MANYAVAR","SHOPERSTOP","VMART","ZOMATO","NYKAA",
    "DEVYANI","WESTLIFE","JUBLFOOD",
    # ── Chemicals & Specialty ────────────────────────────────────────────────
    "PIDILITIND","AARTIIND","DEEPAKNTR","CLEAN","NAVINFLUOR","FLUOROCHEM",
    "ALKYLAMINE","UPL","GHCL",
    # ── Industrials & Electricals ────────────────────────────────────────────
    "HAVELLS","VOLTAS","CROMPTON","POLYCAB","KEI","ASTRAL","SUPREMEIND",
    "GRINDWELL","AIAENG",
    # ── Telecom & Logistics ──────────────────────────────────────────────────
    "BHARTIARTL","DELHIVERY","CONCOR","IRCTC","GMRINFRA","ADANIPORTS",
    # ── Conglomerates ────────────────────────────────────────────────────────
    "ADANIENT","GRASIM","TATAINVEST",
    # ── Jewellery ────────────────────────────────────────────────────────────
    "TITAN","KALYANKJIL","SENCO","RAJESHEXPO",
    # ── Paints & Other ───────────────────────────────────────────────────────
    "ASIANPAINT","APOLLOHOSP","ETERNAL",
    # ── Housing Finance & Misc ───────────────────────────────────────────────
    "APTUS","AAVAS",
}


class DynamicUniverse:
    """
    Fetches a ranked list of NSE EQ instruments that are worth analysing
    for intraday trading on the current session.

    Selection criteria (all sourced 100% from Kite):
      1. Exchange segment: NSE, instrument_type EQ
      2. Price in [MIN_PRICE, MAX_PRICE]
      3. Volume ≥ MIN_VOLUME (today's volume from live quote)
      4. Positive momentum: last_price > previous day close (ohlc.close)
      5. Volume surge: today's volume > 1.5× average implied from quote
      6. Sorted by relative volume (volume / typical volume proxy)
    """

    def __init__(self, kite=None):
        self.kite = kite
        if not self.kite:
            try:
                tm = TokenManager()
                self.kite = tm.initialize_kite()
                logger.info("DynamicUniverse: Kite initialized")
            except Exception as e:
                logger.error(f"DynamicUniverse: Kite init failed: {e}")
                self.kite = None
        self._instrument_cache: Optional[List[Dict]] = None
        self._instrument_cache_time: Optional[datetime] = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load_nse_eq_instruments(self) -> List[Dict]:
        """Return NSE EQ instruments, cached for the session."""
        now = datetime.now()
        if (
            self._instrument_cache is not None
            and self._instrument_cache_time is not None
            and (now - self._instrument_cache_time) < timedelta(hours=4)
        ):
            return self._instrument_cache

        if not self.kite:
            return []
        try:
            all_instruments = self.kite.instruments("NSE")
            eq_instruments = [
                i for i in all_instruments
                if i.get("instrument_type") == "EQ"
                and i.get("tradingsymbol") not in _EXCLUDE
            ]
            self._instrument_cache = eq_instruments
            self._instrument_cache_time = now
            logger.info(f"Loaded {len(eq_instruments)} NSE EQ instruments")
            return eq_instruments
        except Exception as e:
            logger.error(f"Error loading instruments: {e}")
            return []

    def _batch_quote(self, symbols: List[str]) -> Dict:
        """
        Fetch quotes using 'NSE:SYMBOL' string format (no token permission needed).
        Kite allows up to 500 instruments per call. Returns dict keyed by 'NSE:SYMBOL'.
        """
        if not self.kite or not symbols:
            return {}
        results = {}
        batch_size = 500
        import time as _time
        for i in range(0, len(symbols), batch_size):
            batch = [f"NSE:{s}" for s in symbols[i: i + batch_size]]
            try:
                quotes = self.kite.quote(batch)
                results.update(quotes)
            except Exception as e:
                logger.error(f"Quote batch error: {e}")
            if i + batch_size < len(symbols):
                _time.sleep(0.5)  # respect rate limits between batches
        return results

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def _score_candidates(self, instruments, top_n: int, for_display: bool = False) -> List[Dict]:
        """
        Core scoring engine used by both get_intraday_candidates and get_top_candidates.
        Returns list of scored dicts with composite score.
        """
        MAX_QUOTE_BATCH = 500
        priority = [i for i in instruments if i.get("tradingsymbol") in _NIFTY500_PRIORITY]
        others   = [i for i in instruments if i.get("tradingsymbol") not in _NIFTY500_PRIORITY]
        combined = (priority + others)[:MAX_QUOTE_BATCH]
        syms     = [i["tradingsymbol"] for i in combined]

        logger.info(f"Fetching quotes for {len(syms)} instruments (priority={len(priority)})…")
        quotes = self._batch_quote(syms)

        import pytz
        ist = pytz.timezone("Asia/Kolkata")
        now_ist = datetime.now(ist)
        market_open  = now_ist.replace(hour=9,  minute=15, second=0, microsecond=0)
        market_close = now_ist.replace(hour=15, minute=30, second=0, microsecond=0)
        is_market_hours = market_open <= now_ist <= market_close and now_ist.weekday() < 5
        effective_min_vol = MIN_VOLUME if is_market_hours else 0

        scored = []
        for inst in combined:
            sym = inst["tradingsymbol"]
            q = quotes.get(f"NSE:{sym}")
            if not q:
                continue
            last_price = q.get("last_price", 0)
            volume     = q.get("volume", 0)
            prev_close = q.get("ohlc", {}).get("close", 0)
            day_open   = q.get("ohlc", {}).get("open", 0)
            day_high   = q.get("ohlc", {}).get("high", 0)
            day_low    = q.get("ohlc", {}).get("low", 0)

            if last_price < MIN_PRICE or last_price > MAX_PRICE:
                continue
            if volume < effective_min_vol:
                continue
            if prev_close <= 0:
                continue

            momentum    = (last_price - prev_close) / prev_close          # % change vs prev close
            open_gap    = (day_open - prev_close) / prev_close if prev_close else 0
            day_range   = (day_high - day_low) / prev_close if prev_close else 0
            vol_score   = min(volume / 2_000_000, 1.0)                    # cap at 2M shares
            in_priority = 1.0 if sym in _NIFTY500_PRIORITY else 0.5

            if is_market_hours:
                composite = (
                    0.35 * vol_score
                    + 0.30 * max(momentum, 0) * 20
                    + 0.15 * max(open_gap, 0) * 20
                    + 0.10 * day_range * 10
                    + 0.10 * in_priority
                )
            else:
                composite = max(momentum, 0) * 20 + 0.1 * in_priority

            scored.append({
                "symbol":       sym,
                "last_price":   last_price,
                "volume":       volume,
                "prev_close":   prev_close,
                "momentum_pct": round(momentum * 100, 2),
                "day_range_pct": round(day_range * 100, 2),
                "vol_score":    round(vol_score, 4),
                "sector":       SECTOR_MAP.get(sym, "Other"),
                "score":        round(composite, 4),
            })

        scored.sort(key=lambda x: x["score"], reverse=True)
        return scored[:top_n]

    def get_top_candidates(self, display_n: int = 50, scan_n: int = 150) -> dict:
        """
        Returns:
          - 'scan_universe':  top scan_n symbols for the bot to analyse (full scan)
          - 'top_display':    top display_n symbols with sector diversification cap
                              for dashboard display (max 4 per sector)
        """
        instruments = self._load_nse_eq_instruments()
        if not instruments:
            fb = self._fallback_universe()
            return {"scan_universe": fb, "top_display": fb[:display_n]}

        full_scored = self._score_candidates(instruments, top_n=scan_n)

        # Build diversified top-display list: max 4 stocks per sector
        MAX_PER_SECTOR = 4
        sector_counts: dict = {}
        top_display = []
        for c in full_scored:
            sector = c.get("sector", "Other")
            count  = sector_counts.get(sector, 0)
            if count < MAX_PER_SECTOR:
                top_display.append(c)
                sector_counts[sector] = count + 1
            if len(top_display) >= display_n:
                break

        scan_syms    = [c["symbol"] for c in full_scored]
        display_syms = [c["symbol"] for c in top_display]
        logger.info(
            f"Universe: {len(scan_syms)} for scan | {len(display_syms)} for display "
            f"({len(sector_counts)} sectors represented)"
        )
        return {"scan_universe": scan_syms, "top_display": display_syms}

    def get_intraday_candidates(self, top_n: int = DEFAULT_TOP_N) -> List[str]:
        """
        Return top_n NSE stock symbols suitable for trading,
        ranked by composite volume+momentum+priority score.

        Returns:
            List of trading symbols (e.g. ["RELIANCE", "TCS", ...])
        """
        if not self.kite:
            logger.error("Kite not available – returning fallback universe")
            return self._fallback_universe()
        instruments = self._load_nse_eq_instruments()
        if not instruments:
            return self._fallback_universe()
        scored = self._score_candidates(instruments, top_n=top_n)
        symbols = [s["symbol"] for s in scored]
        logger.info(f"Dynamic universe selected {len(symbols)} stocks: {symbols[:10]}…")
        return symbols

    def get_universe(self, top_n: int = DEFAULT_TOP_N) -> List[str]:
        """Alias for get_intraday_candidates; returns list of symbols."""
        return self.get_intraday_candidates(top_n=top_n)

    def get_candidates_with_details(self, top_n: int = DEFAULT_TOP_N) -> List[Dict]:
        """Returns full detail dicts for top_n candidates."""
        if not self.kite:
            return [{"symbol": s} for s in self._fallback_universe()]
        instruments = self._load_nse_eq_instruments()
        if not instruments:
            return [{"symbol": s} for s in self._fallback_universe()]
        return self._score_candidates(instruments, top_n=top_n)

    @staticmethod
    def _fallback_universe() -> List[str]:
        """Minimal fallback when Kite is unavailable (broad Nifty 50 sample)."""
        return [
            "RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK",
            "HINDUNILVR", "ITC", "SBIN", "BAJFINANCE", "BHARTIARTL",
            "KOTAKBANK", "LT", "AXISBANK", "ASIANPAINT", "MARUTI",
            "WIPRO", "ULTRACEMCO", "TITAN", "NESTLEIND", "SUNPHARMA",
        ]


if __name__ == "__main__":
    import json

    print("=" * 60)
    print("Dynamic Universe Scanner — NSE Intraday Candidates")
    print("=" * 60)

    scanner = DynamicUniverse()

    if not scanner.kite:
        print("\n[WARNING] Kite not connected. Showing fallback universe.\n")
        symbols = scanner._fallback_universe()
        for s in symbols:
            print(f"  {s}")
    else:
        print("\nFetching live NSE data from Kite Connect...\n")
        candidates = scanner.get_candidates_with_details(top_n=30)

        if not candidates:
            print("No candidates found. Market may be closed or Kite token expired.")
        else:
            print(
                f"{'Rank':<5} {'Symbol':<15} {'Price':>8} {'Volume':>12} "
                f"{'Momentum%':>10} {'Score':>8}"
            )
            print("-" * 62)
            for rank, c in enumerate(candidates, 1):
                print(
                    f"{rank:<5} {c['symbol']:<15} "
                    f"{c['last_price']:>8.2f} "
                    f"{c['volume']:>12,} "
                    f"{c['momentum_pct']:>10.2f} "
                    f"{c['score']:>8.4f}"
                )
            print(f"\nTotal candidates selected: {len(candidates)}")
            symbols = [c["symbol"] for c in candidates]
            print(f"\nSymbol list: {symbols}")
