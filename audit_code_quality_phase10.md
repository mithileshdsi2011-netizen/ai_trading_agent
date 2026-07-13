# Phase 10 — Code Quality Audit Report

## Scope
PEP8, naming, complex functions, unused imports, duplicate logic, magic numbers, long methods, code smells, documentation, type hints, and comments.

## Summary
- **Three functions are extremely long and complex** (`api_data`, `run_once`, `_generate_morning_report`), making them hard to test, debug, and maintain.
- **35 functions exceed a reasonable cyclomatic complexity threshold**, including several above 50.
- **17 unused imports** and **158 `print()` statements** (should use logger) were found.
- **Duplicate sector mapping** exists inside `order_executor.py`.
- **Type hints and docstrings are sparse:** 69 functions lack type hints, 62 lack docstrings.
- **Positive:** no `eval`/`exec`, no bare `except`, no mutable default arguments, no wildcard imports, and naming is generally PEP8-compliant.

---

## Tooling
- `flake8` and `pylint` are not installed, so analysis was performed with custom AST-based scripts.
- Metrics: function length (lines), cyclomatic complexity (counts of `if`/`for`/`while`/`except`/`with`/`bool`), unused imports, print statements, line length, docstrings, type hints, duplicate blocks.

---

## Critical

### 1. `api_data()` is 738 lines with cyclomatic complexity 202
```@/Users/mithileshsinha/CascadeProjects/ai_trading_agent/dashboard.py:3719
def api_data():
```

This single endpoint does everything: loads positions, holdings, orders, enriches them, calculates portfolio health, analytics, signals, IP status, and bot status. A bug in any one section is hard to isolate; unit testing is nearly impossible.

**Fix:** decompose into focused helpers:
- `_load_positions(kite)`
- `_load_orders(kite)`
- `_calculate_portfolio_health(data)`
- `_build_analytics(data)`
- `_build_signals(data)`
- `_build_ip_status()`

Then `api_data()` becomes a composition of these helpers.

---

### 2. `TradingOrchestrator.run_once()` is 646 lines with complexity 140
```@/Users/mithileshsinha/CascadeProjects/ai_trading_agent/src/trading_orchestrator.py:79
def run_once(self) -> Dict:
```

The function handles market-open checks, token health, regime detection, universe building, signal generation, scoring, multi-timeframe confirmation, risk checks, order execution, position monitoring, and performance tracking all in one method.

**Fix:** extract sub-tasks into small private methods (e.g., `_kite_health_check`, `_build_signal_pipeline`, `_execute_signals`, `_monitor_and_exit`). `run_once()` should read like a high-level checklist.

---

### 3. `_generate_morning_report()` is 356 lines with complexity 94
```@/Users/mithileshsinha/CascadeProjects/ai_trading_agent/dashboard.py:369
def _generate_morning_report():
```

Contains nine separate report sections inline. Each section has its own data fetching, parsing, and formatting.

**Fix:** split each section into its own helper (`_market_overview`, `_sector_strength`, `_top_gainers`, etc.) and assemble the report dict from them.

---

## High

### 4. 36 functions exceed 60 lines

Top offenders:

| Lines | File | Function |
|-------|------|----------|
| 738 | `dashboard.py` | `api_data` |
| 646 | `src/trading_orchestrator.py` | `run_once` |
| 356 | `dashboard.py` | `_generate_morning_report` |
| 277 | `dashboard.py` | `api_ask` |
| 193 | `dashboard.py` | `_run_background_scan` |
| 164 | `src/order_executor.py` | `monitor_holdings` |
| 161 | `src/backtester.py` | `results` |
| 160 | `src/trade_journal.py` | `log_entry` |
| 160 | `src/trade_scorer.py` | `score` |
| 132 | `src/order_executor.py` | `_load_existing_positions` |

Functions longer than 60 lines usually do more than one thing and are harder to reason about.

### 5. 35 functions exceed complexity 10

Top offenders:

| Complexity | File | Function |
|------------|------|----------|
| 202 | `dashboard.py` | `api_data` |
| 140 | `src/trading_orchestrator.py` | `run_once` |
| 94 | `dashboard.py` | `_generate_morning_report` |
| 75 | `dashboard.py` | `api_ask` |
| 60 | `dashboard.py` | `_run_background_scan` |
| 51 | `src/technical_analysis.py` | `get_technical_score` |
| 40 | `src/order_executor.py` | `monitor_holdings` |
| 37 | `src/trade_scorer.py` | `score` |

### 6. 17 unused imports

| File | Import | Line |
|------|--------|------|
| `get_kite_token.py` | `jsonify` | 5 |
| `src/backtester.py` | `annotations` | 18 |
| `src/backtester.py` | `date` | 24 |
| `src/broker_integration.py` | `json` | 8 |
| `src/broker_integration.py` | `KiteConnect` | 38 |
| `src/broker_integration.py` | `urllib.request` | 239 |
| `src/dynamic_universe.py` | `json` | 424 |
| `src/market_data.py` | `np` | 6 |
| `src/order_executor.py` | `time` | 8 |
| `src/risk_manager.py` | `field` | 12 |
| `src/risk_manager.py` | `np` | 16 |
| `src/sentiment_analysis.py` | `requests` | 5 |
| `src/sentiment_analysis.py` | `BeautifulSoup` | 6 |
| `src/sentiment_analysis.py` | `re` | 10 |
| `src/technical_analysis.py` | `np` | 6 |
| `src/trade_journal.py` | `date` | 10 |
| `src/trading_orchestrator.py` | `SCORE_SKIP` | 22 |

Unused imports slow startup, increase memory, and can mask real dependencies.

### 7. 158 `print()` statements

Many modules use `print()` for diagnostics instead of the logger. This produces inconsistent output and bypasses log rotation. `src/backtester.py`, `src/main.py`, and `dashboard.py` are the main offenders.

**Fix:** replace `print()` with `logger.info()` / `logger.error()` consistently.

### 8. Duplicate sector map in `order_executor.py`

The same inline `_SECTOR_MAP` is defined twice inside `execute_signal()`:

```@/Users/mithileshsinha/CascadeProjects/ai_trading_agent/src/order_executor.py:405-422
_SECTOR_MAP = {
    'HDFCBANK':'Banking', ...
}
```

```@/Users/mithileshsinha/CascadeProjects/ai_trading_agent/src/order_executor.py:540-562
_SECTOR_MAP2 = {
    'HDFCBANK':'Banking', ...
}
```

Both maps have identical contents. This violates DRY and risks divergence.

**Fix:** import `SECTOR_MAP` from `dynamic_universe` (already defined there).

---

## Medium

### 9. Long files

| File | Lines |
|------|-------|
| `dashboard.py` | 4,904 |
| `src/trading_orchestrator.py` | 1,500 |
| `src/order_executor.py` | 667 |
| `src/risk_manager.py` | 617 |
| `src/backtester.py` | 597 |
| `src/market_data.py` | 468 |
| `src/dynamic_universe.py` | 459 |
| `src/technical_analysis.py` | 402 |

`dashboard.py` is especially large. Consider splitting it into:
- `routes.py` (Flask endpoints)
- `renderers.py` (HTML builders)
- `data_aggregation.py` (api_data helpers)
- `reporting.py` (morning report)

### 10. 17 functions are deeply nested (depth ≥ 4)

Examples:
- `dashboard._run_background_scan`: depth 7
- `backtester.process_bar`: depth 6
- `technical_analysis.get_technical_score`: depth 6
- `trading_orchestrator.main`: depth 6
- `trading_orchestrator.run_once`: depth 5
- `dashboard.api_data`: depth 5

Deep nesting hurts readability and indicates missing helper extraction.

### 11. 336 lines in `dashboard.py` exceed 120 characters

Long lines are mostly HTML/JS strings and inline styles. They make diffs noisy and code review harder.

**Fix:** break long strings across lines or move HTML templates to separate files.

### 12. `logging.basicConfig` duplicated in 19 files

Nearly every module calls:

```python
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
```

Only the first call has any effect. The rest is boilerplate noise. More importantly, it fragments log configuration.

**Fix:** configure logging once at application entry points (`dashboard.py`, `trading_orchestrator.py`) and use `logger = logging.getLogger(__name__)` in modules.

---

## Low

### 13. 69 functions lack type hints

Type hints are missing from most Flask routes and many helper functions. Adding them would improve IDE support and catch integration bugs.

### 14. 62 functions lack docstrings

Many private helpers and route handlers have no docstring. While some names are descriptive, complex ones like `_run_background_scan` and `calculate_pnl` would benefit from documentation.

### 15. Magic numbers (232 occurrences)

Many literal numbers are used without named constants. Examples from `dashboard.py`:
- `15` (cache TTL minutes)
- `60` (cap on displayed stocks)
- `500` (backtest cache cap)
- `999` (sentinel profit factor)
- `0.35` (historical API rate limit)

While many are benign, important business constants should be named.

### 16. Module-level lowercase constants in `src/live_analysis.py` and `src/quick_scan.py`

`results`, `buys`, `holds` are defined at module level and mutated. They should be function-local or uppercase constants if truly global.

---

## Positive Findings

| Check | Result |
|-------|--------|
| `eval()` / `exec()` | None found |
| Bare `except:` | None found |
| Mutable default arguments | None found |
| `from module import *` | None found |
| CamelCase function names | None found |
| Hardcoded absolute paths | None found |

---

## Recommended Fix Priority

1. **Refactor `api_data()`** into focused helpers (highest impact).
2. **Refactor `run_once()`** into smaller private methods.
3. **Refactor `_generate_morning_report()`** by section.
4. **Remove 17 unused imports**.
5. **Replace `print()` calls with logger calls**.
6. **Remove duplicate `_SECTOR_MAP`** and import from `dynamic_universe`.
7. **Consolidate logging setup** to application entry points.
8. **Break up deeply nested functions** (depth ≥ 4).
9. **Add type hints and docstrings** to public and complex functions.
10. **Move large HTML blocks** out of `dashboard.py`.

## Recommended Next Phase
Phase 11 should be a focused refactoring sprint targeting the three critical functions (`api_data`, `run_once`, `_generate_morning_report`) with regression tests, followed by a clean linting pass once `flake8`/`pylint` are installed.
