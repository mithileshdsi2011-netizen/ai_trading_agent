"""
Enterprise Portfolio Optimizer.

Portfolio-level capital allocation and diversification before every BUY.
"""
import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from config import config
from persistence import get_store

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class EnterprisePortfolioOptimizer:
    """
    Analyze the current portfolio, compute a capital-allocation envelope,
    guard against high correlation, rank BUY candidates, and suggest rebalances.
    """

    _REGIME_CAP = {
        'BULL': 0.90,
        'SIDEWAYS': 0.60,
        'BEAR': 0.30,
        'DOWNTREND': 0.30,
        'UNKNOWN': 0.70,
    }

    _HIGH_VIX_CAP = 0.40
    _VIX_HIGH = 20.0

    def __init__(
        self,
        market_data=None,
        store=None,
        risk_manager=None,
        benchmark: str = 'NIFTY 50',
    ):
        self.market_data = market_data
        self.store = store or get_store()
        self.risk_manager = risk_manager
        self.benchmark = benchmark

    # ── Portfolio State ───────────────────────────────────────────────────

    def current_positions(self) -> List[Dict[str, Any]]:
        """Return open positions as plain dicts."""
        if self.risk_manager is not None:
            try:
                return [
                    {
                        'symbol': p.symbol,
                        'quantity': p.quantity,
                        'entry_price': p.entry_price,
                        'sector': getattr(p, 'sector', 'Unknown'),
                        'industry': getattr(p, 'industry', 'Unknown'),
                    }
                    for p in self.risk_manager.positions
                    if getattr(p, 'status', None) and p.status.value in ('OPEN', 'PARTIAL')
                ]
            except Exception as e:
                logger.warning(f'risk_manager positions failed: {e}')
        # fallback to persistence
        try:
            pos = self.store.open_positions() if hasattr(self.store, 'open_positions') else []
            return [
                {
                    'symbol': p.get('symbol'),
                    'quantity': p.get('quantity', 0),
                    'entry_price': p.get('entry_price', 0.0),
                    'sector': p.get('sector', 'Unknown'),
                    'industry': p.get('industry', 'Unknown'),
                }
                for p in pos
            ]
        except Exception:
            return []

    @staticmethod
    def _portfolio_value(positions: List[Dict[str, Any]], cash: float) -> float:
        capital_used = sum(p['quantity'] * p['entry_price'] for p in positions)
        return capital_used + cash

    @staticmethod
    def _capital_used(positions: List[Dict[str, Any]]) -> float:
        return sum(p['quantity'] * p['entry_price'] for p in positions)

    @staticmethod
    def _exposure_map(positions: List[Dict[str, Any]], total_value: float, key: str) -> Dict[str, float]:
        used = sum(p['quantity'] * p['entry_price'] for p in positions)
        if used <= 0 or total_value <= 0:
            return {}
        buckets: Dict[str, float] = {}
        for p in positions:
            buckets[p.get(key, 'Unknown')] = buckets.get(p.get(key, 'Unknown'), 0.0) + p['quantity'] * p['entry_price']
        return {k: round(v / total_value * 100, 2) for k, v in buckets.items()}

    # ── Returns / Beta / Volatility ───────────────────────────────────────

    def _price_histories(self, symbols: List[str], period: str = '1mo', interval: str = '1d') -> Dict[str, pd.Series]:
        """Return close-price series for each symbol, using market_data if available."""
        hist: Dict[str, pd.Series] = {}
        if self.market_data is None:
            return hist
        for sym in symbols:
            try:
                df = self.market_data.get_stock_data(sym, period=period, interval=interval)
                if df is not None and not df.empty and 'Close' in df.columns:
                    hist[sym] = df['Close'].astype(float)
            except Exception as e:
                logger.debug(f'price history failed for {sym}: {e}')
        return hist

    @staticmethod
    def _returns(hist: Dict[str, pd.Series]) -> pd.DataFrame:
        rets = {}
        for sym, close in hist.items():
            r = close.pct_change().dropna()
            if not r.empty:
                rets[sym] = r
        if not rets:
            return pd.DataFrame()
        df = pd.DataFrame(rets)
        return df.dropna(how='all')

    def portfolio_beta(
        self,
        positions: List[Dict[str, Any]],
        hist: Optional[Dict[str, pd.Series]] = None,
    ) -> float:
        """Weighted portfolio beta to the configured benchmark."""
        symbols = {p['symbol'] for p in positions}
        if not symbols:
            return 1.0
        if hist is None:
            hist = self._price_histories(list(symbols) + [self.benchmark])
        if not hist or self.benchmark not in hist:
            return 1.0
        b_ret = hist[self.benchmark].pct_change().dropna()
        weights = []
        betas = []
        for p in positions:
            sym = p['symbol']
            if sym not in hist:
                continue
            s_ret = hist[sym].pct_change().dropna()
            aligned = pd.concat([s_ret, b_ret], axis=1).dropna()
            if len(aligned) < 5:
                continue
            cov = np.cov(aligned.iloc[:, 0], aligned.iloc[:, 1])[0, 1]
            var = np.var(aligned.iloc[:, 1])
            if var <= 0:
                continue
            w = p['quantity'] * p['entry_price']
            weights.append(w)
            betas.append(cov / var)
        if not weights:
            return 1.0
        total = sum(weights)
        return round(float(np.average(betas, weights=weights)), 2)

    def portfolio_volatility(
        self,
        positions: List[Dict[str, Any]],
        hist: Optional[Dict[str, pd.Series]] = None,
    ) -> float:
        """Annualised portfolio volatility from position weights and covariance."""
        symbols = [p['symbol'] for p in positions]
        if not symbols:
            return 0.0
        if hist is None:
            hist = self._price_histories(symbols)
        if not hist:
            return 0.0
        rets = self._returns(hist)
        if rets.empty or len(rets.columns) < 1:
            return 0.0
        total_value = sum(p['quantity'] * p['entry_price'] for p in positions)
        if total_value <= 0:
            return 0.0
        weights = np.array([p['quantity'] * p['entry_price'] / total_value for p in positions if p['symbol'] in rets.columns])
        cols = [p['symbol'] for p in positions if p['symbol'] in rets.columns]
        cov = rets[cols].cov() * 252  # annualised
        try:
            vol = float(np.sqrt(weights.T @ cov.values @ weights) * 100)
            return round(vol, 2)
        except Exception:
            return 0.0

    # ── Diversification & Allocation ─────────────────────────────────────

    def diversification_score(self, positions: List[Dict[str, Any]]) -> float:
        """
        0-100 score: penalise sector concentration and high average correlation.
        """
        if not positions:
            return 100.0
        # Sector Herfindahl-Hirschman Index
        total_value = sum(p['quantity'] * p['entry_price'] for p in positions)
        sector_exposure = self._exposure_map(positions, total_value, 'sector')
        if not sector_exposure:
            return 100.0
        hhi = sum((v / 100.0) ** 2 for v in sector_exposure.values())
        # industry concentration
        industry_exposure = self._exposure_map(positions, total_value, 'industry')
        ind_hhi = sum((v / 100.0) ** 2 for v in industry_exposure.values()) if industry_exposure else hhi
        # high correlation penalty stub (from rolling matrix if available)
        corr_penalty = 0.0
        cm = self.get_latest_correlation_matrix()
        if cm and 'matrix' in cm:
            matrix = cm['matrix']
            high_count = 0
            total = 0
            for sym1, row in matrix.items():
                for sym2, val in row.items():
                    if sym1 != sym2:
                        total += 1
                        if abs(val) > 0.80:
                            high_count += 1
            if total > 0:
                corr_penalty = (high_count / total) * 100
        raw = 100 - (hhi * 100) - (ind_hhi * 25) - (corr_penalty * 0.5)
        return round(max(0.0, min(100.0, raw)), 2)

    def dynamic_capital_limit(
        self,
        regime: str,
        vix: Optional[float] = None,
    ) -> float:
        cap = self._REGIME_CAP.get(regime.upper(), self._REGIME_CAP['UNKNOWN'])
        if vix is not None and vix >= self._VIX_HIGH:
            cap = min(cap, self._HIGH_VIX_CAP)
        return cap

    # ── Correlation Engine ────────────────────────────────────────────────

    def correlation_matrix(
        self,
        positions: List[Dict[str, Any]],
        hist: Optional[Dict[str, pd.Series]] = None,
    ) -> pd.DataFrame:
        """Compute pairwise return correlation matrix for positions."""
        symbols = [p['symbol'] for p in positions]
        if hist is None:
            hist = self._price_histories(symbols)
        if not hist:
            return pd.DataFrame()
        rets = self._returns(hist)
        if rets.empty:
            return pd.DataFrame()
        return rets.corr()

    def can_open_position(
        self,
        candidate: str,
        candidate_rets: Optional[pd.Series] = None,
        positions: Optional[List[Dict[str, Any]]] = None,
        hist: Optional[Dict[str, pd.Series]] = None,
        threshold: float = 0.80,
    ) -> Tuple[bool, str, Optional[float]]:
        """Return (allowed, reason, max_correlation)."""
        if positions is None:
            positions = self.current_positions()
        if not positions:
            return True, 'no existing positions', None
        if hist is None:
            hist = self._price_histories([p['symbol'] for p in positions])
        if candidate not in hist and candidate_rets is None:
            return True, 'no candidate history', None
        c_ret = candidate_rets
        if c_ret is None:
            c_ret = hist[candidate].pct_change().dropna() if candidate in hist else pd.Series(dtype=float)
        if c_ret.empty:
            return True, 'no candidate history', None
        max_corr = -2.0
        for p in positions:
            sym = p['symbol']
            if sym not in hist:
                continue
            p_ret = hist[sym].pct_change().dropna()
            aligned = pd.concat([c_ret, p_ret], axis=1).dropna()
            if len(aligned) < 5:
                continue
            corr = float(np.corrcoef(aligned.iloc[:, 0], aligned.iloc[:, 1])[0, 1])
            if not np.isnan(corr) and abs(corr) > max_corr:
                max_corr = abs(corr)
            if not np.isnan(corr) and abs(corr) >= threshold:
                return False, f'correlation {abs(corr):.2f} with {sym}', abs(corr)
        return True, 'ok', round(max_corr, 2) if max_corr >= -1 else None

    # ── Smart Position Ranking ────────────────────────────────────────────

    def rank_candidates(
        self,
        candidates: List[Dict[str, Any]],
        max_slots: int,
        positions: Optional[List[Dict[str, Any]]] = None,
    ) -> List[Dict[str, Any]]:
        """Rank BUY candidates and return top `max_slots`."""
        if positions is None:
            positions = self.current_positions()
        pos_symbols = {p['symbol'] for p in positions}

        def _score(c: Dict[str, Any]) -> float:
            ai = float(c.get('ai_score', c.get('overall_score', 0)))
            mi = float(c.get('market_intelligence_score', 0))
            conf = float(c.get('confidence', 0))
            rr = float(c.get('risk_reward_ratio', 0))
            liq = float(c.get('liquidity_score', c.get('liquidity', 70)))
            # prefer lower correlation with open positions (1 if uncorrelated)
            corr = float(c.get('max_correlation', 0))
            corr_penalty = max(0, corr) * 20
            return (
                ai * 0.30
                + mi * 0.25
                + conf * 0.20
                + rr * 10
                - corr_penalty
                + liq * 0.05
            )

        scored = []
        for c in candidates:
            if c.get('symbol') in pos_symbols:
                continue
            sc = _score(c)
            scored.append((sc, c))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [c for _, c in scored[:max_slots]]

    # ── Rebalancing ───────────────────────────────────────────────────────

    def rebalance_suggestions(
        self,
        positions: List[Dict[str, Any]],
        cash: float,
        target_sector_pct: float = 0.25,
    ) -> List[Dict[str, Any]]:
        """Detect over-weight sectors and suggest trimming. No auto-sell."""
        total_value = self._portfolio_value(positions, cash)
        if total_value <= 0:
            return []
        sector_exposure = self._exposure_map(positions, total_value, 'sector')
        suggestions: List[Dict[str, Any]] = []
        for sector, pct in sector_exposure.items():
            if pct > target_sector_pct * 100:
                # identify largest position in this sector
                sector_pos = [p for p in positions if p.get('sector', 'Unknown') == sector]
                if not sector_pos:
                    continue
                largest = max(sector_pos, key=lambda p: p['quantity'] * p['entry_price'])
                excess_pct = pct - (target_sector_pct * 100)
                trim_value = total_value * excess_pct / 100
                qty_to_trim = int(trim_value / largest['entry_price']) if largest['entry_price'] > 0 else 0
                suggestions.append({
                    'symbol': largest['symbol'],
                    'action': 'TRIM',
                    'quantity': max(1, qty_to_trim),
                    'sector': sector,
                    'reason': f'Sector {sector} over-weight {pct:.1f}% > target {target_sector_pct*100:.0f}%',
                })
        return suggestions

    # ── Master Analysis ───────────────────────────────────────────────────

    def analyze(
        self,
        positions: Optional[List[Dict[str, Any]]] = None,
        cash: Optional[float] = None,
        regime: str = 'UNKNOWN',
        vix: Optional[float] = None,
        hist: Optional[Dict[str, pd.Series]] = None,
    ) -> Dict[str, Any]:
        """Run full portfolio analysis."""
        if positions is None:
            positions = self.current_positions()
        if cash is None:
            cash = float(config.TRADING_AMOUNT)

        capital_used = self._capital_used(positions)
        total_value = self._portfolio_value(positions, cash)
        cap_pct = self.dynamic_capital_limit(regime, vix)
        max_deploy = total_value * cap_pct
        cash_remaining = max(0.0, max_deploy - capital_used)

        sector_exposure = self._exposure_map(positions, total_value, 'sector')
        industry_exposure = self._exposure_map(positions, total_value, 'industry')

        # compute beta/vol using provided or fetched history
        use_hist = hist if hist is not None else self._price_histories([p['symbol'] for p in positions])
        beta = self.portfolio_beta(positions, use_hist)
        vol = self.portfolio_volatility(positions, use_hist)
        div = self.diversification_score(positions)

        result = {
            'timestamp': datetime.now().isoformat(),
            'cash': round(cash, 2),
            'capital_used': round(capital_used, 2),
            'total_value': round(total_value, 2),
            'portfolio_beta': beta,
            'portfolio_volatility': vol,
            'diversification_score': div,
            'sector_exposure': sector_exposure,
            'industry_exposure': industry_exposure,
            'regime': regime,
            'vix': vix,
            'capital_limit_pct': round(cap_pct, 2),
            'max_deployable': round(max_deploy, 2),
            'cash_remaining': round(cash_remaining, 2),
            'open_positions': len(positions),
        }
        return result

    # ── Persistence ───────────────────────────────────────────────────────

    def persist_analysis(self, analysis: Dict[str, Any]) -> None:
        """Save the latest portfolio analysis."""
        try:
            if hasattr(self.store, 'save_portfolio_optimizer'):
                self.store.save_portfolio_optimizer(analysis)
            if hasattr(self.store, 'save_portfolio_metrics'):
                self.store.save_portfolio_metrics({
                    'timestamp': analysis['timestamp'],
                    'beta': analysis['portfolio_beta'],
                    'volatility': analysis['portfolio_volatility'],
                    'diversification': analysis['diversification_score'],
                    'capital_used': analysis['capital_used'],
                    'cash_remaining': analysis['cash_remaining'],
                })
        except Exception as e:
            logger.warning(f'Failed to persist portfolio analysis: {e}')

    def persist_correlation(self, positions: List[Dict[str, Any]], hist: Optional[Dict[str, pd.Series]] = None) -> None:
        cm = self.correlation_matrix(positions, hist)
        if cm is None or cm.empty:
            return
        try:
            # convert to nested dict of roundings
            matrix = cm.round(2).to_dict()
            if hasattr(self.store, 'save_correlation_matrix'):
                self.store.save_correlation_matrix({
                    'timestamp': datetime.now().isoformat(),
                    'symbols': list(cm.columns),
                    'matrix': matrix,
                })
        except Exception as e:
            logger.warning(f'Failed to persist correlation matrix: {e}')

    def persist_allocation(self, candidate: str, allocated: float, reason: str) -> None:
        try:
            if hasattr(self.store, 'save_allocation_history'):
                self.store.save_allocation_history({
                    'timestamp': datetime.now().isoformat(),
                    'symbol': candidate,
                    'allocated_amount': allocated,
                    'reason': reason,
                })
        except Exception as e:
            logger.warning(f'Failed to persist allocation history: {e}')

    # ── Retrieval ─────────────────────────────────────────────────────────

    def get_latest(self, method: str, default: Optional[Any] = None):
        try:
            m = getattr(self.store, method, None)
            return m() if m else default
        except Exception:
            return default

    def get_latest_portfolio(self) -> Optional[Dict[str, Any]]:
        return self.get_latest('get_latest_portfolio_optimizer')

    def get_latest_correlation_matrix(self) -> Optional[Dict[str, Any]]:
        return self.get_latest('get_latest_correlation_matrix')

    def get_latest_metrics(self) -> Optional[Dict[str, Any]]:
        return self.get_latest('get_latest_portfolio_metrics')

    def get_latest_allocations(self, limit: int = 50) -> List[Dict[str, Any]]:
        try:
            m = getattr(self.store, 'get_allocation_history', None)
            return m(limit) if m else []
        except Exception:
            return []
