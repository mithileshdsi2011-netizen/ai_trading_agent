#!/usr/bin/env python3
"""
Test script for the enhanced AI-powered sell decision system
"""
import sys
import os
sys.path.insert(0, 'src')

from risk_manager import Position, PositionStatus
from sell_decision_ai import SellDecisionAI
from datetime import datetime

def test_sell_decision_ai():
    """Test the AI-powered sell decision engine"""
    print("🧪 TESTING AI-POWERED SELL DECISION ENGINE")
    print("=" * 60)
    
    # Initialize the sell decision AI
    sell_ai = SellDecisionAI()
    
    # Create test positions
    test_positions = [
        # Profitable position - should hold
        Position(
            symbol="RELIANCE",
            entry_price=2500.0,
            quantity=1,
            stop_loss=2375.0,
            target=3000.0,
            entry_time=datetime.now(),
            status=PositionStatus.OPEN
        ),
        # Small loss position - should evaluate recovery
        Position(
            symbol="BEL",
            entry_price=415.55,
            quantity=3,
            stop_loss=395.0,
            target=457.0,
            entry_time=datetime.now(),
            status=PositionStatus.OPEN
        ),
        # Large loss position - might sell
        Position(
            symbol="BPL",
            entry_price=60.39,
            quantity=15,
            stop_loss=57.0,
            target=66.0,
            entry_time=datetime.now(),
            status=PositionStatus.OPEN
        )
    ]
    
    # Current prices for test positions
    current_prices = {
        "RELIANCE": 2750.0,  # 10% profit
        "BEL": 395.0,       # -5% loss
        "BPL": 48.0         # -20% loss
    }
    
    print("\n📊 Testing Sell Decisions:")
    print("-" * 60)
    
    for position in test_positions:
        current_price = current_prices[position.symbol]
        pnl_pct = (current_price - position.entry_price) / position.entry_price
        
        print(f"\n🔍 Analyzing: {position.symbol}")
        print(f"   Entry Price: ₹{position.entry_price:.2f}")
        print(f"   Current Price: ₹{current_price:.2f}")
        print(f"   P&L: {pnl_pct*100:+.2f}%")
        
        try:
            decision = sell_ai.evaluate_position(position, current_price)
            
            print(f"   🤖 AI Decision: {decision.recommendation}")
            print(f"   📈 Confidence: {decision.confidence:.2f}")
            print(f"   📝 Reason: {decision.reason}")
            
            # Show factor breakdown
            print(f"   📊 Factor Analysis:")
            for factor, value in decision.factors.items():
                status = "🔴" if value > 0.7 else "🟡" if value > 0.4 else "🟢"
                print(f"      {status} {factor.replace('_', ' ').title()}: {value:.2f}")
            
        except Exception as e:
            print(f"   ❌ Error: {e}")
    
    print("\n" + "=" * 60)
    print("✅ Sell Decision AI Test Complete")
    print("=" * 60)

if __name__ == "__main__":
    test_sell_decision_ai()
