"""
Kite Connect Real-Time Stock Analysis
Analyzes stocks using Kite Connect for live market data
"""
import sys
import os
import time
from datetime import datetime

# Add src directory to path
src_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'src')
sys.path.insert(0, src_dir)
os.chdir(src_dir)

from config import config
from token_manager import TokenManager
from technical_analysis import TechnicalAnalyzer

def analyze_with_kite(symbol, kite):
    """Analyze stock using Kite Connect quote API (live prices)"""
    print(f"\n{'='*60}")
    print(f"Analyzing: {symbol}")
    print(f"{'='*60}")
    
    try:
        # Get instrument token for the symbol
        instruments = kite.instruments("NSE")
        instrument = None
        
        for inst in instruments:
            if inst['tradingsymbol'] == symbol:
                instrument = inst
                break
        
        if not instrument:
            print(f"❌ Instrument not found for {symbol}")
            return None
        
        print(f"✓ Instrument found: {instrument['tradingsymbol']}")
        print(f"  Token: {instrument['instrument_token']}")
        print(f"  Exchange: {instrument['exchange']}")
        
        # Get live quote data
        quote = kite.quote([instrument['instrument_token']])
        
        if not quote or str(instrument['instrument_token']) not in quote:
            print(f"❌ No quote data for {symbol}")
            return None
        
        quote_data = quote[str(instrument['instrument_token'])]
        
        # Extract price data
        current_price = quote_data['last_price']
        day_open = quote_data['ohlc']['open']
        day_high = quote_data['ohlc']['high']
        day_low = quote_data['ohlc']['low']
        day_close = quote_data['ohlc']['close']
        volume = quote_data['volume']
        change = quote_data['net_change']
        change_percent = quote_data['ohlc']['change']
        
        print(f"✓ Quote data fetched")
        print(f"  Current Price: ₹{current_price:.2f}")
        print(f"  Day Open: ₹{day_open:.2f}")
        print(f"  Day High: ₹{day_high:.2f}")
        print(f"  Day Low: ₹{day_low:.2f}")
        print(f"  Day Close: ₹{day_close:.2f}")
        print(f"  Volume: {volume:,}")
        print(f"  Change: ₹{change:.2f} ({change_percent:.2f}%)")
        
        # Simple technical analysis based on live data
        print(f"\n📊 Live Technical Analysis:")
        
        # Price action analysis
        if current_price > day_open:
            print(f"  ✅ Bullish: Price above day open")
            price_action_score = 2
        else:
            print(f"  ❌ Bearish: Price below day open")
            price_action_score = -2
        
        # Volume analysis
        if volume > 1000000:
            print(f"  ✅ High Volume: {volume:,}")
            volume_score = 1
        else:
            print(f"  ⚠️  Low Volume: {volume:,}")
            volume_score = -1
        
        # Change analysis
        if change_percent > 2:
            print(f"  ✅ Strong Up: +{change_percent:.2f}%")
            change_score = 3
        elif change_percent > 0:
            print(f"  ✅ Positive: +{change_percent:.2f}%")
            change_score = 1
        elif change_percent < -2:
            print(f"  ❌ Strong Down: {change_percent:.2f}%")
            change_score = -3
        elif change_percent < 0:
            print(f"  ❌ Negative: {change_percent:.2f}%")
            change_score = -1
        else:
            print(f"  ⚠️  Flat: {change_percent:.2f}%")
            change_score = 0
        
        # Calculate overall score
        total_score = price_action_score + volume_score + change_score
        
        # Generate recommendation
        if total_score >= 4:
            action = "STRONG BUY"
        elif total_score >= 2:
            action = "BUY"
        elif total_score <= -4:
            action = "STRONG SELL"
        elif total_score <= -2:
            action = "SELL"
        else:
            action = "HOLD"
        
        print(f"\n🎯 Trading Recommendation:")
        print(f"  Action: {action}")
        print(f"  Score: {total_score}")
        
        # Calculate entry, stop-loss, target
        if action in ["BUY", "STRONG BUY"]:
            stop_loss = current_price * 0.98  # 2% stop-loss
            target = current_price * 1.04  # 4% target
            print(f"  Entry Price: ₹{current_price:.2f}")
            print(f"  Stop Loss: ₹{stop_loss:.2f} (2%)")
            print(f"  Target: ₹{target:.2f} (4%)")
            print(f"  Risk-Reward: 1:2")
        elif action in ["SELL", "STRONG SELL"]:
            stop_loss = current_price * 1.02  # 2% stop-loss for short
            target = current_price * 0.96  # 4% target for short
            print(f"  Entry Price: ₹{current_price:.2f}")
            print(f"  Stop Loss: ₹{stop_loss:.2f} (2%)")
            print(f"  Target: ₹{target:.2f} (4%)")
            print(f"  Risk-Reward: 1:2")
        
        return {
            'symbol': symbol,
            'current_price': current_price,
            'recommendation': {
                'action': action,
                'score': total_score,
                'entry_price': current_price,
                'stop_loss': current_price * 0.98 if action in ["BUY", "STRONG BUY"] else current_price * 1.02,
                'target': current_price * 1.04 if action in ["BUY", "STRONG BUY"] else current_price * 0.96
            },
            'quote_data': {
                'day_open': day_open,
                'day_high': day_high,
                'day_low': day_low,
                'volume': volume,
                'change': change,
                'change_percent': change_percent
            }
        }
        
    except Exception as e:
        print(f"❌ Error analyzing {symbol}: {e}")
        import traceback
        traceback.print_exc()
        return None


def main():
    """Main analysis function"""
    print(f"\n{'='*60}")
    print(f"KITE CONNECT REAL-TIME STOCK ANALYSIS")
    print(f"{'='*60}")
    print(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Market Hours: {config.MARKET_OPEN} - {config.MARKET_CLOSE} IST")
    
    # Check if live trading
    if config.PAPER_TRADING:
        print(f"⚠️  Running in PAPER TRADING mode")
        print(f"   To enable live trading, set PAPER_TRADING=False in .env")
    else:
        print(f"✅ Running in LIVE TRADING mode")
    
    # Initialize Kite Connect
    try:
        token_manager = TokenManager()
        kite = token_manager.initialize_kite()
        print(f"✅ Kite Connect initialized successfully")
        
        # Get user profile
        profile = kite.profile()
        print(f"✅ Connected as: {profile['user_name']} ({profile['user_id']})")
        
    except Exception as e:
        print(f"❌ Failed to initialize Kite Connect: {e}")
        print(f"   Please run: python get_kite_token.py")
        return
    
    # User's watchlist
    watchlist = [
        "BAJFINANCE", "JSWSTEEL", "ADANIENT", "ADANIPORTS", "SUNPHARMA",
        "DIVISLAB", "NESTLEIND", "BHARTIARTL", "BAJAJ-AUTO", "INDUSINDBANK"
    ]
    
    print(f"\n📋 Watchlist: {', '.join(watchlist)}")
    print(f"\nStarting analysis...\n")
    
    results = []
    
    for symbol in watchlist:
        result = analyze_with_kite(symbol, kite)
        if result:
            results.append(result)
        
        # Add delay to avoid rate limiting
        time.sleep(1)
    
    # Summary
    print(f"\n{'='*60}")
    print(f"ANALYSIS SUMMARY")
    print(f"{'='*60}")
    print(f"Stocks Analyzed: {len(watchlist)}")
    print(f"Successful Analysis: {len(results)}")
    
    if results:
        print(f"\n🎯 TOP RECOMMENDATIONS:")
        
        # Sort by score
        results.sort(key=lambda x: x['recommendation']['score'], reverse=True)
        
        for i, result in enumerate(results[:5], 1):
            rec = result['recommendation']
            quote = result['quote_data']
            print(f"\n{i}. {result['symbol']}")
            print(f"   Action: {rec['action']}")
            print(f"   Score: {rec['score']}")
            print(f"   Current Price: ₹{result['current_price']:.2f}")
            print(f"   Day Change: ₹{quote['change']:.2f} ({quote['change_percent']:.2f}%)")
            print(f"   Volume: {quote['volume']:,}")
            print(f"   Entry: ₹{rec['entry_price']:.2f}")
            print(f"   Target: ₹{rec['target']:.2f}")
            print(f"   Stop Loss: ₹{rec['stop_loss']:.2f}")
    
    print(f"\n{'='*60}")
    print(f"\n⚠️  DISCLAIMER: This is for educational purposes only.")
    print(f"   Trading involves risk. Always do your own research.")
    print(f"{'='*60}\n")

if __name__ == "__main__":
    main()
