# Phase 7 — Broker Integration Audit Report

## Scope
Authentication, token refresh, order placement, order status, position/holding/LTP/historical data fetching, rate limiting, retries, circuit breaker, timeouts, network failure handling, and CDSL authorization flow.

## Summary
- **Read-only broker calls are healthy** and fast (positions, holdings, margins, orders, LTP, historical, intraday all return data in <0.4 s).
- **Token is valid** and profile fetch succeeds.
- **Critical gaps found:** no automatic token refresh, no retry/circuit breaker on order/positions/order-status calls, `BrokerIntegration.get_positions()` returns wrong Kite position subset, circuit breaker over-counts failures, and order placement has an in-flight race window.
- **CDSL flow is detected** on SELL errors and alerts are sent, but there is no proactive check.

---

## Verification Performed

| Check | Method | Result |
|-------|--------|--------|
| Token file load | Direct import | ✅ Loaded, valid with 1 h buffer |
| Profile fetch | `tm.initialize_kite().profile()` | ✅ User Mithilesh Prasad |
| Positions fetch | `kite.positions()` | ✅ day=1, net=1 |
| Holdings fetch | `kite.holdings()` | ✅ 6 holdings |
| Margins fetch | `kite.margins()` | ✅ equity net ₹6704.6 |
| Orders fetch | `kite.orders()` | ✅ 1 order |
| LTP fetch | `mdf.get_realtime_price('RELIANCE')` | ✅ ₹1307.8 in 0.028 s |
| Stock info | `mdf.get_stock_info('RELIANCE')` | ✅ price, OHLC, volume |
| Historical data | `mdf.get_stock_data('RELIANCE', '1mo', '1d')` | ✅ 22 rows in 0.044 s |
| Intraday data | `mdf.get_intraday_data('RELIANCE', 1)` | ✅ 25 rows in 0.395 s |
| Historical rate limit | 5 rapid requests | ✅ ~0.39 s spacing enforced |

---

## Critical

### 1. No automatic token refresh
`TokenManager.get_access_token()` checks `is_token_valid()` and simply raises `ValueError` if expired:

```@/Users/mithileshsinha/CascadeProjects/ai_trading_agent/src/token_manager.py:93-104
if self.is_token_valid():
    logger.info("Using existing valid token from storage")
    return self.access_token

raise ValueError(
    "Access token expired. Please run: python get_kite_token.py\n"
    ...
)
```

`_refresh_token(request_token)` exists but is **never called**. The bot cannot self-heal after a token expires during trading hours; it falls back to paper trading only in `BrokerIntegration.__init__`, and the live process later fails every Kite call.

**Fix:** store a refresh token or schedule re-auth before expiry; at minimum, detect "Invalid token" errors and prompt for re-authentication instead of silently failing every order.

---

### 2. `BrokerIntegration.get_positions()` returns the wrong subset
`_get_real_positions()` returns `positions.get('day', [])` instead of `positions.get('net', [])`:

```@/Users/mithileshsinha/CascadeProjects/ai_trading_agent/src/broker_integration.py:323-330
def _get_real_positions(self) -> List[Dict]:
    try:
        positions = self.kite.positions()
        return positions.get('day', [])
    except Exception as e:
        logger.error(f"Error getting positions: {e}")
        return []
```

The `day` array contains only intraday positions. CNC/delivery positions are in `net` and `holdings`. Any module that relies on `BrokerIntegration.get_positions()` will miss delivery positions.

**Fix:** return `positions.get('net', [])` or merge `net` and `holdings`.

---

### 3. Order execution has an in-flight race window
In `execute_signal()` the symbol is removed from `_pending_order_symbols` immediately after `place_order()` returns `success=True`:

```@/Users/mithileshsinha/CascadeProjects/ai_trading_agent/src/order_executor.py:376-380
order_result = self.broker.place_order(signal)

if order_result['success']:
    self._pending_order_symbols.discard(sym)
```

Kite `place_order()` returns an order ID as soon as the order is **accepted**, not when it is **filled**. If the next trading cycle runs before the fill is confirmed, the symbol is no longer "pending" and a duplicate BUY order can be placed.

**Fix:** keep the symbol in `_pending_order_symbols` until the order status is `COMPLETE` (or `REJECTED`/`CANCELLED`), confirmed by polling `kite.orders()`.

---

### 4. Circuit breaker over-counts failures
In `MarketDataFetcher._kite_call_with_retry()`, every retry attempt increments the failure counter:

```@/Users/mithileshsinha/CascadeProjects/ai_trading_agent/src/market_data.py:103-108
except Exception as exc:
    last_exc = exc
    with MarketDataFetcher._cb_lock:
        MarketDataFetcher._cb_failures += 1
        if MarketDataFetcher._cb_failures >= MarketDataFetcher._CB_MAX_FAILURES:
            MarketDataFetcher._cb_open_until = time.time() + MarketDataFetcher._CB_RESET_SECONDS
```

With `max_retries=3`, a single failing symbol adds **3 failures**. The breaker opens after 5 failures, so roughly **two transiently failing symbols** will shut the circuit for 30 seconds, blocking all Kite data calls.

**Fix:** increment the failure counter only after all retries for a given call are exhausted, not on every attempt.

---

## High

### 5. No retry / circuit breaker / rate limiting in `BrokerIntegration`
All order and account calls (`place_order`, `cancel_order`, `get_positions`, `get_holdings`, `get_order_status`, `kite.margins()`) call Kite directly with no retry, no circuit breaker, and no explicit rate limiting.

```@/Users/mithileshsinha/CascadeProjects/ai_trading_agent/src/broker_integration.py:180-190
order_id = self.kite.place_order(
    variety=self.kite.VARIETY_REGULAR,
    exchange=self.kite.EXCHANGE_NSE,
    ...
)
```

A transient network blip or a 429 rate-limit response will fail the order permanently for that cycle.

**Fix:** wrap broker calls in the same `_kite_call_with_retry()` helper used by `MarketDataFetcher` (or a shared one), with a separate failure counter for order/account endpoints.

### 6. No explicit timeout wrapping
Both `BrokerIntegration` and `TokenManager` rely on `kiteconnect` internal timeouts. There is no application-level timeout (e.g., 10 s) to fail fast if Kite hangs.

**Fix:** use `requests` timeout parameters or a wrapper that aborts calls exceeding a configured timeout.

### 7. Order status is not polled after placement
`place_order()` returns `status: 'PENDING'`, but nothing polls `kite.order_history(order_id)` or `kite.orders()` to confirm fill/rejection. The bot assumes acceptance == completion and immediately opens the position in `RiskManager`.

**Fix:** poll order status until terminal state (`COMPLETE`/`REJECTED`/`CANCELLED`) before updating `RiskManager`.

### 8. Paper portfolio is not persisted
`BrokerIntegration.paper_portfolio` is in-memory only:

```@/Users/mithileshsinha/CascadeProjects/ai_trading_agent/src/broker_integration.py:25-29
self.paper_portfolio = {
    'cash': config.TRADING_AMOUNT,
    'positions': {},
    'orders': []
}
```

On restart, paper cash/positions are reset even though the journal persists trades.

**Fix:** persist `paper_portfolio` to disk on every change and load it on startup.

### 9. `_get_instruments()` has no retry or circuit breaker
The instruments cache load is a single Kite call with no retry. On first run or after a 4-hour TTL expiry, a transient failure returns an empty list, breaking all symbol lookups.

```@/Users/mithileshsinha/CascadeProjects/ai_trading_agent/src/market_data.py:55-73
def _get_instruments(self) -> List[Dict]:
    ...
    instruments = self.kite.instruments("NSE")
```

**Fix:** wrap this call in `_kite_call_with_retry()`.

---

## Medium

### 10. Limit orders may not fill without monitoring
`_place_real_order()` submits LIMIT orders with a 1% buffer but never follows up. If the price moves away, the order remains open and unfilled while `RiskManager` thinks the position exists.

**Fix:** after placing, poll order status; if not filled within a short window, cancel/modify.

### 11. Order placement does not validate signal fields
`_place_real_order()` assumes `signal['symbol']`, `signal['current_price']`, and `signal['position_size']` exist. A malformed signal will raise an exception rather than a clean error.

**Fix:** validate required fields and return a structured error.

### 12. CDSL alert set resets on restart
`OrderExecutor._cdsl_alert_sent` prevents same-day alert spam, but it is in-memory. After a restart, the alert is sent again.

**Fix:** persist the set (or use date-based flag) to disk.

### 13. `get_order_status()` fetches all orders
For each order-status check, the code downloads the entire order book:

```@/Users/mithileshsinha/CascadeProjects/ai_trading_agent/src/broker_integration.py:385-390
orders = self.kite.orders()
for order in orders:
    if order['order_id'] == order_id:
        return order
```

This is fine for a few orders but inefficient for large order books.

**Fix:** use `kite.order_history(order_id)` or `kite.order_trades(order_id)`.

### 14. No proactive CDSL auth check
The bot only discovers CDSL TPIN requirement when a SELL is rejected. There is no pre-flight check, so the first sell of the day may fail and the position has to wait another cycle.

**Fix:** optional: attempt a tiny authorised-quantity probe or surface a readiness check before market open.

---

## Low

### 15. `NSE_HOLIDAYS` list contains a duplicate
`"2025-10-02"` appears twice:

```@/Users/mithileshsinha/CascadeProjects/ai_trading_agent/src/market_data.py:428
"2025-10-02", "2025-10-02",
```

Harmless but should be cleaned.

### 16. `MarketDataFetcher` cache eviction is not thread-safe
The in-memory `self.cache` dict is accessed without locks. Multiple threads calling `get_stock_data()` could race during cache eviction.

**Fix:** protect cache mutations with a lock.

### 17. `TokenManager._save_token()` hardcodes 23-hour expiry
It does not use the actual token expiry returned by Kite. If Kite ever issues a shorter-lived token, the bot will think it is valid longer than it is.

**Fix:** use the actual expiry from Kite's response.

---

## Recommended Fix Priority

1. **Implement automatic token refresh** or re-auth prompt on token expiry.
2. **Fix `BrokerIntegration.get_positions()`** to return `net` positions.
3. **Fix the order in-flight race window** by keeping symbols pending until terminal fill state.
4. **Fix circuit breaker failure counting** in `MarketDataFetcher`.
5. **Add retry/circuit breaker/rate limiting to `BrokerIntegration`**.
6. **Add application-level timeouts** around all Kite calls.
7. **Poll order status** after placement before updating `RiskManager`.
8. **Persist paper portfolio** across restarts.
9. **Add retry to `_get_instruments()`**.
10. **Fix holiday list duplicate** and `TokenManager` hardcoded expiry.

## Recommended Next Phase
Phase 8 should be a live paper-trade end-to-end run: place a paper BUY, verify position creation, trigger a paper SELL via SL/target, and confirm journal/CDSL alerts and order-status polling work correctly.
