"""
backtester.py — Historical strategy replay engine.

Replays the live signal logic (technical indicators → score → trade) on
historical daily OHLCV data fetched from Kite Connect.  No external data
sources required.

Usage (standalone):
    python src/backtester.py

Usage (from code):
    from backtester import Backtester
    bt = Backtester()
    result = bt.run(symbols=['RELIANCE','INFY'], years=3)
    print(result['summary'])
"""

from __future__ import annotations

import logging
import math
import sys
import os
from datetime import datetime, timedelta, date
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

logger = logging.getLogger(__name__)


# ─── constants ────────────────────────────────────────────────────────────────
_RISK_FREE_RATE = 0.065          # 6.5% Indian 10-yr gilt
_TRADING_DAYS   = 252
_COMMISSION_PCT = 0.0003         # 0.03% per leg (approx Zerodha)
_SLIPPAGE_PCT   = 0.0005         # 0.05% per leg (market impact)


# ─── regime helper ────────────────────────────────────────────────────────────
def _classify_regime(nifty_ret_1y: float, nifty_vol_30d: float) -> str:
    """Simple regime label from Nifty 1-year return and 30-day volatility."""
    if nifty_ret_1y > 0.15:
        return 'BULL'
    if nifty_ret_1y < -0.10:
        return 'BEAR'
    if nifty_vol_30d > 0.20:
        return 'VOLATILE'
    return 'SIDEWAYS'


# ─── core engine ──────────────────────────────────────────────────────────────
class Backtester:
    """
    Walk-forward daily backtester.

    Strategy rules (mirror live bot exactly):
    - BUY  when:
        * technical_score > 0.45   (tightened from 0.3)
        * trend in UPTREND / STRONG_UPTREND
        * RSI < 75
        * volume_ratio >= 1.0      (must have above-avg volume)
    - SELL when any of:
        * Stop-loss hit             (config SWING_STOP_LOSS_PERCENTAGE)
        * Target hit                (config SWING_TARGET_PERCENTAGE)
        * Max hold days exceeded    (config SWING_MAX_HOLD_DAYS)
        * RSI overbought > 80       (smart exit proxy)
        * technical_score < -0.25 AND bearish trend (tightened exit)
    - Max concurrent positions: config.MAX_POSITIONS
    - Position size: capital / MAX_POSITIONS  (equal-weight)
    - Commission + slippage applied on each leg
    """

    def __init__(self) -> None:
        from config import config
        from market_data import MarketDataFetcher
        from technical_analysis import TechnicalAnalyzer

        self.config  = config
        self.md      = MarketDataFetcher()
        self.ta      = TechnicalAnalyzer()

    # ── public API ─────────────────────────────────────────────────────────────
    def run(
        self,
        symbols: List[str],
        years: int = 3,
        initial_capital: float = None,
        progress_cb=None,           # optional callable(pct, msg)
    ) -> Dict:
        """
        Run backtest across all symbols, return full result dict.

        Args:
            symbols:         List of NSE symbols to backtest
            years:           Look-back window in years (1–5)
            initial_capital: Starting capital (defaults to config.TRADING_AMOUNT)
            progress_cb:     Optional callback(pct:float, msg:str) for UI updates
        """
        initial_capital = initial_capital or self.config.TRADING_AMOUNT
        end_dt   = datetime.now()
        start_dt = end_dt - timedelta(days=int(years * 365))

        def _progress(pct: float, msg: str):
            if progress_cb:
                progress_cb(pct, msg)
            logger.info(f"[backtest {pct:.0f}%] {msg}")

        _progress(0, f"Starting backtest: {len(symbols)} symbols, {years}y")

        # ── 1. Fetch historical data ────────────────────────────────────────
        ohlcv: Dict[str, pd.DataFrame] = {}
        for i, sym in enumerate(symbols):
            try:
                df = self._fetch_daily(sym, start_dt, end_dt)
                if len(df) >= 60:       # need at least 60 bars for indicators
                    ohlcv[sym] = df
            except Exception as e:
                logger.warning(f"Skipping {sym}: {e}")
            _progress(5 + 35 * (i + 1) / len(symbols), f"Fetched {sym}")

        if not ohlcv:
            return {'error': 'No historical data fetched. Check Kite connection.'}

        _progress(40, f"Data ready for {len(ohlcv)} symbols — running simulation")

        # ── 2. Fetch Nifty 50 for regime classification ─────────────────────
        nifty_df = self._fetch_daily('NIFTY 50', start_dt, end_dt, index=True)

        # ── 3. Align all series to a common date index ──────────────────────
        all_dates = sorted(
            set.intersection(*[set(df.index) for df in ohlcv.values()])
        )
        if len(all_dates) < 20:
            return {'error': 'Insufficient overlapping trading days.'}

        # ── 4. Walk-forward simulation ──────────────────────────────────────
        portfolio = _Portfolio(
            initial_capital=initial_capital,
            max_positions=self.config.MAX_POSITIONS,
            sl_pct=self.config.SWING_STOP_LOSS_PERCENTAGE,
            target_pct=self.config.SWING_TARGET_PERCENTAGE,
            max_hold_days=self.config.SWING_MAX_HOLD_DAYS,
            commission=_COMMISSION_PCT,
            slippage=_SLIPPAGE_PCT,
        )

        total_days = len(all_dates)
        for day_i, dt in enumerate(all_dates):
            # Regime on this day
            regime = self._regime_on(nifty_df, dt)

            # ── for each symbol: compute signal on data UP TO this bar ──────
            for sym, df in ohlcv.items():
                if dt not in df.index:
                    continue
                bar_idx  = df.index.get_loc(dt)
                if bar_idx < 50:     # need history for indicators
                    continue
                slice_df = df.iloc[:bar_idx + 1]   # no look-ahead

                try:
                    signals = self.ta.generate_signals(slice_df)
                except Exception:
                    continue

                close      = float(df.loc[dt, 'Close'])
                rsi        = signals.get('rsi', 50.0) or 50.0
                score      = signals.get('technical_score', 0.0) or 0.0
                confidence = signals.get('confidence', 0.0) or 0.0
                trend      = signals.get('trend', 'NEUTRAL')
                vol_ratio  = signals.get('volume_ratio', 1.0) or 1.0
                sig        = signals.get('signal', 'HOLD')

                # Regime gate: no buys in BEAR
                if regime == 'BEAR':
                    sig = 'HOLD' if sig == 'BUY' else sig

                portfolio.process_bar(
                    sym, dt, close, sig, score, rsi,
                    confidence, regime, trend, vol_ratio
                )

            if day_i % 20 == 0:
                _progress(40 + 55 * day_i / total_days, f"Simulating {dt.date()}")

        _progress(95, "Computing metrics")
        result = portfolio.results(initial_capital, start_dt, end_dt)
        _progress(100, "Done")
        return result

    # ── helpers ────────────────────────────────────────────────────────────────
    def _fetch_daily(
        self, symbol: str, start: datetime, end: datetime, index: bool = False
    ) -> pd.DataFrame:
        """Fetch full daily OHLCV from Kite and return date-indexed DataFrame."""
        from market_data import MarketDataFetcher
        import time as _time

        days = (end - start).days + 5
        if not self.md.kite:
            raise RuntimeError("Kite not connected")

        instruments = self.md._get_instruments()
        token = None
        for inst in instruments:
            if inst['tradingsymbol'] == symbol:
                token = inst['instrument_token']
                break

        if token is None and not index:
            raise ValueError(f"Symbol not found: {symbol}")

        # For Nifty index
        if index:
            try:
                # Nifty 50 index token on NSE
                q = self.md.kite.quote(['NSE:NIFTY 50'])
                token = q['NSE:NIFTY 50']['instrument_token']
            except Exception:
                return pd.DataFrame()

        elapsed = _time.time() - MarketDataFetcher._last_historical_request
        if elapsed < MarketDataFetcher._min_historical_interval:
            _time.sleep(MarketDataFetcher._min_historical_interval - elapsed)

        raw = self.md.kite.historical_data(
            instrument_token=token,
            from_date=start,
            to_date=end,
            interval='day',
        )
        MarketDataFetcher._last_historical_request = _time.time()

        if not raw:
            return pd.DataFrame()

        df = pd.DataFrame(raw)
        df.columns = [c.capitalize() for c in df.columns]
        df['Date'] = pd.to_datetime(df['Date'])
        df = df.set_index('Date').sort_index()
        return df

    def _regime_on(self, nifty: pd.DataFrame, dt: datetime) -> str:
        if nifty.empty:
            return 'SIDEWAYS'
        idx = nifty.index
        past = idx[idx <= dt]
        if len(past) < 30:
            return 'SIDEWAYS'
        price_now = float(nifty.loc[past[-1], 'Close'])
        # 1-year return
        yr_ago = past[past <= dt - timedelta(days=365)]
        ret_1y = 0.0
        if len(yr_ago):
            price_1y = float(nifty.loc[yr_ago[-1], 'Close'])
            ret_1y = (price_now - price_1y) / price_1y if price_1y else 0.0
        # 30-day vol
        recent = nifty.loc[past[-30:], 'Close'].pct_change().dropna()
        vol_30 = float(recent.std() * math.sqrt(_TRADING_DAYS)) if len(recent) > 5 else 0.15
        return _classify_regime(ret_1y, vol_30)


# ─── portfolio simulator ──────────────────────────────────────────────────────
class _Position:
    __slots__ = ('symbol', 'entry_date', 'entry_price', 'qty',
                 'sl', 'target', 'max_hold', 'regime', 'confidence')

    def __init__(self, symbol, entry_date, entry_price, qty,
                 sl, target, max_hold, regime, confidence):
        self.symbol      = symbol
        self.entry_date  = entry_date
        self.entry_price = entry_price
        self.qty         = qty
        self.sl          = sl
        self.target      = target
        self.max_hold    = max_hold
        self.regime      = regime
        self.confidence  = confidence


class _Portfolio:
    def __init__(self, initial_capital, max_positions,
                 sl_pct, target_pct, max_hold_days,
                 commission, slippage):
        self.cash         = initial_capital
        self.max_pos      = max_positions
        self.sl_pct       = sl_pct
        self.target_pct   = target_pct
        self.max_hold     = max_hold_days
        self.comm         = commission
        self.slip         = slippage

        self.positions: Dict[str, _Position] = {}
        self.trades:    List[Dict]            = []
        self.equity_curve: List[Tuple]        = []   # (date, equity)

    # ── per-bar processing ────────────────────────────────────────────────
    def process_bar(
        self, sym, dt, close, sig, score, rsi,
        confidence, regime, trend='NEUTRAL', vol_ratio=1.0
    ):
        cost_factor = 1 + self.comm + self.slip    # buy cost
        sell_factor = 1 - self.comm - self.slip    # sell proceeds

        # Check exits first
        if sym in self.positions:
            pos = self.positions[sym]
            hold_days = (dt - pos.entry_date).days
            exit_price = None
            exit_reason = None

            ep = pos.entry_price
            bearish_trend = trend in ('STRONG_DOWNTREND', 'DOWNTREND')
            if close <= pos.sl:
                exit_price  = pos.sl
                exit_reason = 'stop_loss'
            elif close >= pos.target:
                exit_price  = pos.target
                exit_reason = 'target'
            elif hold_days >= pos.max_hold:
                exit_price  = close
                exit_reason = 'max_hold'
            elif rsi > 80:
                exit_price  = close
                exit_reason = 'rsi_overbought'
            elif sig == 'SELL' or (score < -0.25 and bearish_trend):
                exit_price  = close
                exit_reason = 'signal_reversal'

            if exit_price is not None:
                proceeds = exit_price * pos.qty * sell_factor
                self.cash += proceeds
                pnl     = proceeds - (ep * pos.qty * cost_factor)
                pnl_pct = pnl / (ep * pos.qty)
                self.trades.append({
                    'symbol':      sym,
                    'entry_date':  pos.entry_date,
                    'exit_date':   dt,
                    'entry_price': ep,
                    'exit_price':  exit_price,
                    'qty':         pos.qty,
                    'pnl':         pnl,
                    'pnl_pct':     pnl_pct,
                    'hold_days':   hold_days,
                    'exit_reason': exit_reason,
                    'regime':      pos.regime,
                    'confidence':  pos.confidence,
                })
                del self.positions[sym]

        # Check entry — mirror live bot filters (BUY signal + confidence gate)
        not_downtrend = trend not in ('STRONG_DOWNTREND', 'DOWNTREND')
        # Regime-adjusted confidence gate (mirrors TradeScorer SCORE_SKIP_* thresholds)
        regime_upper = str(regime).upper()
        if regime_upper == 'BULL':
            min_confidence = 0.60
        elif regime_upper == 'BEAR':
            min_confidence = 2.0     # Never buy in BEAR
        else:  # SIDEWAYS / VOLATILE / UNKNOWN
            min_confidence = 0.55    # slightly relaxed for backtest

        if (sym not in self.positions
                and sig == 'BUY'
                and score > 0.35
                and not_downtrend
                and rsi < 78
                and confidence >= min_confidence
                and len(self.positions) < self.max_pos
                and self.cash > 0):
            slot_capital = self.cash / max(1, self.max_pos - len(self.positions))
            buy_price    = close * cost_factor
            qty          = max(1, int(slot_capital / buy_price))
            cost         = buy_price * qty
            if cost <= self.cash:
                self.cash -= cost
                self.positions[sym] = _Position(
                    symbol=sym,
                    entry_date=dt,
                    entry_price=close,
                    qty=qty,
                    sl=close * (1 - self.sl_pct),
                    target=close * (1 + self.target_pct),
                    max_hold=self.max_hold,
                    regime=regime,
                    confidence=confidence,
                )

        # Snapshot equity (cash + mark-to-market open positions)
        mtm = sum(p.qty * close for p in self.positions.values()
                  if p.symbol == sym)
        # We record full equity once per day in results()

    # ── metrics ───────────────────────────────────────────────────────────
    def results(self, initial_capital: float, start: datetime, end: datetime) -> Dict:
        if not self.trades:
            return {
                'summary': {'total_trades': 0, 'error': 'No trades generated — signal threshold may be too strict or data too short.'},
                'trades': [],
                'equity_curve': [],
                'by_regime': {},
                'by_sector': {},
                'by_confidence': {},
                'by_exit_reason': {},
            }

        df = pd.DataFrame(self.trades)

        total_trades  = len(df)
        wins          = df[df['pnl'] > 0]
        losses        = df[df['pnl'] <= 0]
        win_rate      = len(wins) / total_trades
        total_pnl     = float(df['pnl'].sum())
        final_capital = initial_capital + total_pnl

        # CAGR
        years = max((end - start).days / 365.25, 0.1)
        cagr  = (final_capital / initial_capital) ** (1 / years) - 1 if initial_capital > 0 else 0

        # Drawdown — build daily equity curve from trade PnLs
        df_sorted = df.sort_values('exit_date')
        cum_pnl   = df_sorted['pnl'].cumsum()
        equity    = initial_capital + cum_pnl
        peak      = equity.cummax()
        drawdown  = (equity - peak) / peak
        max_dd    = float(drawdown.min())

        # Daily returns approximation from equity curve
        eq_vals   = equity.values
        daily_ret = np.diff(eq_vals) / eq_vals[:-1] if len(eq_vals) > 1 else np.array([0.0])

        # Sharpe
        excess = daily_ret - _RISK_FREE_RATE / _TRADING_DAYS
        sharpe = float(np.mean(excess) / np.std(excess) * math.sqrt(_TRADING_DAYS)) \
                 if np.std(excess) > 0 else 0.0

        # Sortino
        downside = daily_ret[daily_ret < 0]
        sortino  = float(np.mean(excess) / np.std(downside) * math.sqrt(_TRADING_DAYS)) \
                   if len(downside) > 1 and np.std(downside) > 0 else 0.0

        # Profit factor
        gross_win  = float(wins['pnl'].sum())    if len(wins)   else 0.0
        gross_loss = float(abs(losses['pnl'].sum())) if len(losses) else 0.0
        profit_factor = (gross_win / gross_loss) if gross_loss > 0 else (999.0 if gross_win > 0 else 0.0)  # 999 = inf (JSON-safe)

        # Avg win / loss
        avg_win  = float(wins['pnl_pct'].mean())   if len(wins)   else 0.0
        avg_loss = float(losses['pnl_pct'].mean())  if len(losses) else 0.0
        avg_hold = float(df['hold_days'].mean())

        # ── Breakdowns ──────────────────────────────────────────────────────

        def _bucket_stats(sub: pd.DataFrame) -> Dict:
            if sub.empty:
                return {}
            w = sub[sub['pnl'] > 0]
            return {
                'trades':      int(len(sub)),
                'win_rate':    round(len(w) / len(sub), 4),
                'avg_pnl_pct': round(float(sub['pnl_pct'].mean()), 4),
                'total_pnl':   round(float(sub['pnl'].sum()), 2),
            }

        by_regime = {
            r: _bucket_stats(df[df['regime'] == r])
            for r in df['regime'].unique()
        }

        by_exit = {
            r: _bucket_stats(df[df['exit_reason'] == r])
            for r in df['exit_reason'].unique()
        }

        # Confidence buckets: 0-0.65, 0.65-0.70, 0.70-0.75, 0.75+
        def _conf_bucket(c):
            if c < 0.65: return '60-65%'
            if c < 0.70: return '65-70%'
            if c < 0.75: return '70-75%'
            return '75%+'
        df['conf_bucket'] = df['confidence'].apply(_conf_bucket)
        by_confidence = {
            b: _bucket_stats(df[df['conf_bucket'] == b])
            for b in ['60-65%', '65-70%', '70-75%', '75%+']
            if b in df['conf_bucket'].values
        }

        # Monthly returns
        df['exit_month'] = df['exit_date'].apply(lambda d: d.strftime('%Y-%m'))
        by_month = df.groupby('exit_month')['pnl'].sum().round(2).to_dict()

        # Equity curve — daily series interpolated between trade exits
        # Build a dict: exit_date -> cumulative equity at that point
        exit_equity = {
            str(row['exit_date'].date()): round(initial_capital + float(cum), 2)
            for cum, (_, row) in zip(cum_pnl, df_sorted.iterrows())
        }
        # Fill every calendar day from start to end, carrying forward last equity
        _eq_dates = pd.date_range(start=start, end=end, freq='B')  # business days
        _last_eq = initial_capital
        eq_curve = []
        for _d in _eq_dates:
            _ds = str(_d.date())
            if _ds in exit_equity:
                _last_eq = exit_equity[_ds]
            eq_curve.append({'date': _ds, 'equity': _last_eq})
        # Downsample to max 400 pts for chart performance
        if len(eq_curve) > 400:
            step = len(eq_curve) // 400
            eq_curve = eq_curve[::step]

        summary = {
            'symbols_tested':  int(df['symbol'].nunique()),
            'total_trades':    total_trades,
            'win_trades':      int(len(wins)),
            'loss_trades':     int(len(losses)),
            'win_rate':        round(win_rate, 4),
            'total_pnl':       round(total_pnl, 2),
            'initial_capital': round(initial_capital, 2),
            'final_capital':   round(final_capital, 2),
            'cagr':            round(cagr, 4),
            'max_drawdown':    round(max_dd, 4),
            'sharpe_ratio':    round(sharpe, 4),
            'sortino_ratio':   round(sortino, 4),
            'profit_factor':   round(profit_factor, 4),
            'avg_win_pct':     round(avg_win, 4),
            'avg_loss_pct':    round(avg_loss, 4),
            'avg_hold_days':   round(avg_hold, 1),
            'gross_win':       round(gross_win, 2),
            'gross_loss':      round(gross_loss, 2),
            'start_date':      start.strftime('%Y-%m-%d'),
            'end_date':        end.strftime('%Y-%m-%d'),
            'years':           round(years, 1),
        }

        trades_out = df[[
            'symbol', 'entry_date', 'exit_date',
            'entry_price', 'exit_price', 'qty',
            'pnl', 'pnl_pct', 'hold_days',
            'exit_reason', 'regime', 'confidence'
        ]].copy()
        trades_out['entry_date'] = trades_out['entry_date'].astype(str)
        trades_out['exit_date']  = trades_out['exit_date'].astype(str)
        trades_out['pnl']        = trades_out['pnl'].round(2)
        trades_out['pnl_pct']    = trades_out['pnl_pct'].round(4)

        return {
            'summary':        summary,
            'trades':         trades_out.to_dict(orient='records'),
            'equity_curve':   eq_curve,
            'by_regime':      by_regime,
            'by_exit_reason': by_exit,
            'by_confidence':  by_confidence,
            'by_month':       by_month,
        }


# ─── standalone runner ────────────────────────────────────────────────────────
if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO,
                        format='%(asctime)s %(levelname)s %(message)s')
    from config import config
    symbols = config.WATCHLIST[:10] if hasattr(config, 'WATCHLIST') else [
        'RELIANCE', 'INFY', 'TCS', 'HDFCBANK', 'ICICIBANK'
    ]
    bt = Backtester()
    result = bt.run(symbols=symbols, years=2)
    s = result.get('summary', {})
    print("\n" + "=" * 60)
    print("  BACKTEST RESULTS")
    print("=" * 60)
    if 'error' in s:
        print(f"  Error: {s['error']}")
    else:
        print(f"  Period:          {s['start_date']} → {s['end_date']} ({s['years']}y)")
        print(f"  Symbols tested:  {s['symbols_tested']}")
        print(f"  Total trades:    {s['total_trades']}  (W:{s['win_trades']} L:{s['loss_trades']})")
        print(f"  Win rate:        {s['win_rate']:.1%}")
        print(f"  CAGR:            {s['cagr']:.2%}")
        print(f"  Max drawdown:    {s['max_drawdown']:.2%}")
        print(f"  Sharpe ratio:    {s['sharpe_ratio']:.2f}")
        print(f"  Sortino ratio:   {s['sortino_ratio']:.2f}")
        print(f"  Profit factor:   {s['profit_factor']:.2f}")
        print(f"  Avg win:         {s['avg_win_pct']:.2%}")
        print(f"  Avg loss:        {s['avg_loss_pct']:.2%}")
        print(f"  Avg hold days:   {s['avg_hold_days']:.1f}")
        print(f"  Final capital:   ₹{s['final_capital']:,.0f}")
        print(f"  Total P&L:       ₹{s['total_pnl']:,.0f}")
        print("\n  By regime:")
        for r, v in result.get('by_regime', {}).items():
            print(f"    {r:10s}  trades={v['trades']}  win={v['win_rate']:.1%}  avg={v['avg_pnl_pct']:.2%}")
        print("\n  By confidence bucket:")
        for b, v in result.get('by_confidence', {}).items():
            print(f"    {b:8s}  trades={v['trades']}  win={v['win_rate']:.1%}  avg={v['avg_pnl_pct']:.2%}")
