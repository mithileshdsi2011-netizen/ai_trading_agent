"""
Live IPO Data Provider — NSE public endpoints

Fetches real IPO data from NSE India's public JSON endpoints:

    GET /api/ipo-current-issue                          open/current issues
    GET /api/all-upcoming-issues?category=ipo           upcoming issues
    GET /api/public-past-issues?from_date=&to_date=     recently closed/listed
    GET /api/ipo-detail?symbol=X&series=EQ              per-issue detail

Broker isolation: this provider uses NO broker API — no Kite, no Angel.
NSE public data keeps the IPO engine broker-agnostic.

Degradation contract: ANY fetch/parse failure raises IPOProviderError.
The dashboard layer falls back to the clearly-flagged mock provider and
reports live_data_available=false — live and demo data are never mixed
in a single response.

NEVER logs or stores credentials — this provider uses none.
"""
import logging
import threading
import time
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any

import requests

from .base import IPODataProvider
from ..models import IPO, IPOStatus, IPOFinancials, IPOSubscription

logger = logging.getLogger(__name__)

NSE_BASE = "https://www.nseindia.com"
DEFAULT_CACHE_TTL_SECONDS = 900   # IPO data moves slowly; 15 min
DEFAULT_TIMEOUT_SECONDS = 12

# NSE's public API requires a browser-like header set plus cookies from a
# landing-page request (Akamai bot protection).
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/market-data/all-upcoming-issues-ipo",
}


class IPOProviderError(Exception):
    """Raised when live IPO data cannot be fetched or parsed."""


class NseIPODataProvider(IPODataProvider):
    """Live IPO provider backed by NSE public endpoints."""

    SOURCE_NAME = "NSE India (live)"

    def __init__(self, session: Optional[requests.Session] = None,
                 timeout: int = DEFAULT_TIMEOUT_SECONDS,
                 cache_ttl: int = DEFAULT_CACHE_TTL_SECONDS):
        self._session = session or requests.Session()
        self._session.headers.update(_HEADERS)
        self._timeout = timeout
        self._cache_ttl = cache_ttl
        self._cache: Dict[str, Any] = {}
        self._cache_expiry: Dict[str, float] = {}
        self._cookies_bootstrapped = False
        self._lock = threading.Lock()

    # ── transport ───────────────────────────────────────────────────
    def _bootstrap_cookies(self):
        """Prime NSE cookies via a landing-page request."""
        self._session.get(NSE_BASE + "/", timeout=self._timeout)
        self._cookies_bootstrapped = True

    def _get_json(self, path: str) -> Any:
        """GET an NSE api path with cookie bootstrap + one auth retry."""
        with self._lock:
            if not self._cookies_bootstrapped:
                self._bootstrap_cookies()
            resp = self._session.get(NSE_BASE + path, timeout=self._timeout)
            if resp.status_code in (401, 403):
                # Akamai cookie expired — re-bootstrap once
                self._cookies_bootstrapped = False
                self._bootstrap_cookies()
                resp = self._session.get(NSE_BASE + path, timeout=self._timeout)
            if resp.status_code != 200:
                raise IPOProviderError(f"NSE {path} -> HTTP {resp.status_code}")
            try:
                return resp.json()
            except Exception as e:
                raise IPOProviderError(f"NSE {path} -> invalid JSON: {e}")

    def _cached(self, key: str, fetch):
        now = time.time()
        if key in self._cache and now < self._cache_expiry.get(key, 0):
            return self._cache[key]
        value = fetch()
        self._cache[key] = value
        self._cache_expiry[key] = now + self._cache_ttl
        return value

    # ── schema-flexible mapping helpers ─────────────────────────────
    @staticmethod
    def _pick(row: Dict[str, Any], *keys: str) -> Optional[Any]:
        """Return the first non-empty value among candidate field names."""
        for k in keys:
            v = row.get(k)
            if v is not None and str(v).strip() not in ('', '-', '--', 'N/A', 'NA'):
                return v
        return None

    @staticmethod
    def _num(v) -> Optional[float]:
        if v is None:
            return None
        s = (str(v).replace(',', '').replace('₹', '')
             .replace('Rs.', '').replace('Rs', '').replace('INR', '')
             .replace('x', '').strip())
        try:
            return float(s)
        except (ValueError, TypeError):
            return None

    @classmethod
    def _num_field(cls, row, *keys) -> Optional[float]:
        return cls._num(cls._pick(row, *keys))

    @staticmethod
    def _date(v) -> Optional[str]:
        """Normalise DD-MM-YYYY / DD-MM-YY / DD-Mon-YYYY / ISO dates to ISO YYYY-MM-DD."""
        if not v:
            return None
        s = str(v).strip()
        for fmt in ('%d-%m-%Y', '%d-%m-%y', '%d/%m/%Y', '%Y-%m-%d',
                    '%d %b %Y', '%d-%b-%Y', '%d-%b-%y', '%d %B %Y'):
            try:
                return datetime.strptime(s[:12], fmt).strftime('%Y-%m-%d')
            except ValueError:
                continue
        return s[:10] or None

    @classmethod
    def _issue_size_cr(cls, row, lo, hi) -> Optional[float]:
        """Issue size in crores — direct crore fields, else shares×price/1e7."""
        direct = cls._num_field(row, 'issueSizeCr', 'issue_size_cr', 'issueSizeCrores')
        if direct is not None:
            return direct
        shares = cls._num_field(row, 'issueSize', 'noOfSharesOffered', 'issue_size')
        price = hi or lo or cls._num_field(row, 'issuePrice')
        if shares and price:
            return round(shares * price / 1e7, 2)
        return None

    @classmethod
    def _price_band(cls, row) -> tuple:
        """Return (min, max) from min/max fields or a 'Rs.208 to Rs.220' band string."""
        lo = cls._num_field(row, 'minPrice', 'priceBandMin', 'price_band_min', 'minprice')
        hi = cls._num_field(row, 'maxPrice', 'priceBandMax', 'price_band_max', 'maxprice',
                            'cutOffPrice', 'cutoffprice')
        band = cls._pick(row, 'priceBand', 'price_band', 'priceRange', 'price')
        if (lo is None or hi is None) and band:
            nums = [cls._num(t) for t in str(band).replace('–', '-').split()]
            nums = [n for n in nums if n is not None]
            if nums:
                lo = lo or min(nums)
                hi = hi or max(nums)
        single = cls._num_field(row, 'issuePrice', 'issue_price')
        if lo is None and hi is None and single is not None:
            lo = hi = single
        return lo, hi

    @staticmethod
    def _issue_type(row) -> Optional[str]:
        """Normalize NSE series/securityType into EQUITY | SME | DEBT."""
        raw = str(row.get('series') or row.get('securityType') or '').upper()
        if raw in ('EQ', 'EQUITY'):
            return 'EQUITY'
        if raw == 'SME':
            return 'SME'
        if 'DEBT' in raw or 'NCD' in raw or 'ZCZP' in raw:
            return 'DEBT'
        return raw or None

    @classmethod
    def _to_ipo(cls, row: Dict[str, Any], status: IPOStatus) -> IPO:
        lo, hi = cls._price_band(row)
        return IPO(
            name=str(cls._pick(row, 'companyName', 'company', 'company_name', 'name',
                               'issueName', 'htmSym')
                     or cls._pick(row, 'symbol', 'symbolName') or 'Unknown'),
            symbol=cls._pick(row, 'symbol', 'symbolName'),
            sector=cls._pick(row, 'industry', 'sector', 'industryNew'),
            status=status,
            issue_type=cls._issue_type(row),
            price_band_min=lo,
            price_band_max=hi,
            issue_size=cls._issue_size_cr(row, lo, hi),
            fresh_issue=cls._num_field(row, 'freshIssueCr', 'fresh_issue'),
            offer_for_sale=cls._num_field(row, 'ofsCr', 'offerForSale', 'offer_for_sale'),
            open_date=cls._date(cls._pick(row, 'issueStartDate', 'ipoStartDate',
                                          'biddingStartDate',
                                          'openDate', 'startDate', 'issueOpenDate')),
            close_date=cls._date(cls._pick(row, 'issueEndDate', 'ipoEndDate',
                                           'biddingEndDate',
                                           'closeDate', 'endDate', 'issueCloseDate')),
            listing_date=cls._date(cls._pick(row, 'listingDate', 'dateOfListing',
                                             't1ModStartDate')),
            subscription=cls._subscription(row),
            business_description=cls._pick(row, 'aboutCompany', 'companyDescription',
                                           'businessDescription'),
            is_demo_data=False,
            data_source=cls.SOURCE_NAME,
        )

    @classmethod
    def _subscription(cls, row) -> Optional[IPOSubscription]:
        """Map subscription multiples when the feed exposes them."""
        sub = row.get('subscriptionData') or row.get('subscription') or {}
        if not isinstance(sub, dict):
            sub = {}
        qib = cls._num_field(sub, 'qib', 'qibSubscription') or cls._num_field(row, 'qibSubscribed')
        nii = cls._num_field(sub, 'nii', 'niiSubscription') or cls._num_field(row, 'niiSubscribed')
        retail = (cls._num_field(sub, 'retail', 'retailSubscription')
                  or cls._num_field(row, 'retailSubscribed'))
        overall = (cls._num_field(sub, 'total', 'overall', 'totalSubscription')
                   or cls._num_field(row, 'totalSubscribed', 'subscriptionTimes',
                                     'noOfTime'))
        if overall is None:
            bid = cls._num_field(row, 'noOfsharesBid')
            offered = cls._num_field(row, 'noOfSharesOffered')
            if bid is not None and offered:
                overall = round(bid / offered, 2)
        if all(v is None for v in (qib, nii, retail, overall)):
            return None
        return IPOSubscription(qib=qib, nii=nii, retail=retail, overall=overall)

    @staticmethod
    def _status_for(row, default: IPOStatus) -> IPOStatus:
        """Derive status from dates when the feed doesn't state it."""
        open_d = NseIPODataProvider._date(
            NseIPODataProvider._pick(row, 'issueStartDate', 'ipoStartDate', 'biddingStartDate',
                                     'openDate', 'startDate'))
        close_d = NseIPODataProvider._date(
            NseIPODataProvider._pick(row, 'issueEndDate', 'ipoEndDate', 'biddingEndDate',
                                     'closeDate', 'endDate'))
        list_d = NseIPODataProvider._date(
            NseIPODataProvider._pick(row, 'listingDate', 'dateOfListing'))
        today = datetime.now().strftime('%Y-%m-%d')
        if list_d and list_d <= today:
            return IPOStatus.LISTED
        if open_d and close_d and open_d <= today <= close_d:
            return IPOStatus.OPEN
        if close_d and close_d < today:
            return IPOStatus.LISTED if list_d else IPOStatus.CLOSED
        return default

    # ── provider interface ──────────────────────────────────────────
    @staticmethod
    def _dedup_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """One row per symbol — prefer the 'Total' category row (per-category
        subscription rows repeat the same symbol)."""
        picked = {}
        order = []
        for r in rows:
            if not isinstance(r, dict):
                continue
            sym = str(r.get('symbol') or r.get('symbolName') or '')
            if not sym:
                continue
            if sym not in picked:
                order.append(sym)
                picked[sym] = r
            elif str(r.get('category', '')).lower() == 'total':
                picked[sym] = r
        return [picked[s] for s in order]

    def _all_ipos(self) -> List[IPO]:
        """All issues (equity + non-equity) across open/upcoming/recent feeds."""
        open_rows, up_rows, re_rows = [], [], []

        data = self._get_json("/api/ipo-current-issue")
        open_rows = self._dedup_rows(data if isinstance(data, list) else data.get('data', []))
        open_syms = {str(r.get('symbol') or r.get('symbolName'))
                     for r in open_rows}

        data = self._get_json("/api/all-upcoming-issues?category=ipo")
        up_rows = [r for r in self._dedup_rows(
            data if isinstance(data, list)
            else (data.get('data') or data.get('upcomingIpos') or []))
            if str(r.get('symbol') or r.get('symbolName')) not in open_syms]

        to_d = datetime.now()
        from_d = to_d - timedelta(days=90)
        data = self._get_json(
            "/api/public-past-issues?from_date={}&to_date={}"
            .format(from_d.strftime('%d-%m-%Y'), to_d.strftime('%d-%m-%Y')))
        re_rows = self._dedup_rows(data if isinstance(data, list)
                                   else (data.get('data') or []))

        return ([self._to_ipo(r, self._status_for(r, IPOStatus.OPEN)) for r in open_rows]
                + [self._to_ipo(r, self._status_for(r, IPOStatus.UPCOMING)) for r in up_rows]
                + [self._to_ipo(r, self._status_for(r, IPOStatus.CLOSED)) for r in re_rows])

    def get_open_ipos(self) -> List[IPO]:
        """Currently open equity/SME issues (DEBT/NCD/ZCZP excluded)."""
        return [i for i in self._cached('all', self._all_ipos)
                if i.is_equity and i.status == IPOStatus.OPEN]

    def get_upcoming_ipos(self) -> List[IPO]:
        """Announced upcoming equity/SME issues — excludes symbols already open
        (NSE's upcoming feed lists currently-open issues too)."""
        return [i for i in self._cached('all', self._all_ipos)
                if i.is_equity and i.status == IPOStatus.UPCOMING]

    def get_recent_ipos(self) -> List[IPO]:
        """Recently closed/listed equity/SME issues (last 90 days)."""
        return [i for i in self._cached('all', self._all_ipos)
                if i.is_equity and i.status in (IPOStatus.CLOSED, IPOStatus.LISTED)]

    def get_non_equity_ipos(self) -> List[IPO]:
        """Non-equity offerings (DEBT/NCD/ZCZP) — tracked separately,
        excluded from equity IPO scoring."""
        return [i for i in self._cached('all', self._all_ipos) if not i.is_equity]

    def get_ipo_details(self, ipo_id: str) -> Optional[IPO]:
        """Per-issue detail; falls back to cached lists on miss."""
        symbol = str(ipo_id).upper()
        try:
            data = self._get_json(f"/api/ipo-detail?symbol={symbol}&series=EQ")
            row = data.get('data', data) if isinstance(data, dict) else data
            if isinstance(row, dict) and row.get('issueInfo'):
                row = row['issueInfo']
            if isinstance(row, dict) and row:
                return self._to_ipo(row, self._status_for(row, IPOStatus.OPEN))
        except IPOProviderError:
            raise
        except Exception as e:
            raise IPOProviderError(f"NSE ipo-detail {symbol}: {e}")
        for ipo in self.get_open_ipos() + self.get_upcoming_ipos() + self.get_recent_ipos():
            if ipo.symbol == symbol:
                return ipo
        return None

    def is_demo_data(self) -> bool:
        return False

    def get_data_source_name(self) -> str:
        return self.SOURCE_NAME


# ── lazy process-wide singleton (NSE data is slow-moving) ────────────
_PROVIDER: Optional[NseIPODataProvider] = None
_PROVIDER_LOCK = threading.Lock()


def get_live_ipo_provider() -> NseIPODataProvider:
    """Process-wide NSE provider singleton with in-memory TTL cache."""
    global _PROVIDER
    with _PROVIDER_LOCK:
        if _PROVIDER is None:
            _PROVIDER = NseIPODataProvider()
        return _PROVIDER
