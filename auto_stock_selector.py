"""
Automated Profitable Stock Selector
Analyzes stocks and selects the best trading opportunities within ₹5000 capital
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
from risk_manager import RiskManager

def analyze_stock_opportunity(symbol, kite, capital_available):
    """Analyze a stock for trading opportunity using quote API"""
    try:
        # Get instrument token
        instruments = kite.instruments("NSE")
        instrument = None
        for inst in instruments:
            if inst['tradingsymbol'] == symbol:
                instrument = inst
                break
        
        if not instrument:
            return None
        
        # Get live quote data
        quote = kite.quote([instrument['instrument_token']])
        
        if not quote or str(instrument['instrument_token']) not in quote:
            return None
        
        quote_data = quote[str(instrument['instrument_token'])]
        
        # Extract price data
        current_price = quote_data['last_price']
        day_open = quote_data['ohlc']['open']
        day_high = quote_data['ohlc']['high']
        day_low = quote_data['ohlc']['low']
        volume = quote_data['volume']
        change = quote_data['net_change']
        change_percent = quote_data['ohlc']['change']
        
        # Calculate trading score based on live data
        score = 0
        reasons = []
        
        # Price action analysis
        if current_price > day_open:
            score += 2
            reasons.append("Price above day open")
        else:
            score -= 2
            reasons.append("Price below day open")
        
        # Volume analysis
        if volume > 1000000:
            score += 1
            reasons.append("High volume")
        else:
            score -= 1
            reasons.append("Low volume")
        
        # Change analysis
        if change_percent > 2:
            score += 3
            reasons.append(f"Strong up (+{change_percent:.2f}%)")
        elif change_percent > 0:
            score += 1
            reasons.append(f"Positive (+{change_percent:.2f}%)")
        elif change_percent < -2:
            score -= 3
            reasons.append(f"Strong down ({change_percent:.2f}%)")
        elif change_percent < 0:
            score -= 1
            reasons.append(f"Negative ({change_percent:.2f}%)")
        
        # Calculate position sizing
        risk_manager = RiskManager()
        risk_per_trade = config.RISK_PER_TRADE * capital_available
        stop_loss_price = current_price * 0.98  # 2% stop-loss
        position_size = risk_manager.calculate_position_size(
            capital_available,
            config.RISK_PER_TRADE,
            config.STOP_LOSS_PERCENTAGE
        )
        
        # Calculate quantity
        quantity = int(position_size / current_price)
        
        # Check if within capital
        required_capital = quantity * current_price
        if required_capital > capital_available:
            quantity = int(capital_available / current_price)
            required_capital = quantity * current_price
            
        if quantity < 1:
            return None
        
        # Calculate potential returns
        target_price = current_price * 1.04  # 4% target
        potential_profit = (target_price - current_price) * quantity
        potential_loss = (current_price - stop_loss_price) * quantity
        
        # Generate recommendation
        if score >= 4:
            action = "STRONG BUY"
        elif score >= 2:
            action = "BUY"
        elif score <= -4:
            action = "STRONG SELL"
        elif score <= -2:
            action = "SELL"
        else:
            action = "HOLD"
        
        return {
            'symbol': symbol,
            'current_price': current_price,
            'action': action,
            'score': score,
            'reasons': reasons,
            'quantity': quantity,
            'required_capital': required_capital,
            'entry_price': current_price,
            'stop_loss': stop_loss_price,
            'target': target_price,
            'potential_profit': potential_profit,
            'potential_loss': potential_loss,
            'risk_reward': potential_profit / potential_loss if potential_loss > 0 else 0,
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
        print(f"Error analyzing {symbol}: {e}")
        return None

def main():
    """Main function to select profitable stocks"""
    print(f"\n{'='*70}")
    print(f"AUTOMATED PROFITABLE STOCK SELECTOR")
    print(f"{'='*70}")
    print(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Capital Available: ₹{config.TRADING_AMOUNT}")
    print(f"Risk Per Trade: {config.RISK_PER_TRADE * 100}% (₹{config.RISK_PER_TRADE * config.TRADING_AMOUNT})")
    print(f"Stop Loss: {config.STOP_LOSS_PERCENTAGE * 100}%")
    print(f"Target: {config.TARGET_PERCENTAGE * 100}%")
    print(f"{'='*70}\n")
    
    # Initialize Kite Connect
    try:
        token_manager = TokenManager()
        kite = token_manager.initialize_kite()
        print(f"✅ Kite Connect initialized\n")
        
        # Get available margin (optional - may not have permission)
        try:
            margins = kite.margins()
            available_margin = margins['equity']['available']['live_margin']
            print(f"Available Margin in Kite: ₹{available_margin}\n")
        except Exception as margin_error:
            print(f"⚠️  Could not fetch margin data (permission issue): {margin_error}")
            print(f"Using configured trading amount: ₹{config.TRADING_AMOUNT}\n")
            available_margin = config.TRADING_AMOUNT
        
    except Exception as e:
        print(f"❌ Failed to initialize Kite Connect: {e}")
        print(f"Please run: python get_kite_token.py\n")
        return
    
    # Watchlist
    watchlist = [
        "BAJFINANCE", "JSWSTEEL", "ADANIENT", "ADANIPORTS", "SUNPHARMA",
        "DIVISLAB", "NESTLEIND", "BHARTIARTL", "BAJAJ-AUTO", "INDUSINDBANK"
    ]
    
    print(f"Analyzing {len(watchlist)} stocks...\n")
    
    opportunities = []
    
    for symbol in watchlist:
        print(f"Analyzing {symbol}...", end=" ")
        opportunity = analyze_stock_opportunity(symbol, kite, config.TRADING_AMOUNT)
        
        if opportunity and opportunity['action'] in ['BUY', 'STRONG BUY']:
            opportunities.append(opportunity)
            print(f"✅ {opportunity['action']} (Score: {opportunity['score']})")
        elif opportunity:
            print(f"⏭️  {opportunity['action']} (Score: {opportunity['score']})")
        else:
            print(f"❌ No opportunity")
        
        time.sleep(0.5)
    
    # Sort by score
    opportunities.sort(key=lambda x: x['score'], reverse=True)
    
    print(f"\n{'='*70}")
    print(f"TOP TRADING OPPORTUNITIES")
    print(f"{'='*70}\n")
    
    if not opportunities:
        print("No BUY opportunities found in current market conditions.")
        print("Consider waiting for better setup or adjusting watchlist.")
        return
    
    # Display top opportunities
    for i, opp in enumerate(opportunities[:5], 1):
        print(f"{i}. {opp['symbol']}")
        print(f"   Action: {opp['action']}")
        print(f"   Score: {opp['score']}/10")
        print(f"   Current Price: ₹{opp['current_price']:.2f}")
        print(f"   Quantity: {opp['quantity']} shares")
        print(f"   Required Capital: ₹{opp['required_capital']:.2f}")
        print(f"   Entry: ₹{opp['entry_price']:.2f}")
        print(f"   Stop Loss: ₹{opp['stop_loss']:.2f} (₹{opp['potential_loss']:.2f} loss)")
        print(f"   Target: ₹{opp['target']:.2f} (₹{opp['potential_profit']:.2f} profit)")
        print(f"   Risk-Reward: 1:{opp['risk_reward']:.1f}")
        print(f"   Reasons: {', '.join(opp['reasons'])}")
        print(f"   Day Change: ₹{opp['quote_data']['change']:.2f} ({opp['quote_data']['change_percent']:.2f}%)")
        print(f"   Volume: {opp['quote_data']['volume']:,}")
        print()
    
    # Best opportunity
    if opportunities:
        best = opportunities[0]
        print(f"{'='*70}")
        print(f"RECOMMENDED TRADE")
        print(f"{'='*70}")
        print(f"Stock: {best['symbol']}")
        print(f"Action: {best['action']}")
        print(f"Buy: {best['quantity']} shares @ ₹{best['entry_price']:.2f}")
        print(f"Total Investment: ₹{best['required_capital']:.2f}")
        print(f"Stop Loss: ₹{best['stop_loss']:.2f} (Risk: ₹{best['potential_loss']:.2f})")
        print(f"Target: ₹{best['target']:.2f} (Reward: ₹{best['potential_profit']:.2f})")
        print(f"Risk-Reward Ratio: 1:{best['risk_reward']:.1f}")
        print(f"{'='*70}\n")
        
        print(f"To execute this trade automatically, run:")
        print(f"python run.py scheduled 15")
        print(f"\nThe system will automatically execute the best opportunities during market hours.")
    
    print(f"\n⚠️  DISCLAIMER: This is for educational purposes only.")
    print(f"   Trading involves risk. Always do your own research.")
    print(f"{'='*70}\n")

if __name__ == "__main__":
    main()
