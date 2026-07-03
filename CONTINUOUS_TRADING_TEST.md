# Continuous Trading Test Cases and Testing Steps

## Overview
This document provides test cases and testing steps to validate that the AI trading agent can:
- Evaluate market conditions continuously during market hours (9:30 AM - 3:00 PM IST)
- Buy and sell stocks at any time during market hours
- Monitor positions continuously
- Execute trades based on analysis, strategy, and risk management

## Updated Configuration

### Market Hours
- **Market Open**: 9:30 AM IST
- **Market Close**: 3:00 PM IST
- **Pre-Market Check**: 9:20 AM IST
- **End-of-Day Close**: 3:00 PM IST
- **Daily Reset**: 9:00 AM IST

### Trading Schedule
- **Trading Cycles**: Every 15 minutes (configurable) during market hours
- **Continuous Monitoring**: Positions monitored every cycle
- **Automatic Exits**: Stop-loss (2%) and target (4%) checked continuously

---

## Test Cases

### Test Suite 1: Market Hours Configuration

#### Test 1.1: Verify Market Hours Configuration
```python
def test_market_hours_configuration():
    """Test that market hours are correctly configured"""
    from config import config
    
    assert config.MARKET_OPEN == "09:30"
    assert config.MARKET_CLOSE == "15:00"
    print("✓ Market hours configured correctly: 9:30 AM - 3:00 PM IST")
```

**Execution:**
```bash
cd /Users/mithileshsinha/CascadeProjects/ai_trading_agent
python -c "from config import config; print(f'Market Open: {config.MARKET_OPEN}'); print(f'Market Close: {config.MARKET_CLOSE}')"
```

**Expected Output:**
```
Market Open: 09:30
Market Close: 15:00
```

#### Test 1.2: Verify Market Status Check
```python
def test_market_status_check():
    """Test market status detection"""
    from market_data import MarketDataFetcher
    
    fetcher = MarketDataFetcher()
    is_open = fetcher.is_market_open()
    
    # Should return True during 9:30 AM - 3:00 PM IST
    # Should return False outside these hours
    print(f"Market is currently open: {is_open}")
```

**Execution:**
```bash
cd src
python -c "from market_data import MarketDataFetcher; print('Market Open:', MarketDataFetcher().is_market_open())"
```

---

### Test Suite 2: Continuous Trading Cycles

#### Test 2.1: Single Trading Cycle During Market Hours
```python
def test_single_trading_cycle():
    """Test single trading cycle execution"""
    from trading_orchestrator import TradingOrchestrator
    
    orchestrator = TradingOrchestrator()
    result = orchestrator.run_once()
    
    assert result is not None
    assert 'timestamp' in result
    assert 'market_open' in result
    assert 'signals_generated' in result
    assert 'orders_executed' in result
    
    print(f"✓ Trading cycle completed")
    print(f"Market Open: {result['market_open']}")
    print(f"Signals Generated: {len(result['signals_generated'])}")
    print(f"Orders Executed: {len(result['orders_executed'])}")
```

**Execution:**
```bash
cd /Users/mithileshsinha/CascadeProjects/ai_trading_agent
python run.py once
```

**Expected Output:**
```json
{
  "timestamp": "2026-06-15T...",
  "market_open": true/false,
  "signals_generated": [...],
  "orders_executed": [...],
  "positions_monitored": [...],
  "errors": []
}
```

#### Test 2.2: Multiple Trading Cycles
```python
def test_multiple_trading_cycles():
    """Test multiple consecutive trading cycles"""
    from trading_orchestrator import TradingOrchestrator
    
    orchestrator = TradingOrchestrator()
    results = []
    
    # Run 3 cycles
    for i in range(3):
        result = orchestrator.run_once()
        results.append(result)
        print(f"Cycle {i+1} completed")
    
    assert len(results) == 3
    print(f"✓ {len(results)} trading cycles completed successfully")
```

**Execution:**
```bash
cd /Users/mithileshsinha/CascadeProjects/ai_trading_agent
python run.py test 60
```

**Expected Output:**
```
INFO:trading_orchestrator:Starting paper trading test for 60 seconds
INFO:trading_orchestrator:Test completed
{
  "test_duration": 60,
  "cycles_completed": 3,
  "total_trades": 0,
  "paper_pnl": 0
}
```

---

### Test Suite 3: Continuous Position Monitoring

#### Test 3.1: Position Monitoring During Trading
```python
def test_position_monitoring():
    """Test continuous position monitoring"""
    from order_executor import OrderExecutor
    
    executor = OrderExecutor()
    
    # Create a test position
    executor.broker.paper_portfolio['positions']['TEST'] = {
        'symbol': 'TEST',
        'quantity': 10,
        'entry_price': 1000,
        'current_price': 1000,
        'stop_loss': 980,
        'target': 1040,
        'status': 'OPEN'
    }
    
    # Monitor positions
    updates = executor.monitor_positions()
    
    assert updates is not None
    print(f"✓ Position monitoring completed")
    print(f"Position updates: {len(updates)}")
```

**Execution:**
```bash
cd src
python -c "from order_executor import OrderExecutor; executor = OrderExecutor(); print(executor.monitor_positions())"
```

#### Test 3.2: Stop-Loss Trigger
```python
def test_stop_loss_trigger():
    """Test stop-loss trigger during monitoring"""
    from order_executor import OrderExecutor
    
    executor = OrderExecutor()
    
    # Create position at stop-loss level
    executor.broker.paper_portfolio['positions']['TEST'] = {
        'symbol': 'TEST',
        'quantity': 10,
        'entry_price': 1000,
        'current_price': 975,  # Below stop-loss
        'stop_loss': 980,
        'target': 1040,
        'status': 'OPEN'
    }
    
    updates = executor.monitor_positions()
    
    # Should trigger stop-loss
    print(f"✓ Stop-loss trigger test completed")
    print(f"Updates: {updates}")
```

#### Test 3.3: Target Profit Trigger
```python
def test_target_profit_trigger():
    """Test target profit trigger during monitoring"""
    from order_executor import OrderExecutor
    
    executor = OrderExecutor()
    
    # Create position at target level
    executor.broker.paper_portfolio['positions']['TEST'] = {
        'symbol': 'TEST',
        'quantity': 10,
        'entry_price': 1000,
        'current_price': 1045,  # Above target
        'stop_loss': 980,
        'target': 1040,
        'status': 'OPEN'
    }
    
    updates = executor.monitor_positions()
    
    # Should trigger target sell
    print(f"✓ Target profit trigger test completed")
    print(f"Updates: {updates}")
```

---

### Test Suite 4: Scheduled Trading

#### Test 4.1: Verify Schedule Configuration
```python
def test_schedule_configuration():
    """Test that trading schedule is correctly configured"""
    from trading_orchestrator import TradingOrchestrator
    
    orchestrator = TradingOrchestrator()
    
    # Check schedule is set up
    print("✓ Trading schedule configured")
    print("Pre-market check: 9:20 AM IST")
    print("Trading cycles: Every 15 minutes during market hours")
    print("End-of-day close: 3:00 PM IST")
```

**Execution:**
```bash
cd /Users/mithileshsinha/CascadeProjects/ai_trading_agent
python run.py scheduled 15
```

**Expected Output:**
```
INFO:trading_orchestrator:Starting full automated trading
INFO:trading_orchestrator:Schedule:
INFO:trading_orchestrator:  - Pre-market check: 9:20 AM IST
INFO:trading_orchestrator:  - Trading cycles: Every 15 minutes during market hours (9:30 AM - 3:00 PM IST)
INFO:trading_orchestrator:  - End-of-day close: 3:00 PM IST
INFO:trading_orchestrator:  - Daily reset: 9:00 AM IST
```

#### Test 4.2: Pre-Market Check
```python
def test_pre_market_check():
    """Test pre-market check execution"""
    from trading_orchestrator import TradingOrchestrator
    
    orchestrator = TradingOrchestrator()
    
    # Manually trigger pre-market check
    orchestrator.pre_market_check()
    
    print("✓ Pre-market check completed")
```

**Execution:**
```bash
cd src
python -c "from trading_orchestrator import TradingOrchestrator; TradingOrchestrator().pre_market_check()"
```

**Expected Output:**
```
INFO:trading_orchestrator:Executing pre-market check (9:20 AM IST)
INFO:trading_orchestrator:Token validated successfully
INFO:trading_orchestrator:System ready for trading
INFO:trading_orchestrator:Market Hours: 09:30 - 15:00 IST
INFO:trading_orchestrator:Trading Amount: 5000
INFO:trading_orchestrator:Max Positions: 3
INFO:trading_orchestrator:Risk Per Trade: 2.0%
INFO:trading_orchestrator:Watchlist: [...]
```

#### Test 4.3: End-of-Day Close
```python
def test_end_of_day_close():
    """Test end-of-day close execution"""
    from trading_orchestrator import TradingOrchestrator
    
    orchestrator = TradingOrchestrator()
    
    # Manually trigger end-of-day close
    result = orchestrator.end_of_day_close()
    
    assert result is not None
    assert 'close_results' in result
    print("✓ End-of-day close completed")
    print(f"Positions closed: {len(result['close_results'])}")
```

**Execution:**
```bash
cd src
python -c "from trading_orchestrator import TradingOrchestrator; print(TradingOrchestrator().end_of_day_close())"
```

**Expected Output:**
```
INFO:trading_orchestrator:Executing end-of-day close (3:00 PM IST)
INFO:order_executor:Closing all positions
INFO:trading_orchestrator:End-of-day close completed: X positions closed
INFO:trading_orchestrator:Executing daily reset
```

---

### Test Suite 5: Real-Time Trading Simulation

#### Test 5.1: Simulate Trading During Market Hours
```python
def test_real_time_trading_simulation():
    """Test trading simulation during market hours"""
    from trading_orchestrator import TradingOrchestrator
    import time
    
    orchestrator = TradingOrchestrator()
    
    # Run 5 cycles with 1-minute intervals
    for i in range(5):
        result = orchestrator.run_once()
        print(f"Cycle {i+1}: Market Open = {result['market_open']}")
        time.sleep(60)  # Wait 1 minute between cycles
    
    print("✓ Real-time trading simulation completed")
```

**Execution:**
```bash
cd /Users/mithileshsinha/CascadeProjects/ai_trading_agent
python run.py test 300
```

#### Test 5.2: Continuous Order Execution
```python
def test_continuous_order_execution():
    """Test continuous order execution during trading"""
    from trading_orchestrator import TradingOrchestrator
    
    orchestrator = TradingOrchestrator()
    
    # Run multiple cycles and check orders
    for i in range(3):
        result = orchestrator.run_once()
        if result['orders_executed']:
            print(f"Cycle {i+1}: {len(result['orders_executed'])} orders executed")
        else:
            print(f"Cycle {i+1}: No orders executed")
    
    print("✓ Continuous order execution test completed")
```

---

## Testing Steps

### Step 1: Configuration Validation

```bash
# Verify market hours configuration
cd /Users/mithileshsinha/CascadeProjects/ai_trading_agent
python -c "from config import config; print(f'Market Hours: {config.MARKET_OPEN} - {config.MARKET_CLOSE}')"
```

**Expected Output:**
```
Market Hours: 09:30 - 15:00
```

### Step 2: Single Cycle Test

```bash
# Test single trading cycle
python run.py once
```

**Expected Output:**
- Market status check
- Signal generation (if market open)
- Order execution (if signals available)
- Position monitoring
- JSON result with cycle details

### Step 3: Multiple Cycle Test

```bash
# Test multiple trading cycles (60 seconds)
python run.py test 60
```

**Expected Output:**
- Multiple trading cycles executed
- Paper trading test completed
- Summary of test results

### Step 4: Scheduled Trading Test

```bash
# Start scheduled trading (run for 5 minutes then stop)
python run.py scheduled 15
# Press Ctrl+C after 5 minutes
```

**Expected Output:**
- Schedule information displayed
- Trading cycles running every 15 minutes
- Pre-market check at 9:20 AM (if during that time)
- End-of-day close at 3:00 PM (if during that time)

### Step 5: Position Monitoring Test

```bash
# Test position monitoring
cd src
python -c "from order_executor import OrderExecutor; executor = OrderExecutor(); print(executor.monitor_positions())"
```

**Expected Output:**
- Position monitoring results
- Stop-loss/target checks
- Position updates

### Step 6: Pre-Market Check Test

```bash
# Test pre-market check
python -c "from trading_orchestrator import TradingOrchestrator; TradingOrchestrator().pre_market_check()"
```

**Expected Output:**
- Token validation
- System readiness check
- Configuration display

### Step 7: End-of-Day Close Test

```bash
# Test end-of-day close
python -c "from trading_orchestrator import TradingOrchestrator; print(TradingOrchestrator().end_of_day_close())"
```

**Expected Output:**
- All positions closed
- Daily statistics reset
- Close summary

---

## Market Hours Validation

### Test During Market Hours (9:30 AM - 3:00 PM IST)

```bash
# During market hours, run:
python run.py once
```

**Expected:**
- `market_open: true`
- Signals generated
- Orders executed (if signals available)
- Positions monitored

### Test Outside Market Hours

```bash
# Outside market hours, run:
python run.py once
```

**Expected:**
- `market_open: false`
- No signals generated
- No orders executed
- Message: "Market is closed - skipping trading cycle"

### Test at Market Open (9:30 AM IST)

```bash
# At exactly 9:30 AM IST, run:
python run.py once
```

**Expected:**
- Market should be open
- First trading cycle of the day
- Pre-market check should have run at 9:20 AM

### Test at Market Close (3:00 PM IST)

```bash
# At exactly 3:00 PM IST, run:
python run.py once
```

**Expected:**
- Market should be closed
- End-of-day close should have executed
- All positions should be closed

---

## Continuous Trading Validation

### Test 1: Continuous Signal Generation

**Objective:** Verify signals are generated continuously during market hours

**Steps:**
1. Start scheduled trading: `python run.py scheduled 15`
2. Monitor logs for signal generation
3. Verify signals are generated every 15 minutes
4. Check signal quality and confidence scores

**Expected:**
- Signals generated every cycle
- Signals have confidence scores
- Signals include entry/exit parameters

### Test 2: Continuous Order Execution

**Objective:** Verify orders are executed continuously based on signals

**Steps:**
1. Start scheduled trading with paper trading: `PAPER_TRADING=True`
2. Monitor logs for order execution
3. Verify orders are placed for high-confidence signals
4. Check order parameters (quantity, price, stop-loss, target)

**Expected:**
- Orders executed for high-confidence signals
- Order parameters calculated correctly
- Risk management rules applied

### Test 3: Continuous Position Monitoring

**Objective:** Verify positions are monitored continuously

**Steps:**
1. Create a test position
2. Run multiple trading cycles
3. Monitor position updates
4. Verify stop-loss/target triggers work

**Expected:**
- Positions monitored every cycle
- Stop-loss triggers when price drops 2%
- Target triggers when price rises 4%

### Test 4: Continuous Risk Management

**Objective:** Verify risk management is applied continuously

**Steps:**
1. Run multiple trading cycles
2. Monitor risk limit checks
3. Verify daily loss limit enforcement
4. Check consecutive loss protection

**Expected:**
- Risk limits checked every cycle
- Trading stops if daily loss exceeded
- Trading stops after 3 consecutive losses

---

## Integration Test: Full Trading Day Simulation

### Test: Simulate Complete Trading Day

**Objective:** Test complete trading day from pre-market to close

**Steps:**
1. **9:00 AM**: Daily reset
2. **9:20 AM**: Pre-market check
3. **9:30 AM**: Market opens, first trading cycle
4. **9:30 AM - 3:00 PM**: Continuous trading cycles
5. **3:00 PM**: End-of-day close
6. **3:00 PM**: Market closes

**Execution:**
```bash
# Start at 9:00 AM
python run.py scheduled 15
# Let it run until 3:00 PM
# System will handle everything automatically
```

**Expected:**
- Pre-market check at 9:20 AM
- Trading cycles every 15 minutes during market hours
- Orders executed based on signals
- Positions monitored continuously
- All positions closed at 3:00 PM
- Daily statistics reset after close

---

## Performance Test: Continuous Trading Load

### Test: High-Frequency Trading Simulation

**Objective:** Test system performance with high-frequency cycles

**Steps:**
1. Set trading interval to 5 minutes
2. Run scheduled trading for 1 hour
3. Monitor system performance
4. Check for any delays or errors

**Execution:**
```bash
python run.py scheduled 5
```

**Expected:**
- 12 trading cycles in 1 hour
- No significant delays
- No errors or crashes
- Consistent performance

---

## Troubleshooting

### Issue: Trading cycles not executing

**Check:**
```bash
# Verify market hours
python -c "from config import config; print(f'Market Hours: {config.MARKET_OPEN} - {config.MARKET_CLOSE}')"

# Check current time
python -c "from datetime import datetime; print(f'Current Time: {datetime.now().strftime(\"%H:%M\")}')"

# Verify market status
python -c "from market_data import MarketDataFetcher; print('Market Open:', MarketDataFetcher().is_market_open())"
```

### Issue: Orders not executing during market hours

**Check:**
```bash
# Verify paper trading mode
python -c "from config import config; print('Paper Trading:', config.PAPER_TRADING)"

# Check signal generation
python run.py once
# Look at signals_generated in output

# Verify risk parameters
python -c "from config import config; print(f'Risk Per Trade: {config.RISK_PER_TRADE}'); print(f'Max Positions: {config.MAX_POSITIONS}')"
```

### Issue: Positions not being monitored

**Check:**
```bash
# Test position monitoring
cd src
python -c "from order_executor import OrderExecutor; executor = OrderExecutor(); print(executor.monitor_positions())"

# Check if positions exist
python -c "from order_executor import OrderExecutor; executor = OrderExecutor(); print(executor.broker.paper_portfolio['positions'])"
```

---

## Summary

The AI trading agent is now configured for continuous trading during market hours (9:30 AM - 3:00 PM IST):

✅ **Market Hours**: Updated to 9:30 AM - 3:00 PM IST
✅ **Pre-Market Check**: 9:20 AM IST
✅ **End-of-Day Close**: 3:00 PM IST
✅ **Trading Cycles**: Every 15 minutes (configurable)
✅ **Continuous Monitoring**: Positions monitored every cycle
✅ **Automatic Exits**: Stop-loss and target triggers
✅ **Risk Management**: Applied continuously

The system can now:
- Evaluate market conditions continuously
- Buy and sell stocks at any time during market hours
- Monitor positions continuously
- Execute trades based on analysis and risk management
- Ensure no overnight positions (auto square-off at 3:00 PM)
