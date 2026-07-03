# Monday Morning Startup Checklist - AI Trading Agent

## Overview
This checklist provides the exact steps to start the automated trading system on Monday morning after adding funds to your Demat account.

## Pre-Market Preparation (Before 9:00 AM IST)

### Step 1: Add Funds to Demat Account
- **Time**: Before 8:30 AM IST
- **Action**: Add funds to your Zerodha account
- **Minimum**: ₹5,000 (as configured)
- **Verification**: Check account balance in Zerodha app

### Step 2: Verify System Status
```bash
# Navigate to project directory
cd /Users/mithileshsinha/CascadeProjects/ai_trading_agent

# Activate virtual environment
source venv/bin/activate

# Check Python and dependencies
python --version
pip list
```

### Step 3: Refresh Kite Token (Required Daily)
```bash
# Run token handler
python get_kite_token.py
```

**Follow the prompts:**
1. Open the displayed login URL in browser
2. Login to Zerodha
3. Authorize the app
4. Copy the request token from the redirect page
5. Update `.env` file with new token

**Update .env:**
```bash
nano .env
# Update: KITE_REQUEST_TOKEN=new_token_here
```

### Step 4: Verify Configuration
```bash
# Check configuration
python -c "from config import config; print('Trading Amount:', config.TRADING_AMOUNT); print('Paper Trading:', config.PAPER_TRADING); print('Watchlist:', config.WATCHLIST)"
```

**Expected Output:**
```
Trading Amount: 5000
Paper Trading: False
Watchlist: ['RELIANCE', 'TCS', 'HDFCBANK', 'INFY', 'ICICIBANK']
```

### Step 5: Test Kite Connection
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

### Step 6: Verify Paper Trading is Disabled
```bash
nano .env
# Ensure: PAPER_TRADING=False
```

**Important**: Set to `False` for live trading with real money.

### Step 7: Check Watchlist
```bash
# Edit watchlist if needed
nano src/config.py
# Modify WATCHLIST list with your preferred stocks
```

## Market Open Sequence (9:00 AM - 9:15 AM IST)

### Step 8: Start Automated Trading System
```bash
# Start full automation
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

### Step 9: Monitor Pre-Market Check (9:10 AM IST)

**System will automatically:**
- Validate Kite token
- Check system configuration
- Verify trading parameters
- Log readiness status

**Watch for:**
```
INFO:trading_orchestrator:Executing pre-market check (9:10 AM IST)
INFO:trading_orchestrator:Token validated successfully
INFO:trading_orchestrator:System ready for trading
INFO:trading_orchestrator:Trading Amount: 5000
INFO:trading_orchestrator:Max Positions: 3
INFO:trading_orchestrator:Risk Per Trade: 2.0%
INFO:trading_orchestrator:Watchlist: ['RELIANCE', 'TCS', 'HDFCBANK', 'INFY', 'ICICIBANK']
```

## During Trading Hours (9:15 AM - 3:25 PM IST)

### What the System Does Automatically

#### Every 15 Minutes (Configurable):
1. **Market Check**: Verifies market is open
2. **Risk Check**: Ensures trading limits not exceeded
3. **AI Research**: Analyzes watchlist stocks
   - Fetches market data
   - Performs technical analysis (RSI, MACD, Bollinger Bands, ADX, Stochastic)
   - Performs sentiment analysis (news-based)
   - Evaluates risk parameters
4. **Signal Generation**: Creates buy/sell signals with confidence scores
5. **Order Execution**: Automatically executes best signals
   - Places buy orders for high-confidence signals
   - Places sell orders for exit signals
6. **Position Monitoring**: Checks stop-loss and target hits
   - Monitors all open positions
   - Triggers stop-loss if price drops 2%
   - Triggers target sell if price rises 4%
7. **Logging**: Records all trades and performance

#### Continuous Monitoring:
- Position P&L tracking
- Stop-loss monitoring
- Target profit monitoring
- Risk limit checking
- Consecutive loss protection

### What You Need to Do Manually

#### Initial Monitoring (First 30 Minutes):
- Watch the first few trading cycles
- Verify orders are being placed correctly
- Check that positions are being monitored
- Ensure no errors in logs

#### Periodic Checks (Every Hour):
- Review trading logs
- Check position status
- Verify P&L
- Ensure system is running smoothly

#### Alerts to Watch For:
- **Token expiry warnings**: Refresh token if needed
- **Risk limit breaches**: System will stop trading automatically
- **Order execution failures**: Check broker connection
- **Market status changes**: System handles automatically

## End of Day (3:25 PM IST)

### Automatic Actions:
- **Auto Square-Off**: All positions closed automatically
- **Daily Reset**: Statistics reset for next day
- **No Overnight Positions**: Ensures intraday-only trading

**Watch for:**
```
INFO:trading_orchestrator:Executing end-of-day close (3:25 PM IST)
INFO:order_executor:Closing all positions
INFO:trading_orchestrator:End-of-day close completed: X positions closed
INFO:trading_orchestrator:Executing daily reset
```

## Post-Market (After 3:30 PM IST)

### Step 10: Generate Performance Report
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
  "max_drawdown": -50.00,
  "best_trade": 100.00,
  "worst_trade": -50.00
}
```

### Step 11: Review Trade Log
```bash
# Check trade log (stored in memory during session)
# Review individual trades, entry/exit points, P&L
```

### Step 12: Stop the System (Optional)
```bash
# Press Ctrl+C in the terminal running the system
# System will stop gracefully
```

## Daily Routine Summary

### Before Market (8:30 AM - 9:00 AM):
1. Add funds to Demat account
2. Refresh Kite token
3. Verify configuration
4. Test connection
5. Start system

### During Market (9:15 AM - 3:25 PM):
- System runs automatically
- Monitor periodically (every hour)
- Watch for alerts

### After Market (3:30 PM onwards):
- Generate performance report
- Review trades
- Stop system (optional)

## Important Notes

### System Capabilities:
✅ **Fully Automated**: No manual order placement required
✅ **AI Analysis**: Technical + sentiment analysis
✅ **Risk Management**: Automatic position sizing, stop-loss, targets
✅ **Intraday Only**: Auto square-off at 3:25 PM
✅ **Continuous Monitoring**: Real-time position tracking
✅ **Automatic Exits**: Sells at target or stop-loss

### Your Responsibilities:
⚠️ **Daily Token Refresh**: Required every morning before market open
⚠️ **Initial Monitoring**: Watch first few trading cycles
⚠️ **Periodic Checks**: Review system status every hour
⚠️ **Performance Review**: Check end-of-day report
⚠️ **Fund Management**: Ensure sufficient margin in account

### Safety Features:
🛡️ **Paper Trading Mode**: Test before live trading
🛡️ **Risk Limits**: Stops trading if limits exceeded
🛡️ **Stop-Loss**: Automatic 2% loss protection
🛡️ **Target Profit**: Automatic 4% profit taking
🛡️ **No Overnight Positions**: Auto square-off at 3:25 PM
🛡️ **Consecutive Loss Protection**: Stops after 3 losses

## Troubleshooting Monday Morning

### Issue: Token Refresh Fails
**Solution:**
```bash
# Check internet connection
# Verify Kite credentials in .env
# Try again with get_kite_token.py
```

### Issue: System Won't Start
**Solution:**
```bash
# Check virtual environment is activated
# Verify dependencies installed
# Check configuration in .env
# Review error logs
```

### Issue: No Orders Placed
**Solution:**
- Check if market is open (9:15 AM - 3:30 PM)
- Verify PAPER_TRADING=False
- Check watchlist configuration
- Review signal confidence threshold
- Ensure sufficient margin in account

### Issue: Orders Not Executing
**Solution:**
- Check Kite connection
- Verify sufficient funds
- Review risk parameters
- Check broker status

## Quick Reference Commands

```bash
# Start system
python run.py scheduled 15

# Test connection
python test_kite_auth.py

# Refresh token
python get_kite_token.py

# Performance report
python run.py report

# Single cycle test
python run.py once

# Check configuration
python -c "from config import config; print(config.TRADING_AMOUNT)"
```

## Success Indicators

✅ **System starts without errors**
✅ **Pre-market check passes at 9:10 AM**
✅ **First trading cycle executes at 9:15 AM**
✅ **Orders placed automatically**
✅ **Positions monitored continuously**
✅ **Auto square-off at 3:25 PM**
✅ **Performance report shows trades**

## Contact Support

If issues arise:
1. Check COMPLETE_SETUP_GUIDE.md
2. Review logs for error messages
3. Verify configuration in .env
4. Check Kite connection status

---

## Final Checklist for Monday Morning

- [ ] Add funds to Demat account (before 8:30 AM)
- [ ] Activate virtual environment
- [ ] Refresh Kite token
- [ ] Update .env with new token
- [ ] Test Kite connection
- [ ] Verify PAPER_TRADING=False
- [ ] Check watchlist configuration
- [ ] Start automated trading system
- [ ] Monitor first trading cycle
- [ ] Verify orders being placed
- [ ] Check position monitoring
- [ ] Review end-of-day report

**The system will handle everything else automatically!**
