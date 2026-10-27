"""
Intraday Strategy Backtester

Deterministic candle-replay harness for the Angel intraday strategy.
Replays the REAL pipeline — IntradayFeatures -> IntradayScorer ->
IntradayValidator -> IntradayPositionSizer — over historical 1-minute
NSE candles, with modeled spread/slippage and the verified Angel One
intraday charge schedule.

Anti-leakage rules enforced here:
- Features at time t are computed only on candles ending at t
  (all rolling/cumulative indicators look strictly backward).
- Entries fill at the signal bar's close plus adverse spread+slippage —
  never at a price the bar did not print.
- When a single bar touches BOTH stop and target, the STOP is assumed
  hit first (conservative; intra-bar order is unknowable from OHLC).
- Market regime for a session is derived from that session's index
  candles up to the evaluation time — same rule as production.
- Out-of-sample evaluation is strictly chronological.

Production-fidelity notes (replicated for BASELINE, flagged in report):
- calculate_vwap() runs over the ENTIRE fetched frame, so production
  VWAP is a trailing multi-session cumulative, not session-anchored.
  Replicated here as a trailing 5-session cumulative (days=5 fetch).
- calculate_opening_range() uses df.head(15), so the opening range is
  the OLDEST session in the fetch window, not today's first 15 bars.
- relative_volume divides by a 20-bar mean that INCLUDES the current bar.
- EMA/RSI/ATR are rolling indicators; computing them once on the full
  frame is numerically equivalent to production's per-scan recompute
  on a trailing window (look-back only).

The verified Angel One charge schedule lives in charges.py —
replicated for the BASELINE run and applied to new PAPER fills by
paper_executor.
"""
import json
import logging
import os
from dataclasses import dataclass
from datetime import time
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from .charges import angel_intraday_charges
from .models import IntradayConfig, SignalDirection, SignalClassification, MarketRegime
from .scorer import IntradayScorer
from .validators import IntradayValidator
from .position_sizer import IntradayPositionSizer

logger = logging.getLogger(__name__)


# ── configuration ───────────────────────────────────────────────────
@dataclass
class BacktestConfig:
    """Independently defined backtest configuration."""
    name: str = "BASELINE"
    sl_atr_mult: float = 0.5
    tp_atr_mult: float = 1.5
    min_score: Optional[float] = None        # extra score floor
    min_net_reward: Optional[float] = None   # require est net >= Rs X
    min_rel_volume: float = 1.0
    max_entries_per_day: int = 5
    max_concurrent: int = 2
    capital: float = 10000.0
    risk_per_trade_pct: float = 0.01
    min_risk_reward: float = 1.5
    slippage_bps: float = 5.0                # adverse fill deviation
    spread_bps: float = 5.0                  # modeled half-spread
    entry_start: str = "09:30"
    last_entry: str = "14:30"
    square_off: str = "15:00"
    session_vwap: bool = False               # fix: session-anchored VWAP/OR
    min_bar_volume: int = 0                  # liquidity floor on entry bar


def _hhmm(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))


# ── data loading ────────────────────────────────────────────────────
def load_candles(cache_dir: str, symbol: str) -> Optional[pd.DataFrame]:
    """Load cached 1-min candles -> DataFrame indexed by datetime."""
    path = os.path.join(cache_dir, f"{symbol}.json")
    if not os.path.exists(path):
        return None
    try:
        rows = json.load(open(path))
        df = pd.DataFrame(
            rows, columns=['datetime', 'open', 'high', 'low', 'close', 'volume'])
        df['datetime'] = pd.to_datetime(df['datetime'])
        df.set_index('datetime', inplace=True)
        df.sort_index(inplace=True)
        return df
    except Exception as e:
        logger.error(f"[BACKTEST] candle load failed {symbol}: {e}")
        return None


# ── vectorized feature precompute ───────────────────────────────────
def precompute_features(df: pd.DataFrame, cfg: IntradayConfig,
                        session_vwap: bool = False) -> pd.DataFrame:
    """One-pass feature frame for a symbol. Every column looks strictly
    backward, so row t is safe to use for a decision at bar t.

    Columns mirror IntradayFeatures.calculate_all_features keys, with
    'vwap'/'or_high'/'or_low' honouring the session_vwap variant.
    """
    F = pd.DataFrame(index=df.index)
    close = df['close']
    F['current_price'] = close
    F['volume'] = df['volume']

    F['ema_9'] = close.ewm(span=cfg.ema_short_period, adjust=False).mean()
    F['ema_20'] = close.ewm(span=cfg.ema_long_period, adjust=False).mean()

    delta = close.diff()
    gain = delta.where(delta > 0, 0.0).rolling(cfg.rsi_period).mean()
    loss = (-delta.where(delta < 0, 0.0)).rolling(cfg.rsi_period).mean()
    F['rsi'] = 100 - 100 / (1 + gain / loss)

    tr = pd.concat([df['high'] - df['low'],
                    (df['high'] - close.shift()).abs(),
                    (df['low'] - close.shift()).abs()], axis=1).max(axis=1)
    F['atr'] = tr.rolling(cfg.atr_period).mean()

    # Production: iloc[-1] vs iloc[-5] -> 4-bar shift
    F['momentum'] = (close - close.shift(4)) / close.shift(4) * 100
    # Production: tail(20).mean() includes the current bar
    F['relative_volume'] = df['volume'] / df['volume'].rolling(20).mean()

    # ── session bookkeeping ──
    sess_of_row, sessions = pd.factorize(df.index.normalize(), sort=True)
    sess_of_row = sess_of_row.astype(int)
    n_sessions = len(sessions)
    sess_start = np.array(
        [np.searchsorted(sess_of_row, i) for i in range(n_sessions)])

    tp = (df['high'] + df['low'] + df['close']) / 3
    cumtp = (tp * df['volume']).cumsum().values
    cumv = df['volume'].cumsum().values

    if session_vwap:
        # Corrected semantics: VWAP and OR anchored to the CURRENT session.
        win_start = sess_start[sess_of_row]
    else:
        # Production semantics: whole fetched window (replicated as a
        # trailing 5-session window, matching the days=5 fetch).
        win_start = sess_start[np.maximum(sess_of_row - 4, 0)]

    base_tp = np.where(win_start > 0, cumtp[win_start - 1], 0.0)
    base_v = np.where(win_start > 0, cumv[win_start - 1], 0.0)
    denom = cumv - base_v
    F['vwap'] = np.where(denom > 0, (cumtp - base_tp) / denom, np.nan)

    # Opening range: first or_minutes bars of the OR session. Production
    # uses head(15) of the whole fetch -> the window's OLDEST session;
    # session_vwap mode uses the current session's first 15 bars.
    sess_or_h = np.zeros(n_sessions)
    sess_or_l = np.zeros(n_sessions)
    for s_i in range(n_sessions):
        start = sess_start[s_i]
        end = min(start + cfg.or_minutes, len(df))
        sess_or_h[s_i] = df['high'].values[start:end].max()
        sess_or_l[s_i] = df['low'].values[start:end].min()
    or_sess_idx = sess_of_row if session_vwap else np.maximum(sess_of_row - 4, 0)
    F['or_high'] = sess_or_h[or_sess_idx]
    F['or_low'] = sess_or_l[or_sess_idx]

    F['ema_alignment'] = np.where(F['ema_9'] > F['ema_20'], 'BULLISH',
                          np.where(F['ema_9'] < F['ema_20'], 'BEARISH', 'NEUTRAL'))
    thr = 0.002
    F['vwap_structure'] = np.where(
        F['current_price'] > F['vwap'] * (1 + thr), 'ABOVE_VWAP',
        np.where(F['current_price'] < F['vwap'] * (1 - thr), 'BELOW_VWAP', 'AT_VWAP'))
    return F


# ── the replay engine ───────────────────────────────────────────────
class IntradayBacktester:
    """Bar-by-bar chronological replay of the production pipeline."""

    def __init__(self, config: BacktestConfig, intraday_config: Optional[IntradayConfig] = None):
        self.cfg = config
        self.iconfig = intraday_config or IntradayConfig()
        self.scorer = IntradayScorer(self.iconfig)
        self.validator = IntradayValidator(self.iconfig)
        self.sizer = IntradayPositionSizer(self.iconfig)

    def _regime_at(self, index_day: Optional[pd.DataFrame], upto_idx: int) -> MarketRegime:
        """Session regime from index candles up to `upto_idx` — same rule
        as IntradayScanner.detect_market_regime (day-open, VWAP, change%)."""
        if index_day is None or index_day.empty or upto_idx < 1:
            return MarketRegime.SIDEWAYS
        today = index_day.iloc[:upto_idx]
        day_open = float(today['open'].iloc[0])
        price = float(today['close'].iloc[-1])
        range_pct = ((float(today['high'].max()) - float(today['low'].min()))
                     / day_open * 100) if day_open else 0.0
        if range_pct >= 1.5:
            return MarketRegime.HIGH_VOLATILITY
        v = float(today['volume'].sum())
        vwap = float((((today['high'] + today['low'] + today['close']) / 3)
                     * today['volume']).sum() / v) if v > 0 else None
        change_pct = ((price - day_open) / day_open * 100) if day_open else 0.0
        if change_pct >= 0.4 and vwap and price > vwap:
            return MarketRegime.BULLISH
        if change_pct <= -0.4 and vwap and price < vwap:
            return MarketRegime.BEARISH
        return MarketRegime.SIDEWAYS

    def _fill(self, price: float, side: str, adverse: bool = True) -> float:
        """Apply modeled half-spread + slippage to a fill price."""
        cost = self.cfg.spread_bps + (self.cfg.slippage_bps if adverse else 0.0)
        adj = price * cost / 10000.0
        return price + adj if side == 'BUY' else price - adj

    def run_day(self, day, day_bars: Dict[str, pd.DataFrame],
                index_day: Optional[pd.DataFrame]) -> List[Dict[str, Any]]:
        """Replay one session. day_bars maps symbol -> PRECOMPUTED feature
        frame (with OHLCV columns merged). Returns closed trades."""
        cfg = self.cfg
        trades: List[Dict[str, Any]] = []
        open_pos: Dict[str, Dict[str, Any]] = {}
        entered_keys = set()
        entries = 0

        all_ts = sorted({ts for df in day_bars.values() for ts in df.index})
        entry_start, last_entry, square_off = (
            _hhmm(cfg.entry_start), _hhmm(cfg.last_entry), _hhmm(cfg.square_off))

        for ts in all_ts:
            t = ts.time()
            # ── manage open positions on this bar ──
            for sym in list(open_pos):
                df = day_bars[sym]
                if ts not in df.index:
                    continue
                bar = df.loc[ts]
                pos = open_pos[sym]
                exit_price = exit_reason = None
                if pos['direction'] == 'LONG':
                    if bar['low'] <= pos['sl']:
                        exit_price, exit_reason = pos['sl'], 'STOP_LOSS'
                    elif bar['high'] >= pos['target']:
                        exit_price, exit_reason = pos['target'], 'TARGET_HIT'
                else:
                    if bar['high'] >= pos['sl']:
                        exit_price, exit_reason = pos['sl'], 'STOP_LOSS'
                    elif bar['low'] <= pos['target']:
                        exit_price, exit_reason = pos['target'], 'TARGET_HIT'
                if exit_price is None and t >= square_off:
                    exit_price, exit_reason = float(bar['close']), 'EOD_SQUARE_OFF'
                if exit_price is not None:
                    trades.append(self._close(pos, exit_price, exit_reason, ts))
                    del open_pos[sym]

            if not (entry_start <= t <= last_entry):
                continue
            if entries >= cfg.max_entries_per_day or len(open_pos) >= cfg.max_concurrent:
                continue

            # index position for regime lookup
            idx_pos = 0
            if index_day is not None:
                idx_pos = int(index_day.index.searchsorted(ts, side='right'))
            regime = self._regime_at(index_day, idx_pos)

            # Engine iterates the universe in configured (NIFTY50) order
            for sym in day_bars:
                if entries >= cfg.max_entries_per_day or len(open_pos) >= cfg.max_concurrent:
                    break
                if sym in open_pos or (sym, day) in entered_keys:
                    continue
                df = day_bars[sym]
                if ts not in df.index:
                    continue
                row = df.loc[ts]
                if pd.isna(row['ema_20']) or pd.isna(row['atr']) or pd.isna(row['vwap']):
                    continue
                feats = row.to_dict()
                if feats.get('relative_volume', 0) < cfg.min_rel_volume:
                    continue
                if cfg.min_bar_volume and feats.get('volume', 0) < cfg.min_bar_volume:
                    continue
                signal = self.scorer.calculate_score(
                    {'symbol': sym, 'features': feats}, regime)
                if signal is None or signal.classification == SignalClassification.NO_TRADE:
                    continue
                if cfg.min_score is not None and signal.score < cfg.min_score:
                    continue
                ok, _ = self.validator.validate_signal(signal)
                if not ok:
                    continue

                atr = feats['atr'] if feats['atr'] > 0 else feats['current_price'] * 0.01
                entry_ref = feats['current_price']
                is_long = signal.direction == SignalDirection.LONG
                if is_long:
                    sl = entry_ref - atr * cfg.sl_atr_mult
                    tp_ = entry_ref + atr * cfg.tp_atr_mult
                else:
                    sl = entry_ref + atr * cfg.sl_atr_mult
                    tp_ = entry_ref - atr * cfg.tp_atr_mult
                risk = abs(entry_ref - sl)
                reward = abs(tp_ - entry_ref)
                if risk <= 0 or reward / risk < cfg.min_risk_reward:
                    continue

                class _S:
                    pass
                s = _S()
                s.entry_price, s.stop_loss, s.symbol = entry_ref, sl, sym
                qty, _ = self.sizer.calculate_quantity(s, cfg.capital)
                if qty <= 0:
                    continue

                if cfg.min_net_reward is not None:
                    buy_v = qty * (entry_ref if is_long else tp_)
                    sell_v = qty * (tp_ if is_long else entry_ref)
                    est_net = qty * reward - angel_intraday_charges(buy_v, sell_v)['total']
                    if est_net < cfg.min_net_reward:
                        continue

                fill = self._fill(entry_ref, 'BUY' if is_long else 'SELL')
                open_pos[sym] = {
                    'symbol': sym, 'direction': signal.direction.value,
                    'entry': fill, 'qty': qty, 'sl': sl, 'target': tp_,
                    'entry_time': ts, 'score': signal.score,
                    'strategy': signal.strategy,
                }
                entered_keys.add((sym, day))
                entries += 1

        for sym, pos in open_pos.items():
            df = day_bars[sym]
            trades.append(self._close(pos, float(df['close'].iloc[-1]),
                                      'EOD_SQUARE_OFF', df.index[-1]))
        return trades

    def _close(self, pos: Dict[str, Any], exit_ref: float,
               reason: str, ts) -> Dict[str, Any]:
        side = 'SELL' if pos['direction'] == 'LONG' else 'BUY'
        # Marketable exits (SL, EOD) pay slippage; a limit target does not.
        fill = self._fill(exit_ref, side, adverse=(reason != 'TARGET_HIT'))
        if pos['direction'] == 'LONG':
            gross = (fill - pos['entry']) * pos['qty']
            buy_v, sell_v = pos['entry'] * pos['qty'], fill * pos['qty']
        else:
            gross = (pos['entry'] - fill) * pos['qty']
            buy_v, sell_v = fill * pos['qty'], pos['entry'] * pos['qty']
        charges = angel_intraday_charges(buy_v, sell_v)
        return {**pos, 'exit': fill, 'exit_time': ts, 'exit_reason': reason,
                'gross_pnl': round(gross, 4),
                'charges': round(charges['total'], 4),
                'net_pnl': round(gross - charges['total'], 4)}

    def run(self, feat_frames: Dict[str, pd.DataFrame],
            index_df: Optional[pd.DataFrame]) -> List[Dict[str, Any]]:
        """Run over all sessions present in the precomputed frames."""
        days = sorted({ts.date() for df in feat_frames.values() for ts in df.index})
        all_trades = []
        for day in days:
            day_bars = {s: f[f.index.date == day] for s, f in feat_frames.items()}
            day_bars = {s: f for s, f in day_bars.items() if len(f) > 30}
            if not day_bars:
                continue
            idx_day = index_df[index_df.index.date == day] if index_df is not None else None
            all_trades.extend(self.run_day(day, day_bars, idx_day))
        return all_trades


# ── metrics ─────────────────────────────────────────────────────────
def metrics(trades: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not trades:
        return {'trades': 0}
    wins = [t for t in trades if t['net_pnl'] > 0]
    losses = [t for t in trades if t['net_pnl'] <= 0]
    gross_w = sum(t['net_pnl'] for t in wins)
    gross_l = abs(sum(t['net_pnl'] for t in losses))
    cum = peak = dd = 0.0
    for t in trades:
        cum += t['net_pnl']
        peak = max(peak, cum)
        dd = max(dd, peak - cum)
    return {
        'trades': len(trades),
        'wins': len(wins), 'losses': len(losses),
        'win_rate': round(len(wins) / len(trades) * 100, 1),
        'net_pnl': round(sum(t['net_pnl'] for t in trades), 2),
        'gross_pnl': round(sum(t['gross_pnl'] for t in trades), 2),
        'costs': round(sum(t['charges'] for t in trades), 2),
        'expectancy': round(sum(t['net_pnl'] for t in trades) / len(trades), 2),
        'avg_win': round(gross_w / len(wins), 2) if wins else 0.0,
        'avg_loss': round(-gross_l / len(losses), 2) if losses else 0.0,
        'profit_factor': round(gross_w / gross_l, 2) if gross_l else None,
        'max_drawdown': round(dd, 2),
    }


# ── candidate configurations (independently defined) ────────────────
CONFIGS = [
    BacktestConfig(name='BASELINE'),
    BacktestConfig(name='ATR_WIDE', sl_atr_mult=1.0, tp_atr_mult=3.0),
    BacktestConfig(name='ATR_MODERATE', sl_atr_mult=0.75, tp_atr_mult=2.25),
    BacktestConfig(name='MIN_NET40', min_net_reward=40.0),
    BacktestConfig(name='STRICT80', min_score=80.0),
    BacktestConfig(name='FEWER2', max_entries_per_day=2),
    BacktestConfig(name='SESS_VWAP', session_vwap=True),
    BacktestConfig(name='COMBO',
                   sl_atr_mult=0.75, tp_atr_mult=2.25,
                   min_net_reward=40.0, session_vwap=True),
]


def compare(cache_dir: str, symbols: List[str],
            configs: Optional[List[BacktestConfig]] = None,
            index_symbol: str = 'NIFTY_IDX',
            oos_fraction: float = 0.4) -> Dict[str, Any]:
    """Run all configs over precomputed feature frames; split sessions
    chronologically into in-sample / out-of-sample."""
    iconfig = IntradayConfig()
    configs = configs or CONFIGS
    raw = {s: load_candles(cache_dir, s) for s in symbols}
    raw = {s: f for s, f in raw.items() if f is not None and not f.empty}
    index_df = load_candles(cache_dir, index_symbol)
    days = sorted({ts.date() for df in raw.values() for ts in df.index})
    split = max(1, int(len(days) * (1 - oos_fraction)))
    is_days, oos_days = set(days[:split]), set(days[split:])

    results = {}
    for cfg in configs:
        feat = {s: pd.concat(
                    [df, precompute_features(df, iconfig, cfg.session_vwap)],
                    axis=1)
                for s, df in raw.items()}
        # keep OHLC + features, drop duplicated price cols
        feat = {s: f.loc[:, ~f.columns.duplicated()] for s, f in feat.items()}
        bt = IntradayBacktester(cfg, iconfig)
        trades = bt.run(feat, index_df)
        is_t = [t for t in trades if t['entry_time'].date() in is_days]
        oos_t = [t for t in trades if t['entry_time'].date() in oos_days]
        results[cfg.name] = {
            'all': metrics(trades),
            'in_sample': metrics(is_t),
            'out_of_sample': metrics(oos_t),
            'trades_detail': trades,
        }
    return {'is_days': sorted(str(d) for d in is_days),
            'oos_days': sorted(str(d) for d in oos_days),
            'results': results}
