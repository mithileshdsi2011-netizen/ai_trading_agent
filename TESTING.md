# Testing Guide

This document provides comprehensive testing steps for the AI Trading Agent.

## Prerequisites

1. Complete the installation steps in README.md
2. Set up your `.env` file with API keys
3. Ensure all dependencies are installed

## Testing Steps

### Phase 1: Unit Testing

Test individual components in isolation.

#### 1.1 Test Market Data Fetcher

```bash
pytest tests/test_market_data.py -v
```

**Expected Output:**
- All tests should pass
- Tests cover: stock data fetching, real-time prices, stock info, market hours

#### 1.2 Test Technical Analysis

```bash
pytest tests/test_technical_analysis.py -v
```

**Expected Output:**
- All tests should pass
- Tests cover: indicator calculation, trend detection, support/resistance, signal generation

#### 1.3 Test Risk Manager

```bash
pytest tests/test_risk_manager.py -v
```

**Expected Output:**
- All tests should pass
- Tests cover: position opening, stop loss, target hits, daily limits

#### 1.4 Test Signal Generator

```bash
pytest tests/test_signal_generator.py -v
```

**Expected Output:**
- All tests should pass
- Tests cover: signal generation, position sizing, risk parameters

#### 1.5 Test Broker Integration

```bash
pytest tests/test_broker_integration.py -v
```

**Expected Output:**
- All tests should pass
- Tests cover: paper trading orders, position management, holdings

### Phase 2: Integration Testing

Test components working together.

#### 2.1 Run Integration Tests

```bash
pytest tests/test_integration.py -v
```

**Expected Output:**
- All tests should pass
- Tests cover: complete trading cycle, orchestrator workflow

### Phase 3: Paper Trading Test

Test the system with simulated trading.

#### 3.1 Run Single Cycle Test

```bash
cd src
python trading_orchestrator.py once
```

**Expected Output:**
- JSON output with trading cycle results
- Should show market status, signals generated, orders executed

#### 3.2 Run Short Paper Trading Test

```bash
cd src
python trading_orchestrator.py test 10
```

**Expected Output:**
- Runs for 10 minutes
- Executes multiple trading cycles
- Shows performance report at the end

**What to Monitor:**
- Market open/close detection
- Signal generation
- Order execution
- Position monitoring
- Risk management triggers

### Phase 4: Component Manual Testing

Manually test each component with real data.

#### 4.1 Test Market Data Fetcher

Create a test script `test_market_data_manual.py`:

```python
import sys
sys.path.insert(0, 'src')

from market_data import MarketDataFetcher

fetcher = MarketDataFetcher()

# Test stock data
print("Testing stock data fetch...")
data = fetcher.get_stock_data('RELIANCE', period='1mo')
print(f"Data points: {len(data)}")
print(data.head())

# Test real-time price
print("\nTesting real-time price...")
price = fetcher.get_realtime_price('RELIANCE')
print(f"Current price: {price}")

# Test stock info
print("\nTesting stock info...")
info = fetcher.get_stock_info('RELIANCE')
print(f"Stock info: {info}")

# Test market hours
print("\nTesting market hours...")
is_open = fetcher.is_market_open()
print(f"Market open: {is_open}")
```

Run:
```bash
python test_market_data_manual.py
```

#### 4.2 Test Technical Analysis

Create `test_technical_manual.py`:

```python
import sys
sys.path.insert(0, 'src')

from market_data import MarketDataFetcher
from technical_analysis import TechnicalAnalyzer

fetcher = MarketDataFetcher()
analyzer = TechnicalAnalyzer()

# Get data
data = fetcher.get_stock_data('RELIANCE', period='3mo')

# Calculate indicators
print("Calculating indicators...")
df = analyzer.calculate_indicators(data)
print(df[['Close', 'SMA_20', 'RSI', 'MACD']].tail())

# Generate signals
print("\nGenerating signals...")
signals = analyzer.generate_signals(data)
print(f"Signal: {signals}")
```

Run:
```bash
python test_technical_manual.py
```

#### 4.3 Test AI Research Agent

Create `test_research_manual.py`:

```python
import sys
sys.path.insert(0, 'src')

from ai_research_agent import AIResearchAgent

agent = AIResearchAgent()

# Research a stock
print("Researching RELIANCE...")
result = agent.research_stock('RELIANCE')
print(f"Recommendation: {result['recommendation']}")
print(f"Confidence: {result['confidence']}")
print(f"Overall Score: {result['overall_score']}")
print(f"Reasoning: {result['reasoning']}")
```

Run:
```bash
python test_research_manual.py
```

#### 4.4 Test Signal Generator

Create `test_signal_manual.py`:

```python
import sys
sys.path.insert(0, 'src')

from signal_generator import SignalGenerator

generator = SignalGenerator()

# Generate signal
print("Generating signal for RELIANCE...")
signal = generator.generate_signal('RELIANCE')
print(f"Action: {signal['action']}")
print(f"Current Price: {signal['current_price']}")
print(f"Position Size: {signal['position_size']}")
print(f"Stop Loss: {signal['stop_loss']}")
print(f"Target: {signal['target']}")
print(f"Risk-Reward Ratio: {signal['risk_reward_ratio']}")
print(f"Confidence: {signal['confidence']}")
```

Run:
```bash
python test_signal_manual.py
```

#### 4.5 Test Risk Manager

Create `test_risk_manual.py`:

```python
import sys
sys.path.insert(0, 'src')

from risk_manager import RiskManager
from datetime import datetime

risk_manager = RiskManager()

# Create sample signal
signal = {
    'symbol': 'RELIANCE',
    'action': 'BUY',
    'current_price': 2500.0,
    'position_size': 2,
    'investment_amount': 5000.0,
    'stop_loss': 2450.0,
    'target': 2600.0,
    'risk_reward_ratio': 2.0,
    'confidence': 0.8,
    'overall_score': 0.6
}

# Test can open position
print("Testing can open position...")
can_open = risk_manager.can_open_position(signal)
print(f"Can open position: {can_open}")

# Test open position
if can_open:
    print("\nOpening position...")
    position = risk_manager.open_position(signal)
    print(f"Position opened: {position.symbol}")

# Test position summary
print("\nPosition summary:")
summary = risk_manager.get_position_summary()
print(summary)
```

Run:
```bash
python test_risk_manual.py
```

#### 4.6 Test Broker Integration (Paper Trading)

Create `test_broker_manual.py`:

```python
import sys
sys.path.insert(0, 'src')

from broker_integration import BrokerIntegration

broker = BrokerIntegration()

# Test paper trading order
signal = {
    'symbol': 'RELIANCE',
    'action': 'BUY',
    'current_price': 2500.0,
    'position_size': 2,
    'investment_amount': 5000.0,
    'stop_loss': 2450.0,
    'target': 2600.0,
    'risk_reward_ratio': 2.0,
    'confidence': 0.8,
    'overall_score': 0.6
}

print("Placing paper trading order...")
result = broker.place_order(signal)
print(f"Order result: {result}")

print("\nGetting positions...")
positions = broker.get_positions()
print(f"Positions: {positions}")

print("\nGetting holdings...")
holdings = broker.get_holdings()
print(f"Holdings: {holdings}")
```

Run:
```bash
python test_broker_manual.py
```

### Phase 5: End-to-End Testing

Test the complete system.

#### 5.1 Run Complete Trading Cycle

```bash
cd src
python trading_orchestrator.py once
```

**Verify:**
- Market status is checked
- Signals are generated for watchlist
- Best signal is selected
- Order is executed (paper trading)
- Positions are monitored
- Summary is generated

#### 5.2 Monitor During Market Hours

Run during market hours (9:15 AM - 3:30 PM IST):

```bash
cd src
python trading_orchestrator.py scheduled 15
```

**Monitor for:**
- Signal generation
- Order execution
- Position updates
- Stop loss triggers
- Target hits
- Risk limit enforcement

### Phase 6: Performance Testing

Test system performance under load.

#### 6.1 Test Multiple Stocks

```python
import sys
sys.path.insert(0, 'src')

from signal_generator import SignalGenerator

generator = SignalGenerator()

# Test with multiple stocks
symbols = ['RELIANCE', 'TCS', 'HDFCBANK', 'INFY', 'ICICIBANK']
signals = generator.generate_signals_for_watchlist(symbols)

print(f"Generated {len(signals)} signals")
for signal in signals:
    print(f"{signal['symbol']}: {signal['action']} (Confidence: {signal['confidence']})")
```

#### 6.2 Test Response Time

```python
import time
import sys
sys.path.insert(0, 'src')

from ai_research_agent import AIResearchAgent

agent = AIResearchAgent()

start = time.time()
result = agent.research_stock('RELIANCE')
end = time.time()

print(f"Research completed in {end - start:.2f} seconds")
print(f"Recommendation: {result['recommendation']}")
```

### Phase 7: Edge Case Testing

Test error handling and edge cases.

#### 7.1 Test Invalid Stock Symbol

```python
import sys
sys.path.insert(0, 'src')

from market_data import MarketDataFetcher

fetcher = MarketDataFetcher()
data = fetcher.get_stock_data('INVALID_SYMBOL')
print(f"Data for invalid symbol: {len(data)} records")
```

#### 7.2 Test Market Closed

```python
import sys
sys.path.insert(0, 'src')

from market_data import MarketDataFetcher

fetcher = MarketDataFetcher()
is_open = fetcher.is_market_open()
print(f"Market open: {is_open}")
```

#### 7.3 Test Insufficient Funds

```python
import sys
sys.path.insert(0, 'src')

from broker_integration import BrokerIntegration

broker = BrokerIntegration()
broker.paper_portfolio['cash'] = 100  # Very low cash

signal = {
    'symbol': 'RELIANCE',
    'action': 'BUY',
    'current_price': 2500.0,
    'position_size': 10,
    'investment_amount': 25000.0,
    'stop_loss': 2450.0,
    'target': 2600.0,
    'risk_reward_ratio': 2.0,
    'confidence': 0.8,
    'overall_score': 0.6
}

result = broker.place_order(signal)
print(f"Order result: {result}")
```

## Test Checklist

- [ ] All unit tests pass
- [ ] All integration tests pass
- [ ] Single cycle test successful
- [ ] Paper trading test successful (10 minutes)
- [ ] Market data fetcher works with real data
- [ ] Technical analysis calculates indicators correctly
- [ ] AI research agent generates recommendations
- [ ] Signal generator creates valid signals
- [ ] Risk manager enforces limits
- [ ] Broker integration executes paper trades
- [ ] End-to-end cycle completes successfully
- [ ] Performance is acceptable (< 30 seconds per cycle)
- [ ] Edge cases handled gracefully

## Troubleshooting

### TA-Lib Installation Issues

If TA-Lib fails to install:
1. Install TA-Lib C library first
2. Then install Python package
3. See README.md for platform-specific instructions

### API Key Issues

If API keys don't work:
1. Verify keys in `.env` file
2. Check OpenAI API key is valid
3. For Zerodha, ensure request token is fresh

### Market Data Issues

If market data fails:
1. Check internet connection
2. Verify stock symbols are correct (NSE format)
3. Ensure yfinance is working

### Import Errors

If import errors occur:
1. Ensure you're in the correct directory
2. Activate virtual environment
3. Install all dependencies

## Continuous Testing

For ongoing monitoring:

1. Run tests daily before market open
2. Monitor paper trading results
3. Review performance reports weekly
4. Adjust parameters based on results
5. Keep test logs for analysis

## Next Steps After Testing

1. **Paper Trading**: Run paper trading for 1-2 weeks
2. **Analyze Results**: Review win rate, P&L, risk metrics
3. **Adjust Parameters**: Optimize based on paper trading results
4. **Start Small**: Begin live trading with small amounts
5. **Scale Gradually**: Increase capital as confidence grows
