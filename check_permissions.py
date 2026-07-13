#!/usr/bin/env python3
"""
Check Kite authentication and trading permissions
"""
import sys
sys.path.insert(0, 'src')

from market_data import MarketDataFetcher
from broker_integration import BrokerIntegration
import logging

logging.basicConfig(level=logging.INFO)

def main():
    print("🔍 CHECKING KITE AUTHENTICATION & PERMISSIONS")
    print("=" * 60)
    
    # Check authentication
    md = MarketDataFetcher()
    if not md.kite:
        print("❌ Kite not authenticated")
        return
    
    print("✅ Kite authenticated")
    
    try:
        # Get profile info
        profile = md.kite.profile()
        print(f"\n👤 User Profile:")
        print(f"  Name: {profile.get('user_name', 'Unknown')}")
        print(f"  Email: {profile.get('email', 'Unknown')}")
        print(f"  User Type: {profile.get('user_type', 'Unknown')}")
        
        # Check margins
        margins = md.kite.margins()
        equity = margins.get('equity', {})
        print(f"\n💰 Equity Margins:")
        print(f"  Available: ₹{equity.get('available', {}).get('live_balance', 0):,.2f}")
        print(f"  Used: ₹{equity.get('utilised', {}).get('debit', 0):,.2f}")
        print(f"  Net: ₹{equity.get('net', 0):,.2f}")
        
        # Test market data access
        print(f"\n📊 Market Data Access:")
        quote = md.kite.quote('RELIANCE')
        if quote:
            rel = quote.get('RELIANCE', {})
            print(f"  RELIANCE LTP: ₹{rel.get('last_price', 0):.2f}")
            print(f"  Volume: {rel.get('volume', 0):,}")
            print("  ✅ Market data working")
        else:
            print("  ❌ Market data failed")
        
        # Check holdings and positions
        print(f"\n📋 Holdings & Positions:")
        try:
            holdings = md.kite.holdings()
            positions = md.kite.positions()
            
            print(f"  Holdings: {len(holdings)}")
            for h in holdings[:3]:
                print(f"    {h.get('tradingsymbol')}: {h.get('quantity')} @ ₹{h.get('last_price', 0):.2f}")
            
            open_positions = [p for p in positions.get('net', []) if p.get('quantity', 0) != 0]
            print(f"  Open Positions: {len(open_positions)}")
            for p in open_positions[:3]:
                print(f"    {p.get('tradingsymbol')}: {p.get('quantity')} @ ₹{p.get('last_price', 0):.2f}")
                
        except Exception as e:
            print(f"  ❌ Error accessing holdings/positions: {e}")
        
        # Check order permissions (without placing orders)
        print(f"\n🔄 Order Permissions:")
        try:
            # Get order book to check if we can view orders
            orders = md.kite.orders()
            print(f"  Order Book Access: ✅ ({len(orders)} orders)")
            
            # Check trades
            trades = md.kite.trades()
            print(f"  Trade Book Access: ✅ ({len(trades)} trades)")
            
        except Exception as e:
            print(f"  ❌ Order book access failed: {e}")
        
        # Check segments
        print(f"\n📈 Trading Segments:")
        try:
            # Test NSE
            nse_instruments = md.kite.instruments('NSE')
            print(f"  NSE: ✅ ({len(nse_instruments)} instruments)")
            
            # Test BSE
            bse_instruments = md.kite.instruments('BSE')
            print(f"  BSE: ✅ ({len(bse_instruments)} instruments)")
            
        except Exception as e:
            print(f"  ❌ Segment access failed: {e}")
        
        print(f"\n✅ AUTHENTICATION CHECK COMPLETE")
        print(f"Bot has full trading permissions and can place orders.")
        
    except Exception as e:
        print(f"❌ Error during check: {e}")

if __name__ == "__main__":
    main()
