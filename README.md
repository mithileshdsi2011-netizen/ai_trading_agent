# AI Stock Trading Agent

An AI-powered intraday trading agent for personal use with Zerodha Kite Connect integration. The system performs market research, generates trading signals, manages risk, and executes trades automatically.

## Features

- **AI-Powered Research**: Combines technical analysis and sentiment analysis
- **Technical Analysis**: RSI, MACD, Bollinger Bands, ADX, Moving Averages
- **Sentiment Analysis**: News-based sentiment using OpenAI GPT
- **Signal Generation**: Automated buy/sell signals with confidence scores
- **Risk Management**: Position sizing, stop-loss, target prices, daily loss limits
- **Broker Integration**: Zerodha Kite Connect (with paper trading mode)
- **Automated Trading**: Scheduled execution or manual trigger
- **Portfolio Tracking**: Real-time position monitoring and P&L tracking

## System Requirements

- Python 3.8+
- 5000 INR initial capital (configurable)
- OpenAI API key (for AI analysis)
- Zerodha account (for live trading, optional for paper trading)

## Installation

### 1. Clone the repository

```bash
cd ai_trading_agent
```

### 2. Create virtual environment

```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Install TA-Lib (required for technical analysis)

**On macOS:**
```bash
brew install ta-lib
pip install ta-lib
```

**On Linux:**
```bash
wget http://prdownloads.sourceforge.net/ta-lib/ta-lib-0.4.0-src.tar.gz
tar -xzf ta-lib-0.4.0-src.tar.gz
cd ta-lib/
./configure --prefix=/usr
make
sudo make install
pip install ta-lib
```

**On Windows:**
Download the appropriate wheel file from https://www.lfd.uci.edu/~gohlke/pythonlibs/#ta-lib and install:
```bash
pip install TA_Lib‑0.4.28‑cp3X‑cp3X‑win_amd64.whl
```

### 5. Configure environment variables

```bash
cp .env.example .env
```

Edit `.env` and add your API keys:

```env
OPENAI_API_KEY=your_openai_api_key_here
KITE_API_KEY=your_kite_api_key_here
KITE_API_SECRET=your_kite_api_secret_here
KITE_REQUEST_TOKEN=your_kite_request_token_here

TRADING_AMOUNT=5000
MAX_POSITIONS=3
RISK_PER_TRADE=0.02
STOP_LOSS_PERCENTAGE=0.02
TARGET_PERCENTAGE=0.04

PAPER_TRADING=True
```

## Usage

### Run single trading cycle

```bash
cd src
python trading_orchestrator.py once
```

### Run scheduled trading (every 15 minutes)

```bash
cd src
python trading_orchestrator.py scheduled 15
```

### Run paper trading test (60 minutes)

```bash
cd src
python trading_orchestrator.py test 60
```

### Generate performance report

```bash
cd src
python trading_orchestrator.py report
```

## Project Structure

```
ai_trading_agent/
├── src/
│   ├── market_data.py           # Market data fetcher (NSE India)
│   ├── technical_analysis.py    # Technical analysis indicators
│   ├── sentiment_analysis.py   # News sentiment analysis
│   ├── ai_research_agent.py     # AI research coordinator
│   ├── signal_generator.py      # Trading signal generation
│   ├── risk_manager.py          # Risk management module
│   ├── broker_integration.py    # Zerodha Kite Connect integration
│   ├── order_executor.py        # Order execution engine
│   ├── trading_orchestrator.py  # Main trading orchestrator
│   └── config.py                # Configuration management
├── tests/
│   ├── test_market_data.py
│   ├── test_technical_analysis.py
│   ├── test_risk_manager.py
│   ├── test_signal_generator.py
│   ├── test_broker_integration.py
│   └── test_integration.py
├── requirements.txt
├── .env.example
└── README.md
```

## Configuration

### Trading Parameters

- `TRADING_AMOUNT`: Total capital for trading (default: 5000)
- `MAX_POSITIONS`: Maximum concurrent positions (default: 3)
- `RISK_PER_TRADE`: Risk per trade as percentage (default: 2%)
- `STOP_LOSS_PERCENTAGE`: Stop loss percentage (default: 2%)
- `TARGET_PERCENTAGE`: Target profit percentage (default: 4%)

### Watchlist

Edit `config.py` to customize the stock watchlist:

```python
WATCHLIST: list = [
    "RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK",
    "SBIN", "BHARTIARTL", "ITC", "KOTAKBANK", "LT"
]
```

## Risk Management

The system includes multiple risk controls:

1. **Position Sizing**: Automatic position calculation based on capital
2. **Stop Loss**: Automatic exit at 2% loss (configurable)
3. **Target Profit**: Automatic exit at 4% profit (configurable)
4. **Daily Loss Limit**: Stops trading if daily loss exceeds 5% of capital
5. **Consecutive Loss Protection**: Stops after 3 consecutive losses
6. **Risk-Reward Ratio**: Only trades with ratio >= 1.5

## Testing

### Run all tests

```bash
pytest tests/ -v
```

### Run specific test file

```bash
pytest tests/test_market_data.py -v
```

### Run with coverage

```bash
pytest tests/ --cov=src --cov-report=html
```

## Paper Trading vs Live Trading

### Paper Trading (Default)
- No real money at risk
- Simulates order execution
- Uses virtual portfolio
- Recommended for testing and learning

### Live Trading
- Requires Zerodha Kite Connect credentials
- Real money at risk
- Actual order execution on NSE
- Set `PAPER_TRADING=False` in `.env`

## Zerodha Kite Connect Setup

1. Create account at https://kite.trade/
2. Generate API key and secret
3. Obtain request token via OAuth flow
4. Add credentials to `.env`

## Important Notes

- **Start with paper trading** to understand the system
- **Monitor positions** regularly, especially during market hours
- **Adjust risk parameters** based on your risk tolerance
- **Keep sufficient margin** for intraday trading
- **Market hours**: 9:15 AM to 3:30 PM IST
- **Square off positions** before 3:30 PM for intraday

## Disclaimer

This software is for educational purposes only. Trading in stocks involves significant risk of loss. The author is not responsible for any financial losses incurred through the use of this software. Always do your own research and consult with a financial advisor before trading.

## License

MIT License
