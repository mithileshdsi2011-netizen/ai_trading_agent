# ALKEM Same-Day Churning Audit Report

## 1. Executive Summary

The repeated ALKEM buy/sell pairs on **2026-07-30** are **not expected swing-trading behaviour**. They are caused by the emergency circuit breaker in `TradingOrchestrator._run_cycle` firing on every 15-minute cycle, closing every open position. Once a position is closed, `RiskManager.can_open_position()` does not enforce `REENTRY_COOLDOWN_HOURS`, so the next cycle can immediately re-buy ALKEM.

This is a **bug**, not a configuration or strategy issue. The exit reason in `trade_journal.json` is misleadingly recorded as `End of day close` while the real trigger is the circuit breaker.

## 2. ALKEM Trade Timeline

| # | Time (IST) | Action | Exit price / Net P&L | Holding time | Journal `exit_reason` | `trade_score` | Confidence | Regime | Re-entry meta |
|---|------------|--------|----------------------|--------------|-----------------------|---------------|------------|--------|---------------|
| 1 | 12:43:53 | BUY | ₹5,840.5 / +₹0.07 | — | `End of day close` | 64 | 0.875 | SIDEWAYS | `is_reentry=False`, `time_since_exit=0h` |
| 1 | 12:52:59 | SELL | ₹5,840.5 / +₹0.07 | ~9 min | `End of day close` | 0 | 1.0 | — | — |
| 2 | 13:09:22 | BUY | ₹5,840.0 / ₹0.00 | — | `kite_order` | 64 | 0.875 | SIDEWAYS | `is_reentry=False`, `time_since_exit=0h` |
| 2 | 13:24:25 | SELL | ₹5,839.5 / -₹10.93 | ~15 min | `End of day close` | 0 | 1.0 | — | — |
| 3 | 13:40:43 | BUY | ₹5,840.0 / -₹0.50 | — | `kite_order` | 64 | 0.875 | SIDEWAYS | `is_reentry=False`, `time_since_exit=0h` |
| 3 | 13:55:46 | SELL | ₹5,830.5 / -₹20.43 | ~15 min | `End of day close` | 0 | 1.0 | — | — |
| 4 | 14:12:28 | BUY | ₹5,840.0 / +₹35.00 | — | `kite_order` | 64 | 0.875 | SIDEWAYS | `is_reentry=False`, `time_since_exit=0h` |

## 3. Execution-Flow Trace

1. **Signal generation** — `src/optimized_signal_generator.py` produces a `BUY` for ALKEM each cycle because the `trade_score=64` is above the skip threshold in `SIDEWAYS` regime. It does **not** attach `_reentry_meta` to the signal (`is_reentry=False`, `time_since_exit_hours=0.0`), so no cooldown is evaluated.
2. **Trade scoring** — `src/trade_scorer.py` scores the signal at `64` (full-size). `overall_score` is `None`, so the AI research score is not being used.
3. **Risk check** — `src/risk_manager.py:can_open_position()` only blocks if an `OPEN`/`PARTIAL` position for the same symbol already exists. After the circuit breaker closes the previous position, the next `BUY` is allowed through.
4. **Order execution** — `src/order_executor.py:execute_signal()` places the `BUY` and `TradeJournal.log_entry()` records it.
5. **Exit trigger** — In the next cycle, `src/trading_orchestrator.py:_run_cycle()` computes a 55% drawdown from `data/peak_value.json` (`peak_value=₹10,523.2`) vs `broker.get_holdings()['total_value']` (≈₹4,684) and calls `OrderExecutor.close_all_positions('Circuit breaker')`.
6. **Trade journal** — `OrderExecutor._execute_sell()` logs the `SELL`, but the journal stores `exit_reason: "End of day close"` while `RiskManager` logs `Reason: Manual close`. The real `Circuit breaker` reason is not propagated end-to-end.

## 4. Findings by Area

### 4.1 Re-entry Cooldown
- `.env` contains `REENTRY_COOLDOWN_HOURS=4.0`.
- `RiskManager.can_open_position()` never reads this value.
- `OrderExecutor._pending_order_symbols` only blocks in-flight duplicate `BUY` orders, not same-day re-entry of a symbol that was just sold.
- The `reentry_meta` fields in the journal are always `is_reentry=False` and `time_since_exit_hours=0.0`, confirming the generator/cooldown plumbing is not active.

### 4.2 Minimum Holding Period
- No minimum holding period exists in `RiskManager` or `OrderExecutor`.
- Positions are held for only 9–15 minutes, which is not swing trading.

### 4.3 Duplicate BUY Signals
- `RiskManager` does block opening a second `OPEN`/`PARTIAL` position for the same symbol, but because every cycle is force-closed, the symbol is no longer `OPEN` in the next cycle.
- `can_open_position()` has no cooldown or daily re-entry limit.

### 4.4 SELL Reasons and Exit Triggers
- The journal says `End of day close`, but `logs/trading.log` shows the real trigger:
  - `2026-07-30 12:52:58,534 ERROR: EMERGENCY CIRCUIT BREAKER: drawdown 55.47% from peak ₹10523 — closing all positions`
  - Repeated at `13:24:24`, `13:55:45`, and `14:15:45` with drawdown ~55.5%.
- `RiskManager._close_position` logs `Reason: Manual close`, meaning `reason_override` is not being set. This is another symptom that the `reason` string is not correctly passed through the `close_all_positions()` path.

### 4.5 Kite-Linked BUYs
- The later three ALKEM BUYs have `exit_reason: kite_order` in the journal. These likely came from dashboard/Kite sync rather than the normal signal pipeline, yet they are still scored and executed.

## 5. Root Cause

The emergency circuit breaker in `src/trading_orchestrator.py` is misfiring because the `total_value` returned by `broker.get_holdings()` is far below the saved `data/peak_value.json` (`peak_value=₹10,523.2`). This produces a ~55% drawdown on every 15-minute cycle, unconditionally closing all open positions. Because the re-entry/cooldown guard is not actually implemented, the next cycle opens a new ALKEM position and the pattern repeats.

## 6. Recommendations

1. **Fix the circuit breaker before any further strategy changes**
   - Validate `broker.get_holdings()['total_value']` (ensure it is cash + position value, and guard against `0` or stale values).
   - Reset `data/peak_value.json` to the opening `total_value` at the start of each trading day.
   - Make the circuit breaker a one-shot per day, or at least add a cooldown, to avoid repeated mass liquidation.

2. **Enforce `REENTRY_COOLDOWN_HOURS`**
   - In `RiskManager.can_open_position()` or `OrderExecutor.execute_signal()`, reject a `BUY` if the same symbol was sold within `REENTRY_COOLDOWN_HOURS`.

3. **Add a minimum swing holding period**
   - For `TRADING_MODE=swing`, refuse to close a position (including via `close_all_positions`) until at least `MIN_HOLD_DAYS=1` or a configured number of minutes has elapsed.

4. **Fix exit-reason propagation**
   - Ensure `OrderExecutor.close_all_positions()` and `_execute_sell()` pass the `reason` string consistently to both `RiskManager.close_position()` and `TradeJournal.log_entry()`.

5. **Reconcile Kite-synced positions before opening**
   - If a position with `exit_reason: kite_order` is already in the journal/holdings, do not duplicate it with a new `BUY`.

6. **Add audit logging**
   - Log `can_open_position` decisions, re-entry checks, and circuit-breaker values (`peak_value`, `total_value`, `drawdown`) so the next audit can see why a trade was allowed or closed.

7. **Immediate safety**
   - Until the circuit breaker and cooldown are fixed, do not run in live mode, or at minimum run with `PAPER_TRADING=True`. The current behaviour will churn capital every 15 minutes.
