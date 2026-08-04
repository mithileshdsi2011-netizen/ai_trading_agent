# GESHIP Same-Day BUY/SELL Root-Cause Analysis

## 1. Project Information

- **Project root:** `/Users/mithileshsinha/CascadeProjects/ai_trading_agent`
- **Git branch:** `trading_development`
- **Git commit hash:** `672e4e6fe1ac0de534e06725ccebcebb643bf894`
- **Latest commit message:** `Capture Position.exit_reason and add Exit Reason column to Recent Activity`
- **Git remote:** `origin  https://github.com/mithileshdsi2011-netizen/ai_trading_agent.git`
- **Uncommitted changes:** 33 modified files including `src/order_executor.py`, `src/risk_manager.py`, `src/trading_orchestrator.py`, `src/reconciliation_engine.py`, `src/broker_integration.py`, `config.py`, `dashboard.py`, and several tests. New untracked files include `rc1_validation.py`, `rc1_report.md`.

## 2. Trading Mode Verification

- `.env` explicitly sets `TRADING_MODE=swing`.
- `config.py` reads this and sets `product_type` to `CNC` in `src/risk_manager.py`, `src/order_executor.py`, and `src/broker_integration.py`.
- Searches for intraday-specific terms (`MIS`, `INTRADAY`, `square off`, `end of day`) show they are guarded by `if config.TRADING_MODE == "swing"` and do **not** trigger forced closes in swing mode.
- **Conclusion:** The bot is configured for swing trading and the code defaults to CNC. The same-day exits are not due to an intraday/EOD misconfiguration.

## 3. GESHIP Trade Timeline (2026-08-04)

| Time (IST) | Order ID | Action | Qty | Price | Source | Notes |
|---|---|---|---|---|---|---|
| 09:30:22 | 260804150252026 | BUY | 2 | ₹1,500.90 | `order_executor` | Signal executed; `sell_decision_ai` later says HOLD |
| 09:30:25 | — | — | — | — | `sell_decision_ai` | `GESHIP: HOLD (confidence 0.70, P&L +0.38%)` |
| 11:20:27 | 260804150776196 | SELL | 2 | ₹1,432.60 | `broker_integration` | **Same-day exit after ~1h 50m**; reason unknown but price was below trailing stop |
| 11:20:27 | — | — | — | — | `__main__` | `Error in trading cycle: name 'position_size' is not defined` |
| 11:36:52 | 260804150831356 | BUY | 2 | ₹1,433.20 | `order_executor` | Re-bought GESHIP |
| 11:36:54 | — | — | — | — | `sell_decision_ai` | `GESHIP: HOLD (confidence 0.67, P&L -0.08%)` |
| 11:37:44 | 260804150834478 | SELL | 2 | ₹1,431.50 | `broker_integration` | **Same-day exit 52 seconds after re-entry** |
| 11:37:44 | — | — | — | — | `__main__` | `Error in trading cycle: name 'position_size' is not defined` again |

- Database confirms two closed GESHIP positions on 2026-08-04 with `holding_hours` of `1.8` and `0.0`.
- `reconciliation_engine` later marks positions `reconciliation: missing in broker` because the SELL removed the holding from the broker account.

## 4. Exit Logic Findings

The relevant SELL path is:

1. `trading_orchestrator.run_once()` calls `order_executor.monitor_positions()` (logs show `Monitoring existing positions (Stop Loss, Target, Trailing)...`).
2. `order_executor.monitor_positions()` calls `risk_manager.check_exits()` and `trade_lifecycle_manager` / `smart_exit` to produce SELL actions.
3. `order_executor.execute_sell()` is called. It has a `MIN_HOLD_HOURS` guard, but **it is bypassed if the SELL reason contains `stop`, `sl`, `max hold`, `end of day`, or `circuit breaker`**.
4. `broker_integration.place_order()` sends the CNC SELL to Zerodha.

### 11:20 SELL — likely a valid swing protective exit
- Position record: `trailing_stop=1432.98`, `last_price=1432.50`.
- Price dropped below the ATR-based trailing stop.
- This is a legitimate protective stop for swing mode and would bypass `MIN_HOLD_HOURS`.

### 11:37 SELL — anomalous 52-second exit
- New position record: `entry_price=1434.20`, `stop_loss=1362.49`, `trailing_stop=1404.49`, `last_price=1432.00`.
- The current price was well above both the fixed stop loss and the trailing stop.
- `sell_decision_ai` was `HOLD` just 2 seconds earlier.
- `smart_exit` requires a minimum profit of `SMARTEXIT_MIN_PROFIT_PCT` (default 1.5%) and the position was at a small unrealised loss, so it could not have triggered.
- The SELL still went through at 11:37:44 and was followed immediately by the runtime `NameError: name 'position_size' is not defined`.

## 5. Root Cause

The bot is **not configured for intraday trading**, but the `trading_orchestrator`/`order_executor` monitoring loop can issue SELL actions that bypass `MIN_HOLD_HOURS` when the reason is classified as a protective stop (`stop`, `sl`, etc.).

For the 11:37 SELL, the available evidence does not show a legitimate protective-stop trigger (price was above all configured stops). The two most probable causes are:

1. **A logic error in `trade_lifecycle_manager.py` / `risk_manager.py` generating a SELL action with a reason containing `stop`/`trailing` when the price has not actually crossed the stop.** This is the leading hypothesis because the `MIN_HOLD_HOURS` guard is bypassed by such keywords and a runtime `NameError: name 'position_size' is not defined` follows every SELL, suggesting the SELL action is not being built from a clean signal path.
2. **A position-reconciliation or duplicate-position confusion.** The reconciliation engine is closing positions as `reconciliation: missing in broker` and creating synthetic `source='reconciliation'` SELL trades. However, the `Real order placed: SELL` log shows the SELL order itself was placed by `broker_integration`, not created in the database after the fact.

In short: the order reached the broker because `order_executor` treated the SELL as a protective stop and did not enforce the 12-hour `MIN_HOLD_HOURS` minimum, even though the price was not actually below the protective stop.

## 6. Files and Functions Responsible

| File | Function / Code Block | Role in the Issue |
|---|---|---|
| `src/order_executor.py` | `execute_sell()` (lines ~630–711) | Contains the `MIN_HOLD_HOURS` guard that is bypassed for reasons containing `stop`/`sl`/`max hold`/`end of day`/`circuit breaker`. |
| `src/order_executor.py` | `monitor_positions()` (lines ~713–...) | Calls lifecycle/risk logic to generate SELL actions. |
| `src/risk_manager.py` | `check_exits()` / `_close_position()` | Generates `trailing_stop`, `target`, `stop_loss` exits. |
| `src/trade_lifecycle_manager.py` | `generate_actions()` / `_check_trailing_stop()` | Can return SELL actions for gap/volume/trailing triggers. |
| `src/trading_orchestrator.py` | `run_once()` | Drives the `monitor_positions()` loop and calls `execute_sell()`. |
| `src/broker_integration.py` | `place_order()` (line ~436) | Places the CNC SELL to Kite. |
| `src/reconciliation_engine.py` | `_reconcile_open_positions()` / `_reconcile_journal()` | Logs missing positions and creates synthetic SELL records after the fact. |

## 7. Expected vs. Actual Behavior

- **Expected for swing mode:** Positions may exit on target, fixed SL, ATR trailing SL, `smart_exit` profit signal, max-hold-days, or manual/rebalance. Same-day exits should occur only when a protective stop is genuinely hit.
- **Actual:** GESHIP was bought and re-sold twice in the same day. The second exit was only 52 seconds after the re-buy, while the price was above all configured protective stops.

## 8. Recommended Next Steps (pending your review)

1. **Add detailed logging around `execute_sell()` and `monitor_positions()`** to capture the exact `reason` string that bypassed `MIN_HOLD_HOURS` for the 11:37 SELL.
2. **Audit `trade_lifecycle_manager.py` trailing/gap/volume exit logic** to ensure it does not emit false SELL signals when the price is above the configured `trailing_stop` and `stop_loss`.
3. **Fix the `NameError: name 'position_size' is not defined` in `trading_orchestrator.py`**; this may be corrupting the signal/position flow and causing unintended SELL actions.
4. **Strengthen the `MIN_HOLD_HOURS` bypass** so a SELL reason must also contain the actual exit type (e.g. `trailing_stop_hit`, `sl_hit`, `target_hit`) and is verified against the actual `position.trailing_stop`/`stop_loss` before it can bypass the swing holding period.
5. **Consider a re-entry cooldown / duplicate-order guard for the same symbol** to prevent re-buying the same stock immediately after a same-day SELL unless it has cooled down.

No code changes have been made.
