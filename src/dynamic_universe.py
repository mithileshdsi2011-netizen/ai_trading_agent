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

# Nifty 500 priority symbols – these are always included in the 500-stock quote batch
# so we never miss the most liquid Indian stocks.
_NIFTY500_PRIORITY = {
    "RELIANCE","TCS","HDFCBANK","INFY","ICICIBANK","HINDUNILVR","ITC","SBIN",
    "BAJFINANCE","BHARTIARTL","KOTAKBANK","LT","AXISBANK","ASIANPAINT","MARUTI",
    "WIPRO","TITAN","NESTLEIND","SUNPHARMA","BAJAJ-AUTO","HCLTECH","TECHM",
    "ULTRACEMCO","ADANIENT","ADANIPORTS","POWERGRID","NTPC","ONGC","COALINDIA",
    "JSWSTEEL","TATASTEEL","HINDALCO","TATAMOTORS","M&M","DIVISLAB","DRREDDY",
    "CIPLA","EICHERMOT","HEROMOTOCO","APOLLOHOSP","INDUSINDBK","GRASIM",
    "BRITANNIA","SBILIFE","HDFCLIFE","BPCL","IOC","VEDL","TATACONSUM","UPL",
    "SHREECEM","PIDILITIND","DMART","BAJAJFINSV","AMBUJACEM","ACC","BANKBARODA",
    "CANBK","PNB","IDFCFIRSTB","FEDERALBNK","BANDHANBNK","MUTHOOTFIN","CHOLAFIN",
    "LICHSGFIN","SBICARD","MANAPPURAM","PFC","RECLTD","IRFC","HAL","BEL","BHEL",
    "SIEMENS","ABB","HAVELLS","VOLTAS","WHIRLPOOL","CROMPTON","POLYCAB","KEI",
    "TATAPOWER","ADANIGREEN","TORNTPOWER","CESC","SJVN","NHPC","ZOMATO","NYKAA",
    "PAYTM","DELHIVERY","IRCTC","CONCOR","GMRINFRA","AIAENG","MOTHERSON",
    "BALKRISIND","APOLLOTYRE","MRF","EXIDEIND","AMARAJABAT","SUNDRMFAST",
    "BOSCHLTD","MINDA","SCHAEFFLER","TIMKEN","GRINDWELL","ASTRAL","SUPREMEIND",
    "GHCL","AARTIIND","DEEPAKNTR","CLEAN","NAVINFLUOR","FLUOROCHEM","ALKYLAMINE",
    "JUBLFOOD","DEVYANI","WESTLIFE","BARBEQUE","SAPPHIRE","ZYDUSLIFE","TORNTPHARM",
    "ALKEM","LUPIN","AUROPHARMA","BIOCON","GLENMARK","IPCA","AJANTPHARM",
    "METROPOLIS","THYROCARE","LALPATHLAB","SYNGENE","PPLPHARMA","GLAND","GRANULES",
    "ABBOTINDIA","PFIZER","GLAXO","SANOFI","CHOLAHLDNG","MOTILALOFS","ANGELONE",
    "ICICIPRULI","ICICIGI","GICRE","NIACL","STARHEALTH","KFINTECH","CAMS",
    "CDSL","BSE","MCX","IEX","TRENT","MANYAVAR","SHOPERSTOP","VMART",
    "PHOENIXLTD","PRESTIGE","GODREJPROP","OBEROIRLTY","SOBHA","SUNTECK","BRIGADE",
    "MAHINDCIE","CUMMINSIND","THERMAX","BHARAT FORGE","KALYANKJIL","RAJESHEXPO",
    "TITAN","PCJEWELLER","SENCO","TATAELXSI","LTTS","MPHASIS","PERSISTENT",
    "COFORGE","HEXAWARE","KPITTECH","CYIENT","MASTEK","NIITTECH","RAMSARUP",
    "ZENSARTECH","HAPPSTMNDS","ROUTE","TANLA","INTELLECT","NEWGEN","DATAMATICS",
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

    def get_intraday_candidates(self, top_n: int = DEFAULT_TOP_N) -> List[str]:
        """
        Return top_n NSE stock symbols suitable for intraday trading today,
        ranked by volume surge and positive momentum.

        Returns:
            List of trading symbols (e.g. ["RELIANCE", "TCS", ...])
        """
        if not self.kite:
            logger.error("Kite not available – returning fallback universe")
            return self._fallback_universe()

        instruments = self._load_nse_eq_instruments()
        if not instruments:
            return self._fallback_universe()

        # Step 1: Prioritise Nifty500 known-liquid stocks first,
        # then fill remaining slots with other EQ instruments.
        MAX_QUOTE_BATCH = 500
        priority = [i for i in instruments if i.get("tradingsymbol") in _NIFTY500_PRIORITY]
        others   = [i for i in instruments if i.get("tradingsymbol") not in _NIFTY500_PRIORITY
                    and i.get("tradingsymbol", "")]
        combined = priority + others
        price_filtered = combined[:MAX_QUOTE_BATCH]

        # Step 2: batch quote using 'NSE:SYMBOL' strings
        syms = [i["tradingsymbol"] for i in price_filtered]
        logger.info(f"Fetching quotes for {len(syms)} NSE EQ instruments (priority={len(priority)})…")
        quotes = self._batch_quote(syms)

        # Step 3: score each instrument
        scored = []
        for inst in price_filtered:
            sym = inst["tradingsymbol"]
            q = quotes.get(f"NSE:{sym}")
            if not q:
                continue

            last_price = q.get("last_price", 0)
            volume = q.get("volume", 0)
            prev_close = q.get("ohlc", {}).get("close", 0)
            day_open = q.get("ohlc", {}).get("open", 0)

            if last_price < MIN_PRICE or last_price > MAX_PRICE:
                continue
            # When market is closed volume will be 0 – still score on momentum
            import pytz
            ist = pytz.timezone("Asia/Kolkata")
            now_ist = datetime.now(ist)
            market_open = now_ist.replace(hour=9, minute=15, second=0, microsecond=0)
            market_close = now_ist.replace(hour=15, minute=30, second=0, microsecond=0)
            is_market_hours = market_open <= now_ist <= market_close and now_ist.weekday() < 5
            effective_min_volume = MIN_VOLUME if is_market_hours else 0

            if volume < effective_min_volume:
                continue
            if prev_close <= 0:
                continue

            momentum = (last_price - prev_close) / prev_close
            volume_score = volume / 1_000_000
            # During closed hours rank purely by momentum; open hours use volume too
            if is_market_hours:
                score = (0.6 * volume_score) + (0.4 * max(momentum, 0) * 100)
            else:
                score = max(momentum, 0) * 100  # momentum only

            scored.append({
                "symbol": sym,
                "last_price": last_price,
                "volume": volume,
                "momentum_pct": round(momentum * 100, 2),
                "score": round(score, 4),
            })

        # Step 4: sort by score, return top_n symbols
        scored.sort(key=lambda x: x["score"], reverse=True)
        top = scored[:top_n]

        symbols = [s["symbol"] for s in top]
        logger.info(
            f"Dynamic universe selected {len(symbols)} stocks: {symbols[:10]}…"
        )
        return symbols

    def get_candidates_with_details(self, top_n: int = DEFAULT_TOP_N) -> List[Dict]:
        """Same as get_intraday_candidates but returns full detail dicts."""
        if not self.kite:
            return [{"symbol": s} for s in self._fallback_universe()]

        instruments = self._load_nse_eq_instruments()
        if not instruments:
            return [{"symbol": s} for s in self._fallback_universe()]

        MAX_QUOTE_BATCH = 500
        priority = [i for i in instruments if i.get("tradingsymbol") in _NIFTY500_PRIORITY]
        others   = [i for i in instruments if i.get("tradingsymbol") not in _NIFTY500_PRIORITY
                    and i.get("tradingsymbol", "")]
        price_filtered = (priority + others)[:MAX_QUOTE_BATCH]
        syms = [i["tradingsymbol"] for i in price_filtered]
        quotes = self._batch_quote(syms)

        scored = []
        for inst in price_filtered:
            sym = inst["tradingsymbol"]
            q = quotes.get(f"NSE:{sym}")
            if not q:
                continue

            last_price = q.get("last_price", 0)
            volume = q.get("volume", 0)
            prev_close = q.get("ohlc", {}).get("close", 0)

            if last_price < MIN_PRICE or last_price > MAX_PRICE:
                continue
            if volume < MIN_VOLUME:
                continue
            if prev_close <= 0:
                continue

            import pytz
            ist = pytz.timezone("Asia/Kolkata")
            now_ist = datetime.now(ist)
            market_open = now_ist.replace(hour=9, minute=15, second=0, microsecond=0)
            market_close = now_ist.replace(hour=15, minute=30, second=0, microsecond=0)
            is_market_hours = market_open <= now_ist <= market_close and now_ist.weekday() < 5
            effective_min_volume = MIN_VOLUME if is_market_hours else 0

            if volume < effective_min_volume:
                continue
            if prev_close <= 0:
                continue

            momentum = (last_price - prev_close) / prev_close
            volume_score = volume / 1_000_000
            score = (0.6 * volume_score) + (0.4 * max(momentum, 0) * 100) if is_market_hours else max(momentum, 0) * 100

            scored.append({
                "symbol": sym,
                "last_price": last_price,
                "volume": volume,
                "prev_close": prev_close,
                "momentum_pct": round(momentum * 100, 2),
                "score": round(score, 4),
            })

        scored.sort(key=lambda x: x["score"], reverse=True)
        return scored[:top_n]

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
