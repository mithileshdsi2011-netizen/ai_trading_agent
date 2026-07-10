#!/usr/bin/env python3
"""
Phase 14 - End-to-End Trading Simulation
Exercises the full pipeline in paper mode with synthetic market data.
"""
import os
import sys
import json
import time
import pandas as pd
import numpy as np
from datetime import datetime, timedelta, date
from unittest.mock import patch, MagicMock

# Ensure src imports work
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from config import config
config.PAPER_TRADING = True
config.TRADING_MODE = "swing"
config.LOG_LEVEL = "INFO"
config.EMAIL_ENABLED = False

from token_manager import TokenManager
from broker_integration import BrokerIntegration
from market_data import MarketDataFetcher
from risk_manager import RiskManager, PositionStatus
from order_executor import OrderExecutor
from ai_research_agent import AIResearchAgent
from signal_generator import SignalGenerator
from technical_analysis import TechnicalAnalyzer
from sentiment_analysis import SentimentAnalyzer
from smart_exit import SmartExitAI
from trade_journal import TradeJournal
from trading_orchestrator import TradingOrchestrator
from telegram_alerts import TelegramAlerter


def make_synthetic_history(symbol: str, days: int = 90, trend: str = "up") -> pd.DataFrame:
    """Create a synthetic OHLCV DataFrame that produces a BUY signal."""
    np.random.seed(42 if symbol == "RELIANCE" else 43)
    dates = pd.date_range(end=datetime.now(), periods=days, freq='D')
    base = 1000.0
    closes = []
    for i in range(days):
        # Upward drift + noise to keep price above moving averages
        ret = 0.0015 + np.random.normal(0, 0.008)
        base *= (1 + ret)
        closes.append(base)
    closes = np.array(closes)
    highs = closes * (1 + np.random.uniform(0.005, 0.015, days))
    lows = closes * (1 - np.random.uniform(0.005, 0.015, days))
    opens = lows + np.random.uniform(0, 1, days) * (highs - lows)
    volumes = np.random.randint(1_000_000, 5_000_000, days)
    df = pd.DataFrame({
        'Open': opens,
        'High': highs,
        'Low': lows,
        'Close': closes,
        'Volume': volumes,
    }, index=dates)
    return df


# Price schedule per cycle (index advances each monitor cycle)
PRICE_SCHEDULE = {
    'RELIANCE': [1000.0, 1000.0, 1055.0, 1090.0, 1080.0, 1045.0],
    'TCS':      [1000.0, 1000.0, 1055.0, 1090.0, 1105.0, 1105.0],
}
cycle_index = [0]


def monkey_patch_market_data():
    """Replace MarketDataFetcher methods with synthetic implementations."""
    def synthetic_init(self, kite=None):
        self.cache = {}
        self.cache_duration = timedelta(minutes=5)
        self.kite = None  # never use real Kite in simulation

    MarketDataFetcher.__init__ = synthetic_init
    MarketDataFetcher.is_market_open = lambda self: True
    MarketDataFetcher.is_trading_day = lambda self: True
    MarketDataFetcher.get_stock_data = lambda self, symbol, period="3mo", interval="1d": make_synthetic_history(symbol)
    MarketDataFetcher.get_intraday_data = lambda self, symbol, days=5: pd.DataFrame()
    MarketDataFetcher.get_realtime_price = lambda self, symbol: PRICE_SCHEDULE.get(symbol, [1000.0])[cycle_index[0]]
    MarketDataFetcher.get_stock_info = lambda self, symbol: {
        'symbol': symbol,
        'last_price': PRICE_SCHEDULE.get(symbol, [1000.0])[cycle_index[0]]
    }


def monkey_patch_sentiment():
    """Return neutral-positive sentiment without external API calls."""
    SentimentAnalyzer.get_market_sentiment = lambda self, symbol: {
        'score': 0.2,
        'sentiment': 'neutral-positive',
        'news_count': 3,
        'news_items': []
    }


def monkey_patch_ai_research():
    """Force a strong BUY recommendation so the simulation exercises execution."""
    def fake_research(self, symbol: str) -> dict:
        from datetime import datetime
        return {
            'symbol': symbol,
            'timestamp': datetime.now().isoformat(),
            'market_data': {'info': {'symbol': symbol, 'last_price': 1000.0}},
            'technical_analysis': {
                'technical_score': 0.65,
                'trend': 'UPTREND',
                'signal': 'BUY',
                'confidence': 0.85,
                'support': 950.0,
                'resistance': 1150.0,
                'reason': 'Mocked bullish trend',
                'rsi': 60.0,
                'macd_histogram': 0.5,
                'macd_histogram_prev': 0.3,
                'volume_ratio': 2.5,
            },
            'sentiment_analysis': {
                'score': 0.2,
                'sentiment': 'neutral-positive',
                'news_count': 3,
                'news_items': []
            },
            'overall_score': 0.70,
            'recommendation': 'STRONG_BUY',
            'confidence': 0.85,
            'reasoning': 'Mocked bullish technical setup',
            'sector_momentum': 0.3,
        }
    AIResearchAgent.research_stock = fake_research


def run_simulation():
    print("=" * 70)
    print("Phase 14 - End-to-End Trading Simulation")
    print("=" * 70)

    # --- Setup paths / reset state ---
    data_dir = os.path.join(os.path.dirname(__file__), 'data')
    os.makedirs(data_dir, exist_ok=True)
    for fname in ['positions.json', 'trade_journal.json', 'peak_value.json']:
        fpath = os.path.join(data_dir, fname)
        if os.path.exists(fpath):
            os.remove(fpath)

    # --- Patch external dependencies ---
    monkey_patch_market_data()
    monkey_patch_sentiment()
    monkey_patch_ai_research()
    TelegramAlerter.buy = lambda *a, **kw: None
    TelegramAlerter.sell = lambda *a, **kw: None
    TelegramAlerter.exit = lambda *a, **kw: None
    TelegramAlerter._send = lambda *a, **kw: None

    # Initialize orchestrator (paper mode)
    orchestrator = TradingOrchestrator()
    broker = orchestrator.order_executor.broker
    # Force paper and reset portfolio
    broker.paper_portfolio['cash'] = config.TRADING_AMOUNT
    broker.paper_portfolio['positions'] = {}
    broker.paper_portfolio['orders'] = []

    # 1. Market opens
    print("\n[1] Market opens (mocked)")

    # 2. Build watchlist
    watchlist = ["RELIANCE", "TCS"]
    print(f"[2] Watchlist: {watchlist}")

    # 3-4. AI research + signal generation
    print("\n[3-4] AI research and signal generation...")
    signals = orchestrator.signal_generator.generate_signals_for_watchlist(watchlist)
    buy_signals = [s for s in signals if s.get('action') == 'BUY']
    print(f"    Generated {len(buy_signals)} BUY signals")
    for s in buy_signals:
        print(f"    - {s['symbol']}: price={s['current_price']:.2f}, "
              f"qty={s['position_size']}, SL={s['stop_loss']:.2f}, "
              f"target={s['target']:.2f}")

    # 5. Execute BUY orders
    print("\n[5] Executing BUY orders...")
    for sig in buy_signals:
        res = orchestrator.order_executor.execute_signal(sig)
        print(f"    {sig['symbol']} BUY: success={res['success']}, "
              f"order_id={res.get('order_id')}, error={res.get('error')}")
        orchestrator._append_trade_log(res)

    # 6. Simulate monitoring cycles
    print("\n[6] Monitoring positions over cycles...")
    open_symbols = {p.symbol for p in orchestrator.order_executor.risk_manager.positions
                    if p.status in {PositionStatus.OPEN, PositionStatus.PARTIAL}}
    for i in range(1, 6):
        cycle_index[0] = i
        print(f"\n--- Cycle {i} (prices: REL={PRICE_SCHEDULE['RELIANCE'][i]:.2f}, "
              f"TCS={PRICE_SCHEDULE['TCS'][i]:.2f}) ---")

        # Monitor positions (SL/target/partial/trailing)
        updates = orchestrator.order_executor.monitor_positions()
        for u in updates:
            es = u.get('exit_signal', {})
            print(f"    Position monitor: {es.get('symbol')} "
                  f"action={es.get('action')} reason={es.get('reason')} "
                  f"partial={es.get('partial')} pnl={es.get('pnl', 0):.2f}")
            if u.get('success') and not es.get('partial'):
                orchestrator._record_exit(
                    es['symbol'], es.get('price', 0), es.get('reason', 'sl_target'),
                    pnl=es.get('pnl', 0)
                )

        # Smart exit check
        open_positions = [p for p in orchestrator.order_executor.risk_manager.positions
                          if p.status in {PositionStatus.OPEN, PositionStatus.PARTIAL}]
        prices = {sym: PRICE_SCHEDULE[sym][i] for sym in open_symbols}
        smart_exits = orchestrator.smart_exit.check_all(open_positions, prices, regime='SIDEWAYS')
        for se in smart_exits:
            print(f"    Smart exit triggered: {se['symbol']} - {se['reason']}")

    # 7. End-of-day close
    print("\n[7] End-of-day close...")
    close_results = orchestrator.order_executor.close_all_positions()
    for cr in close_results:
        print(f"    EOD close: {cr['success']} order_id={cr.get('order_id')}")

    # 8. Update analytics / P&L
    print("\n[8] Analytics / P&L summary...")
    summary = orchestrator.order_executor.risk_manager.get_position_summary()
    print(f"    Open positions: {summary['open_positions']}")
    print(f"    Closed positions: {summary['closed_positions']}")
    print(f"    Total gross P&L: {summary['total_pnl']:.2f}")
    print(f"    Total charges: {summary['total_charges']:.2f}")
    print(f"    Total net P&L: {summary['total_net_pnl']:.2f}")
    print(f"    Daily P&L: {orchestrator.order_executor.risk_manager.daily_pnl:.2f}")

    # 9. Verify state
    print("\n[9] Verifications...")
    positions = orchestrator.order_executor.risk_manager.positions
    stale = [p for p in positions if p.status in {PositionStatus.OPEN, PositionStatus.PARTIAL}]
    print(f"    Stale open/partial positions: {len(stale)}")

    journal_path = os.path.join(data_dir, 'trade_journal.json')
    journal_entries = []
    if os.path.exists(journal_path):
        with open(journal_path) as f:
            journal_entries = json.load(f)
    print(f"    Journal entries: {len(journal_entries)}")

    buy_entries = [e for e in journal_entries if e.get('action') == 'BUY']
    sell_entries = [e for e in journal_entries if e.get('action') == 'SELL']
    print(f"    BUY journal entries: {len(buy_entries)}")
    print(f"    SELL journal entries: {len(sell_entries)}")

    # Check paper portfolio cash consistency (paper cash is gross; charges are tracked separately)
    cash = broker.paper_portfolio['cash']
    positions_value = sum(
        p['quantity'] * p['entry_price']
        for p in broker.paper_portfolio['positions'].values()
    )
    total_paper_value = cash + positions_value
    expected_value = config.TRADING_AMOUNT + summary['total_pnl']
    value_drift = total_paper_value - expected_value
    print(f"    Paper cash: {cash:.2f}, positions value: {positions_value:.2f}")
    print(f"    Total paper value: {total_paper_value:.2f}")
    print(f"    Expected value (capital + gross P&L): {expected_value:.2f}")
    print(f"    Value drift: {value_drift:.2f}")

    # Check quantity conservation per symbol (partial + full exits should equal BUY quantity)
    quantity_errors = []
    for buy in buy_entries:
        sym = buy.get('symbol')
        buy_qty = buy.get('quantity', 0)
        sell_qty = sum(e.get('quantity', 0) for e in sell_entries if e.get('symbol') == sym)
        if buy_qty != sell_qty:
            quantity_errors.append(f"{sym}: bought {buy_qty}, sold {sell_qty}")
    print(f"    Quantity conservation errors: {quantity_errors}")

    # Final status
    checks = [
        (not stale, "no stale positions"),
        (abs(value_drift) < 0.01, f"paper value drift < 0.01 (drift={value_drift:.2f})"),
        (not quantity_errors, "quantity conservation"),
    ]
    failed = [desc for ok, desc in checks if not ok]
    print("\n" + "=" * 70)
    if not failed:
        print("SIMULATION PASSED")
    else:
        print("SIMULATION FAILED")
        for f in failed:
            print(f"  - {f}")
    print("=" * 70)


if __name__ == "__main__":
    run_simulation()
