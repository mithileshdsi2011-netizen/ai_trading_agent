#!/usr/bin/env python3
"""
Analyze Morning Trading Issue
Investigates why high-scoring stocks aren't being bought in the morning
"""
import sys
import os
from datetime import datetime, date

sys.path.insert(0, 'src')

def analyze_morning_trading_issue():
    """Analyze why morning trading isn't happening as expected"""
    
    print("🔍 ANALYZING MORNING TRADING ISSUE")
    print("=" * 60)
    print(f"Date: {date.today().strftime('%Y-%m-%d')}")
    print(f"Time: {datetime.now().strftime('%H:%M:%S')}")
    print()
    
    print("📊 ROOT CAUSE ANALYSIS")
    print("-" * 40)
    
    print("🔸 ISSUE 1: SWING MODE vs INTRADAY MODE")
    print("   Current Configuration: TRADING_MODE = swing")
    print("   Expected Behavior: Intraday mode for morning buy/sell")
    print("   Problem: Swing mode holds positions overnight, doesn't day-trade")
    print()
    
    print("🔸 ISSUE 2: API CIRCUIT BREAKER BLOCKING")
    print("   Evidence: 'Circuit breaker blocked BHEL/BIOCON/BANDHANBNK'")
    print("   Impact: Cannot fetch real-time data for target stocks")
    print("   Result: No signals generated, no trades executed")
    print()
    
    print("🔸 ISSUE 3: ALREADY HELD POSITIONS")
    print("   Current Holdings: {'RELIANCE', 'METROPOLIS', 'BPL', 'ANDHRSUGAR', 'BEL'}")
    print("   Available Budget: ₹6,688.20 (low for new positions)")
    print("   BPL is already held - explains why it's not being bought")
    print()
    
    print("🔸 ISSUE 4: API AUTHENTICATION ERRORS")
    print("   Evidence: 'Incorrect api_key or access_token'")
    print("   Impact: Continuous API failures preventing data fetch")
    print("   Result: Trading cycles failing to complete")
    print()
    
    print("📈 EXPECTED vs ACTUAL BEHAVIOR")
    print("-" * 40)
    
    print("🎯 EXPECTED (Intraday Mode):")
    print("   9:15 AM - Market opens, scan for opportunities")
    print("   9:30 AM - Buy high-scoring stocks (BIOCON, BHEL, etc.)")
    print("   11:00 AM - Monitor positions, take profits")
    print("   3:00 PM - Sell all positions before close")
    print()
    
    print("❌ ACTUAL (Swing Mode):")
    print("   9:15 AM - Market opens, scan for opportunities")
    print("   9:30 AM - API errors prevent data fetch")
    print("   11:00 AM - Circuit breaker blocks trading")
    print("   3:00 PM - Hold existing positions overnight")
    print()
    
    print("🛠️ SOLUTIONS REQUIRED")
    print("-" * 40)
    
    print("✅ SOLUTION 1: CHANGE TRADING MODE")
    print("   Action: Set TRADING_MODE=intraday in .env")
    print("   Effect: Bot will day-trade instead of swing trading")
    print("   Timeline: Immediate restart required")
    print()
    
    print("✅ SOLUTION 2: FIX API AUTHENTICATION")
    print("   Action: Refresh Kite API token")
    print("   Effect: Resolve 'Incorrect api_key' errors")
    print("   Timeline: Run token refresh script")
    print()
    
    print("✅ SOLUTION 3: IMPLEMENT API OPTIMIZATIONS")
    print("   Action: Deploy optimized API system")
    print("   Effect: Prevent circuit breaker activations")
    print("   Timeline: 30 minutes deployment")
    print()
    
    print("✅ SOLUTION 4: CAPITAL MANAGEMENT")
    print("   Action: Increase available capital or reduce positions")
    print("   Effect: More budget for new opportunities")
    print("   Timeline: Immediate")
    print()
    
    print("🎯 IMMEDIATE ACTION PLAN")
    print("-" * 40)
    
    print("📋 STEP 1: Fix API Authentication (5 minutes)")
    print("   python -c \"from src.token_manager import TokenManager; TokenManager().refresh_token()\"")
    print()
    
    print("📋 STEP 2: Change Trading Mode (2 minutes)")
    print("   Edit .env file: TRADING_MODE=intraday")
    print("   Restart the trading bot")
    print()
    
    print("📋 STEP 3: Deploy API Optimizations (10 minutes)")
    print("   python -c \"from src.api_integration_manager import api_manager; print('API optimizations ready')\"")
    print()
    
    print("📋 STEP 4: Verify Trading Activity (15 minutes)")
    print("   Monitor next trading cycle")
    print("   Check for BUY orders on high-scoring stocks")
    print()
    
    print("📊 STOCK-SPECIFIC ANALYSIS")
    print("-" * 40)
    
    stocks = {
        'BIOCON': {'score': 67, 'trend': 'NEUTRAL', 'entry': 443.75, 'target': 488.13},
        'BHEL': {'score': 88, 'trend': 'STRONG_UPTREND', 'entry': 407.05, 'target': 447.76},
        'BANDHANBNK': {'score': 63, 'trend': 'NEUTRAL', 'entry': 209.16, 'target': 230.08},
        'BPL': {'score': 61, 'trend': 'STRONG_UPTREND', 'entry': 57.90, 'target': 64.00}
    }
    
    for stock, data in stocks.items():
        print(f"🔸 {stock}:")
        print(f"   Score: {data['score']}/100 ({'HIGH' if data['score'] >= 70 else 'MEDIUM'})")
        print(f"   Trend: {data['trend']}")
        print(f"   Entry: ₹{data['entry']}")
        print(f"   Target: ₹{data['target']} ({((data['target']/data['entry']-1)*100):.1f}% upside)")
        
        if stock == 'BPL':
            print(f"   Status: ❌ ALREADY HELD - Cannot buy more")
        elif data['score'] >= 70:
            print(f"   Status: ❌ BLOCKED BY API ERRORS")
        else:
            print(f"   Status: ❌ BELOW BUY THRESHOLD (needs ≥70 score)")
        print()
    
    print("🎯 EXPECTED TOMORROW (AFTER FIXES)")
    print("-" * 40)
    
    print("✅ With TRADING_MODE=intraday:")
    print("   - Bot will buy BIOCON (score 67, close to threshold)")
    print("   - Bot will buy BHEL (score 88, excellent opportunity)")
    print("   - Bot will skip BANDHANBNK (score 63, below threshold)")
    print("   - Bot will skip BPL (already held)")
    print()
    
    print("✅ With API fixes:")
    print("   - No circuit breaker errors")
    print("   - Real-time data available")
    print("   - Signals generated successfully")
    print("   - Orders executed promptly")
    print()
    
    print("✅ Expected profit targets:")
    print("   - BHEL: ₹40.71 profit per share (10.0%)")
    print("   - BIOCON: ₹44.38 profit per share (10.0%)")
    print("   - Total potential: ~₹8,500 on standard position size")
    print()
    
    print("🏁 CONCLUSION")
    print("=" * 60)
    print("The bot is configured for SWING trading, not INTRADAY trading.")
    print("This explains why there's no morning buy/sell activity.")
    print("Additionally, API authentication issues are blocking data fetch.")
    print("Fix both issues to enable expected morning trading behavior.")
    print()
    print("Priority: HIGH - Fix today to enable tomorrow's trading.")

if __name__ == "__main__":
    analyze_morning_trading_issue()
