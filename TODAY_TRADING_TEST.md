# Complete Trading Test Steps - Today

## Overview
This guide provides step-by-step instructions to test and start live trading today with minimal involvement.

## ⚠️ Important Note on Token Limitation

**Kite Connect requires daily manual token refresh (browser interaction)**
- This is a Zerodha security requirement that cannot be bypassed
- Even with your complete login details, browser OAuth is required
- This takes ~2 minutes per day
- After that, everything is fully automated

---

## Step 1: System Readiness Check

```bash
cd /Users/mithileshsinha/CascadeProjects/ai_trading_agent
source venv/bin/activate

# Check configuration
python -c "from config import config; print('Trading Amount:', config.TRADING_AMOUNT); print('Paper Trading:', config.PAPER_TRADING); print('Market Hours:', config.MARKET_OPEN, '-', config.MARKET_CLOSE)"
```

**Expected Output:**
```
Trading Amount: 5000
Paper Trading: True
Market Hours: 09:30 - 15:00
```

---

## Step 2: Get Fresh Request Token (Required Daily)

```bash
python get_kite_token.py
```

**Follow the browser prompts:**
1. Open the displayed login URL in your browser
2. Login to Zerodha with your credentials
3. Authorize the app
4. You'll be redirected to `http://localhost:8080/login`
5. Copy the request token displayed on the page
6. Update `.env` file:

```bash
nano .env
# Update this line with the new token:
KITE_REQUEST_TOKEN=your_new_token_here
```

**Save and exit** (Ctrl+O, Enter, Ctrl+X)

---

## Step 3: Test Kite Connection

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

If this fails, the token is invalid. Go back to Step 2.

---

## Step 4: Analyze Profitable Stocks (Optional - See Recommendations)

```bash
python auto_stock_selector.py
```

**This will:**
- Analyze your 10 stocks
- Calculate technical indicators
- Generate trading scores
- Show top opportunities
- Recommend best trade

**Expected Output:**
```
============================================================
AUTOMATED PROFITABLE STOCK SELECTOR
============================================================
Time: 2026-06-24 10:XX:XX
Capital Available: ₹5000
Risk Per Trade: 2.0% (₹100.0)
Stop Loss: 2.0%
Target: 4.0%
============================================================

Analyzing 10 stocks...

Analyzing BAJFINANCE... ✅ BUY (Score: 5)
Analyzing JSWSTEEL... ⏭️  HOLD (Score: 1)
...

============================================================
TOP TRADING OPPORTUNITIES
============================================================

1. BAJFINANCE
   Action: BUY
   Score: 5/10
   Current Price: ₹XXXX.XX
   Quantity: X shares
   Required Capital: ₹XXXX.XX
   Entry: ₹XXXX.XX
   Stop Loss: ₹XXXX.XX (Risk: ₹XX.XX)
   Target: ₹XXXX.XX (Reward: ₹XX.XX)
   Risk-Reward: 1:2.0
   Reasons: Low RSI (<40), Bullish MACD, Price above EMA
   Indicators: RSI=XX.X, MACD=XX.X, ADX=XX.X

============================================================
RECOMMENDED TRADE
============================================================
Stock: BAJFINANCE
Action: BUY
Buy: X shares @ ₹XXXX.XX
Total Investment: ₹XXXX.XX
Stop Loss: ₹XXXX.XX (Risk: ₹XX.XX)
Target: ₹XXXX.XX (Reward: ₹XX.XX)
Risk-Reward Ratio: 1:2.0
============================================================
```

---

## Step 5: Enable Live Trading

```bash
nano .env
# Change this line:
PAPER_TRADING=False
```

**Save and exit** (Ctrl+O, Enter, Ctrl+X)

---

## Step 6: Start Fully Automated Trading

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

---

## What Happens After This (Fully Automated)

### During Market Hours (9:30 AM - 3:00 PM IST)

**Every 15 minutes, the system will:**

1. ✅ Check market status
2. ✅ Read your Kite portfolio
3. ✅ Analyze all 10 stocks
4. ✅ Calculate technical indicators (RSI, MACD, Bollinger Bands, ADX, etc.)
5. ✅ Generate trading signals (BUY/SELL/HOLD)
6. ✅ Execute buy orders for high-confidence signals
7. ✅ Execute sell orders for exit signals
8. ✅ Monitor all open positions
9. ✅ Check stop-loss (2%) and target (4%) triggers
10. ✅ Apply risk management (position sizing, daily limits)
11. ✅ Log all activities

### At 3:00 PM IST

- ✅ Auto square-off all positions
- ✅ No overnight positions
- ✅ Daily statistics reset

---

## Monitoring (Optional)

### Watch for These Log Messages

**Successful Trading:**
```
INFO:trading_orchestrator:Starting trading cycle
INFO:signal_generator:Generating signal for BAJFINANCE
INFO:order_executor:Order placed: BUY 5 BAJFINANCE @ ₹1000
INFO:trading_orchestrator:Monitoring existing positions
```

**Stop-Loss Triggered:**
```
INFO:order_executor:Stop-loss triggered for BAJFINANCE
INFO:order_executor:Order placed: SELL 5 BAJFINANCE @ ₹980
```

**Target Hit:**
```
INFO:order_executor:Target hit for BAJFINANCE
INFO:order_executor:Order placed: SELL 5 BAJFINANCE @ ₹1040
```

**Risk Limit Reached:**
```
WARNING:risk_manager:Daily loss limit reached
INFO:trading_orchestrator:Trading stopped due to risk limits
```

### Performance Report

**In a new terminal (keep trading running):**
```bash
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

---

## Emergency Stop

**If you need to stop trading immediately:**

Press `Ctrl+C` in the terminal running the system

This will:
- Stop new trading cycles
- Keep existing positions open
- Allow manual management via Kite app

---

## Complete Daily Routine (3 Commands)

**Morning (Before 9:20 AM IST):**

```bash
# Command 1: Get fresh token (browser interaction - ~1 minute)
python get_kite_token.py

# Command 2: Update .env with new token (copy-paste - ~30 seconds)
nano .env
# Update: KITE_REQUEST_TOKEN=new_token

# Command 3: Start trading (fully automated - ~5 seconds)
python run.py scheduled 15
```

**Total time: ~2 minutes per day**
**After that: Zero involvement until market close**

---

## Testing Checklist

### Before Market Open

- [ ] Virtual environment activated
- [ ] Fresh request token obtained
- [ ] .env updated with new token
- [ ] Kite connection tested successfully
- [ ] PAPER_TRADING set to False
- [ ] Funds available in Zerodha account (minimum ₹5000)
- [ ] System started with `python run.py scheduled 15`

### During Market Hours

- [ ] System running without errors
- [ ] Trading cycles executing every 15 minutes
- [ ] Orders being placed for high-confidence signals
- [ ] Positions being monitored
- [ ] No risk limit breaches

### After Market Close

- [ ] All positions closed at 3:00 PM
- [ ] Performance report generated
- [ ] Review day's results

---

## Troubleshooting

### Issue: Token expired error

**Solution:**
```bash
python get_kite_token.py
# Update .env with new token
```

### Issue: No orders being placed

**Check:**
```bash
# Verify market is open
python -c "from market_data import MarketDataFetcher; print('Market Open:', MarketDataFetcher().is_market_open())"

# Verify live trading mode
python -c "from config import config; print('Paper Trading:', config.PAPER_TRADING)"

# Check Kite connection
python test_kite_auth.py
```

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

---

## Risk Management

### Built-in Safety Features

✅ **Position Sizing**: Automatic calculation based on capital
✅ **Stop-Loss**: 2% loss protection
✅ **Target Profit**: 4% profit taking
✅ **Daily Loss Limit**: Stops trading if daily loss exceeds 5% (₹250)
✅ **Consecutive Loss Protection**: Stops after 3 consecutive losses
✅ **Max Positions**: Maximum 3 concurrent positions
✅ **No Overnight Positions**: Auto square-off at 3:00 PM
✅ **Risk-Reward Ratio**: Only trades with ratio >= 1:2

### Your Responsibilities

⚠️ **Daily token refresh** (2 minutes per day)
⚠️ **Monitor first 30 minutes** on first day
⚠️ **Keep emergency stop ready** (Ctrl+C)
⚠️ **Review performance daily**
⚠️ **Ensure sufficient margin** in account

---

## Summary

### What the System Does Automatically

✅ Stock analysis
✅ Signal generation
✅ Order execution
✅ Position monitoring
✅ Risk management
✅ Auto square-off
✅ Performance tracking

### Your Daily Involvement

❌ **Daily token refresh** (browser interaction required - 2 minutes)
❌ **Start the system** (1 command - 5 seconds)

**Total: ~2 minutes per day**

### To Start Trading Today

1. **Get fresh token:**
   ```bash
   python get_kite_token.py
   ```

2. **Update .env:**
   ```bash
   nano .env
   # Update KITE_REQUEST_TOKEN
   # Set PAPER_TRADING=False
   ```

3. **Test connection:**
   ```bash
   python test_kite_auth.py
   ```

4. **Start trading:**
   ```bash
   python run.py scheduled 15
   ```

The system will handle everything else automatically.

---

## Disclaimer

**This is an automated trading system. Trading involves significant risk of loss. The author is not responsible for any financial losses. Always monitor the system and use appropriate risk management.**

---

## Next Steps

1. **Complete the testing checklist above**
2. **Start with paper trading mode first** (recommended)
3. **Monitor first few trading cycles**
4. **Switch to live trading when comfortable**
5. **Review performance daily**

**The system is ready for live trading once you complete the setup steps above.**
