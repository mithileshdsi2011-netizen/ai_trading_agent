# Phase 13 — Broker/Kite Flow Verification Report

## Scope
Login, token refresh, session expiry, holdings, positions, order placement, order modification, order cancellation, CDSL handling, TPIN flow, retry mechanism, API rate limits.

## Test Environment
- **Account:** Mithilesh Prasad (Zerodha)
- **Token status:** Valid until `2026-07-11 08:55:45` IST
- **Mode:** Real Kite Connect for read endpoints; paper trading for order writes
- **Live holdings:** 6 CNC holdings
- **Live net positions:** 1

---

## 1. Login / Token Initialization ✅

`TokenManager` successfully loaded the stored access token:

```
INFO:token_manager:Loaded token from file, expires at: 2026-07-11 08:55:45.940938
INFO:token_manager:Using existing valid token from storage
INFO:token_manager:Kite initialized for user: Mithilesh Prasad
Token valid: True
```

Kite profile fetch works and confirms the user.

### 2. Token Refresh / Session Expiry ❌

`TokenManager.get_access_token()` raises `ValueError` when the token is expired and provides only a manual instruction:

```@/Users/mithileshsinha/CascadeProjects/ai_trading_agent/src/token_manager.py:100-104
raise ValueError(
    "Access token expired. Please run: python get_kite_token.py\n"
    ...
)
```

There is **no automated request-token flow** because request tokens are single-use and short-lived. The bot cannot refresh without human browser login.

`TradingOrchestrator.run_once()` detects invalid tokens and exits early:

```@/Users/mithileshsinha/CascadeProjects/ai_trading_agent/src/trading_orchestrator.py:134-140
self._kite_fail_count += 1
if self._kite_fail_count >= self._KITE_FAIL_ALERT_THRESHOLD and not self._kite_alert_sent:
    logger.error(
        f"KITE TOKEN EXPIRED/UNREACHABLE — "
        ...
    )
```

**Issue:** mid-session expiry is detected only after 2 consecutive Kite failures, not proactively.

**Recommendation:** schedule a proactive token-refresh reminder before expiry (e.g., 1 hour buffer) and send Telegram/email alert when expiry is approaching. Also store `token_requested_at` timestamp so manual refresh urgency is clear.

---

## 3. Holdings Fetch ✅

Live holdings fetched successfully:

```
Holdings count: 6
First holding: ANDHRSUGAR 1
```

`BrokerIntegration.get_holdings()` returns a normalized dict in both paper and real modes:
- `cash`: available live balance from margins
- `positions`: list of holdings
- `total_value`: holdings value + cash

**Minor issue:** `margins` fetch was confirmed working but the shape was different from the crude substring check used in the ad-hoc test; the actual endpoint succeeds.

---

## 4. Positions Fetch ⚠️

Kite `positions()` returned 1 day position and 1 net position.

However, `BrokerIntegration.get_positions()` returns **only day positions**:

```@/Users/mithileshsinha/CascadeProjects/ai_trading_agent/src/broker_integration.py:323-330
def _get_real_positions(self) -> List[Dict]:
    positions = self.kite.positions()
    return positions.get('day', [])
```

CNC/prior-day net positions are excluded here. They are loaded separately via `kite.holdings()` in `OrderExecutor._load_existing_positions()`.

**Impact:** callers of `broker.get_positions()` (e.g., daily email report) see only today’s intraday positions, not the full portfolio.

**Recommendation:** rename or split into `get_day_positions()` / `get_net_positions()` to make the semantics explicit.

---

## 5. Order Placement ✅ (Paper)

Paper BUY order placed and cancelled successfully:

```
Paper BUY result: {'success': True, 'order_id': 'PAPER_...', 'status': 'COMPLETED', 'paper_trading': True}
Cancel result: {'success': True}
```

Real order path constructs correct Kite parameters:
- Product: `CNC` for swing, `MIS` for intraday
- Order type: `LIMIT` with 1% buffer
- Validity: `DAY`
- Symbol suffix `.NS` stripped

**Missing:** no pre-flight validation of order quantity/price, and no `modify_order` wrapper.

---

## 6. Order Modification ❌

`BrokerIntegration` does **not** implement order modification:

```
Has modify_order: False
```

The underlying `KiteConnect` client supports `modify_order()`, but there is no wrapper.

**Impact:** if a limit order is not filled because price moved, the bot cannot update the price. The only recourse is cancellation + re-placement, but the order executor does not do that either.

**Recommendation:** add `modify_order(order_id, signal)` to support price/quantity updates for pending orders.

---

## 7. Order Cancellation ✅

Paper cancellation works. Real cancellation path calls `kite.cancel_order()`.

**Issue:** `_cancel_paper_order()` for a BUY reverses cash but deletes the position unconditionally. For a partial exit it would remove the full paper position while only crediting partial proceeds (already noted in Phase 12).

---

## 8. CDSL Handling / TPIN Flow ✅

CDSL/TPIN errors are detected and surfaced to the user:

```@/Users/mithileshsinha/CascadeProjects/ai_trading_agent/src/broker_integration.py:218-233
cdsl_auth = any(k in err.lower() for k in ('cdsl', 'tpin', 'authoris', 'authorize', 'depository'))
if cdsl_auth:
    logger.error(...CDSL TPIN AUTHORISATION REQUIRED...)
return {
    'success': False, ..., 'cdsl_auth_required': cdsl_auth,
}
```

`OrderExecutor.monitor_holdings()` keeps the position open on CDSL failure, sends a Telegram alert once, and retries next cycle:

```@/Users/mithileshsinha/CascadeProjects/ai_trading_agent/src/order_executor.py:312-330
if order_result.get('cdsl_auth_required'):
    # Position stays OPEN — bot will retry next cycle once authorised
    ...
    if position.symbol not in self._cdsl_alert_sent:
        self.telegram._send(...)
        self._cdsl_alert_sent.add(position.symbol)
```

This is correct. The actual TPIN submission must be done manually in Kite, which is unavoidable.

---

## 9. Retry Mechanism ✅

`MarketDataFetcher._kite_call_with_retry()` retries with exponential backoff (1s, 2s, 4s):

```
WARNING:market_data:Kite API error (attempt 1/3): simulated failure — retry in 1s
WARNING:market_data:Kite API error (attempt 2/3): simulated failure — retry in 2s
Retry result: {'success': True, 'attempts': 3}
```

Circuit breaker opens after 5 failures and pauses for 30s.

**Limitation:** retries are triggered by any exception, not specifically by retryable errors. Non-retryable errors (e.g., `InputException` for invalid parameters) are still retried, wasting time and API quota.

---

## 10. API Rate Limits ⚠️

The only explicit rate limiter is the 0.35s gap between historical API calls:

```@/Users/mithileshsinha/CascadeProjects/ai_trading_agent/src/market_data.py:211-214
elapsed = time.time() - MarketDataFetcher._last_historical_request
if elapsed < MarketDataFetcher._min_historical_interval:
    time.sleep(MarketDataFetcher._min_historical_interval - elapsed)
```

There is **no global rate limiting** across:
- `SignalGenerator` ThreadPoolExecutor (8 workers) running quote/historical/LTP calls
- `DynamicUniverse` batch quotes
- `OrderExecutor` LTP calls

Kite Connect limits are approximately **3 req/sec** for most endpoints. Parallel scanning can exceed this, causing 429 errors.

**Issue:** no specific handling of HTTP 429 / rate-limit responses. The generic retry waits 1, 2, 4s regardless of the actual rate-limit reset window.

**Recommendation:**
- Add a global token-bucket rate limiter shared across all Kite callers.
- Catch rate-limit-specific exceptions and honor any retry-after header.
- Use `kite.quote()` batch calls instead of individual LTP where possible.

---

## Test Suite Status ❌

The existing tests fail to collect because of inconsistent imports and missing `src/__init__.py`:

```
E   ModuleNotFoundError: No module named 'src.config'
E   ModuleNotFoundError: No module named 'token_manager'
```

Test files mix `from src.config import config` and `from token_manager import TokenManager`, but `src` is not a package.

**Recommendation:** add `src/__init__.py` and standardize imports, or use `pytest` rootdir/project config.

---

## Summary Table

| Flow | Status | Notes |
|------|--------|-------|
| Login / token init | ✅ | Works with stored token |
| Token auto-refresh | ❌ | Manual only; no proactive expiry alert |
| Session expiry detection | ⚠️ | Detected after 2 failures |
| Holdings fetch | ✅ | Normalized dict returned |
| Positions fetch | ⚠️ | Only day positions returned |
| Order placement (paper) | ✅ | Works; real path params correct |
| Order modification | ❌ | Not implemented |
| Order cancellation | ✅ | Works |
| CDSL / TPIN handling | ✅ | Detected, alerts, retries next cycle |
| Retry mechanism | ✅ | 3 attempts with backoff + circuit breaker |
| API rate limits | ⚠️ | Historical only; no global limiter |
| Test suite | ❌ | Import errors prevent collection |

---

## Recommended Fix Priority

1. **Add proactive token-expiry alert** before the 1-hour buffer runs out.
2. **Implement `BrokerIntegration.modify_order()`** to update pending limit prices.
3. **Add a global rate limiter** across all Kite API callers.
4. **Fix test suite imports** (`src/__init__.py` + standardized imports).
5. **Make `get_positions()` semantics explicit** or return both day and net positions.
6. **Differentiate retryable vs non-retryable Kite exceptions** to avoid wasted retries.
