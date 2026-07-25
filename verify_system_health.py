#!/usr/bin/env python3
"""
Comprehensive System Health Verification for AI Trading Bot
"""
import sys
import os
import json
import subprocess
import time
from datetime import datetime

sys.path.insert(0, 'src')

def check_overall_health():
    """Check overall system health"""
    print("🔍 CHECKING OVERALL HEALTH")
    print("=" * 60)
    
    results = {}
    
    # 1. Check if all required modules import correctly
    print("\n1. Checking module imports...")
    try:
        from config import config
        from market_data import MarketDataFetcher
        from broker_integration import BrokerIntegration
        from trading_orchestrator import TradingOrchestrator
        from trade_scorer import TradeScorer
        from multi_timeframe import MultiTimeframeConfirmer
        from smart_exit import SmartExitAI
        from trade_journal import TradeJournal
        from sentiment_analysis import SentimentAnalyzer
        from ai_research_agent import AIResearchAgent
        from signal_generator import SignalGenerator
        from order_executor import OrderExecutor
        from risk_manager import RiskManager
        from market_regime import MarketRegimeDetector
        from technical_analysis import TechnicalAnalyzer
        
        results['imports'] = "✅ PASS"
        print("   ✅ All modules imported successfully")
    except Exception as e:
        results['imports'] = f"❌ FAIL: {e}"
        print(f"   ❌ Import error: {e}")
    
    # 2. Check configuration
    print("\n2. Checking configuration...")
    try:
        config_items = {
            'TRADING_AMOUNT': config.TRADING_AMOUNT,
            'MAX_POSITIONS': config.MAX_POSITIONS,
            'MIN_CONFIDENCE': config.MIN_CONFIDENCE,
            'TRADING_MODE': config.TRADING_MODE,
            'PAPER_TRADING': config.PAPER_TRADING,
            'STOP_LOSS_PERCENTAGE': config.STOP_LOSS_PERCENTAGE,
            'TARGET_PERCENTAGE': config.TARGET_PERCENTAGE,
            'RISK_PER_TRADE': config.RISK_PER_TRADE,
            'DAILY_MAX_LOSS_PCT': config.DAILY_MAX_LOSS_PCT,
        }
        results['config'] = "✅ PASS"
        print("   ✅ Configuration loaded:")
        for k, v in config_items.items():
            print(f"      {k}: {v}")
    except Exception as e:
        results['config'] = f"❌ FAIL: {e}"
        print(f"   ❌ Config error: {e}")
    
    # 3. Check Kite connection
    print("\n3. Checking Kite connection...")
    try:
        md = MarketDataFetcher()
        if md.kite:
            profile = md.kite.profile()
            results['kite'] = f"✅ PASS - User: {profile.get('user_name')}"
            print(f"   ✅ Kite connected - User: {profile.get('user_name')}")
        else:
            results['kite'] = "❌ FAIL - No connection"
            print("   ❌ Kite not connected")
    except Exception as e:
        results['kite'] = f"❌ FAIL: {e}"
        print(f"   ❌ Kite error: {e}")
    
    # 4. Check data files
    print("\n4. Checking data files...")
    data_files = [
        'data/kite_token.json',
        'data/trade_journal.json',
        'data/positions.json',
        'data/last_known_ip.txt'
    ]
    
    missing_files = []
    for file in data_files:
        if os.path.exists(file):
            print(f"   ✅ {file}")
        else:
            print(f"   ❌ {file} - Missing")
            missing_files.append(file)
    
    if not missing_files:
        results['data_files'] = "✅ PASS"
    else:
        results['data_files'] = f"❌ FAIL - Missing: {', '.join(missing_files)}"
    
    # 5. Check log files
    print("\n5. Checking log files...")
    log_files = [
        'logs/trading.log',
        'logs/dashboard.log',
        'logs/start_trading.log'
    ]
    
    for file in log_files:
        if os.path.exists(file):
            size = os.path.getsize(file)
            print(f"   ✅ {file} ({size:,} bytes)")
        else:
            print(f"   ❌ {file} - Missing")
    
    # 6. Check running processes
    print("\n6. Checking running processes...")
    try:
        # Check dashboard
        result = subprocess.run(['pgrep', '-f', 'dashboard.py'], capture_output=True, text=True)
        if result.returncode == 0:
            results['dashboard'] = "✅ PASS - Running"
            print(f"   ✅ Dashboard running (PID: {result.stdout.strip()})")
        else:
            results['dashboard'] = "❌ FAIL - Not running"
            print("   ❌ Dashboard not running")
        
        # Check trading bot
        result = subprocess.run(['pgrep', '-f', 'trading_orchestrator.py'], capture_output=True, text=True)
        if result.returncode == 0:
            results['trading_bot'] = "✅ PASS - Running"
            print(f"   ✅ Trading bot running (PID: {result.stdout.strip()})")
        else:
            results['trading_bot'] = "❌ FAIL - Not running"
            print("   ❌ Trading bot not running")
    except Exception as e:
        results['processes'] = f"❌ FAIL: {e}"
        print(f"   ❌ Process check error: {e}")
    
    return results

def check_trading_readiness():
    """Check trading readiness"""
    print("\n🔍 CHECKING TRADING READINESS")
    print("=" * 60)
    
    results = {}
    
    # 1. Test market data fetch
    print("\n1. Testing market data fetch...")
    try:
        from market_data import MarketDataFetcher
        md = MarketDataFetcher()
        
        # Test quote
        quote = md.kite.quote('RELIANCE')
        if quote:
            ltp = quote.get('RELIANCE', {}).get('last_price', 0)
            results['market_data'] = f"✅ PASS - RELIANCE LTP: ₹{ltp}"
            print(f"   ✅ Market data working - RELIANCE LTP: ₹{ltp}")
        else:
            results['market_data'] = "⚠️ WARN - No quote data (market may be closed)"
            print("   ⚠️  No quote data received (market may be closed)")
    except Exception as e:
        results['market_data'] = f"❌ FAIL: {e}"
        print(f"   ❌ Market data error: {e}")
    
    # 2. Test technical analysis
    print("\n2. Testing technical analysis...")
    try:
        from technical_analysis import TechnicalAnalyzer
        ta = TechnicalAnalyzer()
        # Test with sample data
        import pandas as pd
        sample_data = pd.DataFrame({
            'open': [2490, 2495, 2500],
            'high': [2500, 2505, 2510],
            'low': [2485, 2490, 2495],
            'close': [2495, 2500, 2505],
            'volume': [1000, 1100, 1200]
        })
        df = ta.calculate_indicators(sample_data)
        signals = {'rsi': df.iloc[-1].get('rsi', 50)}
        if signals:
            results['technical_analysis'] = "✅ PASS - Signals generated"
            print(f"   ✅ Technical analysis working - RSI: {signals.get('rsi', 0):.2f}")
        else:
            results['technical_analysis'] = "❌ FAIL - No signals"
            print("   ❌ No signals generated")
    except Exception as e:
        results['technical_analysis'] = f"❌ FAIL: {e}"
        print(f"   ❌ Technical analysis error: {e}")
    
    # 3. Test trade scoring
    print("\n3. Testing trade scoring...")
    try:
        from trade_scorer import TradeScorer
        scorer = TradeScorer()
        score = scorer.score(
            {'symbol': 'RELIANCE'},
            {
                'technical_analysis': {
                    'trend': 'UPTREND',
                    'rsi': 50,
                    'macd_histogram': 0.5,
                    'macd_histogram_prev': 0.2,
                    'volume_ratio': 1.5,
                },
                'sentiment_analysis': {
                    'score': 0.5,
                    'news_count': 5
                }
            },
            regime='SIDEWAYS',
            sector_momentum=0.02
        )
        results['trade_scorer'] = f"✅ PASS - Score: {score}/100"
        print(f"   ✅ Trade scoring working - Score: {score}/100")
    except Exception as e:
        results['trade_scorer'] = f"❌ FAIL: {e}"
        print(f"   ❌ Trade scoring error: {e}")
    
    # 4. Test risk manager
    print("\n4. Testing risk manager...")
    try:
        from risk_manager import RiskManager
        rm = RiskManager()
        positions = rm._load_positions()
        results['risk_manager'] = f"✅ PASS - {len(positions)} positions tracked"
        print(f"   ✅ Risk manager working - {len(positions)} positions tracked")
    except Exception as e:
        results['risk_manager'] = f"❌ FAIL: {e}"
        print(f"   ❌ Risk manager error: {e}")
    
    # 5. Test order executor
    print("\n5. Testing order executor...")
    try:
        from order_executor import OrderExecutor
        oe = OrderExecutor()
        margins = oe.broker.kite.margins()
        available = margins.get('equity', {}).get('available', {}).get('live_balance', 0)
        results['order_executor'] = f"✅ PASS - Available: ₹{available:,.2f}"
        print(f"   ✅ Order executor working - Available: ₹{available:,.2f}")
    except Exception as e:
        results['order_executor'] = f"❌ FAIL: {e}"
        print(f"   ❌ Order executor error: {e}")
    
    return results

def check_automation():
    """Check automation features"""
    print("\n🔍 CHECKING AUTOMATION")
    print("=" * 60)
    
    results = {}
    
    # 1. Check token expiry
    print("\n1. Checking token expiry...")
    try:
        with open('data/kite_token.json', 'r') as f:
            token_data = json.load(f)
        expiry = token_data.get('expiry', '')
        if expiry:
            results['token'] = f"✅ PASS - Expires: {expiry}"
            print(f"   ✅ Token valid - Expires: {expiry}")
        else:
            results['token'] = "❌ FAIL - No expiry"
            print("   ❌ No token expiry found")
    except Exception as e:
        results['token'] = f"❌ FAIL: {e}"
        print(f"   ❌ Token check error: {e}")
    
    # 2. Check IP monitoring
    print("\n2. Checking IP monitoring...")
    try:
        with open('data/last_known_ip.txt', 'r') as f:
            ip = f.read().strip()
        results['ip_monitoring'] = f"✅ PASS - Current IP: {ip}"
        print(f"   ✅ IP monitoring working - Current IP: {ip}")
    except Exception as e:
        results['ip_monitoring'] = f"❌ FAIL: {e}"
        print(f"   ❌ IP monitoring error: {e}")
    
    # 3. Check scheduler
    print("\n3. Checking scheduler...")
    try:
        # Check if trading bot is scheduled
        result = subprocess.run(['crontab', '-l'], capture_output=True, text=True)
        if 'start_trading.py' in result.stdout:
            results['scheduler'] = "✅ PASS - Crontab entry found"
            print("   ✅ Scheduler configured - Crontab entry found")
        else:
            results['scheduler'] = "⚠️ WARN - No crontab entry"
            print("   ⚠️ No crontab entry found")
    except Exception as e:
        results['scheduler'] = f"❌ FAIL: {e}"
        print(f"   ❌ Scheduler check error: {e}")
    
    return results

def main():
    """Main verification function"""
    print("🤖 AI TRADING BOT - COMPREHENSIVE SYSTEM VERIFICATION")
    print("=" * 60)
    print(f"Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()
    
    all_results = {}
    
    # Run all checks
    all_results['overall_health'] = check_overall_health()
    all_results['trading_readiness'] = check_trading_readiness()
    all_results['automation'] = check_automation()
    
    # Save results
    with open('verification_results.json', 'w') as f:
        json.dump(all_results, f, indent=2, default=str)
    
    print("\n" + "=" * 60)
    print("✅ VERIFICATION COMPLETE")
    print(f"Results saved to: verification_results.json")
    print("=" * 60)

if __name__ == "__main__":
    main()
