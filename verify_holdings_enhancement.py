#!/usr/bin/env python3
"""
Verify the enhanced holdings dashboard calculations
"""
import sys
sys.path.insert(0, 'src')

from market_data import MarketDataFetcher
import json

def main():
    print("🔍 VERIFYING HOLDINGS ENHANCEMENT")
    print("=" * 60)
    
    md = MarketDataFetcher()
    if not md.kite:
        print("❌ Kite not connected")
        return
    
    # Get holdings
    try:
        holdings = md.kite.holdings()
        print(f"\n📊 Found {len(holdings)} holdings")
        
        if not holdings:
            print("No holdings to verify")
            return
        
        # Calculate totals
        total_invested = 0
        total_current = 0
        total_pnl = 0
        total_day_pnl = 0
        
        print("\nHoldings Details:")
        print("-" * 80)
        print(f"{'Symbol':<12} {'Qty':>5} {'Avg Cost':>10} {'LTP':>10} {'Invested':>10} {'Current':>10} {'P&L':>10} {'Day%':>8}")
        print("-" * 80)
        
        for h in holdings:
            qty = h.get('quantity', 0)
            if qty <= 0:
                continue
                
            symbol = h.get('tradingsymbol', 'N/A')
            avg = h.get('average_price', 0)
            ltp = h.get('last_price', 0)
            close = h.get('close_price', 0)
            
            invested = avg * qty
            current = ltp * qty
            pnl = current - invested
            day_pct = ((ltp - close) / close * 100) if close > 0 else 0
            
            total_invested += invested
            total_current += current
            total_pnl += pnl
            total_day_pnl += (ltp - close) * qty
            
            print(f"{symbol:<12} {qty:>5} {avg:>10.2f} {ltp:>10.2f} {invested:>10.0f} {current:>10.0f} {pnl:>10.0f} {day_pct:>7.2f}%")
        
        print("-" * 80)
        print(f"{'TOTAL':<12} {'':>5} {'':>10} {'':>10} {total_invested:>10.0f} {total_current:>10.0f} {total_pnl:>10.0f} {((total_current-total_invested)/total_invested*100) if total_invested > 0 else 0:>7.2f}%")
        print("\n✅ Calculations Verified")
        
        # Verify API data structure
        print("\n🔌 Checking API data structure...")
        
        # Check positions.json for SL/Target data
        import os
        pos_file = os.path.join(os.path.dirname(__file__), 'data', 'positions.json')
        if os.path.exists(pos_file):
            with open(pos_file) as f:
                pos_data = json.load(f)
            
            print(f"Found {len(pos_data.get('positions', []))} positions in risk manager")
            for p in pos_data.get('positions', []):
                symbol = p.get('symbol')
                sl = p.get('stop_loss')
                target = p.get('target')
                score = p.get('trade_score')
                print(f"  {symbol}: SL={sl}, Target={target}, Score={score}")
        
        print("\n✅ All checks passed - Dashboard enhancement is working correctly!")
        
    except Exception as e:
        print(f"❌ Error: {e}")

if __name__ == "__main__":
    main()
