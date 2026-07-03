# Complete Setup and Run Guide - AI Trading Agent

## Table of Contents
1. [Prerequisites](#prerequisites)
2. [Initial Setup](#initial-setup)
3. [Configuration](#configuration)
4. [Kite Connect Setup](#kite-connect-setup)
5. [Running the System](#running-the-system)
6. [Testing](#testing)
7. [Debugging](#debugging)
8. [Troubleshooting](#troubleshooting)

---

## Prerequisites

### System Requirements
- **Operating System**: macOS, Linux, or Windows
- **Python**: 3.11 or higher
- **Memory**: Minimum 4GB RAM
- **Disk Space**: 500MB free space
- **Internet**: Stable connection for market data and API calls

### Required Software
```bash
# Check Python version
python3 --version

# Install pip if not available
python3 -m ensurepip --upgrade
```

---

## Initial Setup

### Step 1: Clone or Navigate to Project

```bash
cd /Users/mithileshsinha/CascadeProjects/ai_trading_agent
```

### Step 2: Create Virtual Environment

```bash
# Create virtual environment
python3 -m venv venv

# Activate virtual environment
# On macOS/Linux:
source venv/bin/activate

# On Windows:
# venv\Scripts\activate
```

### Step 3: Install Dependencies

```bash
# Install all required packages
pip install -r requirements.txt

# Verify installation
pip list
```

### Step 4: Verify Project Structure

```bash
# List project files
ls -la

# Expected structure:
# ai_trading_agent/
# ├── src/
# │   ├── __init__.py
# │   ├── config.py
# │   ├── market_data.py
# │   ├── technical_analysis.py
# │   ├── sentiment_analysis.py
# │   ├── ai_research_agent.py
# │   ├── signal_generator.py
# │   ├── risk_manager.py
# │   ├── broker_integration.py
# │   ├── order_executor.py
# │   ├── trading_orchestrator.py
# │   ├── token_manager.py
# │   └── main.py
# ├── tests/
# │   ├── test_market_data.py
# │   ├── test_technical_analysis.py
# │   ├── test_risk_manager.py
# │   ├── test_signal_generator.py
# │   ├── test_broker_integration.py
# │   └── test_integration.py
# ├── data/
# ├── .env
# ├── .env.example
# ├── requirements.txt
# ├── run.py
# ├── get_kite_token.py
# ├── README.md
# ├── TESTING.md
# └── AUTOMATION.md
```

---

## Configuration

### Step 1: Create Environment File

```bash
# Copy example environment file
cp .env.example .env

# Edit the file
nano .env
```

### Step 2: Configure Basic Settings

```env
# OpenAI API Configuration (required for AI analysis)
OPENAI_API_KEY=your_openai_api_key_here

# Trading Configuration
TRADING_AMOUNT=5000
MAX_POSITIONS=3
RISK_PER_TRADE=0.02
STOP_LOSS_PERCENTAGE=0.02
TARGET_PERCENTAGE=0.04

# Paper Trading Mode (True for testing, False for live trading)
PAPER_TRADING=True

# Logging
LOG_LEVEL=INFO
```

### Step 3: Configure Watchlist (Optional)

Edit `src/config.py` to customize your watchlist:

```python
WATCHLIST: list = [
    "RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK"
]
```

---

## Kite Connect Setup (Required for Live Trading)

### Step 1: Get Kite Connect API Credentials

1. Go to https://kite.trade/
2. Sign in to your Zerodha account
3. Navigate to **Developer Console** or **API Settings**
4. Click **Create App** or **Generate API Key**
5. Fill in the required details:
   - App Name: "AI Trading Agent"
   - Description: "Personal trading bot"
   - Redirect URL: `http://localhost:8080/login`
6. Submit and note down:
   - **API Key** (starts with your app name)
   - **API Secret** (provided by Zerodha)

### Step 2: Add Kite Credentials to .env

```env
# Zerodha Kite Connect Configuration
KITE_API_KEY=your_actual_kite_api_key
KITE_API_SECRET=your_actual_kite_api_secret
KITE_REQUEST_TOKEN=your_kite_request_token_here
```

### Step 3: Get Request Token

```bash
# Run the token handler
python get_kite_token.py
```

Follow the instructions:
1. Open the displayed login URL in your browser
2. Login to Zerodha
3. Authorize the app
4. You'll be redirected to `http://localhost:8080/login`
5. Copy the request token displayed on the page
6. Add it to your `.env` file

### Step 4: Verify Kite Connection

```bash
# Test the connection
python test_kite_auth.py
```

Expected output:
```
✓ Session generated successfully!
✓ Access Token: <token>
✓ Connected successfully!
User ID: <your_id>
User Name: <your_name>
```

---

## Running the System

### Option 1: Single Trading Cycle (Test)

```bash
# Run one trading cycle
python run.py once
```

**Expected Output:**
```
INFO:trading_orchestrator:Starting trading cycle at <timestamp>
INFO:trading_orchestrator:Market is closed - skipping trading cycle
{
  "timestamp": "<timestamp>",
  "market_open": false,
  "signals_generated": [],
  "orders_executed": [],
  "positions_monitored": [],
  "errors": []
}
```

### Option 2: Paper Trading Test

```bash
# Run paper trading test for 60 seconds
python run.py test 60
```

**Expected Output:**
```
INFO:trading_orchestrator:Starting paper trading test for 60 seconds
INFO:trading_orchestrator:Test completed
{
  "test_duration": 60,
  "cycles_completed": 1,
  "total_trades": 0,
  "paper_pnl": 0
}
```

### Option 3: Full Automated Trading

```bash
# Start automated trading with 15-minute intervals
python run.py scheduled 15
```

**Expected Output:**
```
INFO:trading_orchestrator:Starting full automated trading
INFO:trading_orchestrator:Schedule:
INFO:trading_orchestrator:  - Pre-market check: 9:10 AM IST
INFO:trading_orchestrator:  - Trading cycles: Every 15 minutes during market hours
INFO:trading_orchestrator:  - End-of-day close: 3:25 PM IST
INFO:trading_orchestrator:  - Daily reset: 9:00 AM IST
INFO:trading_orchestrator:Starting scheduled trading with 15 minute intervals
```

**To Stop:** Press `Ctrl+C`

### Option 4: Performance Report

```bash
# Generate performance report
python run.py report
```

---

## Testing

### Run All Unit Tests

```bash
# Run all tests
pytest tests/ -v

# Run specific test file
pytest tests/test_market_data.py -v

# Run with coverage
pytest tests/ --cov=src --cov-report=html
```

### Test Individual Components

#### Test Market Data Fetcher
```bash
cd src
python -c "from market_data import MarketDataFetcher; fetcher = MarketDataFetcher(); print(fetcher.fetch_stock_data('RELIANCE'))"
```

#### Test Technical Analysis
```bash
cd src
python -c "from technical_analysis import TechnicalAnalyzer; import pandas as pd; df = pd.DataFrame({'Close': [100, 101, 102, 103, 104]}); analyzer = TechnicalAnalyzer(); print(analyzer.calculate_indicators(df))"
```

#### Test Signal Generation
```bash
cd src
python -c "from signal_generator import SignalGenerator; generator = SignalGenerator(); print(generator.generate_signal('RELIANCE'))"
```

### Integration Tests

```bash
# Run integration tests
pytest tests/test_integration.py -v
```

---

## Debugging

### Enable Debug Logging

Edit `.env` file:
```env
LOG_LEVEL=DEBUG
```

### Common Debugging Commands

#### Check Configuration
```bash
cd src
python -c "from config import config; print('Trading Amount:', config.TRADING_AMOUNT); print('Paper Trading:', config.PAPER_TRADING)"
```

#### Check Token Status
```bash
cd src
python -c "from token_manager import TokenManager; tm = TokenManager(); print('Token Valid:', tm.is_token_valid()); print('Token Expiry:', tm.token_expiry)"
```

#### Test Market Data
```bash
cd src
python -c "from market_data import MarketDataFetcher; fetcher = MarketDataFetcher(); data = fetcher.fetch_stock_data('RELIANCE'); print(data.head())"
```

#### Test Technical Indicators
```bash
cd src
python -c "from technical_analysis import TechnicalAnalyzer; import pandas as pd; import yfinance as yf; data = yf.download('RELIANCE.NS', period='1mo', interval='1d'); analyzer = TechnicalAnalyzer(); indicators = analyzer.calculate_indicators(data); print(indicators.columns)"
```

#### Test Kite Connection
```bash
python test_kite_auth.py
```

### Debug Mode with Python Debugger

```bash
# Run with pdb debugger
python -m pdb run.py once

# Common pdb commands:
# n - next line
# s - step into function
# c - continue
# p variable - print variable value
# l - list code
# q - quit
```

### Log File Analysis

Logs are printed to console. To save logs to file:

```bash
python run.py once > trading.log 2>&1
```

View logs:
```bash
cat trading.log
```

---

## Troubleshooting

### Issue 1: ModuleNotFoundError

**Error:**
```
ModuleNotFoundError: No module named 'config'
```

**Solution:**
```bash
# Always run from project root using run.py
python run.py once

# Or set PYTHONPATH
export PYTHONPATH=/Users/mithileshsinha/CascadeProjects/ai_trading_agent/src
python src/trading_orchestrator.py once
```

### Issue 2: Port Already in Use

**Error:**
```
Address already in use
Port 8080 is in use by another program
```

**Solution:**
```bash
# On macOS, disable AirPlay Receiver
# System Settings > General > AirDrop & Handoff > AirPlay Receiver > Off

# Or use a different port by editing get_kite_token.py
# Change port from 8080 to 8081
```

### Issue 3: Token Expired

**Error:**
```
Token expired. Provide request_token to refresh.
```

**Solution:**
```bash
# Refresh token
python get_kite_token.py

# Update .env with new request token
nano .env
```

### Issue 4: Market Data Fetching Fails

**Error:**
```
Error fetching market data
```

**Solution:**
```bash
# Check internet connection
ping google.com

# Test yfinance
python -c "import yfinance as yf; print(yf.download('RELIANCE.NS', period='1d'))"

# Check if NSE suffix is needed
# Use .NS suffix for NSE stocks
```

### Issue 5: Kite Connection Fails

**Error:**
```
Error initializing Kite Connect
```

**Solution:**
```bash
# Verify credentials
cat .env | grep KITE

# Test connection
python test_kite_auth.py

# Check if kiteconnect is installed
pip list | grep kiteconnect

# Reinstall if needed
pip install --upgrade kiteconnect
```

### Issue 6: No Trading Signals Generated

**Error:**
```
signals_generated: []
```

**Solution:**
```bash
# Check if market is open
# Market hours: 9:15 AM - 3:30 PM IST

# Check watchlist configuration
python -c "from config import config; print(config.WATCHLIST)"

# Lower confidence threshold in config.py
# Edit CONFIDENCE_THRESHOLD
```

### Issue 7: Paper Trading Mode Active

**Error:**
```
WARNING:broker_integration:Falling back to paper trading mode
```

**Solution:**
```bash
# This is normal if token expired or not configured
# To enable live trading:

# 1. Get fresh token
python get_kite_token.py

# 2. Update .env
nano .env
# Set PAPER_TRADING=False

# 3. Restart system
python run.py scheduled 15
```

### Issue 8: Dependencies Installation Fails

**Error:**
```
ERROR: Could not find a version that satisfies the requirement
```

**Solution:**
```bash
# Upgrade pip
python -m pip install --upgrade pip

# Install packages individually
pip install yfinance pandas numpy requests python-dotenv
pip install openai langchain langchain-openai langgraph
pip install kiteconnect beautifulsoup4 lxml
pip install pytest pytest-asyncio pytest-mock responses
pip install pytz schedule flask

# For Python 3.11, pandas-ta is not compatible
# Technical analysis uses manual implementation
```

---

## Quick Reference

### Essential Commands

```bash
# Setup
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Configuration
cp .env.example .env
nano .env

# Kite Setup
python get_kite_token.py

# Run System
python run.py once              # Single cycle
python run.py test 60          # Paper trading test
python run.py scheduled 15     # Full automation
python run.py report           # Performance report

# Testing
pytest tests/ -v               # All tests
pytest tests/test_market_data.py -v  # Specific test

# Debugging
python -c "from config import config; print(config.TRADING_AMOUNT)"
python test_kite_auth.py       # Test Kite connection
```

### File Locations

- **Configuration**: `.env`
- **Watchlist**: `src/config.py`
- **Logs**: Console output (can redirect to file)
- **Token Storage**: `data/kite_token.json`
- **Test Files**: `tests/`
- **Main Entry**: `run.py`

### Important Times (IST)

- **9:00 AM**: Daily reset
- **9:10 AM**: Pre-market check
- **9:15 AM**: Market opens
- **3:25 PM**: Auto square-off
- **3:30 PM**: Market closes

---

## Next Steps

1. **Start with Paper Trading**: Test the system thoroughly in paper trading mode
2. **Monitor Initial Runs**: Watch the first few trading cycles
3. **Review Performance**: Check performance reports regularly
4. **Adjust Parameters**: Optimize based on your trading style
5. **Enable Live Trading**: Only after thorough testing
6. **Regular Maintenance**: Refresh tokens daily, monitor system health

---

## Support

For issues or questions:
1. Check this guide first
2. Review `AUTOMATION.md` for automation details
3. Review `TESTING.md` for testing details
4. Check logs for error messages
5. Verify configuration in `.env`

---

## Security Notes

- Never commit `.env` file to version control
- Keep API keys secure
- Use paper trading mode for testing
- Monitor system regularly
- Review trade logs periodically
