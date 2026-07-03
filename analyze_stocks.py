"""
Real-time Stock Analysis Script
Analyzes stocks for breakout and pullback patterns using Kite Connect
"""
import sys
import os
import time
from datetime import datetime, timedelta

# Add src directory to path
src_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'src')
sys.path.insert(0, src_dir)
os.chdir(src_dir)

from config import config
from token_manager import TokenManager
from market_data import MarketDataFetcher
from technical_analysis import TechnicalAnalyzer
from signal_generator import SignalGenerator

def analyze_stock(symbol, kite=None):
    """Analyze a single stock for breakout/pullback patterns"""
    print(f"\n{'='*60}")
    print(f"Analyzing: {symbol}")
    print(f"{'='*60}")
    
    try:
        # Fetch market data
        fetcher = MarketDataFetcher()
        data = fetcher.get_stock_data(symbol, period="1mo", interval="1d")
        
        if data is None or data.empty:
            print(f"❌ No data available for {symbol}")
            return None
        
        print(f"✓ Data fetched: {len(data)} candles")
        print(f"  Current Price: ₹{data['Close'].iloc[-1]:.2f}")
        print(f"  Volume: {data['Volume'].iloc[-1]:,}")
        
        # Calculate technical indicators
        analyzer = TechnicalAnalyzer()
        indicators = analyzer.calculate_indicators(data)
        
        # Get latest values
        latest = indicators.iloc[-1]
        
        print(f"\n📊 Technical Indicators:")
        print(f"  RSI (14): {latest.get('RSI', 0):.2f}")
        print(f"  MACD: {latest.get('MACD', 0):.2f}")
        print(f"  Signal: {latest.get('MACD_Signal', 0):.2f}")
        print(f"  ADX: {latest.get('ADX', 0):.2f}")
        print(f"  SMA 20: ₹{latest.get('SMA_20', 0):.2f}")
        print(f"  EMA 20: ₹{latest.get('EMA_20', 0):.2f}")
        
        # Generate trading signal
        generator = SignalGenerator()
        signal = generator.generate_signal(symbol)
        
        if signal:
            print(f"\n🎯 Trading Signal:")
            print(f"  Action: {signal.get('action', 'N/A')}")
            print(f"  Confidence: {signal.get('confidence', 0) * 100:.1f}%")
            print(f"  Entry Price: ₹{signal.get('entry_price', 0):.2f}")
            print(f"  Stop Loss: ₹{signal.get('stop_loss', 0):.2f}")
            print(f"  Target: ₹{signal.get('target', 0):.2f}")
            print(f"  Risk-Reward: {signal.get('risk_reward', 0):.2f}")
            
            # Analyze pattern
            analyze_pattern(data, indicators, signal)
            
            return signal
        else:
            print(f"\n⚠️  No signal generated")
            return None
            
    except Exception as e:
        print(f"❌ Error analyzing {symbol}: {e}")
        return None

def analyze_pattern(data, indicators, signal):
    """Analyze breakout and pullback patterns"""
    latest = indicators.iloc[-1]
    prev = indicators.iloc[-2]
    
    print(f"\n🔍 Pattern Analysis:")
    
    # Breakout detection
    if latest['Close'] > latest['SMA_20'] and prev['Close'] <= prev['SMA_20']:
        print(f"  ✅ BREAKOUT: Price crossed above SMA 20")
    elif latest['Close'] < latest['SMA_20'] and prev['Close'] >= prev['SMA_20']:
        print(f"  ❌ BREAKDOWN: Price crossed below SMA 20")
    
    # Pullback detection
    if latest['Close'] < latest['EMA_20'] and latest['RSI'] < 40:
        print(f"  ✅ PULLBACK: Price below EMA 20 with oversold RSI")
    elif latest['Close'] > latest['EMA_20'] and latest['RSI'] > 60:
        print(f"  ⚠️  EXTENSION: Price above EMA 20 with overbought RSI")
    
    # Momentum
    if latest['MACD'] > latest['MACD_Signal'] and prev['MACD'] <= prev['MACD_Signal']:
        print(f"  ✅ BULLISH CROSS: MACD crossed above signal")
    elif latest['MACD'] < latest['MACD_Signal'] and prev['MACD'] >= prev['MACD_Signal']:
        print(f"  ❌ BEARISH CROSS: MACD crossed below signal")
    
    # Trend strength
    if latest['ADX'] > 25:
        print(f"  ✅ STRONG TREND: ADX > 25 ({latest['ADX']:.2f})")
    elif latest['ADX'] > 20:
        print(f"  ⚠️  MODERATE TREND: ADX > 20 ({latest['ADX']:.2f})")
    else:
        print(f"  ❌ WEAK TREND: ADX < 20 ({latest['ADX']:.2f})")

def main():
    """Main analysis function"""
    print(f"\n{'='*60}")
    print(f"REAL-TIME STOCK ANALYSIS")
    print(f"{'='*60}")
    print(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Market Hours: {config.MARKET_OPEN} - {config.MARKET_CLOSE} IST")
    print(f"Paper Trading: {config.PAPER_TRADING}")
    
    # User's watchlist
    watchlist = [
        "BAJFINANCE", "JSWSTEEL", "ADANIENT", "ADANIPORTS", "SUNPHARMA",
        "DIVISLAB", "NESTLEIND", "BHARTIARTL", "BAJAJ-AUTO", "INDUSINDBANK"
    ]
    
    print(f"\n📋 Watchlist: {', '.join(watchlist)}")
    print(f"\nStarting analysis...\n")
    
    results = []
    
    for symbol in watchlist:
        signal = analyze_stock(symbol)
        if signal:
            results.append(signal)
        
        # Add delay to avoid rate limiting
        time.sleep(2)
    
    # Summary
    print(f"\n{'='*60}")
    print(f"ANALYSIS SUMMARY")
    print(f"{'='*60}")
    print(f"Stocks Analyzed: {len(watchlist)}")
    print(f"Signals Generated: {len(results)}")
    
    if results:
        print(f"\n🎯 TOP SIGNALS:")
        # Sort by confidence
        results.sort(key=lambda x: x.get('confidence', 0), reverse=True)
        
        for i, signal in enumerate(results[:3], 1):
            print(f"\n{i}. {signal['symbol']}")
            print(f"   Action: {signal['action']}")
            print(f"   Confidence: {signal['confidence'] * 100:.1f}%")
            print(f"   Entry: ₹{signal['entry_price']:.2f}")
            print(f"   Target: ₹{signal['target']:.2f}")
            print(f"   Stop Loss: ₹{signal['stop_loss']:.2f}")
    
    print(f"\n{'='*60}")

if __name__ == "__main__":
    main()
