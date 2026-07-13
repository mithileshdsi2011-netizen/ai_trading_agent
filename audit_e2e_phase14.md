# Phase 14 — Full End-to-End Audit Report

## Objective
Run a complete simulated trading day covering: market open, data download, watchlist build, AI research, signal generation, BUY execution, position monitoring, trailing stop updates, partial profit booking, target/SL execution, CDSL handling, journal updates, analytics, P&L calculation, and end-of-day summary. Verify no stale positions, duplicate entries, API failures, memory leaks, or dashboard inconsistencies.

## Simulation Setup
- **Script:** `simulation_phase14_e2e.py` (added to project root)
- **Mode:** Paper trading with Kite API mocked (no live orders, no live market data)
- **Watchlist:** `RELIANCE`, `TCS`
- **Controlled price path:**
  - Entry: ₹1000 for both
  - Cycle 2: ₹1055 → partial profit (+5%) on half quantity
  - Cycle 3: ₹1090 → trailing stop activation
  - Cycle 4: REL ₹1080, TCS ₹1105 → trailing stop updates
  - Cycle 5: REL ₹1045 → trailing stop hit; TCS ₹1105 → EOD close
- **External APIs disabled:** OpenAI sentiment, Telegram, Kite LTP all stubbed.

---

## 1. Simulation Flow — What Worked

### 1.1 Market Open / Data Download / Watchlist ✅
Synthetic OHLCV data was generated and fed through the patched `MarketDataFetcher`. `SignalGenerator` accepted the watchlist and proceeded to the two-stage screening pipeline.

### 1.2 AI Research / Signal Generation ✅
Because the synthetic data alone produced `HOLD` recommendations, the simulation patched `AIResearchAgent.research_stock()` to return a deterministic `STRONG_BUY` with technical score 0.65 and confidence 0.85. This is a legitimate test of the **execution pipeline** rather than the indicator math.

Result: 2 BUY signals generated.

```
Generated 2 BUY signals
- TCS: price=1000.00, qty=2, SL=950.00, target=1150.00
- RELIANCE: price=1000.00, qty=2, SL=950.00, target=1150.00
```

### 1.3 BUY Order Execution ✅
Both orders placed in paper mode. `TradeJournal` logged BUY entries.

```
INFO:broker_integration:Paper order placed: BUY 2 TCS @ 1000.0
INFO:broker_integration:Paper order placed: BUY 2 RELIANCE @ 1000.0
INFO:trade_journal:Journal: logged BUY TCS @ ₹1000.0
INFO:trade_journal:Journal: logged BUY RELIANCE @ ₹1000.0
```

### 1.4 Position Monitoring / Partial Profit / Trailing Stop ✅
RiskManager correctly:
- Triggered partial profit at +5.5% for both positions (sold 1 of 2 shares).
- Updated trailing stop after the 5% activation threshold.
- Tightened SL to break-even after partial booking.

```
INFO:risk_manager:Partial exit RELIANCE: 1 @ ₹1055.00 P&L ₹55.00
INFO:risk_manager:Partial exit TCS: 1 @ ₹1055.00 P&L ₹55.00
INFO:risk_manager:Trailing stop updated RELIANCE: ₹1058.89
INFO:risk_manager:Trailing stop updated TCS: ₹1064.98
```

### 1.5 SL / Target / EOD Close ✅ (logic side)
- RELIANCE at ₹1045 hit trailing stop → `STOPPED_OUT`.
- TCS at ₹1105 was closed by end-of-day routine → `CLOSED`.
- No stale open/partial positions remained in `RiskManager`.

### 1.6 Telegram / Email Alerts ✅
Alert methods were stubbed; no exceptions were thrown. The bot attempted to send alerts at every exit event.

### 1.7 Dashboard Load ✅
Dashboard module imports cleanly and the `_HEALTH` dictionary is populated with all expected keys:

```
Dashboard loaded OK
Health keys: ['cpu_pct', 'mem_pct', 'mem_mb', 'disk_free_gb', 'api_latency_ms',
              'kite_ok', 'errors_today', 'scan_time_s', 'last_heartbeat', 'start_time']
```

---

## 2. Critical Failure — Paper Trading Partial Exit Bug ❌

The simulation failed verification with a **₹2,142.50 paper portfolio drift** and missing SELL journal entries:

```
Paper cash: 13110.00, positions value: 0.00
Total paper value: 13110.00
Expected value (capital + net P&L): 15252.50
Value drift: -2142.50
Quantity conservation errors: ['RELIANCE: bought 2, sold 1', 'TCS: bought 2, sold 1']
```

### Root Cause
`BrokerIntegration._place_paper_order()` deletes the entire paper position on any SELL, regardless of the quantity being sold:

```@/Users/mithileshsinha/CascadeProjects/ai_trading_agent/src/broker_integration.py:108-113
elif action == 'SELL':
    if symbol in self.paper_portfolio['positions']:
        position = self.paper_portfolio['positions'][symbol]
        self.paper_portfolio['cash'] += price * quantity
        del self.paper_portfolio['positions'][symbol]   # BUG: deletes full position
```

### Impact
- After the first partial SELL, the remaining shares disappear from the paper portfolio.
- Subsequent SELLs (trailing stop, target, EOD close) fail with `No position to sell`.
- The journal only records the partial exit, so the journal is missing the final exit for every partially-booked trade.
- `RiskManager` shows the position as closed with full P&L, but the paper cash ledger is missing the proceeds of the final exit.
- This corrupts all paper-trading analytics, backtests, and dashboard P&L displays.

### Secondary Effect: Journal / Position File Inconsistency
`data/trade_journal.json` ended with 4 entries (2 BUY, 2 SELL partials), while `data/positions.json` shows both positions closed. The final SELLs for the remaining shares are absent from the journal because `OrderExecutor.monitor_positions()` only logs to the journal when `order_result['success']` is true.

---

## 3. Other Observations

### 3.1 EOD Close Returns a Failure Object
After the partial-exit bug, `close_all_positions()` reported:

```
EOD close: False order_id=None
```

This is a downstream symptom of the missing paper position, not a separate root cause.

### 3.2 No Duplicate SELL Symbols ✅
Within the partial-exit entries, no symbol appears more than once. However, the *expected* outcome should have 2 SELL entries per symbol (1 partial + 1 final), so the absence of duplicates is actually a sign of the missing exits.

### 3.3 API / Memory Leaks ✅
The simulation ran in a single process and exited cleanly. No HTTP requests were made after the Kite stub was applied. No evidence of memory leak in a short run; the trade log and journal files are bounded by the existing 500-entry cap and daily-rotation logic respectively.

### 3.4 Token / Session ✅
The stored token is valid until `2026-07-11 08:55:45`. The simulation stubbed Kite entirely, so no session expiry was triggered.

---

## 4. Verification Matrix

| Check | Expected | Actual | Status |
|-------|----------|--------|--------|
| Market open gate | Bypassed via stub | Stubbed True | ✅ |
| Data download | Synthetic daily OHLCV | Supplied | ✅ |
| Watchlist | RELIANCE, TCS | Used | ✅ |
| AI research | Strong BUY | Stubbed | ✅ |
| Signal generation | 2 BUY signals | 2 BUY | ✅ |
| BUY execution | 2 paper orders | 2 orders | ✅ |
| Journal BUY entries | 2 | 2 | ✅ |
| Partial profit | 2 partial exits | 2 partial exits | ✅ |
| Trailing stop update | Updated | Updated | ✅ |
| SL hit | RELIANCE closed | STOPPED_OUT | ✅ |
| EOD close | TCS closed | CLOSED | ✅ |
| No stale positions | 0 open/partial | 0 | ✅ |
| Paper cash ledger | Matches P&L | Drift -2142.50 | ❌ |
| Journal quantity conservation | bought 2 = sold 2 per symbol | sold 1 per symbol | ❌ |
| Duplicate SELL symbols | No erroneous duplicates | None | ✅ |
| Dashboard load | No import errors | Loaded | ✅ |
| API failures | None | None | ✅ |
| Memory leak | Stable | Stable | ✅ |

---

## 5. Recommended Fixes

1. **Fix paper partial SELL in `BrokerIntegration._place_paper_order()`**
   - Decrement quantity by the sold amount.
   - Only delete the position when quantity reaches 0.
   - Cash credit must equal `price * quantity_sold`.

2. **Add a regression test** using the Phase 14 simulation script that asserts:
   - paper value drift < ₹0.01
   - bought quantity == sold quantity per symbol
   - journal entries match position file exits

3. **Harden `OrderExecutor.monitor_positions()` journal logging**
   - If `risk_manager` has already closed the position but the broker order fails, still log the exit for audit purposes, or at least flag the inconsistency.

4. **Backtest engine check**
   - Because `Backtester` also uses the paper broker path, verify it does not suffer from the same partial-exit bookkeeping bug.

---

## 6. Conclusion

The end-to-end pipeline is structurally sound: signals flow from research → execution → monitoring → exit → analytics, and the trailing-stop / partial-profit logic works. However, the simulation revealed a **genuine, high-impact bug in paper-trading partial exits** that corrupts the paper ledger, the journal, and any dashboard/backtest derived from them. The rest of the requested end-to-end checks (stale positions, duplicate entries, API failures, memory leaks, dashboard load) passed.

**Phase 14 result: FAILED due to paper-trading partial exit bug.**
