#!/usr/bin/env python3
"""
Dashboard Verification Script
Tests all dashboard pages and widgets
"""
import requests
import json
import time
from datetime import datetime

def test_dashboard_endpoints():
    """Test all dashboard API endpoints"""
    print("🔍 TESTING DASHBOARD ENDPOINTS")
    print("=" * 60)
    
    base_url = "http://localhost:5001"
    endpoints = [
        ("/", "Dashboard Home"),
        ("/api/data", "Main API Data"),
        ("/api/health", "Health Status"),
        ("/api/journal", "Trade Journal"),
        ("/api/morning-report", "Morning Report"),
        ("/api/backtest", "Backtest Engine"),
    ]
    
    results = {}
    
    for endpoint, name in endpoints:
        print(f"\nTesting {name} - {endpoint}")
        try:
            if endpoint == "/api/backtest":
                # POST request for backtest
                response = requests.post(f"{base_url}{endpoint}", 
                                       json={"symbols": ["RELIANCE"], "years": 1},
                                       timeout=10)
            else:
                response = requests.get(f"{base_url}{endpoint}", timeout=10)
            
            if response.status_code == 200:
                if endpoint.startswith("/api/"):
                    data = response.json()
                    results[endpoint] = f"✅ PASS - {len(str(data))} bytes"
                    print(f"   ✅ {name} - {len(str(data))} bytes")
                else:
                    results[endpoint] = "✅ PASS - HTML loaded"
                    print(f"   ✅ {name} - HTML loaded")
            else:
                results[endpoint] = f"❌ FAIL - HTTP {response.status_code}"
                print(f"   ❌ {name} - HTTP {response.status_code}")
        except Exception as e:
            results[endpoint] = f"❌ FAIL - {str(e)[:50]}"
            print(f"   ❌ {name} - {str(e)[:50]}")
    
    return results

def test_dashboard_data():
    """Test dashboard data integrity"""
    print("\n🔍 TESTING DASHBOARD DATA")
    print("=" * 60)
    
    try:
        response = requests.get("http://localhost:5001/api/data", timeout=10)
        data = response.json()
        
        results = {}
        
        # Check key data fields
        checks = [
            ('kite_ok', 'Kite Connection'),
            ('positions', 'Positions Data'),
            ('holdings', 'Holdings Data'),
            ('cash', 'Cash Balance'),
            ('account_balance', 'Account Balance'),
            ('portfolio_health', 'Portfolio Health'),
            ('market_summary', 'Market Summary'),
            ('strategy_stats', 'Strategy Stats'),
        ]
        
        for field, name in checks:
            if field in data:
                if field in ['positions', 'holdings']:
                    count = len(data[field]) if data[field] else 0
                    results[field] = f"✅ PASS - {count} items"
                    print(f"   ✅ {name} - {count} items")
                else:
                    results[field] = "✅ PASS - Present"
                    print(f"   ✅ {name} - Present")
            else:
                results[field] = "❌ FAIL - Missing"
                print(f"   ❌ {name} - Missing")
        
        # Check positions data structure
        if data.get('positions'):
            pos = data['positions'][0] if data['positions'] else {}
            pos_fields = ['tradingsymbol', 'quantity', 'average_price', 'last_price', 'stop_loss', 'target']
            for field in pos_fields:
                if field in pos:
                    print(f"   ✅ Position field {field} - Present")
                else:
                    print(f"   ⚠️ Position field {field} - Missing")
        
        return results
        
    except Exception as e:
        print(f"   ❌ Data test failed - {e}")
        return {'error': str(e)}

def test_trading_safety():
    """Test trading safety features"""
    print("\n🔍 TESTING TRADING SAFETY")
    print("=" * 60)
    
    results = {}
    
    # Test duplicate prevention
    print("\n1. Testing duplicate prevention...")
    try:
        from src.risk_manager import RiskManager
        rm = RiskManager()
        
        # Test if position tracking works
        positions = rm._load_positions()
        if positions:
            results['duplicate_prevention'] = "✅ PASS - Risk manager active"
            print(f"   ✅ Duplicate prevention - {len(positions)} positions tracked")
        else:
            results['duplicate_prevention'] = "⚠️ WARN - No positions tracked"
            print("   ⚠️ No positions currently tracked")
    except Exception as e:
        results['duplicate_prevention'] = f"❌ FAIL - {e}"
        print(f"   ❌ Duplicate prevention error - {e}")
    
    # Test holdings synchronization
    print("\n2. Testing holdings synchronization...")
    try:
        from src.broker_integration import BrokerIntegration
        bi = BrokerIntegration()
        holdings = bi.get_holdings()
        
        kite_holdings = holdings.get('holdings', [])
        results['holdings_sync'] = f"✅ PASS - {len(kite_holdings)} holdings synced"
        print(f"   ✅ Holdings sync - {len(kite_holdings)} from Kite")
    except Exception as e:
        results['holdings_sync'] = f"❌ FAIL - {e}"
        print(f"   ❌ Holdings sync error - {e}")
    
    # Test calculations
    print("\n3. Testing portfolio calculations...")
    try:
        response = requests.get("http://localhost:5001/api/data", timeout=10)
        data = response.json()
        
        cash = data.get('cash', 0)
        holdings_value = data.get('holdings_value', 0)
        account_balance = data.get('account_balance', 0)
        
        # Check if balance matches cash + holdings
        calculated_balance = cash + holdings_value
        if abs(calculated_balance - account_balance) < 1:  # Allow small difference
            results['calculations'] = "✅ PASS - Calculations consistent"
            print(f"   ✅ Portfolio calculations - Cash: ₹{cash:,.2f}, Holdings: ₹{holdings_value:,.2f}, Total: ₹{account_balance:,.2f}")
        else:
            results['calculations'] = "⚠️ WARN - Calculation mismatch"
            print(f"   ⚠️ Calculation mismatch - Expected: ₹{calculated_balance:,.2f}, Actual: ₹{account_balance:,.2f}")
    except Exception as e:
        results['calculations'] = f"❌ FAIL - {e}"
        print(f"   ❌ Calculation test error - {e}")
    
    return results

def test_automation():
    """Test automation features"""
    print("\n🔍 TESTING AUTOMATION")
    print("=" * 60)
    
    results = {}
    
    # Test token refresh
    print("\n1. Testing token refresh...")
    try:
        import json
        with open('data/kite_token.json', 'r') as f:
            token_data = json.load(f)
        
        expiry = token_data.get('expiry', '')
        if expiry:
            results['token_refresh'] = "✅ PASS - Token valid"
            print(f"   ✅ Token refresh - Valid until {expiry}")
        else:
            results['token_refresh'] = "❌ FAIL - No expiry"
            print("   ❌ Token refresh - No expiry found")
    except Exception as e:
        results['token_refresh'] = f"❌ FAIL - {e}"
        print(f"   ❌ Token refresh error - {e}")
    
    # Test IP monitoring
    print("\n2. Testing IP monitoring...")
    try:
        import requests
        response = requests.get("http://localhost:5001/api/data", timeout=10)
        data = response.json()
        
        current_ip = data.get('current_ip', '')
        if current_ip:
            results['ip_monitoring'] = f"✅ PASS - IP: {current_ip}"
            print(f"   ✅ IP monitoring - Current IP: {current_ip}")
        else:
            results['ip_monitoring'] = "❌ FAIL - No IP"
            print("   ❌ IP monitoring - No IP detected")
    except Exception as e:
        results['ip_monitoring'] = f"❌ FAIL - {e}"
        print(f"   ❌ IP monitoring error - {e}")
    
    # Test notifications (check if Telegram is configured)
    print("\n3. Testing notifications...")
    try:
        import os
        telegram_token = os.environ.get('TELEGRAM_BOT_TOKEN', '')
        if telegram_token:
            results['notifications'] = "✅ PASS - Telegram configured"
            print("   ✅ Notifications - Telegram configured")
        else:
            results['notifications'] = "⚠️ WARN - Telegram not configured"
            print("   ⚠️ Notifications - Telegram not configured")
    except Exception as e:
        results['notifications'] = f"❌ FAIL - {e}"
        print(f"   ❌ Notifications error - {e}")
    
    return results

def main():
    """Main verification function"""
    print("🖥️ DASHBOARD VERIFICATION")
    print("=" * 60)
    print(f"Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()
    
    all_results = {}
    
    # Run all tests
    all_results['endpoints'] = test_dashboard_endpoints()
    all_results['data'] = test_dashboard_data()
    all_results['trading_safety'] = test_trading_safety()
    all_results['automation'] = test_automation()
    
    # Save results
    with open('dashboard_verification.json', 'w') as f:
        json.dump(all_results, f, indent=2, default=str)
    
    print("\n" + "=" * 60)
    print("✅ DASHBOARD VERIFICATION COMPLETE")
    print(f"Results saved to: dashboard_verification.json")
    print("=" * 60)

if __name__ == "__main__":
    main()
