# Phase 11 — Trading Logic Validation Report

## Scope
Realistic trading scenarios: market open/closed, BUY/SELL success/rejection, CDSL authorization, network/internet/Kite failures, dashboard/bot restart, crash/power failure, token expiry, duplicate positions, target/SL/trailing SL/partial profit, and smart exits.

## Summary
- **Critical bug found:** `OrderExecutor.execute_signal()` runs the BUY-only `RiskManager.can_open_position()` check on **all** signals, including SELLs. This blocks smart-exit SELL orders.
- **Target-hit behavior is surprising:** a single price move that crosses both the partial-profit level and the full target only books the partial profit in that cycle; the remainder is left `PARTIAL`.
- **Recovery from crash/power failure is generally good** because positions, blacklist, and daily P&L are persisted to `data/positions.json`, and positions are reloaded from the broker on startup.
- **Token expiry requires manual re-auth;** no automatic refresh.
- **Network/Kite failures** are detected after two consecutive cycles and halt trading with alerts.

---

## Scenario-by-Scenario Findings

| Scenario | Code Path | Status | Notes |
|----------|-----------|--------|-------|
| Market closed | `run_once()` → `is_market_open()` | ✅ | Cycle returns early, no orders |
| Market open | `run_once()` | ✅ | Continues after checks |
| BUY success | `execute_signal()` → `place_order()` → `open_position()` | ✅ | Position opened and journal logged |
| BUY rejected | `execute_signal()` → `can_open_position()` or `place_order()` | ⚠️ | Rejection reason is generic “Risk parameters not met” |
| SELL success (monitor path) | `monitor_positions()` → `broker.place_order()` | ✅ | Works |
| SELL success (smart-exit path) | `execute_signal()` → `can_open_position()` | ❌ | **Blocked by duplicate-position / R:R checks** |
| SELL rejected / CDSL pending | `broker._place_real_order()` → `cdsl_auth_required` | ✅ | Position kept open, alert sent, retried next cycle |
| Network failure | `broker.place_order()` exception | ⚠️ | Returns error; no intra-cycle retry |
| Internet lost | `run_once()` margins probe fails | ✅ | Counts failures, alerts after 2 cycles, stops trading |
| Kite down | Same as internet lost | ✅ | Same handling |
| Dashboard restart | `dashboard.py` reinitializes | ✅ | Independent of bot; reads fresh data |
| Bot restart | `OrderExecutor._load_existing_positions()` + `RiskManager._load_positions()` | ✅ | Positions restored from broker and file |
| Unexpected crash | Persistence in `positions.json` / `trade_journal.json` | ✅ | Most state survives; in-flight orders may need broker reconcile |
| Power failure | Same as crash | ✅ | Persistent files survive |
| Token expired | `TokenManager.get_access_token()` raises; `run_once()` probe fails | ⚠️ | Falls back to paper mode on startup; runtime outage needs manual refresh |
| Position already exists | `can_open_position()` duplicate check + `_pending_order_symbols` | ⚠️ | Prevents duplicate BUYs, but pending set is cleared on acceptance (race window) |
| Target hit | `check_positions()` | ⚠️ | Partial profit takes precedence; full target not checked same cycle |
| SL hit | `check_positions()` | ✅ | Closes position, blacklists symbol |
| Trailing SL hit | `check_positions()` | ✅ | Trail updates and exits correctly |
| Partial profit | `check_positions()` | ✅ | Sells half, moves to `PARTIAL`, tightens SL to breakeven |
| Smart exit | `SmartExitAI.check_all()` → `execute_signal(SELL)` | ❌ | **Does not execute due to SELL bug** |

---

## Critical

### 1. Smart-exit SELL orders are blocked by `can_open_position()`
`OrderExecutor.execute_signal()` validates every signal with `RiskManager.can_open_position()` regardless of `action`:

```@/Users/mithileshsinha/CascadeProjects/ai_trading_agent/src/order_executor.py:363-369
# Check if we can open position based on risk parameters
if not self.risk_manager.can_open_position(signal):
    return {
        'success': False,
        'reason': 'Risk parameters not met',
        'signal': signal
    }
```

`can_open_position()` is written for BUY signals:

```@/Users/mithileshsinha/CascadeProjects/ai_trading_agent/src/risk_manager.py:245-267
# Check duplicate position
if any(p.status in (PositionStatus.OPEN, PositionStatus.PARTIAL) and p.symbol == symbol
       for p in self.positions):
    logger.warning(f"Duplicate position: {symbol} already open")
    return False

# Check risk-reward ratio
if signal['risk_reward_ratio'] < config.MIN_RISK_REWARD:
    ...
```

For a SELL signal from `SmartExitAI`:
- `risk_reward_ratio` is set to `0` → fails the R:R check.
- The symbol already has an open position → fails the duplicate check.

**Verified by simulation:**
```python
sell_signal = {'symbol':'RELIANCE','action':'SELL',...}
result = order_executor.execute_signal(sell_signal)
# => {'success': False, 'reason': 'Risk parameters not met'}
```

**Impact:** Smart exits never execute. Positions that should be closed by `SmartExitAI` remain open.

**Fix:** in `execute_signal()`, only call `can_open_position()` when `action == 'BUY'`. For `SELL`, validate only that a matching open/partial position exists.

---

## High

### 2. Full target is not checked when partial-profit level is also crossed
In `RiskManager.check_positions()`:

```@/Users/mithileshsinha/CascadeProjects/ai_trading_agent/src/risk_manager.py:387-426
partial_target = getattr(position, '_partial_target', position.entry_price * 1.05)
if (not position.partial_booked
        and position.status == PositionStatus.OPEN
        and current_price >= partial_target
        and position.quantity >= 2):
    ... # sells half and sets status to PARTIAL
    continue  # skips target/SL checks this cycle
```

If a price gaps from below `partial_target` to above `target` in one candle, only the partial exit is booked. The remaining shares stay `PARTIAL` and are not evaluated for the full target until the next cycle. In a fast-moving market this can leave profit on the table.

**Fix:** after partial booking, immediately re-evaluate the remaining quantity against SL/target/trailing-stop rather than `continue`.

### 3. No automatic token refresh
When the access token expires, `TokenManager.get_access_token()` raises `ValueError`. The bot either starts in paper mode or, if already running, halts after two failed health checks. There is no scheduled re-authentication before expiry or automatic refresh.

**Fix:** schedule a refresh shortly before the saved expiry, or at minimum alert the operator well in advance.

### 4. Paper portfolio is not persisted
If `PAPER_TRADING=True`, `BrokerIntegration.paper_portfolio` is in-memory only. A bot restart resets cash and paper positions even though the journal persists trades. This can cause the paper account state to diverge from the journal.

**Fix:** persist `paper_portfolio` to disk and load it on startup.

---

## Medium

### 5. No intra-cycle retry for failed orders
If a BUY or SELL fails because of a transient Kite/network error, the bot logs the error and waits until the next 15-minute cycle to retry. With a 15-minute interval, this can mean missing the intended price.

**Fix:** add a small retry loop with backoff for order-placement calls.

### 6. Generic BUY rejection reason
When `can_open_position()` rejects a BUY, the caller receives:

```python
{'success': False, 'reason': 'Risk parameters not met', ...}
```

It does not indicate whether the failure was due to daily loss limit, duplicate position, low confidence, low R:R, or capital cap. This makes debugging and alerting harder.

**Fix:** return a specific reason from `can_open_position()`.

### 7. In-flight BUY race window
After `broker.place_order()` returns success, the symbol is removed from `_pending_order_symbols`:

```@/Users/mithileshsinha/CascadeProjects/ai_trading_agent/src/order_executor.py:378-380
if order_result['success']:
    self._pending_order_symbols.discard(sym)
```

Kite returns an order ID on **acceptance**, not on **fill**. If the next cycle runs before the order fills, a duplicate BUY can be placed because the symbol is no longer marked pending.

**Fix:** keep the symbol in `_pending_order_symbols` until the order reaches a terminal state (`COMPLETE`, `REJECTED`, `CANCELLED`), confirmed by polling `kite.orders()`.

### 8. `position.slippage` is always zero
In `RiskManager._close_position()`:

```@/Users/mithileshsinha/CascadeProjects/ai_trading_agent/src/risk_manager.py:469-470
position.slippage = exit_price - position.exit_price  # 0 here; broker fills in real mode
```

`position.exit_price` was just set to `exit_price`, so the subtraction is always 0. This line is effectively dead code.

**Fix:** remove it or compute slippage against the signal price if available.

### 9. Daily trade counter not persisted
`RiskManager.daily_trades` is not saved to `positions.json` and resets on restart. It is also not used in `can_open_position()`, so it currently has no functional impact, but if a daily trade limit is added later it will break across restarts.

---

## Low

### 10. Circuit breaker `peak_value.json` is never reset
The emergency drawdown circuit breaker compares current value to the all-time peak stored in `peak_value.json`. The peak is never decayed or reset. If the account value drops permanently (e.g., withdrawal), the drawdown threshold may become too sensitive.

**Fix:** reset peak at the start of each trading day, or use a rolling window.

### 11. No explicit handling for partially filled orders
The bot assumes orders are either fully accepted or rejected. If Kite partially fills an order, the position quantity tracked in `RiskManager` may not match the actual filled quantity.

**Fix:** after placing an order, poll order trades and update `Position.quantity` to the filled quantity.

---

## Positive Findings

| Scenario | Handling |
|----------|----------|
| Market closed | Early return, no trading |
| Intraday cutoff | Monitoring only / close all |
| Daily loss limit | Trading halted, positions still monitored |
| Consecutive losses | Survives restart via journal scan |
| CDSL TPIN required | Alert sent, position kept open, retries next cycle |
| Internet/Kite down | 2-cycle failure threshold, alert, halt |
| Bot restart | Positions loaded from broker + persistence |
| Crash/power failure | Positions + daily P&L + blacklist persisted |

---

## Recommended Fix Priority

1. **Fix SELL through `execute_signal()`** so smart exits work.
2. **Fix target-hit/partial-profit ordering** so a full target can exit remaining shares in the same cycle.
3. **Persist paper portfolio** across restarts.
4. **Add intra-cycle retry** for transient order failures.
5. **Return specific reasons** from `can_open_position()`.
6. **Fix in-flight race window** by waiting for terminal fill status.
7. **Add automatic token refresh** alerting before expiry.

## Recommended Next Phase
Phase 12 should be a live paper-trading end-to-end run that specifically exercises: BUY → partial profit → full target/SL → smart exit → bot restart mid-trade, verifying each state transition and recovery path.
