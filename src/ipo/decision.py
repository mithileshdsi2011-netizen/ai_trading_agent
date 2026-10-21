"""
IPO Investment Decision Engine

Deterministic, explainable scoring that produces two independent
recommendations per equity IPO:

  A. Listing Gains  — apply intending to exit around listing day
  B. Long-Term      — apply intending to hold 1-3 years

Design principles (documented assumptions, not validated predictors):
  - Every component returns a 0-100 score or None when its inputs are
    absent. Missing inputs are EXCLUDED from the weighted average and
    reported — never silently scored zero or neutral.
  - A horizon score is only emitted when the covered weight reaches
    MIN_COVERAGE; otherwise the recommendation is INSUFFICIENT DATA.
  - Subscription demand alone cannot produce a confident score: it is
    30% of listing weight and 10% of long-term weight, so no other
    inputs => coverage below the gate => INSUFFICIENT DATA.
  - STALE source data caps confidence at LOW and downgrades
    CONSIDER APPLYING to WAIT.
  - Weights are configurable via the WEIGHTS dicts; tests pin the
    defaults.
"""

import logging
from datetime import datetime
from typing import Dict, List, Optional

from .models import IPO, IPOFinancials, MarketSentiment, IPOStatus

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configurable scoring weights (starting assumptions, not validated predictors)
# ---------------------------------------------------------------------------
LISTING_WEIGHTS = {
    'subscription': 0.30,   # institutional/retail demand signal
    'valuation':    0.25,   # implied P/E vs peers
    'sentiment':    0.20,   # NIFTY trend + India VIX
    'fundamentals': 0.15,   # growth, profitability, leverage, cash flow
    'structure':    0.10,   # fresh issue vs OFS, retail accessibility
}

LONGTERM_WEIGHTS = {
    'fundamentals': 0.40,
    'valuation':    0.30,
    'structure':    0.10,
    'sentiment':    0.10,
    'subscription': 0.10,
}

# Fraction of total horizon weight that must have data before a score is
# emitted. Subscription(.30) + structure(.10) = .40 is below this gate —
# demand + issue mechanics alone cannot produce a score; at least one of
# valuation / sentiment / fundamentals must also be present.
MIN_COVERAGE = 0.45

# Score -> recommendation thresholds.
REC_CONSIDER = 65.0   # >= 65 -> CONSIDER APPLYING
REC_WAIT = 40.0       # >= 40 -> WAIT ; below -> AVOID

CONFIDENCE_HIGH_COVERAGE = 0.70
CONFIDENCE_MED_COVERAGE = 0.45


class IPODecisionEngine:
    """Deterministic two-horizon IPO decision engine."""

    def __init__(self, listing_weights: Optional[Dict[str, float]] = None,
                 longterm_weights: Optional[Dict[str, float]] = None):
        self.listing_weights = dict(listing_weights or LISTING_WEIGHTS)
        self.longterm_weights = dict(longterm_weights or LONGTERM_WEIGHTS)

    # ------------------------------------------------------------------
    def decide(self, ipo: IPO,
               sentiment: Optional[MarketSentiment] = None,
               fundamentals: Optional[IPOFinancials] = None,
               stale: bool = False) -> Dict:
        """Produce the decision payload for one IPO."""
        fin = fundamentals or ipo.financials
        sub = ipo.subscription
        notes: List[str] = []          # data-integrity observations
        components = {
            'subscription': self._subscription_component(sub),
            'valuation': self._valuation_component(ipo, fin, notes),
            'sentiment': self._sentiment_component(sentiment),
            'fundamentals': self._fundamentals_component(fin, notes),
            'structure': self._structure_component(ipo, notes),
        }

        listing_score, listing_cov = self._weighted_score(components, self.listing_weights)
        longterm_score, longterm_cov = self._weighted_score(components, self.longterm_weights)

        listing_rec = self._recommend(listing_score, listing_cov)
        longterm_rec = self._recommend(longterm_score, longterm_cov)

        coverage = max(listing_cov, longterm_cov)
        confidence = self._confidence(coverage, stale)

        if stale:
            # Never issue a fresh high-conviction call on stale data.
            if listing_rec == 'CONSIDER APPLYING':
                listing_rec = 'WAIT'
            if longterm_rec == 'CONSIDER APPLYING':
                longterm_rec = 'WAIT'
            notes.append('Source data is stale — recommendation downgraded and '
                         'confidence capped at LOW.')

        rationale = self._rationale(ipo, sub, sentiment, fin, components)
        key_risks, missing = self._risks_and_missing(ipo, fin, sentiment,
                                                   components, sub)

        return {
            'listing_recommendation': listing_rec,
            'longterm_recommendation': longterm_rec,
            'listing_score': round(listing_score, 1) if listing_score is not None else None,
            'longterm_score': round(longterm_score, 1) if longterm_score is not None else None,
            'listing_coverage': round(listing_cov, 2),
            'longterm_coverage': round(longterm_cov, 2),
            'risk_level': self._risk_level(ipo, fin, components, coverage),
            'confidence': confidence,
            'rationale': rationale[:5],
            'key_risks': key_risks[:5],
            'missing': missing,
            'components': {k: (round(v, 1) if v is not None else None)
                           for k, v in components.items()},
            'weights': {'listing': self.listing_weights,
                        'longterm': self.longterm_weights},
            'valuation_class': self._valuation_class(ipo, fin),
            'notes': notes,
            'analyzed_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        }

    # ------------------------------------------------------------------
    # Component scorers — return 0-100 or None when inputs absent
    # ------------------------------------------------------------------
    def _subscription_component(self, sub) -> Optional[float]:
        """Demand strength. f(x)=min(100, x*25): 1x->25, 4x->100.
        QIB weighted heaviest — institutional demand is the listing-gain
        signal that actually matters."""
        if sub is None:
            return None
        parts = []
        if sub.qib is not None:
            parts.append((min(100.0, sub.qib * 25.0), 0.5))
        if sub.overall is not None:
            parts.append((min(100.0, sub.overall * 25.0), 0.3))
        if sub.retail is not None:
            parts.append((min(100.0, sub.retail * 25.0), 0.2))
        if not parts:
            return None
        w = sum(wt for _, wt in parts)
        return sum(s * wt for s, wt in parts) / w

    def _valuation_component(self, ipo: IPO, fin: Optional[IPOFinancials],
                             notes: List[str]) -> Optional[float]:
        """Implied P/E (price cap / EPS) vs peer P/E. P/E is not applied
        to loss-making issuers."""
        pe = None
        if fin and fin.pe_ratio:
            pe = fin.pe_ratio
        elif fin and fin.eps and fin.eps > 0 and ipo.price_band_max:
            pe = ipo.price_band_max / fin.eps

        if pe is None:
            if fin and ((fin.profit is not None and fin.profit <= 0) or
                        (fin.eps is not None and fin.eps <= 0)):
                notes.append('Issuer is loss-making — P/E valuation not applicable.')
            return None

        peer = fin.peer_pe if fin else None
        if peer and peer > 0:
            if pe < peer * 0.8:
                return 85.0
            if pe < peer:
                return 70.0
            if pe < peer * 1.2:
                return 50.0
            return 25.0
        # No peer benchmark: absolute P/E sanity bands only.
        notes.append('No peer P/E benchmark — valuation judged on absolute P/E only.')
        if pe < 15:
            return 70.0
        if pe < 25:
            return 55.0
        if pe < 40:
            return 40.0
        return 25.0

    def _sentiment_component(self, sent: Optional[MarketSentiment]) -> Optional[float]:
        """NIFTY daily change + India VIX level. Base 50, adjusted and
        clamped to 0-100."""
        if sent is None or (sent.nifty_change_pct is None and sent.india_vix is None):
            return None
        score = 50.0
        c = sent.nifty_change_pct
        if c is not None:
            if c > 0.5:
                score += 20
            elif c > 0:
                score += 10
            elif c < -0.5:
                score -= 20
            elif c < 0:
                score -= 10
        v = sent.india_vix
        if v is not None:
            if v < 13:
                score += 15
            elif v < 18:
                score += 5
            elif v > 28:
                score -= 25
            elif v > 22:
                score -= 15
        return max(0.0, min(100.0, score))

    def _fundamentals_component(self, fin: Optional[IPOFinancials],
                                notes: List[str]) -> Optional[float]:
        """Growth, profitability, leverage, cash flow — weighted sub-parts,
        each excluded when absent."""
        if fin is None:
            return None
        parts = []

        def growth(v):
            if v is None:
                return None
            if v >= 25:
                return 100.0
            if v >= 15:
                return 75.0
            if v >= 5:
                return 50.0
            if v >= 0:
                return 30.0
            return 10.0

        def quality(v):  # ROE / margin style metric
            if v is None:
                return None
            if v >= 20:
                return 100.0
            if v >= 12:
                return 70.0
            if v >= 5:
                return 45.0
            return 20.0

        g = growth(fin.revenue_growth)
        if g is not None:
            parts.append((g, 0.25))
        g = growth(fin.profit_growth)
        if g is not None:
            parts.append((g, 0.20))

        q = quality(fin.roe if fin.roe is not None else fin.profitability)
        if q is not None:
            parts.append((q, 0.25))

        if fin.debt_to_equity is not None:
            de = fin.debt_to_equity
            parts.append((90.0 if de < 0.5 else 65.0 if de < 1.0 else
                          40.0 if de < 2.0 else 15.0, 0.15))
        elif fin.debt is not None and fin.revenue:
            dr = fin.debt / fin.revenue
            parts.append((65.0 if dr < 0.5 else 40.0 if dr < 1.5 else 15.0, 0.15))

        if fin.cash_flow is not None:
            parts.append((80.0 if fin.cash_flow > 0 else 20.0, 0.15))

        if not parts:
            return None
        w = sum(wt for _, wt in parts)
        return sum(s * wt for s, wt in parts) / w

    def _structure_component(self, ipo: IPO, notes: List[str]) -> Optional[float]:
        """Issue structure: fresh-issue share, OFS burden, retail
        accessibility via minimum investment."""
        score = 55.0
        touched = False
        if ipo.fresh_issue and ipo.issue_size and ipo.issue_size > 0:
            touched = True
            if ipo.fresh_issue > ipo.issue_size:
                notes.append('Fresh-issue figure exceeds reported issue size — '
                             'source figures inconsistent (reserved-portion '
                             'accounting differs between feeds).')
            ratio = min(1.0, ipo.fresh_issue / ipo.issue_size)
            if ratio > 0.6:
                score += 20
            elif ratio > 0.3:
                score += 10
        if ipo.offer_for_sale and ipo.issue_size and ipo.issue_size > 0:
            touched = True
            ratio = ipo.offer_for_sale / ipo.issue_size
            if ratio > 0.5:
                score -= 20
            elif ratio > 0.3:
                score -= 10
        if ipo.min_investment is not None:
            touched = True
            if ipo.min_investment <= 15000:
                score += 10  # accessible single retail lot
        if not touched:
            return None
        return max(0.0, min(100.0, score))

    def _valuation_class(self, ipo: IPO, fin: Optional[IPOFinancials]) -> str:
        pe = None
        if fin and fin.pe_ratio:
            pe = fin.pe_ratio
        elif fin and fin.eps and fin.eps > 0 and ipo.price_band_max:
            pe = ipo.price_band_max / fin.eps
        if pe is None:
            return 'UNKNOWN'
        peer = fin.peer_pe if fin else None
        if peer and peer > 0:
            if pe < peer * 0.85:
                return 'ATTRACTIVE'
            if pe <= peer * 1.15:
                return 'FAIR'
            return 'EXPENSIVE'
        if pe < 20:
            return 'ATTRACTIVE'
        if pe < 40:
            return 'FAIR'
        return 'EXPENSIVE'

    # ------------------------------------------------------------------
    # Aggregation
    # ------------------------------------------------------------------
    def _weighted_score(self, components: Dict[str, Optional[float]],
                        weights: Dict[str, float]):
        present = {k: v for k, v in components.items() if v is not None}
        coverage = sum(weights[k] for k in present)
        if not present or coverage < MIN_COVERAGE:
            return None, coverage
        score = sum(weights[k] * v for k, v in present.items()) / coverage
        return score, coverage

    def _recommend(self, score: Optional[float], coverage: float) -> str:
        if score is None:
            return 'INSUFFICIENT DATA'
        if score >= REC_CONSIDER:
            return 'CONSIDER APPLYING'
        if score >= REC_WAIT:
            return 'WAIT'
        return 'AVOID'

    def _confidence(self, coverage: float, stale: bool) -> str:
        if stale or coverage < CONFIDENCE_MED_COVERAGE:
            return 'LOW'
        return 'HIGH' if coverage >= CONFIDENCE_HIGH_COVERAGE else 'MEDIUM'

    def _risk_level(self, ipo: IPO, fin: Optional[IPOFinancials],
                    components: Dict[str, Optional[float]],
                    coverage: float) -> str:
        if coverage < MIN_COVERAGE:
            return 'UNKNOWN'
        points = 0
        if components['sentiment'] is not None and components['sentiment'] < 35:
            points += 2
        if components['valuation'] is not None and components['valuation'] < 35:
            points += 2
        sub = ipo.subscription
        if sub and sub.qib is not None and sub.qib < 1.0:
            points += 2
        if fin and fin.debt_to_equity is not None and fin.debt_to_equity > 2.0:
            points += 2
        if ipo.issue_size and ipo.issue_size > 5000:
            points += 1  # very large issues can strain listing demand
        if points >= 4:
            return 'HIGH'
        if points >= 2:
            return 'MODERATE'
        return 'LOW'

    # ------------------------------------------------------------------
    # Explanation builders
    # ------------------------------------------------------------------
    def _rationale(self, ipo: IPO, sub, sent: Optional[MarketSentiment],
                   fin: Optional[IPOFinancials],
                   components: Dict[str, Optional[float]]) -> List[str]:
        out = []
        if sub:
            if sub.qib is not None:
                if sub.qib >= 2:
                    out.append(f'QIB subscription {sub.qib:.1f}x — meaningful '
                               f'institutional demand.')
                elif sub.qib < 1:
                    out.append(f'QIB subscription only {sub.qib:.2f}x — weak '
                               f'institutional interest.')
            elif sub.overall is not None:
                out.append(f'Overall subscription {sub.overall:.2f}x '
                           f'(category split unavailable).')
            if sub.retail is not None and sub.retail < 0.5:
                out.append(f'Retail participation low ({sub.retail:.2f}x) — '
                           f'limited broad demand evidence.')
        if sent:
            if sent.nifty_change_pct is not None:
                direction = 'up' if sent.nifty_change_pct >= 0 else 'down'
                out.append(f'NIFTY 50 {direction} {abs(sent.nifty_change_pct):.2f}% '
                           f'— market backdrop {"supportive" if sent.nifty_change_pct >= 0 else "weak"}.')
            if sent.india_vix is not None:
                if sent.india_vix > 22:
                    out.append(f'India VIX {sent.india_vix:.1f} — elevated volatility '
                               f'raises listing-day risk.')
                elif sent.india_vix < 14:
                    out.append(f'India VIX {sent.india_vix:.1f} — calm market '
                               f'conditions favour debuts.')
        if components['valuation'] is None:
            out.append('Valuation cannot be assessed — no EPS/peer P/E data '
                       'in available sources.')
        else:
            out.append(self._valuation_phrase(ipo, fin))
        if components['fundamentals'] is None:
            out.append('No verified financial disclosures — fundamentals '
                       'unassessed; long-term view not evidence-based.')
        if ipo.price_band_max and ipo.min_investment:
            out.append(f'Entry cost {self._fmt_inr(ipo.min_investment)} per lot '
                       f'at ₹{ipo.price_band_max:,.0f} cap price.')
        return out

    def _valuation_phrase(self, ipo: IPO, fin: Optional[IPOFinancials]) -> str:
        pe = None
        if fin and fin.pe_ratio:
            pe = fin.pe_ratio
        elif fin and fin.eps and fin.eps > 0 and ipo.price_band_max:
            pe = ipo.price_band_max / fin.eps
        if pe is None:
            return 'Valuation could not be quantified.'
        peer = fin.peer_pe if fin else None
        if peer and peer > 0:
            rel = 'a discount to' if pe < peer else 'a premium to'
            return (f'Implied P/E {pe:.1f}x vs peer {peer:.1f}x — priced at '
                    f'{rel} peers.')
        return f'Implied P/E {pe:.1f}x with no peer benchmark available.'

    def _risks_and_missing(self, ipo: IPO, fin: Optional[IPOFinancials],
                           sent: Optional[MarketSentiment],
                           components: Dict[str, Optional[float]], sub):
        risks, missing = [], []
        if components['fundamentals'] is None:
            missing.extend(['Revenue and profit history', 'EPS / ROE / ROCE',
                            'Debt and cash-flow disclosures',
                            'Promoter shareholding'])
        if components['valuation'] is None:
            missing.append('Peer P/E benchmark for valuation comparison')
        if components['sentiment'] is None:
            missing.append('Market sentiment (NIFTY / India VIX)')
        missing.append('Grey-market premium (no official source — never assumed)')
        if ipo.use_of_proceeds is None:
            missing.append('Use of IPO proceeds')

        if sub and sub.qib is not None and sub.qib < 1.0:
            risks.append('Institutional demand below 1x — listing could open weak.')
        if sent and sent.india_vix and sent.india_vix > 22:
            risks.append('High market volatility (VIX) increases listing-day drawdown risk.')
        if ipo.issue_size and ipo.issue_size > 15000:
            risks.append(f'Very large issue ({self._fmt_inr(ipo.issue_size)}) — '
                         f'heavy supply can cap listing gains.')
        if fin is None or not any([fin.revenue, fin.profit, fin.eps]):
            risks.append('Applying without verified financials relies on '
                         'demand momentum alone.')
        if ipo.status == IPOStatus.UPCOMING:
            risks.append('Issue not yet open — subscription evidence unavailable.')
        return risks, missing

    @staticmethod
    def _fmt_inr(v: float) -> str:
        if v >= 1e7:
            return f'₹{v/1e7:,.0f} Cr'
        if v >= 1e5:
            return f'₹{v/1e5:,.1f} L'
        return f'₹{v:,.0f}'
