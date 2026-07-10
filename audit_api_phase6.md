# Phase 6 — API Audit Report

## Scope
All HTTP endpoints exposed by the project:
- `dashboard.py` (port 5001)
- `get_kite_token.py` (port 8080, only run during auth)

## Summary
- **All dashboard endpoints respond** and return valid JSON where expected.
- **Average `/api/data` response time is ~0.47 s** (max 0.70 s in 20 sequential calls); acceptable for current data size.
- **Thread-safety primitives are in place** for shared caches.
- **Three critical bugs found:**
  1. `/api/backtest` crashes with 500 whenever it generates trades (`name 'start_dt' is not defined`).
  2. `/api/backtest` returns 500 for invalid `years` (should be 400).
  3. `/api/ask` crashes when `question` is `null` (should be 400).
- **Race condition:** concurrent backtest requests for the same key all execute before the first one can populate the cache (thundering herd).
- **No overall request timeout** on `/api/data`; a slow Kite call can make the whole dashboard refresh hang.
- **404 returns HTML**, not JSON — inconsistent for a JSON API.
- **Token server** is not running and exposes the access token in HTML.

---

## Endpoint Inventory

| File | Endpoint | Method | Purpose |
|------|----------|--------|---------|
| `dashboard.py` | `/` | GET | Serve dashboard HTML |
| `dashboard.py` | `/api/js-error` | POST | Client JS error logging |
| `dashboard.py` | `/api/data` | GET | Aggregated dashboard data |
| `dashboard.py` | `/api/ask` | POST | AI chat / local Q&A |
| `dashboard.py` | `/api/journal` | GET | Trade journal analytics |
| `dashboard.py` | `/api/morning-report` | GET | Cached morning report |
| `dashboard.py` | `/api/morning-report/refresh` | POST | Force regenerate report |
| `dashboard.py` | `/api/health` | GET | System health |
| `dashboard.py` | `/api/backtest` | POST | Historical backtest |
| `get_kite_token.py` | `/` | GET | Token handler home |
| `get_kite_token.py` | `/login` | GET | Kite OAuth callback |

---

## Response Time & JSON Validity

| Endpoint | Avg time | JSON valid? | Notes |
|----------|----------|-------------|-------|
| GET `/` | 0.015 s | No (HTML) | 169 KB HTML |
| GET `/api/data` | 0.469 s | Yes | ~37 KB; 20 sequential calls max 0.70 s |
| GET `/api/health` | 0.001 s | Yes | 419 bytes |
| GET `/api/journal` | 0.093 s | Yes | ~15 KB |
| GET `/api/morning-report` | 0.001 s | Yes | Cached |
| POST `/api/js-error` | 0.001 s | Yes | Returns `{"ok":true}` |
| POST `/api/ask` | 0.109 s | Yes | Local answer path |
| POST `/api/backtest` | 2.1–4.0 s | Yes (unless 500) | Varies by symbol/years |
| POST `/api/morning-report/refresh` | 0.001 s | Yes | Triggers background thread |

20 concurrent `/api/health` requests completed in **0.012 s** — server is threaded and handles lightweight concurrency well.

---

## Critical

### 1. `/api/backtest` crashes with 500 whenever trades are generated
`_Portfolio.results()` references `start_dt` and `end_dt`, but the method parameters are named `start` and `end`:

```@/Users/mithileshsinha/CascadeProjects/ai_trading_agent/src/backtester.py:502
_eq_dates = pd.date_range(start=start_dt, end=end_dt, freq='B')  # business days
```

The variables `start_dt` and `end_dt` are defined in `Backtester.run()`, not in `_Portfolio.results()`.

**Impact:** Any backtest that produces at least one trade returns HTTP 500 with `"name 'start_dt' is not defined"`.

**Verification:**
```bash
POST /api/backtest {"symbols":["RELIANCE"],"years":2}
# => 500 {"error":"name 'start_dt' is not defined"}
```

**Fix:** change `start_dt` → `start` and `end_dt` → `end` in the `results()` method.

---

### 2. `/api/backtest` returns 500 for invalid `years` input
The endpoint converts `years` with `int(body.get('years', 2))` and does not catch `ValueError`:

```@/Users/mithileshsinha/CascadeProjects/ai_trading_agent/dashboard.py:4858
years = int(body.get('years', 2))
```

```bash
POST /api/backtest {"symbols":["RELIANCE"],"years":"abc"}
# => 500 {"error":"invalid literal for int() with base 10: 'abc'"}
```

**Fix:** wrap the conversion in a try/except and return HTTP 400 with a clear message.

---

### 3. `/api/ask` crashes when `question` is `null`
Line 4467 calls `.strip()` on the `question` value without coercing `None` to a string:

```@/Users/mithileshsinha/CascadeProjects/ai_trading_agent/dashboard.py:4467
question = (freq.get_json(force=True) or {}).get('question', '').strip()
```

```bash
POST /api/ask {"question":null}
# => 200 {"error":"Server error: 'NoneType' object has no attribute 'strip'"}
```

**Fix:** use `question = (body.get('question') or '').strip()`.

---

## High

### 4. Concurrent backtest requests cause a cache thundering herd
The cache check and write are protected by `_BT_LOCK`, but the **expensive backtest computation runs outside the lock**:

```@/Users/mithileshsinha/CascadeProjects/ai_trading_agent/dashboard.py:4868-4887
with _BT_LOCK:
    cached = _BT_CACHE.get(cache_key)
    if cached and (time.time() - cached['ts']) < 1800:
        return jsonify(cached['data'])

# <-- computation happens here, outside the lock -->

result = bt.run(symbols=symbols, years=years)

with _BT_LOCK:
    _BT_CACHE[cache_key] = {'data': result, 'ts': time.time()}
```

If 5 identical backtest requests arrive before the first completes, all 5 run the full backtest. Verified with 5 concurrent requests: total time 4.0 s and each request took ~4 s.

**Fix:** use a per-key "in-flight" lock/dict so only the first request runs the backtest; subsequent requests wait and then reuse the result.

### 5. `/api/data` has no overall request timeout
The endpoint makes multiple synchronous Kite API calls inside a single request. If any Kite call stalls, the entire dashboard refresh stalls. There is no `timeout=` wrapper around the whole endpoint, and the client-side dashboard refresh uses its own 60-second auto-refresh cadence.

**Fix:** add an overall per-endpoint timeout wrapper, or split `/api/data` into smaller endpoints so one slow call does not block everything.

### 6. 404 returns HTML instead of JSON
```bash
GET /api/nonexistent
# => 404 HTML page
```

This is inconsistent with the rest of the JSON API. A JSON client receives an HTML error body.

**Fix:** register a JSON 404 error handler with `app.register_error_handler(404, ...)`.

### 7. `/api/ask` returns HTTP 200 for client errors
Empty question, malformed JSON, and `null` question all return HTTP 200 with an `error` field. This forces the front-end to inspect the body to detect failures.

**Fix:** return HTTP 400 for malformed/empty requests.

---

## Medium

### 8. Token server exposes access token in HTML
`get_kite_token.py` renders the access token in the `/login` response HTML:

```@/Users/mithileshsinha/CascadeProjects/ai_trading_agent/get_kite_token.py:68-80
return f"""
...
<h3>Access Token (for reference):</h3>
<pre>{access_token}</pre>
...
"""
```

The token is also saved to `data/kite_token.json`. Displaying it in HTML increases the risk of accidental exposure via browser history, screenshots, or proxy logs.

**Fix:** return a generic success page without printing the token.

### 9. `/api/backtest` silently accepts malformed `symbols`
Passing a string instead of a list is not validated:

```bash
POST /api/backtest {"symbols":"RELIANCE","years":2}
# => 200 {"error":"No historical data fetched. Check Kite connection."}
```

The endpoint iterates over the string characters (`'R','E',...`), finds no valid data, and returns a confusing message.

**Fix:** validate that `symbols` is a list and return HTTP 400 otherwise.

### 10. `/api/morning-report/refresh` race window
The endpoint checks `generating` under the lock and then starts a thread. If two requests arrive nearly simultaneously, both could see `generating=False` and start two generation threads.

**Fix:** set `generating=True` inside the lock before releasing it and starting the thread.

---

## Low

### 11. `/api/data` response size grows linearly
Current response is ~37 KB. It will grow with more positions, orders, holdings, and signals. Monolithic design means every tab refresh pulls the entire payload.

**Fix:** split into `/api/positions`, `/api/orders`, `/api/signals`, etc.

### 12. Dashboard process memory usage is healthy
Current RSS: ~148 MB. Acceptable for the current data set.

### 13. Caching strategy is mostly sound
- `_SIGNAL_CACHE` TTL: 15 minutes with lock.
- `_MORNING_CACHE` TTL: daily with lock.
- `_IP_CACHE` TTL: 5 minutes with lock.
- `_BT_CACHE` TTL: 30 minutes with lock.

Cache invalidation is manual for the morning report refresh. No stale-cache issue observed for the current data.

---

## Recommended Fix Priority

1. **Fix the backtest `start_dt`/`end_dt` NameError** (critical — backtest is broken for any profitable run).
2. **Add input validation to `/api/backtest`** (invalid `years`, non-list `symbols`).
3. **Fix `/api/ask` null-question crash** and return HTTP 400 for bad input.
4. **Prevent backtest thundering herd** with per-key in-flight locks.
5. **Add a JSON 404 handler** for `/api/*` routes.
6. **Hide the Kite access token** in the token-server success page.
7. **Consider splitting `/api/data`** into smaller endpoints for better performance and maintainability.

## Recommended Next Phase
Phase 7 should be an End-to-End / Paper-Trading Validation: restart the dashboard after fixes, run a paper trade, and verify that every affected endpoint (`/api/data`, `/api/journal`, `/api/ask`, `/api/backtest`) returns correct, duplicate-free data.
