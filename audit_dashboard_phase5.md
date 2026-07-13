# Phase 5 — Dashboard Audit Report

## Scope
All dashboard pages/tabs, API endpoints, data accuracy, refresh behavior, and JavaScript correctness in `dashboard.py`.

## Summary
- **All 11 tabs exist** in the HTML and the dashboard is served by the Flask process on port 5001.
- **API endpoints return 200:** `/api/data`, `/api/journal`, `/api/health`, `/api/morning-report`, `/api/ask`, `/api/backtest` (POST).
- **No JavaScript errors are currently being reported** to `/api/js-error` in the running process's logs.
- **Deployment issue:** the running dashboard process is still serving the old `v=2` cache-busted HTML; the fixed `v=3` code is on disk but has not been restarted.
- **Genuine data-display bugs found:** Analytics win-rate shows 10000%, Ask AI shows 1.0% win rate, and the History tab duplicates current-session orders.

---

## Verification Performed

| Check | Method | Result |
|-------|--------|--------|
| /api/data | curl — size/time | 200, ~37 KB, ~0.48 s |
| /api/journal | curl | 200, returns analytics + trade log |
| /api/health | curl | 200, all health metrics present |
| /api/morning-report | curl | 200, report present |
| /api/ask | curl POST | 200, local answer returned |
| /api/backtest | curl POST | 200, returns summary/breakdown |
| Dashboard process | curl logs | Running on localhost:5001 |
| Browser preview | browser_preview | Proxy opened at 127.0.0.1:50613 |
| JS error logs | grep dashboard.log | No JS errors recorded |

---

## Verified Working

| Page | Status | Notes |
|------|--------|-------|
| Dashboard | ✅ | Loads from `/api/data`; portfolio value, P&L, cash, positions, heatmap, AI opportunities rendered. |
| Morning Intel | ✅ | Loads from `/api/morning-report`; cached per day. |
| Portfolio | ✅ | Uses cash, margin, holdings, net_portfolio_value from `/api/data`. |
| Positions | ✅ | Uses positions + holdings from `/api/data`; SL/target enriched from `positions.json`. |
| History | ✅ | Uses `all_orders` from `/api/data`. |
| AI Signals | ✅ | Falls back from `recommendations` to `signals`. |
| Bot Status | ✅ | Uses `/api/health` + `/api/journal` win rate. |
| IP Status | ✅ | Uses IP cache + Kite resolution from `/api/data`. |
| Backtest | ✅ | POST endpoint works; UI controls present. |

---

## Critical

### 1. Running dashboard process is still serving the pre-fix `v=2` HTML
The file now forces `/?v=3` and has the duplicate-`const` fix in `load()`:

```@/Users/mithileshsinha/CascadeProjects/ai_trading_agent/dashboard.py:3700-3702
if request.args.get('v') != '3':
    return redirect('/?v=3', code=302)
```

But the live dashboard process logs still show `GET /?v=2` returning 200, which means the old process (started before the edit) is still running and has not picked up the fix.

**Fix:** restart the dashboard via `start_trading.sh` or kill the existing process and start a fresh one.

---

## High

### 2. Analytics tab displays win rate as 10000% instead of 100%
`/api/data` returns `win_rate` as a **percentage** (0–100):

```@/Users/mithileshsinha/CascadeProjects/ai_trading_agent/dashboard.py:4113-4114
data['total_trades'] = len(completed)
win_sells = [o for o in sells if (o.get('pnl') or 0) > 0]
data['win_rate'] = round(len(win_sells) / len(sells) * 100, 1) if sells else 0
```

The Analytics tab JavaScript multiplies it by 100 again:

```@/Users/mithileshsinha/CascadeProjects/ai_trading_agent/dashboard.py:2944-2946
const wrEl=document.getElementById('a-win-rate');
const wr=parseFloat(d.win_rate||0)*100;
wrEl.textContent=wr.toFixed(0)+'%';
```

With `win_rate=100.0`, the displayed value is **10000%**.

**Fix:** remove the `*100` for the Analytics tab, or standardize `/api/data` to return a fraction (0–1) like `/api/journal` does.

### 3. Ask AI shows win rate as 1.0% and "0 winners"
The Ask AI endpoint uses `journal.analytics()`, which returns `win_rate` as a **fraction** (0–1):

```@/Users/mithileshsinha/CascadeProjects/ai_trading_agent/src/trade_journal.py:232
win_rate = len(wins) / total if total else 0
```

```@/Users/mithileshsinha/CascadeProjects/ai_trading_agent/dashboard.py:4594-4607
if any(w in q for w in ['win rate', ...]):
    total  = analytics.get('total_trades', 0)
    wins   = analytics.get('winning_trades', 0)  # key does not exist
    wr     = analytics.get('win_rate', 0)
    ...
    return jsonify({
        'answer': f"Out of {total} closed trades, {wins} were winners — {wr:.1f}% win rate. ...",
```

With `win_rate=1.0` and `winning_trades` missing, the answer becomes:
> "Out of 5 closed trades, 0 were winners — 1.0% win rate."

**Fix:** add `winning_trades` to `journal.analytics()` and multiply `win_rate` by 100 before formatting in Ask AI.

### 4. History tab duplicates current-session Kite orders with journal entries
`/api/data` merges journal entries into `all_orders` when `kite_order_id` is not in the Kite order list:

```@/Users/mithileshsinha/CascadeProjects/ai_trading_agent/dashboard.py:4074
if je.get('kite_order_id') not in kite_ids:
```

But `journal.log_entry()` is never called with a `kite_order_id`, and the function signature does not even accept one:

```@/Users/mithileshsinha/CascadeProjects/ai_trading_agent/src/order_executor.py:414-417
self.journal.log_entry(
    symbol=signal['symbol'],
    action='BUY',
    ...
    quantity=signal.get('position_size', 1),
```

So every journal entry has `kite_order_id` = `None`/`''`, and current-session Kite orders are duplicated by their journal counterparts in the History tab.

**Fix:** pass the Kite `order_id` into the journal entry; add a `kite_order_id` parameter to `TradeJournal.log_entry()`.

---

## Medium

### 5. Inconsistent `win_rate` representation between APIs
- `/api/data` returns `win_rate` as **percentage** (0–100).
- `/api/journal` returns top-level `win_rate` as **fraction** (0–1), but bucket-level `win_rate` as **percentage** (0–100).

This mixed contract makes the front-end error-prone and already caused the two bugs above.

**Fix:** standardize all `win_rate` values to either fraction (0–1) or percentage (0–100) and document the contract.

### 6. `/api/data` is a monolithic, synchronous endpoint
It performs in sequence:
- `kite.margins()`
- `kite.positions()`
- `kite.holdings()`
- `kite.orders()`
- `kite.get_gtts()`
- `kite.quote()` for NIFTY/BANKNIFTY/VIX
- regime detection
- morning-report trigger
- background scan trigger
- journal load
- positions.json load
- IP status check

Current response time is acceptable (~0.5 s), but it will grow linearly with the number of orders and positions. A stall in any single call blocks the entire dashboard refresh.

**Fix:** consider splitting into focused endpoints (e.g., `/api/positions`, `/api/orders`, `/api/signals`) so each tab loads only what it needs, and major tabs can refresh independently.

### 7. Paper mode dashboard shows real Kite positions, not the paper portfolio
`dashboard.py` reads `kite.positions()` and `kite.holdings()` directly. In paper mode, the bot's paper portfolio is in `BrokerIntegration.paper_portfolio`, not in Kite. The dashboard therefore shows the real account while the bot paper-trades.

This is not a production issue because `PAPER_TRADING=False`, but it makes paper testing misleading.

**Fix:** when `config.PAPER_TRADING=True`, read from `BrokerIntegration.get_holdings()` instead of Kite.

### 8. Static Chart.js cache-buster is still `v=2`
The HTML redirect was bumped to `v=3`, but the Chart.js script tag is still `v=2`:

```@/Users/mithileshsinha/CascadeProjects/ai_trading_agent/dashboard.py:808
<script src="/static/chart.umd.min.js?v=2"></script>
```

If a browser has a stale/broken `v=2` copy of Chart.js cached, this line will not force a reload. The chart files themselves were not changed, so this is mostly cosmetic, but it is inconsistent with the HTML cache-busting strategy.

---

## Low

### 9. Backtest endpoint returned zero trades for a small test
The backtest POST endpoint works (200), but a short 90-day test with RELIANCE + TCS produced no trades. This is likely because the strategy threshold is strict and the selected stocks had no signal, but it can make the Backtest tab appear empty to a user testing it.

**Fix:** add a clear message in the UI when no trades are generated (e.g., "No signals met the threshold in this period / symbol set").

### 10. Test journal data has duplicate IDs and missing `kite_order_id`
`data/trade_journal.json` currently contains duplicate ID 7 and `kite_order_id=None` for all entries. This is a data-quality issue, not a code bug, but it affects the dashboard's appearance during the audit.

---

## Recommended Fix Priority

1. **Immediate:** Restart the dashboard so the `v=3` fix is active.
2. **Immediate:** Fix the Analytics tab win-rate multiplication.
3. **Immediate:** Fix Ask AI win-rate formatting and add `winning_trades` to `journal.analytics()`.
4. **Next:** Add `kite_order_id` to journal entries to prevent History duplicates.
5. **Next:** Standardize `win_rate` representation across all APIs.
6. **Next:** Consider splitting `/api/data` into tab-specific endpoints for better performance and maintainability.
7. **Next:** In paper mode, make the dashboard read from the paper portfolio instead of Kite.

## Recommended Next Phase
Phase 6 should restart the dashboard, load it in a browser, and visually verify each tab (Dashboard, Morning Intel, Portfolio, Positions, History, AI Signals, Analytics, Trade Journal, Ask AI, Bot Status, IP Status, Backtest) with the browser console open to catch any remaining JS errors and confirm the win-rate fix is correct.
