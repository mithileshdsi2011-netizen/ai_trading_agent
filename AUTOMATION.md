# Automated Trading System Documentation

## Overview

The AI Trading Agent now supports full automation with the following features:

- **Automated Zerodha Authentication**: Token refresh mechanism eliminates manual token updates
- **AI-Based Trading**: Market research, technical analysis, sentiment analysis, and risk assessment
- **Automatic Order Execution**: Buy/sell orders placed based on AI signals and predefined rules
- **Auto Square-Off**: All positions closed before market close (3:25 PM IST)
- **Risk Management**: Built-in position sizing, stop-loss, and daily loss limits

## Automation Features

### 1. Automated Token Management

The system automatically:
- Stores access tokens in `data/kite_token.json`
- Checks token validity before each use
- Refreshes tokens when expired (requires manual OAuth flow)
- Provides 1-hour buffer before expiry

**Token Refresh Process:**
```bash
python get_kite_token.py
```

### 2. Trading Schedule

The automated trading system follows this schedule:

| Time (IST) | Action |
|------------|--------|
| 9:00 AM | Daily statistics reset |
| 9:10 AM | Pre-market check (token validation, system readiness) |
| 9:15 AM - 3:25 PM | Trading cycles (configurable interval, default 15 min) |
| 3:25 PM | Auto square-off all positions |
| 3:30 PM | Market close |

### 3. Automated Trading Cycle

Each trading cycle performs:

1. **Market Check**: Verifies market is open
2. **Risk Check**: Ensures trading limits not exceeded
3. **AI Research**: Analyzes watchlist stocks
   - Technical analysis (RSI, MACD, Bollinger Bands, etc.)
   - Sentiment analysis (news-based)
   - Risk assessment
4. **Signal Generation**: Creates buy/sell signals with confidence scores
5. **Order Execution**: Automatically executes best signals
6. **Position Monitoring**: Checks stop-loss and target hits
7. **Logging**: Records all trades and performance

### 4. Risk Management

Built-in risk controls:

- **Position Sizing**: Automatic calculation based on capital
- **Stop Loss**: 2% loss trigger (configurable)
- **Target Profit**: 4% profit target (configurable)
- **Daily Loss Limit**: Stops trading if daily loss exceeds 5% of capital
- **Consecutive Loss Protection**: Stops after 3 consecutive losses
- **Max Positions**: Limits concurrent positions (default: 3)
- **Risk-Reward Ratio**: Only trades with ratio >= 1.5

## Usage

### Initial Setup

1. **Configure Environment Variables** (.env):
```env
OPENAI_API_KEY=your_openai_api_key
KITE_API_KEY=your_kite_api_key
KITE_API_SECRET=your_kite_api_secret
TRADING_AMOUNT=5000
PAPER_TRADING=False  # Set to False for live trading
```

2. **Get Initial Kite Token**:
```bash
python get_kite_token.py
```

3. **Update .env with Request Token**:
```env
KITE_REQUEST_TOKEN=your_request_token
```

### Running Automated Trading

**Start Full Automation:**
```bash
cd src
python trading_orchestrator.py scheduled 15
```

This starts:
- Pre-market check at 9:10 AM
- Trading cycles every 15 minutes during market hours
- Auto square-off at 3:25 PM
- Daily reset at 9:00 AM

**Single Trading Cycle:**
```bash
python trading_orchestrator.py once
```

**Paper Trading Test:**
```bash
python trading_orchestrator.py test 60
```

**Performance Report:**
```bash
python trading_orchestrator.py report
```

## Token Refresh

Tokens expire after 24 hours. The system will:
- Automatically check token validity
- Alert when token needs refresh
- Fall back to paper trading if token invalid

**Manual Token Refresh:**
```bash
python get_kite_token.py
```

## Monitoring

### Log Files

All trading activities are logged:
- Console output with INFO level
- Trade log stored in memory
- Performance reports available via `report` command

### Key Metrics

Monitor these metrics:
- Total trades executed
- Win rate
- Daily P&L
- Position status
- Risk limit status

### Alerts

The system alerts on:
- Token expiry
- Risk limit breaches
- Order execution failures
- Market status changes

## Safety Features

### Pre-Market Validation

At 9:10 AM IST, the system:
- Validates Kite token
- Checks system configuration
- Verifies trading parameters
- Logs readiness status

### End-of-Day Protection

At 3:25 PM IST, the system:
- Closes all open positions
- Resets daily statistics
- Ensures no overnight positions
- Logs closing summary

### Risk Limits

The system stops trading if:
- Daily loss exceeds 5% of capital
- 3 consecutive losses occur
- Risk-reward ratio < 1.5
- Maximum positions reached

## Troubleshooting

### Token Issues

**Problem**: Token expired
**Solution**: Run `python get_kite_token.py`

**Problem**: Token invalid
**Solution**: Check API credentials in .env

### Order Execution Failures

**Problem**: Order not executed
**Solution**: Check:
- Market is open
- Sufficient funds
- Risk parameters met
- Broker connection active

### System Not Trading

**Problem**: No orders placed
**Solution**: Check:
- Market hours (9:15 AM - 3:30 PM IST)
- Signal confidence threshold
- Risk limits not exceeded
- Paper trading mode

## Best Practices

1. **Start with Paper Trading**: Test thoroughly before live trading
2. **Monitor Initial Runs**: Watch first few trading cycles closely
3. **Adjust Parameters**: Optimize based on performance
4. **Regular Token Refresh**: Refresh token daily before market open
5. **Review Performance**: Analyze reports weekly
6. **Keep Sufficient Margin**: Ensure adequate margin for intraday trading
7. **Monitor Risk Limits**: Watch for risk limit breaches

## Configuration

### Key Parameters (.env)

```env
TRADING_AMOUNT=5000          # Total capital
MAX_POSITIONS=3              # Max concurrent positions
RISK_PER_TRADE=0.02         # 2% risk per trade
STOP_LOSS_PERCENTAGE=0.02   # 2% stop loss
TARGET_PERCENTAGE=0.04      # 4% target profit
PAPER_TRADING=False         # True for testing, False for live
```

### Watchlist (config.py)

Edit `config.py` to customize:
```python
WATCHLIST: list = [
    "RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK"
]
```

## Security

- Tokens stored locally in `data/kite_token.json`
- No credentials in source code
- Environment variables for sensitive data
- Paper trading mode for safe testing

## Disclaimer

This is an automated trading system. Trading involves significant risk of loss. The author is not responsible for any financial losses. Always monitor the system and use appropriate risk management.
