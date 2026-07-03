# Comprehensive Test Cases - AI Trading Agent

## Table of Contents
1. [Unit Tests](#unit-tests)
2. [Integration Tests](#integration-tests)
3. [End-to-End Tests](#end-to-end-tests)
4. [Manual Testing Scenarios](#manual-testing-scenarios)
5. [Performance Tests](#performance-tests)
6. [Test Execution Guide](#test-execution-guide)

---

## Unit Tests

### Test Suite 1: Market Data Fetcher

#### Test 1.1: Fetch Stock Data
```python
# File: tests/test_market_data.py

def test_fetch_stock_data_success():
    """Test successful stock data fetching"""
    fetcher = MarketDataFetcher()
    data = fetcher.fetch_stock_data("RELIANCE")
    
    assert data is not None
    assert not data.empty
    assert 'Close' in data.columns
    assert 'Volume' in data.columns
    assert len(data) > 0
```

**Expected Result:** Pass - Data fetched successfully with required columns

#### Test 1.2: Fetch Invalid Stock
```python
def test_fetch_invalid_stock():
    """Test fetching data for invalid stock symbol"""
    fetcher = MarketDataFetcher()
    data = fetcher.fetch_stock_data("INVALID123")
    
    assert data is None or data.empty
```

**Expected Result:** Pass - Returns None or empty DataFrame

#### Test 1.3: Fetch Multiple Stocks
```python
def test_fetch_multiple_stocks():
    """Test fetching data for multiple stocks"""
    fetcher = MarketDataFetcher()
    symbols = ["RELIANCE", "TCS", "INFY"]
    data = fetcher.fetch_multiple_stocks(symbols)
    
    assert len(data) == len(symbols)
    assert all(symbol in data for symbol in symbols)
```

**Expected Result:** Pass - Data fetched for all symbols

---

### Test Suite 2: Technical Analysis

#### Test 2.1: Calculate Indicators
```python
# File: tests/test_technical_analysis.py

def test_calculate_indicators():
    """Test technical indicator calculation"""
    analyzer = TechnicalAnalyzer()
    
    # Create sample data
    data = pd.DataFrame({
        'Open': [100, 101, 102, 103, 104, 105, 106, 107, 108, 109,
                 110, 111, 112, 113, 114, 115, 116, 117, 118, 119],
        'High': [101, 102, 103, 104, 105, 106, 107, 108, 109, 110,
                 111, 112, 113, 114, 115, 116, 117, 118, 119, 120],
        'Low': [99, 100, 101, 102, 103, 104, 105, 106, 107, 108,
                109, 110, 111, 112, 113, 114, 115, 116, 117, 118],
        'Close': [100, 101, 102, 103, 104, 105, 106, 107, 108, 109,
                 110, 111, 112, 113, 114, 115, 116, 117, 118, 119],
        'Volume': [1000000] * 20
    })
    
    result = analyzer.calculate_indicators(data)
    
    # Check if indicators are calculated
    assert 'SMA_20' in result.columns
    assert 'RSI' in result.columns
    assert 'MACD' in result.columns
    assert 'BB_Upper' in result.columns
    assert 'ADX' in result.columns
```

**Expected Result:** Pass - All indicators calculated

#### Test 2.2: Generate Trading Signal
```python
def test_generate_trading_signal():
    """Test trading signal generation"""
    analyzer = TechnicalAnalyzer()
    
    # Create bullish data
    data = pd.DataFrame({
        'Close': [100, 101, 102, 103, 104, 105, 106, 107, 108, 109,
                 110, 111, 112, 113, 114, 115, 116, 117, 118, 119,
                 120, 121, 122, 123, 124, 125, 126, 127, 128, 129],
        'High': [101, 102, 103, 104, 105, 106, 107, 108, 109, 110,
                 111, 112, 113, 114, 115, 116, 117, 118, 119, 120,
                 121, 122, 123, 124, 125, 126, 127, 128, 129, 130],
        'Low': [99, 100, 101, 102, 103, 104, 105, 106, 107, 108, 109,
                110, 111, 112, 113, 114, 115, 116, 117, 118, 119, 120,
                121, 122, 123, 124, 125, 126, 127, 128],
        'Volume': [1000000] * 30
    })
    
    indicators = analyzer.calculate_indicators(data)
    signal = analyzer.generate_trading_signal(indicators)
    
    assert signal in ['BUY', 'SELL', 'HOLD']
```

**Expected Result:** Pass - Signal generated (BUY, SELL, or HOLD)

---

### Test Suite 3: Risk Manager

#### Test 3.1: Calculate Position Size
```python
# File: tests/test_risk_manager.py

def test_calculate_position_size():
    """Test position size calculation"""
    risk_manager = RiskManager()
    
    capital = 5000
    risk_per_trade = 0.02
    stop_loss = 0.02
    
    position_size = risk_manager.calculate_position_size(
        capital, risk_per_trade, stop_loss
    )
    
    expected_size = (capital * risk_per_trade) / stop_loss
    assert position_size == expected_size
    assert position_size <= capital
```

**Expected Result:** Pass - Position size calculated correctly

#### Test 3.2: Check Risk Limits
```python
def test_check_risk_limits():
    """Test risk limit checking"""
    risk_manager = RiskManager()
    
    # Test within limits
    assert risk_manager.check_risk_limits(
        daily_loss=100,
        capital=5000,
        consecutive_losses=1
    ) == True
    
    # Test daily loss limit exceeded
    assert risk_manager.check_risk_limits(
        daily_loss=300,
        capital=5000,
        consecutive_losses=1
    ) == False
    
    # Test consecutive loss limit exceeded
    assert risk_manager.check_risk_limits(
        daily_loss=100,
        capital=5000,
        consecutive_losses=4
    ) == False
```

**Expected Result:** Pass - Risk limits enforced correctly

#### Test 3.3: Create Position
```python
def test_create_position():
    """Test position creation"""
    risk_manager = RiskManager()
    
    position = risk_manager.create_position(
        symbol="RELIANCE",
        entry_price=1000,
        quantity=5,
        stop_loss=980,
        target=1040
    )
    
    assert position.symbol == "RELIANCE"
    assert position.entry_price == 1000
    assert position.quantity == 5
    assert position.status == PositionStatus.OPEN
```

**Expected Result:** Pass - Position created successfully

---

### Test Suite 4: Signal Generator

#### Test 4.1: Generate Signal for Stock
```python
# File: tests/test_signal_generator.py

def test_generate_signal():
    """Test signal generation for a stock"""
    generator = SignalGenerator()
    
    signal = generator.generate_signal("RELIANCE")
    
    assert signal is not None
    assert 'symbol' in signal
    assert 'action' in signal
    assert 'confidence' in signal
    assert signal['action'] in ['BUY', 'SELL', 'HOLD']
    assert 0 <= signal['confidence'] <= 1
```

**Expected Result:** Pass - Signal generated with required fields

#### Test 4.2: Generate Signals for Watchlist
```python
def test_generate_signals_for_watchlist():
    """Test signal generation for watchlist"""
    generator = SignalGenerator()
    
    watchlist = ["RELIANCE", "TCS", "INFY"]
    signals = generator.generate_signals_for_watchlist(watchlist)
    
    assert len(signals) <= len(watchlist)
    assert all('symbol' in signal for signal in signals)
    assert all('action' in signal for signal in signals)
```

**Expected Result:** Pass - Signals generated for watchlist

---

### Test Suite 5: Broker Integration

#### Test 5.1: Paper Trading Mode
```python
# File: tests/test_broker_integration.py

def test_paper_trading_mode():
    """Test paper trading mode initialization"""
    broker = BrokerIntegration()
    
    assert broker.paper_trading == True
    assert broker.kite is None
    assert broker.paper_portfolio is not None
```

**Expected Result:** Pass - Paper trading mode active

#### Test 5.2: Place Paper Order
```python
def test_place_paper_order():
    """Test placing paper trading order"""
    broker = BrokerIntegration()
    
    order = {
        'symbol': 'RELIANCE',
        'action': 'BUY',
        'quantity': 10,
        'price': 1000
    }
    
    result = broker.place_order(order)
    
    assert result['success'] == True
    assert 'order_id' in result
    assert result['paper_trading'] == True
```

**Expected Result:** Pass - Paper order placed successfully

---

## Integration Tests

### Test Suite 6: Trading Orchestrator

#### Test 6.1: Single Trading Cycle
```python
# File: tests/test_integration.py

def test_single_trading_cycle():
    """Test complete single trading cycle"""
    orchestrator = TradingOrchestrator()
    
    result = orchestrator.run_once()
    
    assert result is not None
    assert 'timestamp' in result
    assert 'market_open' in result
    assert 'signals_generated' in result
    assert 'orders_executed' in result
```

**Expected Result:** Pass - Trading cycle completed

#### Test 6.2: Paper Trading Test
```python
def test_paper_trading_test():
    """Test paper trading mode"""
    orchestrator = TradingOrchestrator()
    
    result = orchestrator.run_paper_trading_test(duration=10)
    
    assert result is not None
    assert 'test_duration' in result
    assert 'cycles_completed' in result
    assert 'paper_pnl' in result
```

**Expected Result:** Pass - Paper trading test completed

#### Test 6.3: Performance Report
```python
def test_performance_report():
    """Test performance report generation"""
    orchestrator = TradingOrchestrator()
    
    report = orchestrator.get_performance_report()
    
    assert report is not None
    assert 'total_trades' in report
    assert 'win_rate' in report
    assert 'total_pnl' in report
```

**Expected Result:** Pass - Performance report generated

---

## End-to-End Tests

### Test Suite 7: Complete Trading Workflow

#### Test 7.1: Full Trading Workflow (Paper Trading)
```python
def test_full_trading_workflow():
    """Test complete trading workflow from start to finish"""
    
    # 1. Initialize system
    orchestrator = TradingOrchestrator()
    assert orchestrator is not None
    
    # 2. Run trading cycle
    result = orchestrator.run_once()
    assert result is not None
    
    # 3. Check signals generated
    if result['market_open']:
        assert 'signals_generated' in result
        assert 'orders_executed' in result
    
    # 4. Generate performance report
    report = orchestrator.get_performance_report()
    assert report is not None
    
    # 5. End of day close
    close_result = orchestrator.end_of_day_close()
    assert close_result is not None
```

**Expected Result:** Pass - Complete workflow executed successfully

#### Test 7.2: Token Management Workflow
```python
def test_token_management_workflow():
    """Test token management workflow"""
    
    # 1. Create token manager
    token_manager = TokenManager()
    assert token_manager is not None
    
    # 2. Check token validity
    is_valid = token_manager.is_token_valid()
    assert isinstance(is_valid, bool)
    
    # 3. Clear token
    token_manager.clear_token()
    assert token_manager.access_token is None
```

**Expected Result:** Pass - Token management workflow successful

---

## Manual Testing Scenarios

### Scenario 1: First-Time Setup

**Steps:**
1. Navigate to project directory
2. Create virtual environment
3. Install dependencies
4. Configure .env file
5. Run single trading cycle

**Expected Result:** System runs successfully in paper trading mode

**Verification:**
```bash
python run.py once
# Check output for successful execution
```

---

### Scenario 2: Kite Connect Integration

**Steps:**
1. Get Kite API credentials
2. Configure .env with credentials
3. Run token handler
4. Update .env with request token
5. Test connection

**Expected Result:** Kite connection successful

**Verification:**
```bash
python test_kite_auth.py
# Check for successful connection message
```

---

### Scenario 3: Market Hours Trading

**Steps:**
1. Wait for market hours (9:15 AM - 3:30 PM IST)
2. Run single trading cycle
3. Verify signals generated
4. Check orders executed

**Expected Result:** Trading cycle executes during market hours

**Verification:**
```bash
python run.py once
# Check for signals and orders in output
```

---

### Scenario 4: End-of-Day Close

**Steps:**
1. Start automated trading
2. Wait until 3:25 PM IST
3. Verify auto square-off executes
4. Check positions closed

**Expected Result:** All positions closed at 3:25 PM

**Verification:**
```bash
python run.py scheduled 15
# Monitor logs at 3:25 PM for close execution
```

---

### Scenario 5: Risk Limit Breach

**Steps:**
1. Set low daily loss limit in config
2. Execute multiple losing trades
3. Verify trading stops when limit reached

**Expected Result:** Trading stops after risk limit breach

**Verification:**
```bash
# Monitor logs for risk limit messages
# Check that no new orders placed after breach
```

---

## Performance Tests

### Test Suite 8: Performance Metrics

#### Test 8.1: Market Data Fetch Speed
```python
import time

def test_market_data_fetch_speed():
    """Test market data fetching performance"""
    fetcher = MarketDataFetcher()
    
    start_time = time.time()
    data = fetcher.fetch_stock_data("RELIANCE")
    end_time = time.time()
    
    fetch_time = end_time - start_time
    assert fetch_time < 5.0  # Should complete in under 5 seconds
```

**Expected Result:** Pass - Data fetched within 5 seconds

#### Test 8.2: Signal Generation Speed
```python
def test_signal_generation_speed():
    """Test signal generation performance"""
    generator = SignalGenerator()
    
    start_time = time.time()
    signal = generator.generate_signal("RELIANCE")
    end_time = time.time()
    
    generation_time = end_time - start_time
    assert generation_time < 10.0  # Should complete in under 10 seconds
```

**Expected Result:** Pass - Signal generated within 10 seconds

#### Test 8.3: Complete Cycle Speed
```python
def test_complete_cycle_speed():
    """Test complete trading cycle performance"""
    orchestrator = TradingOrchestrator()
    
    start_time = time.time()
    result = orchestrator.run_once()
    end_time = time.time()
    
    cycle_time = end_time - start_time
    assert cycle_time < 30.0  # Should complete in under 30 seconds
```

**Expected Result:** Pass - Cycle completed within 30 seconds

---

## Test Execution Guide

### Run All Tests

```bash
# Run all unit tests
pytest tests/ -v

# Run with coverage report
pytest tests/ --cov=src --cov-report=html

# Run with detailed output
pytest tests/ -vv
```

### Run Specific Test Suites

```bash
# Market data tests
pytest tests/test_market_data.py -v

# Technical analysis tests
pytest tests/test_technical_analysis.py -v

# Risk manager tests
pytest tests/test_risk_manager.py -v

# Signal generator tests
pytest tests/test_signal_generator.py -v

# Broker integration tests
pytest tests/test_broker_integration.py -v

# Integration tests
pytest tests/test_integration.py -v
```

### Run Specific Tests

```bash
# Run specific test
pytest tests/test_market_data.py::TestMarketDataFetcher::test_fetch_stock_data_success -v

# Run tests matching pattern
pytest tests/ -k "test_fetch" -v

# Run tests excluding pattern
pytest tests/ -k "not test_invalid" -v
```

### Run Tests with Markers

```bash
# Run only unit tests
pytest tests/ -m unit -v

# Run only integration tests
pytest tests/ -m integration -v

# Run only slow tests
pytest tests/ -m slow -v
```

### Parallel Test Execution

```bash
# Run tests in parallel (requires pytest-xdist)
pip install pytest-xdist
pytest tests/ -n auto
```

### Test Output Options

```bash
# Generate JUnit XML report
pytest tests/ --junitxml=test-results.xml

# Generate HTML report
pip install pytest-html
pytest tests/ --html=test-report.html

# Generate coverage report
pytest tests/ --cov=src --cov-report=term-missing
```

---

## Test Data Management

### Mock Data

For reliable testing, use mock data:

```python
# Create sample market data
def create_sample_stock_data():
    return pd.DataFrame({
        'Open': [100, 101, 102, 103, 104],
        'High': [101, 102, 103, 104, 105],
        'Low': [99, 100, 101, 102, 103],
        'Close': [100, 101, 102, 103, 104],
        'Volume': [1000000] * 5
    })
```

### Test Fixtures

Use pytest fixtures for common test data:

```python
@pytest.fixture
def sample_market_data():
    return create_sample_stock_data()

@pytest.fixture
def mock_kite_connection():
    return Mock(spec=KiteConnect)
```

---

## Continuous Integration

### GitHub Actions Example

```yaml
name: Tests

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    
    steps:
    - uses: actions/checkout@v2
    
    - name: Set up Python
      uses: actions/setup-python@v2
      with:
        python-version: '3.11'
    
    - name: Install dependencies
      run: |
        python -m pip install --upgrade pip
        pip install -r requirements.txt
    
    - name: Run tests
      run: |
        pytest tests/ -v --cov=src --cov-report=xml
    
    - name: Upload coverage
      uses: codecov/codecov-action@v2
```

---

## Test Maintenance

### Regular Test Updates

1. **Update test data** when market data format changes
2. **Add new tests** for new features
3. **Remove obsolete tests** for deprecated features
4. **Update test expectations** when business logic changes

### Test Coverage Goals

- **Unit tests**: > 80% coverage
- **Integration tests**: > 60% coverage
- **Critical paths**: 100% coverage

### Test Documentation

- Document complex test scenarios
- Add comments for non-obvious test logic
- Maintain test case descriptions
- Update this document when tests change

---

## Troubleshooting Tests

### Common Test Failures

#### Issue: Tests fail due to network issues
**Solution:** Use mock data for network-dependent tests

#### Issue: Tests fail due to time dependencies
**Solution:** Use fixed timestamps or mock time

#### Issue: Tests fail due to market hours
**Solution:** Mock market status or run during market hours

#### Issue: Tests fail due to missing dependencies
**Solution:** Ensure all dependencies installed in test environment

---

## Best Practices

1. **Write independent tests** - Each test should run independently
2. **Use descriptive names** - Test names should describe what they test
3. **Keep tests fast** - Unit tests should complete in seconds
4. **Test edge cases** - Test boundary conditions and error cases
5. **Mock external dependencies** - Don't rely on external services
6. **Maintain test data** - Keep test data up to date
7. **Review test coverage** - Regularly check coverage reports
8. **Refactor tests** - Keep test code clean and maintainable

---

## Summary

This comprehensive test suite covers:
- **Unit tests** for individual components
- **Integration tests** for component interactions
- **End-to-end tests** for complete workflows
- **Manual testing scenarios** for user workflows
- **Performance tests** for system performance
- **Test execution guide** for running tests

Run tests regularly to ensure system reliability and catch regressions early.
