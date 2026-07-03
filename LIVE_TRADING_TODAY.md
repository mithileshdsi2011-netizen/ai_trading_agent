# Live Trading Test Guide - Today

## Overview
This guide provides step-by-step instructions to test the AI trading agent with live trading today. The system will automatically read your Kite portfolio, analyze stocks, and execute trades without your involvement after initial setup.

## ⚠️ IMPORTANT WARNING

**LIVE TRADING INVOLVES REAL MONEY AND REAL RISK**

- You will be trading with your actual funds
- The system will automatically place buy/sell orders
- Losses are possible and can be significant
- Start with a small amount (₹5000 as configured)
- Monitor the system closely during testing
- Be ready to stop the system immediately if needed

## Pre-Requisites Checklist

Before starting live trading today, ensure:

- [ ] Funds added to Zerodha account (minimum ₹5000)
- [ ] Kite API credentials configured in .env
- [ ] Kite request token obtained and added to .env
- [ ] PAPER_TRADING set to False in .env
- [ ] Virtual environment activated
- [ ] All dependencies installed
- [ ] System tested in paper trading mode first

## Step-by-Step Live Trading Setup

### Step 1: Navigate to Project Directory

```bash
cd /Users/mithileshsinha/CascadeProjects/ai_trading_agent
source venv/bin/activate
```

### Step 2: Verify Configuration

```bash
# Check current configuration
python -c "from config import config; print('Trading Amount:', config.TRADING_AMOUNT); print('Paper Trading:', config.PAPER_TRADING); print('Market Hours:', config.MARKET_OPEN, '-', config.MARKET_CLOSE)"
```

**Expected Output:**
```
Trading Amount: 5000
Paper Trading: False
Market Hours: 09:30 - 15:00
```

**If Paper Trading is True, update .env:**
```bash
nano .env
# Change: PAPER_TRADING=False
```

### Step 3: Refresh Kite Token (Required Daily)

```bash
python get_kite_token.py
```

**Follow the prompts:**
1. Open the displayed login URL in browser
2. Login to Zerodha
3. Authorize the app
4. Copy the request token from the redirect page
5. Update .env with new token

```bash
nano .env
# Update: KITE_REQUEST_TOKEN=new_token_here
```

### Step 4: Test Kite Connection

```bash
python test_kite_auth.py
```

**Expected Output:**
```
✓ Session generated successfully!
✓ Access Token: <token>
✓ Connected successfully!
User ID: <your_id>
User Name: <your_name>
Email: <your_email>
✓ Available Margins: <amount>
```

### Step 5: Check Your Kite Portfolio

```bash
cd src
python -c "from broker_integration import BrokerIntegration; broker = BrokerIntegration(); print(broker.get_holdings())"
```

**Expected Output:**
```
{
  'cash': 0,
  'positions': [...],
  'total_value': <amount>
}
```

This will show your current holdings in Kite.

### Step 6: Test Single Trading Cycle

```bash
cd /Users/mithileshsinha/CascadeProjects/ai_trading_agent
python run.py once
```

**Expected Output:**
```json
{
  "timestamp": "2026-06-20T...",
  "market_open": true/false,
  "signals_generated": [...],
  "orders_executed": [...],
  "positions_monitored": [...],
  "errors": []
}
```

**If market is closed (current time is not 9:30 AM - 3:00 PM IST):**
- You will see `market_open: false`
- No orders will be executed
- This is normal - system will trade during market hours

### Step 7: Start Automated Live Trading

```bash
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
INFO:trading_orchestrator:Starting scheduled trading with 15 minute intervals
```

## What the System Will Do Automatically

### During Market Hours (9:30 AM - 3:00 PM IST)

**Every 15 minutes, the system will:**

1. **Check Market Status**: Verify market is open
2. **Read Your Kite Portfolio**: Get current holdings and positions
3. **Analyze Watchlist Stocks**: 
   - Fetch market data for all stocks in watchlist
   - Perform technical analysis (RSI, MACD, Bollinger Bands, etc.)
   - Perform sentiment analysis (news-based)
   - Evaluate risk parameters
4. **Generate Trading Signals**: Create buy/sell signals with confidence scores
5. **Execute Orders Automatically**:
   - Place buy orders for high-confidence buy signals
   - Place sell orders for exit signals or target hits
   - Apply risk management (position sizing, stop-loss, targets)
6. **Monitor Positions**: Check all open positions for:
   - Stop-loss triggers (2% loss)
   - Target profit triggers (4% gain)
   - Risk limit compliance
7. **Log All Activity**: Record trades, P&L, and performance

### At 3:00 PM IST

- **Auto Square-Off**: All positions closed automatically
- **No Overnight Positions**: Ensures intraday-only trading
- **Daily Reset**: Statistics reset for next day

## Monitoring the System

### What to Watch For

**During Trading:**
- Look for successful order execution messages
- Check that positions are being monitored
- Monitor for any error messages
- Watch for risk limit warnings

**Key Log Messages:**
- `INFO:trading_orchestrator:Starting trading cycle` - New cycle started
- `INFO:order_executor:Order placed: BUY ...` - Buy order executed
- `INFO:order_executor:Order placed: SELL ...` - Sell order executed
- `INFO:order_executor:Stop-loss triggered for ...` - Stop-loss hit
- `INFO:order_executor:Target hit for ...` - Target achieved
- `WARNING:risk_manager:Daily loss limit reached` - Trading stopped

### Performance Tracking

**Generate Performance Report:**
```bash
# In a new terminal (keep trading running)
python run.py report
```

**Expected Output:**
```json
{
  "total_trades": 5,
  "winning_trades": 3,
  "losing_trades": 2,
  "win_rate": 60.0,
  "total_pnl": 150.50,
  "max_drawdown": -50.00
}
```

## Stopping the System

### Emergency Stop

**Press Ctrl+C in the terminal running the system**

This will:
- Stop new trading cycles
- Not close existing positions (they remain open)
- Allow you to manually manage positions

### Graceful Stop at Market Close

The system will automatically:
- Close all positions at 3:00 PM
- Reset daily statistics
- Stop trading

## Risk Management

### Built-in Safety Features

**Position Sizing:**
- Automatically calculates position size based on capital
- Risk per trade: 2% of capital (₹100 on ₹5000)

**Stop-Loss:**
- Automatic 2% loss protection
- Position closed if price drops 2%

**Target Profit:**
- Automatic 4% profit taking
- Position closed if price rises 4%

**Daily Loss Limit:**
- Trading stops if daily loss exceeds 5% of capital (₹250 on ₹5000)

**Consecutive Loss Protection:**
- Trading stops after 3 consecutive losses

**Max Positions:**
- Maximum 3 concurrent positions

**No Overnight Positions:**
- All positions closed at 3:00 PM

### Manual Intervention

**If you need to stop trading:**
1. Press Ctrl+C to stop the system
2. Manually close positions in Kite app if needed
3. Review what happened

**If you see unexpected behavior:**
1. Stop the system immediately (Ctrl+C)
2. Check the logs for errors
3. Review your positions in Kite app
4. Contact support if needed

## Testing Today (Sunday)

Since the market is closed on Sunday, you can:

### Test 1: Verify Configuration
```bash
python -c "from config import config; print('Paper Trading:', config.PAPER_TRADING)"
```

### Test 2: Test Kite Connection
```bash
python test_kite_auth.py
```

### Test 3: Check Portfolio
```bash
cd src
python -c "from broker_integration import BrokerIntegration; print(BrokerIntegration().get_holdings())"
```

### Test 4: Test Single Cycle (Market Closed)
```bash
cd /Users/mithileshsinha/CascadeProjects/ai_trading_agent
python run.py once
```

This will verify the system works even when market is closed.

## Live Trading Tomorrow (Monday)

### Morning Checklist (Before 9:20 AM IST)

1. **8:30 AM**: Add funds to Zerodha account (if needed)
2. **9:00 AM**: Refresh Kite token
   ```bash
   python get_kite_token.py
   ```
3. **9:10 AM**: Update .env with new token
4. **9:15 AM**: Test Kite connection
   ```bash
   python test_kite_auth.py
   ```
5. **9:18 AM**: Verify configuration
   ```bash
   python -c "from config import config; print('Paper Trading:', config.PAPER_TRADING)"
   ```
6. **9:19 AM**: Start the system
   ```bash
   python run.py scheduled 15
   ```

### During Trading (9:30 AM - 3:00 PM IST)

- Monitor the terminal for activity
- Check for order execution messages
- Watch for any errors
- Review performance periodically

### After Trading (3:00 PM IST)

- System will auto square-off all positions
- Generate performance report
- Review the day's results

## Troubleshooting

### Issue: System won't start

**Check:**
```bash
# Verify virtual environment
source venv/bin/activate

# Check dependencies
pip list

# Check configuration
python -c "from config import config; print(config.validate())"
```

### Issue: No orders being placed

**Check:**
```bash
# Verify market is open
python -c "from market_data import MarketDataFetcher; print('Market Open:', MarketDataFetcher().is_market_open())"

# Check paper trading mode
python -c "from config import config; print('Paper Trading:', config.PAPER_TRADING)"

# Check Kite connection
python test_kite_auth.py
```

### Issue: Orders failing

**Check:**
- Sufficient margin in account
- Kite API credentials correct
- Token is valid (refresh if needed)
- Market is open

### Issue: System not reading Kite portfolio

**Check:**
```bash
cd src
python -c "from broker_integration import BrokerIntegration; broker = BrokerIntegration(); print(broker.get_holdings())"
```

## Code Readiness Assessment

### ✅ Ready for Live Trading

The code includes:

1. **Kite Integration**: Full integration with Zerodha Kite Connect
2. **Portfolio Reading**: Automatically reads your Kite holdings and positions
3. **Market Data**: Fetches real-time market data
4. **Technical Analysis**: Calculates indicators (RSI, MACD, Bollinger Bands, etc.)
5. **Sentiment Analysis**: Analyzes news for market sentiment
6. **Signal Generation**: Creates buy/sell signals with confidence scores
7. **Order Execution**: Automatically places orders via Kite
8. **Position Monitoring**: Continuously monitors open positions
9. **Risk Management**: Built-in stop-loss, targets, and risk limits
10. **Auto Square-Off**: Closes all positions at 3:00 PM
11. **Token Management**: Automatic token validation and refresh
12. **Error Handling**: Comprehensive error handling and logging

### ⚠️ Recommendations Before Live Trading

1. **Test in Paper Trading Mode First**: Run the system in paper trading mode for at least one full trading day
2. **Start with Small Amount**: Use minimum amount (₹5000) for initial testing
3. **Monitor Closely**: Watch the system during the first few trading cycles
4. **Have Exit Plan**: Know how to stop the system quickly if needed
5. **Review Logs**: Check logs regularly for any issues
6. **Test Token Refresh**: Practice refreshing the token before market open

## Quick Reference

### Essential Commands

```bash
# Start live trading
python run.py scheduled 15

# Stop trading
# Press Ctrl+C

# Test connection
python test_kite_auth.py

# Refresh token
python get_kite_token.py

# Performance report
python run.py report

# Single cycle
python run.py once

# Check portfolio
cd src
python -c "from broker_integration import BrokerIntegration; print(BrokerIntegration().get_holdings())"
```

### Important Times (IST)

- **9:00 AM**: Daily reset
- **9:20 AM**: Pre-market check
- **9:30 AM**: Market opens, trading starts
- **3:00 PM**: Market close, auto square-off

### Configuration

- **Trading Amount**: ₹5000 (configurable in .env)
- **Market Hours**: 9:30 AM - 3:00 PM IST
- **Risk Per Trade**: 2%
- **Stop Loss**: 2%
- **Target**: 4%
- **Max Positions**: 3

## Final Checklist Before Live Trading

- [ ] Funds added to Zerodha account
- [ ] Kite API credentials configured
- [ ] Kite token refreshed today
- [ ] PAPER_TRADING set to False
- [ ] System tested in paper trading mode
- [ ] Kite connection verified
- [ ] Portfolio reading verified
- [ ] Risk parameters reviewed
- [ ] Emergency stop procedure understood
- [ ] Monitoring plan in place

## Disclaimer

**This is an automated trading system. Trading involves significant risk of loss. The author is not responsible for any financial losses. Always monitor the system and use appropriate risk management.**

---

## Next Steps

1. **Complete the pre-requisites checklist**
2. **Test the system today (Sunday) with market closed**
3. **Start live trading tomorrow (Monday) following the morning checklist**
4. **Monitor the system closely during trading hours**
5. **Review performance at end of day**
6. **Adjust parameters based on results**

**The system is ready for live trading once you complete the setup and testing steps above.**
