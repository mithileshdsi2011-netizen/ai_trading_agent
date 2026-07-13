# Phase 12 — Deep Trading Logic Audit Report

## Scope
AI research, technical analysis, entry logic, exit logic, risk management, position sizing, ATR, trailing SL, partial booking, smart exit, re-entry logic, blacklist, duplicate-position prevention, CNC holdings, MIS positions.

## Summary
- **Critical:** SELL paths that go through `OrderExecutor.execute_signal()` are broken. This blocks both **research-based SELL signals** and **Smart Exit** exits.
- **Critical:** Position sizing does **not** use ATR/volatility, so risk per trade varies widely. The dedicated `volatility_position_size()` helper is dead code.
- **High:** The stop-loss in the generated signal (percentage-based) differs from the stop-loss actually used in `RiskManager.open_position()` (ATR-based with floor), so the bot’s real R:R can differ from what the scorer sees.
- **High:** CNC holdings run a separate, simplified exit path that does **not** implement partial profit booking.
- **Medium:** Re-entry engine state is in-memory only and is lost on restart.
- **Medium:** Paper portfolio mishandles partial SELLs.

---

## Critical

### 1. SELL orders via `execute_signal()` are blocked for both research signals and smart exits
`OrderExecutor.execute_signal()` validates every signal with `RiskManager.can_open_position()`:

```@/Users/mithileshsinha/CascadeProjects/ai_trading_agent/src/order_executor.py:363-369
if not self.risk_manager.can_open_position(signal):
    return {
        'success': False,
        'reason': 'Risk parameters not met',
        'signal': signal
    }
```

`can_open_position()` is designed for BUY signals:

```@/Users/mithileshsinha/CascadeProjects/ai_trading_agent/src/risk_manager.py:263-276
if any(p.status in (PositionStatus.OPEN, PositionStatus.PARTIAL) and p.symbol == symbol
       for p in self.positions):
    logger.warning(f"Duplicate position: {symbol} already open")
    return False

if signal['risk_reward_ratio'] < config.MIN_RISK_REWARD:
    logger.warning(f"R:R too low: {signal['risk_reward_ratio']:.2f}")
    return False
```

Both research-based SELLs and Smart Exit SELLs are constructed with `risk_reward_ratio=0` and target a symbol that already has an open position, so they fail.

```@/Users/mithileshsinha/CascadeProjects/ai_trading_agent/src/trading_orchestrator.py:624
execution_result = self.order_executor.execute_signal(sell_signal)
```

```@/Users/mithileshsinha/CascadeProjects/ai_trading_agent/src/trading_orchestrator.py:646-657
exec_r = self.order_executor.execute_signal({
    'symbol': se['symbol'], 'action': 'SELL', ...
    'risk_reward_ratio': 0, 'confidence': 1.0,
    ...
})
```

**Impact:**
- Research-generated SELL recommendations for held positions never execute.
- Smart Exit AI never sells positions.
- Only `monitor_positions()` (SL/target/trailing) and `monitor_holdings()` (CNC) actually exit positions.

**Fix:** route SELL signals around `can_open_position()` and validate only that a matching open/partial position exists with sufficient quantity.

---

### 2. Position sizing ignores ATR and volatility
`SignalGenerator._calculate_position_size()` uses fixed capital-per-slot sizing:

```@/Users/mithileshsinha/CascadeProjects/ai_trading_agent/src/signal_generator.py:104-121
def _calculate_position_size(self, current_price: float) -> int:
    max_investment = config.TRADING_AMOUNT / config.MAX_POSITIONS
    shares = int(max_investment / current_price)
    return max(1, shares)
```

It fetches ATR at line 61 but never uses it. `RiskManager.volatility_position_size()` exists but is never called:

```@/Users/mithileshsinha/CascadeProjects/ai_trading_agent/src/risk_manager.py:232-244
def volatility_position_size(capital_per_trade, entry_price, atr, risk_pct):
    risk_amount = config.TRADING_AMOUNT * risk_pct
    if atr > 0 and entry_price > 0:
        qty = int(risk_amount / atr)
        qty = max(1, min(qty, int(capital_per_trade / entry_price)))
    ...
```

**Impact:** a low-volatility stock and a high-volatility stock can receive the same capital allocation, leading to very different per-trade risk. The “2% risk per trade” intent in the config is not enforced.

**Fix:** size positions using `volatility_position_size()` or similar ATR-based sizing, then optionally scale by `score_result['size_fraction']`.

---

### 3. Signal SL ≠ actual position SL
`SignalGenerator._calculate_risk_parameters()` sets SL as a fixed percentage:

```@/Users/mithileshsinha/CascadeProjects/ai_trading_agent/src/signal_generator.py:150-160
stop_loss = current_price * (1 - sl_pct)
```

`RiskManager.open_position()` recomputes SL using ATR:

```@/Users/mithileshsinha/CascadeProjects/ai_trading_agent/src/risk_manager.py:307-314
sl_multiplier = float(os.environ.get('ATR_SL_MULTIPLIER', '2.0'))
stop_loss = self.atr_stop_loss(entry_price, atr, sl_multiplier)
sl_floor = entry_price * (1 - config.SWING_STOP_LOSS_PERCENTAGE)
stop_loss = max(stop_loss, sl_floor)
```

So the SL and R:R used in scoring/gating are different from the SL actually stored in the position. If ATR is wide, the real SL is wider than the signal assumed, worsening real R:R. If ATR is tight, the floor dominates and the SL equals the signal assumption, but the target may still be based on a resistance level rather than ATR-based R:R.

**Fix:** compute SL once, consistently, and pass it through the signal so all gates see the same number.

---

## High

### 4. CNC holdings path lacks partial-profit booking
`order_executor.monitor_holdings()` duplicates trailing-stop/SL/target logic but does **not** include the partial-profit logic from `RiskManager.check_positions()`.

```@/Users/mithileshsinha/CascadeProjects/ai_trading_agent/src/order_executor.py:171-334
def monitor_holdings(self) -> List[Dict]:
    ...
```

**Impact:** CNC holdings can only exit fully via SL, target, or max-hold-days. They never scale out at +5%, which is available for in-session positions.

**Fix:** reuse `RiskManager.check_positions()` for holdings too, or extract a single position-monitoring function that handles both CNC and in-session positions.

### 5. Partial profit skips full-target check in the same cycle
Already noted in Phase 11. `RiskManager.check_positions()` books a partial profit and `continue`s, so the remaining half is not evaluated for the full target in that cycle.

```@/Users/mithileshsinha/CascadeProjects/ai_trading_agent/src/risk_manager.py:413
continue
```

### 6. Paper portfolio mishandles partial SELLs
`_place_paper_order()` deletes the entire paper position regardless of the quantity sold:

```@/Users/mithileshsinha/CascadeProjects/ai_trading_agent/src/broker_integration.py:108-113
elif action == 'SELL':
    if symbol in self.paper_portfolio['positions']:
        position = self.paper_portfolio['positions'][symbol]
        self.paper_portfolio['cash'] += price * quantity
        del self.paper_portfolio['positions'][symbol]
```

A partial SELL (e.g., half) removes the full position from tracking while only crediting half the proceeds.

**Fix:** decrement position quantity by sold amount, and only delete when quantity reaches zero.

### 7. Re-entry engine is in-memory only
`_recently_sold` is a plain dict on `TradingOrchestrator`:

```@/Users/mithileshsinha/CascadeProjects/ai_trading_agent/src/trading_orchestrator.py:920-927
def _record_exit(self, symbol, exit_price, reason, pnl=0.0, confidence=0.0):
    self._recently_sold[symbol] = {...}
```

On bot restart the dict is empty, so cooldown, retrace, and score requirements are bypassed for symbols sold before the restart.

**Fix:** persist `_recently_sold` to disk (e.g., `data/recently_sold.json`) and load it on init.

---

## Medium

### 8. AIResearchAgent uses KeyError-prone access
```python
technical_signals['technical_score']
technical_signals['confidence']
sentiment_result['score']
```

If `TechnicalAnalyzer.generate_signals()` returns the insufficient-data dict or an older format, these keys can raise `KeyError`.

**Fix:** use `.get()` with sensible defaults.

### 9. Quick-screen RSI threshold contradicts technical score
Quick screen skips stocks with `RSI < 25` as “falling knives”:

```@/Users/mithileshsinha/CascadeProjects/ai_trading_agent/src/signal_generator.py:207-208
_SCREEN_RSI_LOW = 25.0   # oversold floor (below = skip, likely falling knife)
```

But `TechnicalAnalyzer.get_technical_score()` rewards `RSI < 30` with the highest RSI score:

```@/Users/mithileshsinha/CascadeProjects/ai_trading_agent/src/technical_analysis.py:229-230
if rsi < 30:
    score += 0.25   # Oversold - strong buy
```

So a stock with RSI 26-30 is skipped by the fast gate despite being a strong-buy signal in the full analysis.

### 10. Volume pre-screen does not check today vs average
The comment says “Volume: today > 50% of 10-day avg,” but the code only checks that the 10-day average is greater than 10,000:

```@/Users/mithileshsinha/CascadeProjects/ai_trading_agent/src/signal_generator.py:241-244
vol_avg = float(volume.iloc[-11:-1].mean())
vol_ok  = vol_avg > 10_000
```

Today’s volume is never compared to the average.

### 11. “Sector momentum” is actually relative strength vs Nifty
`AIResearchAgent.research_stock()` computes `sector_momentum` as the 5-day return of the stock relative to Nifty 50. It has nothing to do with the stock’s actual sector.

```@/Users/mithileshsinha/CascadeProjects/ai_trading_agent/src/ai_research_agent.py:69-81
nifty = self.market_data.get_stock_data('NIFTY 50', ...)
stock_ret = ...
nifty_ret = ...
rel = float(np.clip((stock_ret - nifty_ret) * 10, -1, 1))
research_result['sector_momentum'] = rel
```

This is misleading naming and can distort the `TradeScorer.sector` component.

### 12. Technical score weights sum to 1.2
Documented weights: RSI 0.25 + MACD 0.20 + MA 0.20 + price-vs-SMA20 0.10 + BB 0.15 + Stoch 0.10 + Volume 0.10 + Momentum 0.10 = 1.20. The score is clamped to ±1, so it is not a bug, but it compresses the scale and makes thresholds less intuitive.

### 13. Duplicate trailing-stop logic
Trailing-stop update, effective-stop calculation, and exit-reason logic appear in both:
- `RiskManager.check_positions()` (for in-session positions)
- `OrderExecutor.monitor_holdings()` (for CNC holdings)

Any future change must be made in two places, increasing the risk of divergence.

### 14. `position.slippage` is dead code
In `RiskManager._close_position()`:

```@/Users/mithileshsinha/CascadeProjects/ai_trading_agent/src/risk_manager.py:469-470
position.slippage = exit_price - position.exit_price  # 0 here
```

`position.exit_price` was just set to `exit_price`, so this is always zero.

---

## Low

### 15. `daily_trades` counter is not persisted
`RiskManager.save_positions()` writes `daily_pnl` and `daily_blacklist`, but not `daily_trades`. It is currently unused in trading decisions, but any future daily-trade limit would break across restarts.

### 16. AI recommendation thresholds are not configurable
The `STRONG_BUY`/`BUY`/`SELL` thresholds in `AIResearchAgent._generate_recommendation()` are hardcoded. Fine for now, but less flexible than env-driven thresholds.

### 17. Smart Exit uses daily RSI threshold >80, while TradeScorer penalizes >70
Minor inconsistency in overbought definition.

---

## CNC / MIS Handling

| Aspect | Status | Notes |
|--------|--------|-------|
| Product type selection | ✅ | CNC for swing, MIS for intraday based on `config.TRADING_MODE` |
| CNC holdings loaded at startup | ✅ | `kite.holdings()` loaded separately from `kite.positions()` |
| T+1 duplicate prevention | ✅ | Only settled `quantity` loaded from holdings; T+1 handled by positions() |
| CNC exit on CDSL auth failure | ✅ | Position kept open, alert sent, retried next cycle |
| CNC partial profit booking | ❌ | Not implemented in `monitor_holdings()` |
| MIS intraday close at cutoff | ✅ | `run_once()` closes all at `INTRADAY_CUTOFF` |

---

## Blacklist / Duplicate Prevention

| Check | Status | Notes |
|-------|--------|-------|
| Blacklist after SL | ✅ | Adds symbol to `_daily_blacklist`, persists, clears next day |
| Duplicate open position block | ✅ | `can_open_position()` rejects duplicate symbol |
| In-flight BUY deduplication | ⚠️ | `_pending_order_symbols` cleared on acceptance, creating fill race window |
| Sector concentration limit | ✅ | `TradingOrchestrator._sector_concentration_exceeded()` max 2 per sector |

---

## Recommended Fix Priority

1. **Fix SELL execution in `execute_signal()`** (critical — smart exits and research SELLs are dead).
2. **Unify position sizing with ATR** (critical — risk per trade is uncontrolled).
3. **Compute stop-loss once and pass it through the signal** (high — real vs assumed R:R mismatch).
4. **Add partial-profit booking to CNC holdings path** (high).
5. **Fix partial SELL in paper portfolio** (high).
6. **Persist re-entry engine state** (medium).
7. **Defensive `.get()` access in AIResearchAgent** (medium).
8. **Align quick-screen RSI with technical-score reward** or document the deliberate narrow band (medium).
9. **Fix volume pre-screen to compare today vs average** (medium).
10. **Extract common trailing-stop/exit logic** to remove duplication (medium).

## Recommended Next Phase
Phase 13 should be a focused live paper-trading run that intentionally triggers: research-based SELL, smart exit, partial profit, re-entry after cooldown, and a bot restart mid-trade, verifying that each logic branch behaves correctly after fixes are applied.
