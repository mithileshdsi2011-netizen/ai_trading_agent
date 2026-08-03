"""
Enterprise AI Learning Engine.

Turns every completed trade into a training sample and continuously learns
which features predict winners.  Updates the Enterprise AI Decision Engine's
sub-score weights without blocking live trading.
"""
import json
import logging
import math
import threading
from copy import deepcopy
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from config import config
from persistence import get_store

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

_DEFAULT_CATEGORY_MAP = {
    'technical': ['technical_score', 'rsi', 'macd', 'ema', 'vwap', 'support', 'resistance'],
    'market': ['market_intelligence_score', 'breadth_score', 'vix', 'fii_dii_net', 'options_pcr', 'global_sentiment'],
    'sector': ['sector_score'],
    'liquidity': ['confidence'],
    'relative_strength': ['confidence'],
    'volume_profile': ['confidence'],
    'historical_success': ['holding_period', 'mae_pct', 'mfe_pct'],
}


class EnterpriseLearningEngine:
    """
    Online learning from closed trades.
    """

    def __init__(
        self,
        store=None,
        decision_engine=None,
        max_history: int = 5000,
        retrain_every: int = 50,
        category_map: Optional[Dict[str, List[str]]] = None,
        async_retrain: bool = True,
    ):
        self.store = store or get_store()
        self.decision_engine = decision_engine
        self.max_history = max(max_history, 1)
        self.retrain_every = max(retrain_every, 1)
        self.category_map = category_map or deepcopy(_DEFAULT_CATEGORY_MAP)
        self.async_retrain = async_retrain
        self._lock = threading.Lock()
        self._last_metrics: Optional[Dict[str, Any]] = None

    # ── Feature Extraction ────────────────────────────────────────────────

    @staticmethod
    def _to_float(v):
        try:
            return float(v) if v is not None else 0.0
        except (TypeError, ValueError):
            return 0.0

    def _extract_features(self, trade: Dict[str, Any]) -> Dict[str, Any]:
        sc = trade.get('score_components') or {}
        rc = trade.get('_enterprise_risk') or {}
        vol = rc.get('volatility') or {}

        technical_score = self._to_float(sc.get('technical') or sc.get('technical_score'))
        market_intelligence_score = self._to_float(sc.get('market_intelligence_score'))
        sector_score = self._to_float(sc.get('sector') or sc.get('sector_score'))
        breadth_score = self._to_float(sc.get('breadth') or sc.get('breadth_score'))

        vix = self._to_float((sc.get('vix') or {}).get('vix') if isinstance(sc.get('vix'), dict) else sc.get('vix'))
        fii_dii = sc.get('fii_dii') or {}
        fii_dii_net = self._to_float(fii_dii.get('net_flow') or fii_dii.get('fii_net'))
        options = sc.get('options_intelligence') or {}
        options_pcr = self._to_float(options.get('pcr'))
        global_markets = sc.get('global_markets') or {}
        global_sentiment = self._to_float(global_markets.get('sentiment_score'))

        confidence = self._to_float(sc.get('final_confidence') or trade.get('confidence'))

        technical = trade.get('technical_analysis') or {}
        rsi = self._to_float(technical.get('rsi'))
        macd = self._to_float(technical.get('macd'))
        ema = self._to_float(technical.get('above_ema') or technical.get('ema'))
        vwap = self._to_float(technical.get('above_vwap') or technical.get('vwap'))
        support = self._to_float(technical.get('support'))
        resistance = self._to_float(technical.get('resistance'))

        pnl = self._to_float(trade.get('net_pnl'))
        win = 1 if pnl > 0 else 0

        entry_dt = self._parse_dt(trade.get('entry_date'))
        exit_dt = self._parse_dt(trade.get('exit_date') or trade.get('timestamp'))
        holding = (exit_dt - entry_dt).days if entry_dt and exit_dt else 0

        mae_pct = self._to_float(trade.get('max_adverse_excursion_pct'))
        mfe_pct = self._to_float(trade.get('max_favorable_excursion_pct'))
        exit_reason = trade.get('exit_reason') or trade.get('reason') or 'unknown'

        return {
            'trade_id': trade.get('id') or trade.get('trade_id') or '0',
            'timestamp': datetime.now().isoformat(),
            'technical_score': technical_score,
            'market_intelligence_score': market_intelligence_score,
            'sector_score': sector_score,
            'breadth_score': breadth_score,
            'vix': vix,
            'fii_dii_net': fii_dii_net,
            'options_pcr': options_pcr,
            'global_sentiment': global_sentiment,
            'confidence': confidence,
            'rsi': rsi,
            'macd': macd,
            'ema': ema,
            'vwap': vwap,
            'support': support,
            'resistance': resistance,
            'risk_atr_pct': self._to_float(vol.get('atr_pct')),
            'risk_beta': self._to_float(vol.get('beta')),
            'holding_period': max(0, holding),
            'exit_reason': str(exit_reason)[:80],
            'final_pnl': pnl,
            'mae_pct': mae_pct,
            'mfe_pct': mfe_pct,
            'win': win,
            'entry_features_json': json.dumps(sc),
            'risk_metrics_json': json.dumps(vol),
        }

    @staticmethod
    def _parse_dt(v) -> Optional[datetime]:
        if not v:
            return None
        if isinstance(v, datetime):
            return v
        try:
            return pd.to_datetime(v).to_pydatetime()
        except Exception:
            return None

    # ── Public API ────────────────────────────────────────────────────────

    def on_trade_closed(self, trade: Dict[str, Any]) -> None:
        """Ingest a closed trade and trigger async retrain if threshold hit."""
        try:
            sample = self._extract_features(trade)
            if self.store and hasattr(self.store, 'save_ai_training_data'):
                self.store.save_ai_training_data(sample)
                if hasattr(self.store, 'prune_ai_training_data'):
                    self.store.prune_ai_training_data(self.max_history)
            count = self._count_samples()
            if count % self.retrain_every == 0:
                if self.async_retrain:
                    threading.Thread(target=self.retrain, daemon=True).start()
                else:
                    self.retrain()
        except Exception as e:
            logger.warning(f"Learning engine on_trade_closed failed: {e}")

    def _count_samples(self) -> int:
        if not self.store or not hasattr(self.store, 'count_ai_training_data'):
            return 0
        try:
            return self.store.count_ai_training_data()
        except Exception:
            return 0

    def retrain(self) -> Dict[str, Any]:
        """Load trade history, recompute feature importance and weights."""
        samples = self._load_samples()
        if not samples:
            return self._empty_result()

        df = pd.DataFrame(samples)
        features = self._numeric_feature_cols(df)
        if not features:
            return self._empty_result()

        feature_df = df[features].astype(float)
        target_pnl = df['final_pnl'].astype(float)
        target_win = df['win'].astype(int)

        # Feature importance: absolute correlation with win and pnl
        importance = {}
        for f in features:
            if feature_df[f].nunique() <= 1 or feature_df[f].std() == 0:
                importance[f] = 0.0
                continue
            c1 = abs(float(feature_df[f].corr(target_win)))
            c2 = abs(float(feature_df[f].corr(target_pnl)))
            raw = np.nanmean([c1, c2])
            importance[f] = round(float(raw) if not np.isnan(raw) else 0.0, 4)

        # Simple linear model accuracy for win direction
        X = feature_df.fillna(0).values
        y = target_win.values
        try:
            Xn = (X - X.mean(axis=0)) / (X.std(axis=0) + 1e-9)
            coefs = np.linalg.lstsq(Xn, y, rcond=None)[0]
            pred_proba = 1 / (1 + np.exp(-Xn @ coefs))  # sigmoid
            pred = (pred_proba > 0.5).astype(int)
            accuracy = float(np.mean(pred == y))
        except Exception:
            accuracy = float(target_win.mean())

        win_rate = float(target_win.mean() * 100)
        category_weights = self._derive_weights(importance)

        # Save
        timestamp = datetime.now().isoformat()
        self._save_feature_importance(importance, timestamp)
        self._save_model_weights(category_weights, timestamp)

        result = {
            'timestamp': timestamp,
            'trades_used': len(samples),
            'accuracy': round(accuracy, 4),
            'win_rate': round(win_rate, 2),
            'feature_importance': importance,
            'category_weights': category_weights,
            'top_indicators': self._top_bottom(importance, top=True),
            'worst_indicators': self._top_bottom(importance, top=False),
        }
        self._last_metrics = result
        self._save_learning_metrics(result)

        if self.decision_engine and hasattr(self.decision_engine, 'update_weights'):
            self.decision_engine.update_weights(category_weights)
        elif self.decision_engine and hasattr(self.decision_engine, 'weights'):
            self._set_engine_weights(category_weights)

        return result

    def _numeric_feature_cols(self, df: pd.DataFrame) -> List[str]:
        exclude = {'trade_id', 'timestamp', 'win', 'exit_reason', 'entry_features_json', 'risk_metrics_json', 'final_pnl'}
        nums = []
        for c in df.columns:
            if c in exclude:
                continue
            try:
                df[c].astype(float)
                nums.append(c)
            except (ValueError, TypeError):
                pass
        return nums

    def _derive_weights(self, importance: Dict[str, float]) -> Dict[str, float]:
        # Build per-category average importance
        raw = {}
        for cat, mapped in self.category_map.items():
            vals = [importance.get(f, 0.0) for f in mapped]
            raw[cat] = float(np.mean(vals)) if vals else 0.0
        total = sum(raw.values())
        if total <= 0:
            # No signal: fall back to equal category weights
            n = len(raw)
            return {k: round(1.0 / n, 4) for k in raw}
        # blend with a baseline so extreme features don't dominate
        baseline = {k: 1.0 / len(raw) for k in raw}
        blended = {k: 0.6 * (raw[k] / total) + 0.4 * baseline[k] for k in raw}
        bsum = sum(blended.values())
        return {k: round(v / bsum, 4) for k, v in blended.items()}

    def _set_engine_weights(self, weights: Dict[str, float]) -> None:
        # Direct assignment with normalization
        total = sum(weights.values())
        if total > 0:
            self.decision_engine.weights = {k: round(v / total, 3) for k, v in weights.items()}

    def _load_samples(self) -> List[Dict[str, Any]]:
        if not self.store or not hasattr(self.store, 'get_ai_training_data'):
            return []
        try:
            return self.store.get_ai_training_data(self.max_history)
        except Exception as e:
            logger.warning(f"Load training samples failed: {e}")
            return []

    def _save_feature_importance(self, importance: Dict[str, float], timestamp: str) -> None:
        if not self.store or not hasattr(self.store, 'save_feature_importance'):
            return
        try:
            for f, imp in importance.items():
                self.store.save_feature_importance({
                    'timestamp': timestamp,
                    'feature': f,
                    'correlation': float(imp),
                    'importance': float(imp),
                })
        except Exception as e:
            logger.warning(f"Save feature importance failed: {e}")

    def _save_model_weights(self, weights: Dict[str, float], timestamp: str) -> None:
        if not self.store or not hasattr(self.store, 'save_model_weights'):
            return
        try:
            for cat, w in weights.items():
                self.store.save_model_weights({
                    'timestamp': timestamp,
                    'category': cat,
                    'weight': float(w),
                })
        except Exception as e:
            logger.warning(f"Save model weights failed: {e}")

    def _save_learning_metrics(self, result: Dict[str, Any]) -> None:
        if not self.store or not hasattr(self.store, 'save_learning_metrics'):
            return
        try:
            self.store.save_learning_metrics({
                'timestamp': result['timestamp'],
                'accuracy': result['accuracy'],
                'win_rate': result['win_rate'],
                'trades_used': result['trades_used'],
            })
        except Exception as e:
            logger.warning(f"Save learning metrics failed: {e}")

    @staticmethod
    def _top_bottom(importance: Dict[str, float], top: bool, n: int = 5) -> List[Dict[str, Any]]:
        s = sorted(importance.items(), key=lambda x: x[1], reverse=top)
        return [{'feature': f, 'importance': round(i, 4)} for f, i in s[:n]]

    def _empty_result(self) -> Dict[str, Any]:
        return {
            'timestamp': datetime.now().isoformat(),
            'trades_used': 0,
            'accuracy': 0.0,
            'win_rate': 0.0,
            'feature_importance': {},
            'category_weights': {},
            'top_indicators': [],
            'worst_indicators': [],
        }

    # ── Dashboard / API ───────────────────────────────────────────────────

    def get_dashboard_data(self) -> Dict[str, Any]:
        result = self._last_metrics or {}
        if not result and self.store and hasattr(self.store, 'get_latest_learning_metrics'):
            lm = self.store.get_latest_learning_metrics()
            if lm:
                result = {
                    'timestamp': lm.get('timestamp'),
                    'trades_used': lm.get('trades_used'),
                    'accuracy': lm.get('accuracy'),
                    'win_rate': lm.get('win_rate'),
                }

        features = []
        if self.store and hasattr(self.store, 'get_latest_feature_importance'):
            features = self.store.get_latest_feature_importance(limit=30)

        weights = []
        if self.store and hasattr(self.store, 'get_latest_model_weights'):
            weights = self.store.get_latest_model_weights()

        curve = []
        if self.store and hasattr(self.store, 'get_learning_curve'):
            curve = self.store.get_learning_curve(limit=30)

        return {
            'metrics': result,
            'feature_importance': features,
            'model_weights': weights,
            'learning_curve': curve,
            'top_indicators': result.get('top_indicators', []),
            'worst_indicators': result.get('worst_indicators', []),
        }
