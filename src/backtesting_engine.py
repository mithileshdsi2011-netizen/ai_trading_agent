"""
Enterprise Backtesting & Strategy Validation Framework.

Simulates complete trading flow, computes institutional-grade metrics,
supports walk-forward validation, Monte Carlo simulation and strategy comparison.
"""
import json
import logging
import math
import random
from copy import deepcopy
from datetime import datetime, date, timedelta
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from config import config

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

_RISK_FREE_RATE = 0.065  # 6.5% annual
_TRADING_DAYS = 252


def _safe_div(a: float, b: float) -> float:
    if b == 0 or not np.isfinite(b):
        return 0.0
    return a / b


class EnterpriseBacktestEngine:
    """
    Institutional-grade backtest engine.

    The engine takes a strategy callable (decision_fn) and price data and replays
    the full trading flow. It can be supplied with the real Enterprise AI Decision,
    Risk, Portfolio Optimizer and Trade Lifecycle objects, or with lightweight
    callables for unit testing and research.
    """

    def __init__(
        self,
        initial_capital: float = 15000.0,
        commission_pct: float = 0.0003,
        slippage_pct: float = 0.0005,
        store=None,
        decision_engine=None,
        risk_engine=None,
        portfolio_optimizer=None,
        trade_lifecycle=None,
    ):
        self.initial_capital = float(initial_capital)
        self.commission_pct = float(commission_pct)
        self.slippage_pct = float(slippage_pct)
        self.store = store
        self.decision_engine = decision_engine
        self.risk_engine = risk_engine
        self.portfolio_optimizer = portfolio_optimizer
        self.trade_lifecycle = trade_lifecycle
        self.risk_free_daily = _RISK_FREE_RATE / _TRADING_DAYS

    # ── Core Simulation ───────────────────────────────────────────────────

    def _execute_price(self, price: float, side: str) -> float:
        slip = price * self.slippage_pct
        return price + slip if side == 'BUY' else price - slip

    def _transaction_cost(self, value: float) -> float:
        return value * (self.commission_pct + self.slippage_pct)

    def run(
        self,
        price_data: Dict[str, pd.DataFrame],
        strategy: Optional[Callable] = None,
        name: str = 'current',
        meta: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Run a backtest on the supplied price data.

        `price_data` maps symbol to a DataFrame indexed by date with OHLCV columns.
        `strategy` is a callable `strategy(symbol, current_price, hist, context) -> action`
        where action is one of 'BUY', 'SELL', 'HOLD' or a dict with extra fields.
        """
        if strategy is None:
            strategy = self._default_strategy

        cash = self.initial_capital
        positions: Dict[str, Dict[str, Any]] = {}
        equity_curve: List[Dict[str, Any]] = []
        trades: List[Dict[str, Any]] = []
        dates = sorted({d for df in price_data.values() for d in df.index})

        for current_date in dates:
            day_open = False
            total_value = cash

            # update P&L and check exits first
            for sym, pos in list(positions.items()):
                if current_date not in price_data[sym].index:
                    continue
                close = float(price_data[sym].loc[current_date, 'Close'])
                pos['mark'] = close
                pos_value = pos['quantity'] * close
                total_value += pos_value

                decision = strategy(sym, close, price_data[sym].loc[:current_date], {
                    'date': current_date,
                    'position': pos,
                    'cash': cash,
                    'portfolio_value': total_value,
                })

                action = decision if isinstance(decision, str) else (decision or {}).get('action', 'HOLD')
                if action in ('SELL', 'SELL_ALL'):
                    sell_px = self._execute_price(close, 'SELL')
                    gross = pos['quantity'] * sell_px
                    cost = self._transaction_cost(gross)
                    net = gross - cost
                    pnl = net - pos['cost']
                    cash += net
                    trades.append({
                        'symbol': sym,
                        'entry_date': pos['entry_date'],
                        'exit_date': current_date.isoformat() if hasattr(current_date, 'isoformat') else str(current_date),
                        'entry_price': pos['entry_price'],
                        'exit_price': sell_px,
                        'quantity': pos['quantity'],
                        'gross_pnl': pnl,
                        'costs': cost,
                        'net_pnl': pnl,
                    })
                    del positions[sym]
                    day_open = True

            # check new entries
            for sym, df in price_data.items():
                if sym in positions:
                    continue
                if current_date not in df.index:
                    continue
                close = float(df.loc[current_date, 'Close'])
                decision = strategy(sym, close, df.loc[:current_date], {
                    'date': current_date,
                    'cash': cash,
                    'portfolio_value': total_value,
                })
                action = decision if isinstance(decision, str) else (decision or {}).get('action', 'HOLD')
                if action == 'BUY':
                    qty = 1
                    if isinstance(decision, dict) and 'quantity' in decision:
                        qty = int(decision['quantity'])
                    buy_px = self._execute_price(close, 'BUY')
                    gross = qty * buy_px
                    if gross > cash:
                        continue
                    cost = self._transaction_cost(gross)
                    cash -= (gross + cost)
                    positions[sym] = {
                        'symbol': sym,
                        'quantity': qty,
                        'entry_price': buy_px,
                        'entry_date': current_date.isoformat() if hasattr(current_date, 'isoformat') else str(current_date),
                        'cost': gross + cost,
                    }
                    day_open = True

            # record daily equity
            pv = cash
            for sym, pos in positions.items():
                if current_date in price_data[sym].index:
                    pv += pos['quantity'] * float(price_data[sym].loc[current_date, 'Close'])
            equity_curve.append({'date': str(current_date), 'equity': round(pv, 2), 'cash': round(cash, 2)})

        # close any remaining positions at the last available price
        if positions:
            for sym, pos in positions.items():
                last = price_data[sym].iloc[-1]
                sell_px = self._execute_price(float(last['Close']), 'SELL')
                gross = pos['quantity'] * sell_px
                cost = self._transaction_cost(gross)
                net = gross - cost
                pnl = net - pos['cost']
                cash += net
                trades.append({
                    'symbol': sym,
                    'entry_date': pos['entry_date'],
                    'exit_date': str(price_data[sym].index[-1]),
                    'entry_price': pos['entry_price'],
                    'exit_price': sell_px,
                    'quantity': pos['quantity'],
                    'gross_pnl': pnl,
                    'costs': cost,
                    'net_pnl': pnl,
                })
            positions.clear()

        result = self._compute_result(equity_curve, trades, name, meta)
        return result

    def _default_strategy(self, symbol: str, price: float, hist: pd.DataFrame, context: Dict) -> str:
        """Default naive momentum strategy for stand-alone usage."""
        if len(hist) < 20:
            return 'HOLD'
        sma20 = hist['Close'].rolling(20).mean().iloc[-1]
        sma50 = hist['Close'].rolling(50).mean().iloc[-1]
        if pd.isna(sma20) or pd.isna(sma50):
            return 'HOLD'
        return 'BUY' if price > sma20 > sma50 else 'SELL' if price < sma20 else 'HOLD'

    # ── Metrics ───────────────────────────────────────────────────────────

    def _compute_result(
        self,
        equity_curve: List[Dict[str, Any]],
        trades: List[Dict[str, Any]],
        name: str,
        meta: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        if not equity_curve:
            return self._empty_result(name)

        equity = pd.Series([e['equity'] for e in equity_curve],
                           index=pd.to_datetime([e['date'] for e in equity_curve]))
        returns = equity.pct_change().dropna()

        total_return = (equity.iloc[-1] - self.initial_capital) / self.initial_capital
        years = max(len(returns) / _TRADING_DAYS, 1 / _TRADING_DAYS)
        cagr = (equity.iloc[-1] / self.initial_capital) ** (1 / years) - 1

        # Drawdown
        running_max = equity.cummax()
        drawdown = (equity - running_max) / running_max
        max_drawdown = float(drawdown.min())

        # Risk-adjusted
        excess = returns - self.risk_free_daily
        sharpe = _safe_div(excess.mean(), returns.std()) * math.sqrt(_TRADING_DAYS)
        downside = returns[returns < 0]
        sortino = _safe_div(excess.mean(), downside.std()) * math.sqrt(_TRADING_DAYS)
        calmar = _safe_div(cagr, abs(max_drawdown))

        # Trade-level
        wins = [t['net_pnl'] for t in trades if t['net_pnl'] > 0]
        losses = [t['net_pnl'] for t in trades if t['net_pnl'] <= 0]
        win_rate = _safe_div(len(wins), len(trades)) * 100 if trades else 0
        profit_factor = _safe_div(sum(wins), abs(sum(losses)))
        avg_win = np.mean(wins) if wins else 0
        avg_loss = np.mean(losses) if losses else 0
        expectancy = _safe_div(sum(t['net_pnl'] for t in trades), len(trades))

        # Holding period
        holding_days = []
        for t in trades:
            try:
                ed = pd.to_datetime(t['entry_date'])
                xd = pd.to_datetime(t['exit_date'])
                holding_days.append((xd - ed).days)
            except Exception:
                pass
        avg_holding = np.mean(holding_days) if holding_days else 0

        # Monthly returns
        monthly = equity.resample('ME').last().pct_change().dropna().round(4).to_dict()
        monthly = {str(k): float(v) for k, v in monthly.items()}

        return {
            'name': name,
            'initial_capital': round(self.initial_capital, 2),
            'final_equity': round(equity.iloc[-1], 2),
            'total_return_pct': round(total_return * 100, 2),
            'cagr_pct': round(cagr * 100, 2),
            'sharpe': round(sharpe, 2),
            'sortino': round(sortino, 2),
            'calmar': round(calmar, 2),
            'max_drawdown_pct': round(max_drawdown * 100, 2),
            'win_rate_pct': round(win_rate, 2),
            'profit_factor': round(profit_factor, 2),
            'avg_win': round(float(avg_win), 2),
            'avg_loss': round(float(avg_loss), 2),
            'expectancy': round(float(expectancy), 2),
            'avg_holding_days': round(float(avg_holding), 2),
            'trades_count': len(trades),
            'equity_curve': equity_curve,
            'drawdown_curve': [{'date': str(i), 'drawdown_pct': round(float(v) * 100, 2)} for i, v in drawdown.items()],
            'monthly_returns': monthly,
            'trades': trades,
            'meta': meta or {},
            'timestamp': datetime.now().isoformat(),
        }

    def _empty_result(self, name: str) -> Dict[str, Any]:
        return {
            'name': name,
            'initial_capital': round(self.initial_capital, 2),
            'final_equity': round(self.initial_capital, 2),
            'total_return_pct': 0.0,
            'cagr_pct': 0.0,
            'sharpe': 0.0,
            'sortino': 0.0,
            'calmar': 0.0,
            'max_drawdown_pct': 0.0,
            'win_rate_pct': 0.0,
            'profit_factor': 0.0,
            'avg_win': 0.0,
            'avg_loss': 0.0,
            'expectancy': 0.0,
            'avg_holding_days': 0.0,
            'trades_count': 0,
            'equity_curve': [],
            'drawdown_curve': [],
            'monthly_returns': {},
            'trades': [],
            'meta': {},
            'timestamp': datetime.now().isoformat(),
        }

    # ── Walk-Forward Testing ─────────────────────────────────────────────

    def walk_forward(
        self,
        price_data: Dict[str, pd.DataFrame],
        strategy: Optional[Callable] = None,
        train_size: int = 90,
        test_size: int = 30,
    ) -> List[Dict[str, Any]]:
        """Rolling train/test windows. If `strategy` has a `fit` method, it is called on the train window."""
        if strategy is None:
            strategy = self._default_strategy

        dates = sorted({d for df in price_data.values() for d in df.index})
        if len(dates) < train_size + test_size:
            return []

        results = []
        for start in range(0, len(dates) - train_size - test_size + 1, test_size):
            train_dates = dates[start:start + train_size]
            test_dates = dates[start + train_size:start + train_size + test_size]

            train_data = {sym: df.loc[df.index.isin(train_dates)] for sym, df in price_data.items()}
            test_data = {sym: df.loc[df.index.isin(test_dates)] for sym, df in price_data.items()}

            if hasattr(strategy, 'fit'):
                strategy.fit(train_data)

            res = self.run(test_data, strategy=strategy, name=f'wf_{start}', meta={'train_start': str(train_dates[0]), 'test_start': str(test_dates[0])})
            results.append(res)

        return results

    # ── Monte Carlo Simulation ──────────────────────────────────────────

    def monte_carlo(
        self,
        equity_curve: List[Dict[str, Any]],
        n_simulations: int = 1000,
    ) -> Dict[str, Any]:
        """Resample historical returns to generate confidence bands."""
        if not equity_curve or len(equity_curve) < 2:
            return self._empty_monte_carlo()

        equity = pd.Series([e['equity'] for e in equity_curve])
        returns = equity.pct_change().dropna().values
        if len(returns) < 2:
            return self._empty_monte_carlo()

        final_pnl = []
        worst_dd = []
        for _ in range(n_simulations):
            sampled = np.random.choice(returns, size=len(returns), replace=True)
            path = self.initial_capital * np.cumprod(1 + sampled)
            final_pnl.append(path[-1] - self.initial_capital)
            dd = (path - np.maximum.accumulate(path)) / np.maximum.accumulate(path)
            worst_dd.append(np.min(dd))

        final_arr = np.array(final_pnl)
        dd_arr = np.array(worst_dd)

        def percentile(p):
            return float(np.percentile(final_arr, p))

        return {
            'n_simulations': n_simulations,
            'probability_of_profit': round(float(np.mean(final_arr > 0) * 100), 2),
            'worst_drawdown_pct': round(float(np.percentile(dd_arr, 5) * 100), 2),
            'best_drawdown_pct': round(float(np.percentile(dd_arr, 95) * 100), 2),
            'mean_final_pnl': round(float(np.mean(final_arr)), 2),
            'ci_5_final_pnl': round(percentile(5), 2),
            'ci_95_final_pnl': round(percentile(95), 2),
            'timestamp': datetime.now().isoformat(),
        }

    def _empty_monte_carlo(self) -> Dict[str, Any]:
        return {
            'n_simulations': 0,
            'probability_of_profit': 0.0,
            'worst_drawdown_pct': 0.0,
            'best_drawdown_pct': 0.0,
            'mean_final_pnl': 0.0,
            'ci_5_final_pnl': 0.0,
            'ci_95_final_pnl': 0.0,
            'timestamp': datetime.now().isoformat(),
        }

    # ── Strategy Comparison ─────────────────────────────────────────────

    def compare_strategies(
        self,
        price_data: Dict[str, pd.DataFrame],
        strategies: Dict[str, Callable],
        baseline: str = 'current',
    ) -> Dict[str, Any]:
        """Run multiple strategies on the same data and report improvements over baseline."""
        runs = {name: self.run(price_data, strategy=strategy, name=name) for name, strategy in strategies.items()}
        if baseline not in runs:
            return {'error': 'baseline not found'}

        base = runs[baseline]
        comparison = []
        for name, res in runs.items():
            row = {'strategy': name, 'metrics': res}
            if name != baseline:
                row['improvement_pct'] = self._improvement(base, res)
            else:
                row['improvement_pct'] = {}
            comparison.append(row)

        return {
            'baseline': baseline,
            'strategies': comparison,
            'timestamp': datetime.now().isoformat(),
        }

    @staticmethod
    def _improvement(base: Dict, other: Dict) -> Dict[str, float]:
        keys = ['total_return_pct', 'cagr_pct', 'sharpe', 'sortino', 'calmar', 'win_rate_pct', 'profit_factor']
        out = {}
        for k in keys:
            bv = base.get(k, 0)
            ov = other.get(k, 0)
            out[k] = round(_safe_div((ov - bv), abs(bv)) * 100 if bv != 0 else 0.0, 2)
        # drawdown is better when lower
        if base.get('max_drawdown_pct'):
            out['max_drawdown_pct'] = round(_safe_div((other.get('max_drawdown_pct', 0) - base['max_drawdown_pct']), abs(base['max_drawdown_pct'])) * 100, 2)
        return out

    # ── Persistence ─────────────────────────────────────────────────────

    def save_backtest(self, result: Dict[str, Any], store=None) -> None:
        s = store or self.store
        if s is None:
            return
        try:
            run_id = None
            if hasattr(s, 'save_backtest_run'):
                run_id = s.save_backtest_run({
                    'timestamp': result.get('timestamp'),
                    'name': result.get('name'),
                    'initial_capital': result.get('initial_capital'),
                    'final_equity': result.get('final_equity'),
                    'total_return_pct': result.get('total_return_pct'),
                    'trades_count': result.get('trades_count'),
                    'meta_json': json.dumps(result.get('meta', {})),
                })
            if hasattr(s, 'save_backtest_results'):
                s.save_backtest_results({
                    'backtest_run_id': run_id,
                    'timestamp': result.get('timestamp'),
                    'result_json': json.dumps(result),
                })
        except Exception as e:
            logger.warning(f'Backtest persistence failed: {e}')

    def save_walk_forward(self, results: List[Dict], store=None) -> None:
        s = store or self.store
        if s is None:
            return
        if not hasattr(s, 'save_walk_forward_results'):
            return
        try:
            for r in results:
                s.save_walk_forward_results({
                    'timestamp': r.get('timestamp'),
                    'name': r.get('name'),
                    'result_json': json.dumps(r),
                })
        except Exception as e:
            logger.warning(f'Walk-forward persistence failed: {e}')

    def save_monte_carlo(self, result: Dict, store=None) -> None:
        s = store or self.store
        if s is None:
            return
        if not hasattr(s, 'save_monte_carlo_results'):
            return
        try:
            s.save_monte_carlo_results({
                'timestamp': result.get('timestamp'),
                'result_json': json.dumps(result),
            })
        except Exception as e:
            logger.warning(f'Monte Carlo persistence failed: {e}')

    def get_latest_backtest(self, store=None) -> Optional[Dict[str, Any]]:
        s = store or self.store
        if s is None or not hasattr(s, 'get_latest_backtest_results'):
            return None
        try:
            return s.get_latest_backtest_results()
        except Exception:
            return None
