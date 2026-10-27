"""
Strategy economics + backtester tests.

Covers: verified Angel charge model (per-order value, side-specific
statutory charges), backtester anti-lookahead, fill model, capacity
limits, chronological IS/OOS split, and the paper executor's new
realistic charge accounting.
"""
import os
import sys
import math
import json
import tempfile
from datetime import datetime, date, timedelta
from types import SimpleNamespace

import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from intraday.charges import (
    angel_intraday_charges, per_order_brokerage,
    BROKERAGE_MAX, BROKERAGE_MIN, STT_SELL_PCT, TXN_PCT,
    STAMP_BUY_PCT, SEBI_PCT, IPFT_PCT, GST_PCT,
)
from intraday.backtest import (
    BacktestConfig, IntradayBacktester, precompute_features,
    metrics, compare,
)
from intraday.models import (
    IntradayConfig, IntradayPosition, SignalDirection,
    SignalClassification, MarketRegime,
)


# ══════════════════════════════════════════════════════════════════
# Charge model
# ══════════════════════════════════════════════════════════════════

class TestChargeModel:

    def test_brokerage_floor(self):
        """Small orders pay the Rs 5 minimum, not 0.1%."""
        assert per_order_brokerage(100.0) == BROKERAGE_MIN
        assert per_order_brokerage(4000.0) == BROKERAGE_MIN  # 0.1% = 4 < 5

    def test_brokerage_cap(self):
        """Large orders are capped at Rs 20."""
        assert per_order_brokerage(1_000_000.0) == BROKERAGE_MAX

    def test_brokerage_linear_region(self):
        """Between floor and cap, brokerage is exactly 0.1%."""
        v = 10_000.0  # 0.1% = 10, inside [5, 20]
        assert per_order_brokerage(v) == pytest.approx(10.0)

    def test_stt_sell_side_only(self):
        c = angel_intraday_charges(buy_value=10_000.0, sell_value=10_000.0)
        assert c['stt'] == pytest.approx(STT_SELL_PCT * 10_000.0)
        # Doubling only the buy side must not change STT
        c2 = angel_intraday_charges(buy_value=20_000.0, sell_value=10_000.0)
        assert c2['stt'] == c['stt']

    def test_stamp_buy_side_only(self):
        c = angel_intraday_charges(buy_value=10_000.0, sell_value=10_000.0)
        assert c['stamp'] == pytest.approx(STAMP_BUY_PCT * 10_000.0)
        c2 = angel_intraday_charges(buy_value=10_000.0, sell_value=30_000.0)
        assert c2['stamp'] == c['stamp']

    def test_gst_base_excludes_stt_and_stamp(self):
        c = angel_intraday_charges(buy_value=10_000.0, sell_value=10_000.0)
        expected = GST_PCT * (c['brokerage'] + c['txn'] + c['sebi'])
        assert c['gst'] == pytest.approx(expected)

    def test_total_equals_component_sum(self):
        c = angel_intraday_charges(buy_value=9_500.0, sell_value=9_700.0)
        total = (c['brokerage'] + c['stt'] + c['txn'] + c['stamp']
                 + c['sebi'] + c['ipft'] + c['gst'])
        assert c['total'] == pytest.approx(total)

    def test_charges_scale_with_order_value(self):
        """Not a universal fixed fee: bigger notionals pay more txn/STT."""
        small = angel_intraday_charges(1_000.0, 1_000.0)['total']
        big = angel_intraday_charges(50_000.0, 50_000.0)['total']
        assert big > small

    def test_realistic_magnitude(self):
        """~Rs 9.5k round trip should cost roughly Rs 20-30, not paise."""
        c = angel_intraday_charges(9_500.0, 9_500.0)['total']
        assert 20.0 < c < 35.0


# ══════════════════════════════════════════════════════════════════
# Feature precompute — anti-lookahead
# ══════════════════════════════════════════════════════════════════

def _frame(day_strs, n_bars=40, base=100.0, drift=0.05):
    """Synthetic OHLCV frame over given sessions."""
    rows = []
    for d_i, d in enumerate(day_strs):
        start = datetime.fromisoformat(f"{d}T09:15:00")
        for i in range(n_bars):
            c = base + d_i * 10 + i * drift
            rows.append([start + timedelta(minutes=i),
                         c, c + 0.4, c - 0.4, c, 1000 + i])
    df = pd.DataFrame(rows, columns=['datetime', 'open', 'high', 'low',
                                     'close', 'volume'])
    df.set_index('datetime', inplace=True)
    return df


class TestPrecompute:

    def test_no_lookahead_future_candle_mutation(self):
        """Mutating a future candle must not change earlier features."""
        cfg = IntradayConfig()
        df = _frame(['2026-10-12', '2026-10-13'])
        f1 = precompute_features(df, cfg)
        df2 = df.copy()
        df2.iloc[-1, df2.columns.get_loc('close')] *= 3  # last bar
        f2 = precompute_features(df2, cfg)
        cutoff = df.index[-2]
        pd.testing.assert_frame_equal(
            f1[f1.index <= cutoff], f2[f2.index <= cutoff],
            check_dtype=False)

    def test_warmup_rows_are_nan(self):
        cfg = IntradayConfig()
        df = _frame(['2026-10-12'])
        f = precompute_features(df, cfg)
        # Rolling indicators are undefined before their window fills;
        # run_day skips such rows (NaN guard).
        assert math.isnan(f['rsi'].iloc[0])
        assert math.isnan(f['atr'].iloc[0])
        assert not math.isnan(f['ema_9'].iloc[0])

    def test_session_vwap_resets_daily(self):
        cfg = IntradayConfig()
        df = _frame(['2026-10-12', '2026-10-13'])
        f = precompute_features(df, cfg, session_vwap=True)
        day2_first = f[f.index.date == date(2026, 10, 13)].iloc[0]
        # Session VWAP on the first bar equals its typical price
        bar0 = df[df.index.date == date(2026, 10, 13)].iloc[0]
        tp0 = (bar0['high'] + bar0['low'] + bar0['close']) / 3
        assert day2_first['vwap'] == pytest.approx(tp0)

    def test_baseline_or_uses_oldest_window_session(self):
        """Baseline replicates production: OR = head(15) of the window's
        oldest session, not today's."""
        cfg = IntradayConfig()
        df = _frame(['2026-10-12', '2026-10-13'], n_bars=40)
        f = precompute_features(df, cfg, session_vwap=False)
        day1 = df[df.index.date == date(2026, 10, 12)]
        expected_or_high = day1['high'].iloc[:cfg.or_minutes].max()
        # Rows in day 2 (still within the 5-session window) keep day-1 OR
        day2_row = f[f.index.date == date(2026, 10, 13)].iloc[-1]
        assert day2_row['or_high'] == pytest.approx(expected_or_high)
        # Session-anchored variant uses day-2's own OR instead
        fs = precompute_features(df, cfg, session_vwap=True)
        day2_or = df[df.index.date == date(2026, 10, 13)]
        assert (fs[fs.index.date == date(2026, 10, 13)].iloc[-1]['or_high']
                == pytest.approx(day2_or['high'].iloc[:cfg.or_minutes].max()))

    def test_relative_volume_includes_current_bar(self):
        """Production divides by tail(20) mean including current bar."""
        cfg = IntradayConfig()
        df = _frame(['2026-10-12'], n_bars=40)
        f = precompute_features(df, cfg)
        i = 30
        expected = df['volume'].iloc[i] / df['volume'].iloc[i - 19:i + 1].mean()
        assert f['relative_volume'].iloc[i] == pytest.approx(expected)


# ══════════════════════════════════════════════════════════════════
# Replay engine — fills, exits, limits
# ══════════════════════════════════════════════════════════════════

def _feat_frame(ts_list, price=100.0, atr=2.0):
    """Minimal precomputed-looking frame for run_day."""
    rows = []
    for ts in ts_list:
        rows.append({'open': price, 'high': price + 1.5, 'low': price - 1.5,
                     'close': price, 'volume': 10_000, 'current_price': price,
                     'ema_9': price, 'ema_20': price - 1, 'rsi': 55.0,
                     'atr': atr, 'momentum': 0.6, 'relative_volume': 2.0,
                     'vwap': price - 0.5, 'or_high': price - 5, 'or_low': price - 10,
                     'ema_alignment': 'BULLISH', 'vwap_structure': 'ABOVE_VWAP'})
    return pd.DataFrame(rows, index=pd.DatetimeIndex(ts_list))


class _FakeScorer:
    """Returns a fixed LONG signal for every query."""

    def __init__(self, score=75.0, direction=SignalDirection.LONG):
        self.score, self.direction = score, direction

    def calculate_score(self, symbol_data, regime):
        return SimpleNamespace(
            symbol=symbol_data['symbol'], direction=self.direction,
            score=self.score, classification=SignalClassification.GOOD_SETUP,
            strategy='TEST', signal_id='sig-1')


class _FakeValidator:
    def validate_signal(self, signal):
        return True, ''

    def is_square_off_time(self):
        return False


class _FakeSizer:
    def __init__(self, qty=10):
        self.qty = qty

    def calculate_quantity(self, signal, capital):
        return self.qty, self.qty * signal.entry_price


def _bt(cfg=None, qty=10, score=75.0, direction=SignalDirection.LONG):
    cfg = cfg or BacktestConfig()
    bt = IntradayBacktester(cfg)
    bt.scorer = _FakeScorer(score, direction)
    bt.validator = _FakeValidator()
    bt.sizer = _FakeSizer(qty)
    return bt


class TestReplay:

    def _day(self, prices, start='2026-10-12T09:30:00'):
        ts = [datetime.fromisoformat(start) + timedelta(minutes=i)
              for i in range(len(prices))]
        return ts

    def test_long_stop_and_target(self):
        """LONG: bar touching SL exits at SL with slippage."""
        bt = _bt()
        # entry at close 100 -> SL = 100 - 0.5*2 = 99, target = 103
        bars = _feat_frame(self._day([100, 98.0]))  # bar2 low 96.5 < 99
        bars.iloc[1, bars.columns.get_loc('low')] = 96.0
        trades = bt.run_day(date(2026, 10, 12), {'AAA': bars}, None)
        assert len(trades) == 1
        t = trades[0]
        assert t['exit_reason'] == 'STOP_LOSS'
        assert t['exit'] < 99.0  # adverse slippage on marketable exit
        assert t['gross_pnl'] < 0

    def test_long_target_no_slippage(self):
        """LIMIT target pays only the modeled half-spread — no slippage."""
        bt = _bt()
        bars = _feat_frame(self._day([100, 100]))
        bars.iloc[1, bars.columns.get_loc('low')] = 102.5   # no SL touch
        bars.iloc[1, bars.columns.get_loc('high')] = 105.0  # > target 103
        trades = bt.run_day(date(2026, 10, 12), {'AAA': bars}, None)
        t = trades[0]
        assert t['exit_reason'] == 'TARGET_HIT'
        # target 103 less 5bps spread, zero slippage
        assert t['exit'] == pytest.approx(103.0 * (1 - 5e-4), rel=1e-6)
        assert t['gross_pnl'] > 0

    def test_both_hit_stop_wins(self):
        """Bar touching both SL and target: STOP assumed first."""
        bt = _bt()
        bars = _feat_frame(self._day([100, 100.0]))
        bars.iloc[1, bars.columns.get_loc('high')] = 104.0  # > target 103
        bars.iloc[1, bars.columns.get_loc('low')] = 98.0    # < SL 99
        trades = bt.run_day(date(2026, 10, 12), {'AAA': bars}, None)
        assert trades[0]['exit_reason'] == 'STOP_LOSS'

    def test_short_direction(self):
        bt = _bt(direction=SignalDirection.SHORT)
        bars = _feat_frame(self._day([100, 98.0]))  # SHORT SL=101, TP=97
        bars.iloc[1, bars.columns.get_loc('high')] = 102.0  # hits SL
        trades = bt.run_day(date(2026, 10, 12), {'AAA': bars}, None)
        t = trades[0]
        assert t['direction'] == 'SHORT'
        assert t['exit_reason'] == 'STOP_LOSS'
        assert t['exit'] > 101.0  # adverse slippage on buy-to-cover

    def test_max_entries_per_day(self):
        cfg = BacktestConfig(max_entries_per_day=1)
        bt = _bt(cfg)
        day_bars = {s: _feat_frame(self._day([100] * 5))
                    for s in ('AAA', 'BBB')}
        trades = bt.run_day(date(2026, 10, 12), day_bars, None)
        assert len(trades) == 1

    def test_max_concurrent_positions(self):
        cfg = BacktestConfig(max_concurrent=1, max_entries_per_day=5)
        bt = _bt(cfg)
        # bars that never touch SL or target -> position stays open
        def open_frame():
            f = _feat_frame(self._day([100] * 5))
            f['low'] = 99.5
            f['high'] = 100.5
            return f
        day_bars = {s: open_frame() for s in ('AAA', 'BBB')}
        trades = bt.run_day(date(2026, 10, 12), day_bars, None)
        # BBB can only enter after AAA exits — but AAA never exits until
        # EOD, so BBB never gets in.
        assert {t['symbol'] for t in trades} == {'AAA'}

    def test_no_reentry_same_symbol_same_day(self):
        cfg = BacktestConfig(max_entries_per_day=5, max_concurrent=2)
        bt = _bt(cfg)
        # bars: enter bar1, SL bar2 — symbol must not re-enter at bar3
        bars = _feat_frame(self._day([100, 98.0, 100, 98.0, 100]))
        bars.iloc[1, bars.columns.get_loc('low')] = 96.0
        trades = bt.run_day(date(2026, 10, 12), {'AAA': bars}, None)
        assert len(trades) == 1

    def test_eod_square_off(self):
        bt = _bt()
        ts = self._day([100] * 5)
        ts[-1] = ts[-1].replace(hour=15, minute=5)  # past 15:00
        bars = _feat_frame(ts)
        bars['low'] = 99.5   # keep position alive until the EOD bar
        bars['high'] = 100.5
        trades = bt.run_day(date(2026, 10, 12), {'AAA': bars}, None)
        assert trades[0]['exit_reason'] == 'EOD_SQUARE_OFF'
        assert trades[0]['exit'] != 100.0  # pays slippage at EOD

    def test_min_net_reward_gate(self):
        """Entries whose expected net at target is below the floor are skipped."""
        cfg = BacktestConfig(min_net_reward=200.0)  # unreachable w/ qty 10
        bt = _bt(cfg)
        bars = _feat_frame(self._day([100] * 5))
        trades = bt.run_day(date(2026, 10, 12), {'AAA': bars}, None)
        assert trades == []
        # And with a permissive floor the same signal enters
        cfg2 = BacktestConfig(min_net_reward=1.0)
        bt2 = _bt(cfg2)
        trades2 = bt2.run_day(date(2026, 10, 12), {'AAA': bars}, None)
        assert len(trades2) == 1

    def test_min_score_gate(self):
        cfg = BacktestConfig(min_score=80.0)
        bt = _bt(cfg, score=75.0)
        bars = _feat_frame(self._day([100] * 5))
        assert bt.run_day(date(2026, 10, 12), {'AAA': bars}, None) == []

    def test_spread_and_slippage_applied_to_entry(self):
        cfg = BacktestConfig(spread_bps=10.0, slippage_bps=10.0)
        bt = _bt(cfg)
        bars = _feat_frame(self._day([100] * 5))
        trades = bt.run_day(date(2026, 10, 12), {'AAA': bars}, None)
        # LONG entry = close + (10+10)bps = 100 + 0.2
        assert trades[0]['entry'] == pytest.approx(100.2)

    def test_zero_qty_blocks_entry(self):
        bt = _bt(qty=0)
        bars = _feat_frame(self._day([100] * 5))
        assert bt.run_day(date(2026, 10, 12), {'AAA': bars}, None) == []


class TestMetrics:

    def test_metrics_fields(self):
        trades = [
            {'net_pnl': 100.0, 'gross_pnl': 120.0, 'charges': 20.0},
            {'net_pnl': -50.0, 'gross_pnl': -30.0, 'charges': 20.0},
            {'net_pnl': -30.0, 'gross_pnl': -10.0, 'charges': 20.0},
        ]
        m = metrics(trades)
        assert m['trades'] == 3
        assert m['win_rate'] == pytest.approx(33.3)
        assert m['net_pnl'] == pytest.approx(20.0)
        assert m['costs'] == pytest.approx(60.0)
        assert m['expectancy'] == pytest.approx(20.0 / 3, abs=0.01)
        assert m['profit_factor'] == pytest.approx(100.0 / 80.0, abs=0.01)
        assert m['max_drawdown'] == pytest.approx(80.0)

    def test_empty(self):
        assert metrics([]) == {'trades': 0}


class TestChronologicalSplit:

    def test_compare_splits_days_in_order(self, tmp_path):
        """compare() must never put later sessions in-sample."""
        # 5 sessions of flat data across two symbols
        for sym in ('AAA', 'BBB'):
            rows = []
            for d in range(5):
                day = date(2026, 10, 12) + timedelta(days=d)
                for i in range(60):
                    ts = datetime.combine(day, datetime.min.time()).replace(hour=9, minute=15) + timedelta(minutes=i)
                    rows.append([ts.isoformat(), 100.0, 100.5, 99.5, 100.0, 1000])
            json.dump(rows, open(tmp_path / f'{sym}.json', 'w'))
        out = compare(str(tmp_path), ['AAA', 'BBB'],
                      configs=[BacktestConfig()], oos_fraction=0.4)
        assert out['is_days'] == ['2026-10-12', '2026-10-13', '2026-10-14']
        assert out['oos_days'] == ['2026-10-15', '2026-10-16']
        assert max(out['is_days']) < min(out['oos_days'])


# ══════════════════════════════════════════════════════════════════
# Paper executor — realistic charge accounting on new fills
# ══════════════════════════════════════════════════════════════════

class TestPaperExecutorCharges:

    def _executor(self, tmp_path):
        from intraday.risk_manager import IntradayRiskManager
        from intraday.position_sizer import IntradayPositionSizer
        from intraday.paper_executor import PaperTradeExecutor
        from intraday.state import IntradayStateStore
        cfg = IntradayConfig()
        rm = IntradayRiskManager(cfg, IntradayStateStore(str(tmp_path)))
        return PaperTradeExecutor(rm, IntradayPositionSizer(cfg), cfg), cfg

    def _position(self, direction):
        return IntradayPosition(
            id='p1', symbol='AAA', direction=direction,
            entry_price=100.0, quantity=50,
            stop_loss=99.0, target=103.0,
            entry_time=datetime.now(), current_price=100.0,
            unrealized_pnl=0.0, unrealized_pnl_pct=0.0, is_paper=True)

    def test_long_exit_uses_verified_schedule(self, tmp_path):
        ex, _ = self._executor(tmp_path)
        pos = self._position(SignalDirection.LONG)
        trade = ex.execute_exit(pos, 103.0, 'TARGET_HIT')
        expected = angel_intraday_charges(100.0 * 50, 103.0 * 50)['total']
        assert trade.charges == pytest.approx(expected, abs=0.01)
        assert trade.net_pnl == pytest.approx(
            (103.0 - 100.0) * 50 - expected, abs=0.01)

    def test_short_exit_buy_sell_sides_inverted(self, tmp_path):
        ex, _ = self._executor(tmp_path)
        pos = self._position(SignalDirection.SHORT)
        trade = ex.execute_exit(pos, 97.0, 'TARGET_HIT')
        # SHORT: sold at entry (STT/stamp-free side swap)
        expected = angel_intraday_charges(97.0 * 50, 100.0 * 50)['total']
        assert trade.charges == pytest.approx(expected, abs=0.01)
        assert trade.net_pnl == pytest.approx(
            (100.0 - 97.0) * 50 - expected, abs=0.01)

    def test_charges_are_realistic_not_paise(self, tmp_path):
        """Regression: charges must be ~Rs 20+ per round trip, not 0.1% of P&L."""
        ex, _ = self._executor(tmp_path)
        pos = self._position(SignalDirection.LONG)
        trade = ex.execute_exit(pos, 103.0, 'TARGET_HIT')
        assert trade.charges > 10.0  # ~Rs 14 on a Rs 10k round trip

    def test_paper_flag_preserved(self, tmp_path):
        ex, _ = self._executor(tmp_path)
        pos = self._position(SignalDirection.LONG)
        trade = ex.execute_exit(pos, 103.0, 'TARGET_HIT')
        assert trade.is_paper is True
