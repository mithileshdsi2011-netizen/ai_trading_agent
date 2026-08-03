"""
Enterprise Risk Engine
Master pre-BUY portfolio-level risk gate.
Checks: sector exposure, correlation, volatility/beta, Kelly sizing,
market-regime allocation, drawdown governor, and VIX/ATR size factors.
"""
import logging
import os
from datetime import date
from typing import Dict, List, Optional, Tuple
import numpy as np
import pandas as pd

from config import config
from market_data import MarketDataFetcher
from vix_risk_engine import IndiaVIXRiskEngine
from economic_events import EconomicEventRiskEngine

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Defensive sectors for bear markets
_DEFENSIVE_SECTORS = {
    "Pharma", "Healthcare", "FMCG", "Consumer Defensive", "Utilities", "IT"
}

# Regime-based position limits (override MAX_POSITIONS)
_REGIME_LIMITS = {
    "BULL": int(os.getenv("REGIME_MAX_POSITIONS_BULL", "8")),
    "UPTREND": int(os.getenv("REGIME_MAX_POSITIONS_TREND", "5")),
    "TREND": int(os.getenv("REGIME_MAX_POSITIONS_TREND", "5")),
    "SIDEWAYS": int(os.getenv("REGIME_MAX_POSITIONS_SIDEWAYS", "2")),
    "BEAR": int(os.getenv("REGIME_MAX_POSITIONS_BEAR", "0")),
    "DOWNTREND": int(os.getenv("REGIME_MAX_POSITIONS_BEAR", "0")),
    "VOLATILE": 0,
    "UNKNOWN": int(os.getenv("REGIME_MAX_POSITIONS_SIDEWAYS", "2")),
}

# Risk thresholds
_MAX_SECTOR_EXPOSURE_PCT = float(os.getenv("MAX_SECTOR_EXPOSURE_PCT", "0.30"))
_CORRELATION_THRESHOLD = float(os.getenv("CORRELATION_THRESHOLD", "0.85"))
_VIX_HIGH_THRESHOLD = float(os.getenv("VIX_HIGH_THRESHOLD", "20.0"))
_BETA_HIGH_THRESHOLD = float(os.getenv("BETA_HIGH_THRESHOLD", "1.5"))
_ATR_HIGH_PCT = float(os.getenv("ATR_HIGH_PCT", "0.05"))
_GAP_RISK_PCT = float(os.getenv("GAP_RISK_PCT", "0.04"))

# Kelly confidence tiers (size factor multipliers)
_KELLY_TIERS = [
    (0.95, 1.0),
    (0.70, 0.5),
    (0.65, 0.25),
    (0.0, 0.0),
]


class EnterpriseRiskEngine:
    """Portfolio-level master risk gate before any BUY."""

    def __init__(self, risk_manager=None):
        self._risk = risk_manager
        self._market_data: Optional[MarketDataFetcher] = None
        self._vix_engine = IndiaVIXRiskEngine(market_data=self.market_data)
        self._event_engine = EconomicEventRiskEngine()

    @property
    def market_data(self) -> MarketDataFetcher:
        if self._market_data is None:
            self._market_data = MarketDataFetcher()
        return self._market_data

    # ── portfolio state helpers ─────────────────────────────────────────

    def _open_positions(self) -> List[dict]:
        if self._risk is None:
            return []
        return [
            {
                "symbol": p.symbol,
                "quantity": p.quantity,
                "entry_price": p.entry_price,
                "sector": p.sector,
            }
            for p in self._risk.positions
            if p.status.value in ("OPEN", "PARTIAL")
        ]

    def _portfolio_value(self) -> float:
        """Cash + current value of open positions (uses last known entry price if no LTP)."""
        open_positions = self._open_positions()
        holdings_value = sum(p["quantity"] * p["entry_price"] for p in open_positions)
        return float(config.TRADING_AMOUNT) + holdings_value

    def _exposure_by_sector(self, candidate_value: float = 0,
                            candidate_sector: str = "Unknown") -> Dict[str, float]:
        """Return sector exposure percentages, optionally including candidate."""
        total = self._portfolio_value()
        if total == 0:
            return {}
        open_positions = self._open_positions()
        by_sector: Dict[str, float] = {}
        for p in open_positions:
            sec = p.get("sector") or "Unknown"
            by_sector[sec] = by_sector.get(sec, 0.0) + p["quantity"] * p["entry_price"]
        if candidate_value > 0:
            by_sector[candidate_sector] = by_sector.get(candidate_sector, 0.0) + candidate_value
        return {sec: round(val / total, 4) for sec, val in by_sector.items()}

    # ── risk checks ─────────────────────────────────────────────────────

    def _regime_allow(self, signal: Dict) -> Tuple[bool, str]:
        regime = (signal.get("market_regime") or "UNKNOWN").upper()
        limit = _REGIME_LIMITS.get(regime, _REGIME_LIMITS["UNKNOWN"])
        if limit == 0:
            return False, f"No new buys in {regime} regime"
        open_count = len(self._open_positions())
        if open_count >= limit:
            return False, f"Regime {regime} limit {limit} reached ({open_count})"
        # Bear market: only defensive sectors
        if regime in ("BEAR", "DOWNTREND"):
            sector = (signal.get("_research") or {}).get("sector", "Unknown")
            if sector not in _DEFENSIVE_SECTORS:
                return False, f"Bear market — {sector} is not a defensive sector"
        return True, "ok"

    def _drawdown_governor(self) -> Tuple[bool, str]:
        if self._risk is None:
            return True, "ok"
        # Existing circuit: daily loss and consecutive losses
        if self._risk.should_stop_trading():
            return False, "Daily drawdown governor halt"
        return True, "ok"

    def _sector_exposure_allow(self, signal: Dict) -> Tuple[bool, str]:
        price = signal.get("current_price", 0.0)
        raw_qty = signal.get("position_size", 0)
        candidate_value = price * raw_qty
        sector = (signal.get("_research") or {}).get("sector", "Unknown")
        exposure = self._exposure_by_sector(candidate_value, sector)
        current_pct = exposure.get(sector, 0.0)
        if current_pct > _MAX_SECTOR_EXPOSURE_PCT:
            return False, f"Sector {sector} exposure {current_pct:.1%} > {_MAX_SECTOR_EXPOSURE_PCT:.1%}"
        return True, f"{sector} exposure {current_pct:.1%}"

    def _correlation_guard(self, signal: Dict) -> Tuple[bool, str]:
        symbol = signal["symbol"]
        open_positions = self._open_positions()
        if not open_positions:
            return True, "ok"
        try:
            candidate_hist = self.market_data.get_stock_data(symbol, period="1mo", interval="1d")
            if candidate_hist.empty or len(candidate_hist) < 10:
                return True, "insufficient candidate history"
            cand_rets = candidate_hist["Close"].pct_change().dropna()
            for pos in open_positions:
                pos_hist = self.market_data.get_stock_data(pos["symbol"], period="1mo", interval="1d")
                if pos_hist.empty or len(pos_hist) < 10:
                    continue
                pos_rets = pos_hist["Close"].pct_change().dropna()
                aligned = pd.concat([cand_rets, pos_rets], axis=1).dropna()
                if len(aligned) < 10:
                    continue
                corr = float(np.corrcoef(aligned.iloc[:, 0], aligned.iloc[:, 1])[0, 1])
                if not np.isnan(corr) and abs(corr) >= _CORRELATION_THRESHOLD:
                    return False, f"Correlation with {pos['symbol']} = {corr:.2f} (limit {_CORRELATION_THRESHOLD})"
        except Exception as e:
            logger.warning(f"Correlation guard failed for {symbol}: {e}")
        return True, "ok"

    def _volatility_metrics(self, signal: Dict) -> Tuple[float, Dict]:
        """Return size factor (1.0 = normal) and a metrics dict."""
        factor = 1.0
        metrics = {"vix": None, "atr_pct": 0.0, "beta": None, "gap_risk": False}

        # ATR-based volatility
        atr = signal.get("atr", 0.0)
        price = signal.get("current_price", 0.0)
        if atr > 0 and price > 0:
            atr_pct = atr / price
            metrics["atr_pct"] = round(atr_pct, 4)
            if atr_pct > _ATR_HIGH_PCT:
                factor *= 0.5
                logger.info(f"High ATR {atr_pct:.2%} — halving size")

        # VIX high
        try:
            vix_data = self._vix_engine.compute()
            vix = vix_data.get("vix")
            risk_factor = vix_data.get("risk_factor", 1.0)
            volatility_score = vix_data.get("volatility_score", 0.0)
            metrics["vix"] = round(vix, 2) if vix is not None else None
            metrics["volatility_score"] = volatility_score
            metrics["risk_level"] = vix_data.get("risk_level", "UNKNOWN")
            factor *= risk_factor
            if risk_factor < 1.0:
                logger.info(f"VIX {vix:.1f} ({vix_data.get('risk_level')}) — risk factor {risk_factor}")
        except Exception:
            pass

        # Beta to Nifty
        try:
            symbol = signal["symbol"]
            sym_hist = self.market_data.get_stock_data(symbol, period="1mo", interval="1d")
            nifty_hist = self.market_data.get_stock_data("NIFTY 50", period="1mo", interval="1d")
            if not sym_hist.empty and not nifty_hist.empty and len(sym_hist) > 10 and len(nifty_hist) > 10:
                s_rets = sym_hist["Close"].pct_change().dropna()
                n_rets = nifty_hist["Close"].pct_change().dropna()
                aligned = pd.concat([s_rets, n_rets], axis=1).dropna()
                if len(aligned) > 10:
                    cov = np.cov(aligned.iloc[:, 0], aligned.iloc[:, 1])[0, 1]
                    var = np.var(aligned.iloc[:, 1])
                    if var > 0:
                        beta = cov / var
                        metrics["beta"] = round(float(beta), 2)
                        if abs(beta) > _BETA_HIGH_THRESHOLD:
                            factor *= 0.7
                            logger.info(f"High beta {beta:.2f} — reducing size")
        except Exception:
            pass

        # Gap risk: previous close vs current price gap > threshold
        try:
            sym_hist = self.market_data.get_stock_data(symbol, period="5d", interval="1d")
            if not sym_hist.empty and len(sym_hist) >= 2:
                prev_close = float(sym_hist["Close"].iloc[-2])
                gap = abs(price - prev_close) / prev_close
                if gap > _GAP_RISK_PCT:
                    metrics["gap_risk"] = True
                    factor *= 0.6
                    logger.info(f"Gap risk {gap:.2%} — reducing size")
        except Exception:
            pass

        return factor, metrics

    def _kelly_size_factor(self, confidence: float) -> float:
        for threshold, factor in _KELLY_TIERS:
            if confidence >= threshold:
                return factor
        return 0.0

    def _dynamic_position_size(self, signal: Dict, vol_factor: float) -> int:
        """ATR-based risk sizing with Kelly and volatility adjustments."""
        price = signal.get("current_price", 0.0)
        atr = signal.get("atr", 0.0)
        confidence = float(signal.get("confidence", 0.0))

        # Kelly scaling
        kelly = self._kelly_size_factor(confidence)

        # Regime size factor (e.g., sideways)
        regime = (signal.get("market_regime") or "UNKNOWN").upper()
        regime_factor = 1.0
        if regime == "SIDEWAYS":
            regime_factor = float(getattr(config, "SIDEWAYS_SIZE_FACTOR", 0.75))
        elif regime in ("BEAR", "DOWNTREND"):
            regime_factor = 0.5

        # Effective risk amount
        risk_amount = float(config.TRADING_AMOUNT) * float(config.RISK_PER_TRADE) * kelly * vol_factor * regime_factor

        if atr > 0 and price > 0:
            qty = int(risk_amount / atr)
        else:
            qty = int(risk_amount / (price * 0.02)) if price > 0 else 0  # 2% default SL

        # Capital cap per slot
        per_slot = float(config.TRADING_AMOUNT) / max(1, int(config.MAX_POSITIONS))
        max_qty_by_capital = int(per_slot / price) if price > 0 else 0

        qty = max(1, min(qty, max_qty_by_capital)) if max_qty_by_capital > 0 else max(1, qty)
        return qty

    def _event_risk_gate(self, signal: Dict) -> Tuple[bool, str, float]:
        """Economic event risk: block new BUYs within 6h, halve size within 24h."""
        try:
            status = self._event_engine.risk_status()
            if status.get("no_new_buy"):
                return False, status.get("reason", "High-impact event within 6h"), 0.0
            if status.get("reduce_size"):
                return True, status.get("reason", "High-impact event within 24h"), 0.5
        except Exception:
            pass
        return True, "ok", 1.0

    # ── master pre-BUY gate ─────────────────────────────────────────────

    def pre_buy_risk_check(self, signal: Dict) -> bool:
        """
        Master decision engine before every BUY.
        Mutates signal with _enterprise_risk metadata (allow, reason, quantity, metrics).
        Returns True if the BUY may proceed.
        """
        symbol = signal.get("symbol", "")
        reason = "ok"
        allow = True

        # 1. Economic event risk (6h no-buy / 24h size reduction)
        if allow:
            ok, msg, event_factor = self._event_risk_gate(signal)
            if not ok:
                allow, reason = False, msg

        # 2. Regime allocation
        if allow:
            ok, msg = self._regime_allow(signal)
            if not ok:
                allow, reason = False, msg

        # 3. Drawdown governor
        if allow:
            ok, msg = self._drawdown_governor()
            if not ok:
                allow, reason = False, msg

        # 3. Sector exposure
        if allow:
            ok, msg = self._sector_exposure_allow(signal)
            if not ok:
                allow, reason = False, msg

        # 4. Correlation
        if allow:
            ok, msg = self._correlation_guard(signal)
            if not ok:
                allow, reason = False, msg

        # 5. Volatility + Kelly -> position size
        vol_factor, vol_metrics = (1.0, {})
        quantity = signal.get("position_size", 0)
        kelly_factor = 1.0
        if allow:
            vol_factor, vol_metrics = self._volatility_metrics(signal)
            # Apply economic event size factor (24h rule)
            vol_factor *= event_factor
            kelly_factor = self._kelly_size_factor(float(signal.get("confidence", 0.0)))
            quantity = self._dynamic_position_size(signal, vol_factor)
            signal["position_size"] = quantity

        signal["_enterprise_risk"] = {
            "allow": allow,
            "reason": reason,
            "quantity": quantity,
            "volatility_factor": round(vol_factor, 2),
            "kelly_factor": kelly_factor,
            "regime": signal.get("market_regime", "UNKNOWN"),
            "regime_limit": _REGIME_LIMITS.get((signal.get("market_regime") or "UNKNOWN").upper(), 2),
            "exposure": self._exposure_by_sector(),
            "volatility": vol_metrics,
        }

        if not allow:
            logger.warning(f"Enterprise risk REJECTED {symbol}: {reason}")

        return allow

    def portfolio_heat(self) -> Dict:
        """Expose current portfolio heat for dashboard."""
        return {
            "total_value": round(self._portfolio_value(), 2),
            "exposure_by_sector": self._exposure_by_sector(),
            "open_positions": len(self._open_positions()),
            "regime_limits": _REGIME_LIMITS,
            "max_sector_exposure_pct": _MAX_SECTOR_EXPOSURE_PCT,
        }
