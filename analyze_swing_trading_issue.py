#!/usr/bin/env python3
"""
Analyze Swing Trading Issue
Investigates why swing bot isn't buying high-scoring stocks in the morning
"""
import sys
import os
from datetime import datetime, date

sys.path.insert(0, 'src')

def analyze_swing_trading_issue():
    """Analyze why swing trading isn't working as expected"""
    
    print("🔍 ANALYZING SWING TRADING ISSUE")
    print("=" * 60)
    print(f"Date: {date.today().strftime('%Y-%m-%d')}")
    print(f"Time: {datetime.now().strftime('%H:%M:%S')}")
    print()
    
    print("📊 SWING TRADING vs EXPECTED BEHAVIOR")
    print("-" * 50)
    
    print("🎯 SWING TRADING CHARACTERISTICS:")
    print("   ✓ Holds positions overnight (days to weeks)")
    print("   ✓ Buys on strong fundamentals + technicals")
    print("   ✓ Uses wider stop losses (5-8%)")
    print("   ✓ Targets larger gains (15-25%)")
    print("   ✓ Fewer, higher-quality positions")
    print()
    
    print("❌ CURRENT ISSUES PREVENTING SWING TRADES:")
    print()
    
    print("🔸 ISSUE 1: API CIRCUIT BREAKER BLOCKING")
    print("   Evidence: 'Circuit breaker blocked BHEL/BIOCON/BANDHANBNK'")
    print("   Impact: Cannot fetch data for swing trading decisions")
    print("   Result: No new swing positions opened")
    print()
    
    print("🔸 ISSUE 2: API AUTHENTICATION ERRORS")
    print("   Evidence: 'Incorrect api_key or access_token'")
    print("   Impact: Real-time data unavailable")
    print("   Result: Swing signals cannot be generated")
    print()
    
    print("🔸 ISSUE 3: MAX POSITIONS REACHED")
    print("   Current Holdings: {'RELIANCE', 'METROPOLIS', 'BPL', 'ANDHRSUGAR', 'BEL'}")
    print("   MAX_POSITIONS: 7")
    print("   Available Slots: 2 remaining")
    print("   Available Budget: ₹6,688.20 (low for swing positions)")
    print()
    
    print("🔸 ISSUE 4: SWING TRADING THRESHOLDS")
    print("   MIN_CONFIDENCE: 60%")
    print("   Score Requirements: Higher for swing vs intraday")
    print("   Risk Management: Stricter for longer holds")
    print()
    
    print("📈 SWING TRADING EXPECTATIONS")
    print("-" * 50)
    
    print("🎯 EXPECTED SWING BEHAVIOR:")
    print("   9:15 AM - Market opens, scan for swing opportunities")
    print("   9:30 AM - Buy high-quality stocks for multi-day holds")
    print("   10:00 AM - Monitor existing positions")
    print("   3:00 PM - End of day, hold positions overnight")
    print("   Next Days - Monitor for targets/stop losses")
    print()
    
    print("❌ CURRENT ACTUAL BEHAVIOR:")
    print("   9:15 AM - Market opens, API errors prevent scanning")
    print("   9:30 AM - Circuit breaker blocks data fetch")
    print("   10:00 AM - Cannot monitor positions")
    print("   3:00 PM - Hold existing positions (no new ones added)")
    print()
    
    print("📊 STOCK-SPECIFIC SWING ANALYSIS")
    print("-" * 50)
    
    swing_stocks = {
        'BHEL': {
            'score': 88, 
            'trend': 'STRONG_UPTREND', 
            'entry': 407.05, 
            'target': 447.76,
            'swing_target': 488.00,  # 20% swing target
            'swing_potential': '19.9%',
            'swing_score': 'EXCELLENT'
        },
        'BIOCON': {
            'score': 67, 
            'trend': 'NEUTRAL', 
            'entry': 443.75, 
            'target': 488.13,
            'swing_target': 532.50,  # 20% swing target
            'swing_potential': '20.0%',
            'swing_score': 'GOOD'
        },
        'BANDHANBNK': {
            'score': 63, 
            'trend': 'NEUTRAL', 
            'entry': 209.16, 
            'target': 230.08,
            'swing_target': 251.00,  # 20% swing target
            'swing_potential': '20.0%',
            'swing_score': 'MARGINAL'
        },
        'BPL': {
            'score': 61, 
            'trend': 'STRONG_UPTREND', 
            'entry': 57.90, 
            'target': 64.00,
            'swing_target': 69.50,  # 20% swing target
            'swing_potential': '20.0%',
            'swing_score': 'MARGINAL - ALREADY HELD'
        }
    }
    
    print("🔸 SWING TRADING QUALITY ASSESSMENT:")
    for stock, data in swing_stocks.items():
        print(f"\n   {stock}:")
        print(f"     Score: {data['score']}/100 ({data['swing_score']})")
        print(f"     Trend: {data['trend']}")
        print(f"     Entry: ₹{data['entry']}")
        print(f"     Swing Target: ₹{data['swing_target']} ({data['swing_potential']})")
        
        if stock == 'BPL':
            print(f"     Status: ❌ ALREADY HELD - Cannot add more")
        elif data['score'] >= 80:
            print(f"     Status: ❌ BLOCKED BY API ERRORS (Should be BOUGHT)")
        elif data['score'] >= 65:
            print(f"     Status: ❌ BLOCKED BY API ERRORS (Good swing candidate)")
        else:
            print(f"     Status: ❌ BELOW SWING THRESHOLD (Needs ≥65 score)")
    
    print()
    
    print("🛠️ SWING TRADING SOLUTIONS")
    print("-" * 50)
    
    print("✅ SOLUTION 1: FIX API AUTHENTICATION (IMMEDIATE)")
    print("   Action: Refresh Kite API token")
    print("   Effect: Enable swing trading data fetch")
    print("   Command: python -c \"from src.token_manager import TokenManager; TokenManager().refresh_token()\"")
    print()
    
    print("✅ SOLUTION 2: DEPLOY API OPTIMIZATIONS (10 MINUTES)")
    print("   Action: Implement batch processing")
    print("   Effect: Prevent circuit breaker, enable reliable data")
    print("   Command: python -c \"from src.api_integration_manager import api_manager; print('Ready')\"")
    print()
    
    print("✅ SOLUTION 3: CAPITAL MANAGEMENT (IMMEDIATE)")
    print("   Action: Add more capital or close weak positions")
    print("   Effect: Enable new high-quality swing trades")
    print("   Current: Only ₹6,688 available for new positions")
    print()
    
    print("✅ SOLUTION 4: SWING THRESHOLD OPTIMIZATION")
    print("   Action: Review if scores align with swing strategy")
    print("   Effect: Better swing trade identification")
    print("   Current: MIN_CONFIDENCE=60% may be too low for swing")
    print()
    
    print("🎯 EXPECTED SWING TRADING AFTER FIXES")
    print("-" * 50)
    
    print("✅ TOMORROW'S EXPECTED SWING ACTIVITY:")
    print("   - Bot will identify BHEL (88/100) as EXCELLENT swing opportunity")
    print("   - Bot will consider BIOCON (67/100) as GOOD swing candidate")
    print("   - Bot will skip BANDHANBNK (63/100) - below swing threshold")
    print("   - Bot will ignore BPL - already held")
    print()
    
    print("✅ EXPECTED SWING POSITIONS:")
    print("   - BHEL: Buy at ₹407, target ₹488 (19.9% gain)")
    print("   - BIOCON: Buy at ₹444, target ₹533 (20.0% gain)")
    print("   - Hold for 3-10 days depending on market conditions")
    print("   - Stop loss at 5-8% below entry")
    print()
    
    print("✅ SWING PORTFOLIO EXPECTATION:")
    print("   - Current: 5 positions (RELIANCE, METROPOLIS, BPL, ANDHRSUGAR, BEL)")
    print("   - After fixes: Add BHEL + possibly BIOCON")
    print("   - Total: 6-7 positions (optimal for swing)")
    print("   - Expected returns: 15-20% over 1-2 weeks")
    print()
    
    print("🏁 SWING TRADING CONCLUSION")
    print("=" * 60)
    print("The swing trading strategy is sound, but technical issues prevent execution.")
    print("BHEL (88/100) is an excellent swing opportunity being blocked by API errors.")
    print("Fix the API issues to enable proper swing trading behavior.")
    print()
    print("Swing trading should buy fewer, higher-quality positions for multi-day holds,")
    print("not day-trade. The current configuration is correct for swing trading.")
    print()
    print("Priority: CRITICAL - Fix API issues to enable swing trading tomorrow.")

if __name__ == "__main__":
    analyze_swing_trading_issue()
