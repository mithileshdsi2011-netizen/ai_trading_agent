# Phase 8 — Performance Audit Report

## Scope
CPU usage, RAM usage, thread count, background tasks, API latency, dashboard latency, scan duration, memory growth over time, resource leaks, file handle leaks, log growth, and cache growth.

## Summary
- **Both processes are lightweight at idle:** dashboard ~83 MB RSS / 3 threads; trading bot ~32 MB RSS / 11 threads.
- **No memory growth or resource leaks observed over a 2-minute sampling window** while hitting `/api/health` and `/api/data` every 10 s.
- **Dashboard page loads in ~13 ms; `/api/health` ~3 ms; `/api/data` ~460 ms.**
- **Concurrent load is the main bottleneck:** 20 parallel `/api/data` requests took 9.6 s (~2.1 req/s), and temporary RSS spiked to 101 MB.
- **Background signal scan takes ~16.7 s** for the full universe.
- **Logs are growing unbounded** because `autostart.sh` redirects stdout/stderr to fixed files with no rotation.
- **Heartbeat creates a new `KiteConnect` instance every 60 s**, which is wasteful and could contribute to resource pressure over very long uptimes.

---

## Processes and Baseline

| Process | PID | RSS | Threads | FDs | Open Files | Connections | CPU at idle |
|---------|-----|-----|---------|-----|------------|-------------|-------------|
| Dashboard | 6225 | ~83 MB | 3 | ~21 | 2 | 10 | 0.0 % |
| Trading bot | 6224 | ~32 MB | 11 | ~16 | 3 | 4 | 0.0 % |

Both processes have been running for ~30 minutes with stable resource counts.

---

## Latency Measurements

| Endpoint | Method | Avg latency | Notes |
|----------|--------|-------------|-------|
| `/` (redirect) | GET | 0.9 ms | 302 to `/?v=3` |
| `/` (full HTML) | GET | 13.5 ms | 169 KB HTML |
| `/static/chart.umd.min.js` | GET | 2.0 ms | 205 KB JS |
| `/api/health` | GET | 2.9 ms | Lightweight |
| `/api/data` | GET | 463 ms | Aggregates many Kite calls |
| `/api/journal` | GET | 93 ms | |
| `/api/morning-report` | GET | 1 ms | Cached |
| `/api/ask` | POST | 109 ms | Local answer |
| `/api/backtest` | POST | 2–4 s | Depends on symbol count/years |

20 sequential `/api/data` calls averaged **469 ms** (max 701 ms). 20 concurrent `/api/data` calls completed in **9.57 s**; the first finished in **6.43 s**.

---

## Memory Growth Over Time

A 2-minute sampler polled both processes every 10 s while calling `/api/health` and `/api/data`.

| Time | Dashboard RSS | Dashboard Threads | Dashboard FDs | Bot RSS | Bot Threads | Bot FDs |
|------|---------------|-------------------|---------------|---------|-------------|---------|
| 0 s | 82.7 MB | 3 | 21 | 31.9 MB | 11 | 16 |
| 30 s | 82.5 MB | 3 | 21 | 31.9 MB | 11 | 16 |
| 60 s | 82.5 MB | 3 | 21 | 31.9 MB | 11 | 16 |
| 90 s | 83.4 MB | 4 | 23 | 31.9 MB | 11 | 16 |
| 120 s | 83.5 MB | 3 | 21 | 31.9 MB | 11 | 16 |

**Conclusion:** no meaningful memory growth and no FD/thread leaks over the sampled window.

---

## Concurrent Load Test

20 concurrent `/api/data` requests:
- Total time: **9.57 s**
- Throughput: ~2.1 req/s
- Dashboard RSS temporarily spiked from **83 MB → 101 MB**
- RSS recovered to **90.5 MB** after 50 s

The spike is likely transient response data and not a leak, because memory began to fall back. However, the throughput is low because each `/api/data` performs sequential Kite API calls and the Flask development server cannot parallelise them effectively.

---

## Background Tasks

### Dashboard (`dashboard.py`)
| Task | Cadence | Impact |
|------|---------|--------|
| `_heartbeat_loop` | every 60 s | psutil CPU/mem + Kite LTP probe |
| `_refresh_ip_cache` | every 5 min (background) | IP lookup |
| `_maybe_trigger_background_scan` | on page load if cache > 15 min | 150-stock scan |
| `_maybe_trigger_morning_report` | once per day | Morning report generation |

### Trading bot (`trading_orchestrator.py`)
| Task | Cadence | Impact |
|------|---------|--------|
| `run_once` | every 15 min | Full trading cycle |
| `end_of_day_close` | 14:55 IST | Square off |
| `daily_email_report` | 16:00 IST | Email summary |
| `pre_market_check` | 09:20 IST | Pre-market checks |
| `daily_reset` | 09:00 IST | Reset daily counters |
| `_check_ip_whitelist` | every 30 min | IP change alert |

### Scan Duration
`/api/health` reported `scan_time_s: 16.7` with `signals_count: 53`. This consumes a noticeable portion of each 15-minute trading cycle but is currently acceptable.

---

## Resource Leaks

No leaks detected in the 2-minute window:
- Dashboard file descriptors: stable at 21 (brief spike to 23 under load).
- Trading bot file descriptors: stable at 16.
- Dashboard threads: stable at 3 (brief spike to 5 under load).
- Trading bot threads: stable at 11.

Longer-running monitoring (hours) is recommended to confirm.

---

## Log Growth

Current log sizes:

| Log file | Size | Rotated? |
|----------|------|----------|
| `logs/dashboard.log` | 7.4 MB | No |
| `logs/trading.log` | 4.1 MB | No |
| `logs/start_trading.log` | 2.5 MB | No |
| `logs/autostart.log` | 4.9 KB | No |

`autostart.sh` redirects stdout/stderr to fixed files:

```@/Users/mithileshsinha/CascadeProjects/ai_trading_agent/autostart.sh:84-91
"$PYTHON" "$PROJ/dashboard.py" >> "$PROJ/logs/dashboard.log" 2>&1 &
...
"$PYTHON" "$PROJ/src/trading_orchestrator.py" scheduled 15 >> "$PROJ/logs/trading.log" 2>&1 &
```

There is no log rotation. Over days/weeks these files will grow without bound.

**Fix:** use `RotatingFileHandler` in Python or `logrotate` on the host.

---

## Cache Growth

| Cache | TTL | Size observed | Risk |
|-------|-----|---------------|------|
| `_SIGNAL_CACHE` | 15 min | 53 signals | Low |
| `_MORNING_CACHE` | daily | ~12 KB on disk | Low |
| `_IP_CACHE` | 5 min | tiny | Low |
| `_BT_CACHE` | 30 min | up to 10 entries | Low to medium |
| `MarketDataFetcher.cache` | 30–60 s | up to 500 DataFrames | Medium |

The `MarketDataFetcher` cache can hold up to 500 DataFrames. With daily data (~20–100 rows per frame), this is a few MB at most. However, if intraday frames or larger periods are cached, it could grow. Cache eviction logic exists but is not thread-safe.

---

## High

### 1. No log rotation
As noted above, logs are appended to fixed files indefinitely. The dashboard process was actually writing to `start_trading.log` in this session, so the largest file (`dashboard.log`, 7.4 MB) is stale but still unbounded in the long run.

**Fix:** switch to `RotatingFileHandler(maxBytes=5*1024*1024, backupCount=5)` or configure `logrotate`.

### 2. `/api/data` is a monolithic, synchronous bottleneck
Every dashboard refresh pulls everything via `/api/data`, which makes multiple sequential Kite API calls. Under 20 concurrent requests, throughput drops to ~2.1 req/s and latency increases sharply.

**Fix:** split into smaller endpoints (e.g., `/api/positions`, `/api/orders`, `/api/signals`) so the dashboard can load tabs independently and the browser fetches only what is visible.

### 3. Heartbeat creates a new `KiteConnect` every 60 s
```@/Users/mithileshsinha/CascadeProjects/ai_trading_agent/dashboard.py:62-68
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))
from token_manager import TokenManager
_tm = TokenManager()
_kite = _tm.initialize_kite()
if _kite:
    t0 = time.time()
    _kite.ltp(["NSE:NIFTY 50"])
```

Each heartbeat loads the token file, creates a `KiteConnect`, and calls `profile()` (via `initialize_kite`). Over months of uptime this wastes CPU and may leak small amounts of memory or connections.

**Fix:** reuse the same `KiteConnect` instance held by the dashboard or share it with `MarketDataFetcher`.

---

## Medium

### 4. Trading bot uses 11 threads
A single-threaded scheduler loop plus Kite calls should not need 11 threads. Some of these are likely daemon threads spawned by imported libraries (e.g., `kiteconnect`, background tasks). This is not necessarily a problem, but it is worth auditing which threads are active and whether any are stuck.

**Fix:** periodically dump `threading.enumerate()` to logs for diagnostics.

### 5. Background scan competes with dashboard refresh
Both the scan and `/api/data` use the same Kite connection and the same `_SIGNAL_CACHE_LOCK`. While the lock is brief, the CPU and Kite rate-limit are shared. Calling `/api/data` while a scan is in progress can push response time above 1 s.

**Fix:** decouple scan state from request path; optionally return stale signals if a scan is running rather than blocking.

### 6. `MarketDataFetcher` cache is not thread-safe
Cache reads/writes in `get_stock_data()` are not protected by a lock. Concurrent scans and dashboard requests could race during eviction.

**Fix:** add a lock around cache mutation.

---

## Low

### 7. `psutil.cpu_percent(interval=1)` blocks heartbeat for 1 s
The heartbeat thread samples CPU with a 1-second blocking call. This runs in its own thread, so it does not block HTTP requests, but it is a fixed overhead every 60 s.

### 8. Backtest cache can store large objects
Each backtest result can include a large `trades` list and equity curve. Cache size is capped at 10 entries, but if users run multi-year backtests on large watchlists, each entry could be multi-MB.

### 9. Intraday historical calls are slow
Fetching 5 days of 15-minute data for one symbol took **395 ms** in testing. A full scan that needs intraday data would be much slower.

---

## Recommended Next Steps

1. **Add log rotation** to prevent disk exhaustion.
2. **Split `/api/data`** into smaller, tab-specific endpoints.
3. **Reuse Kite instance** in the heartbeat loop.
4. **Long-run memory test:** sample processes for several hours to confirm no leaks.
5. **Profile a full trading cycle** to identify which step (signal generation, Kite calls, regime detection) consumes the most time.

## Recommended Next Phase
Phase 9 should be a full End-to-End Live/Paper Trade Cycle: restart with fixes, run one complete BUY-and-SELL loop, and measure resource usage before, during, and after the cycle.
