#!/usr/bin/env python3
"""
Analyze Today's Skipped Opportunities
Investigates why high-scoring stocks were skipped and provides detailed decision logs
"""
import sys
import os
sys.path.insert(0, 'src')

from datetime import datetime, date
import json
from decision_logger import DecisionLogger
from market_data import MarketDataFetcher
from ai_research_agent import AIResearchAgent
from signal_generator import SignalGenerator
from risk_manager import RiskManager
from config import config

def analyze_today_skipped_opportunities():
    """Comprehensive analysis of today's skipped opportunities"""
    
    print("🔍 ANALYZING TODAY'S SKIPPED OPPORTUNITIES")
    print("=" * 70)
    print(f"Date: {date.today().strftime('%Y-%m-%d')}")
    print(f"Time: {datetime.now().strftime('%H:%M:%S')}")
    print()
    
    # Initialize components
    decision_logger = DecisionLogger()
    market_data = MarketDataFetcher()
    research_agent = AIResearchAgent()
    signal_generator = SignalGenerator()
    risk_manager = RiskManager()
    
    # Get today's decisions
    skipped = decision_logger.get_skipped_opportunities()
    executed = decision_logger.get_executed_trades()
    summary = decision_logger.get_decision_summary()
    
    print(f"📊 TODAY'S DECISION SUMMARY")
    print("-" * 50)
    print(f"Total Evaluated: {summary.get('total_evaluated', 0)}")
    print(f"Skipped: {summary.get('skipped', 0)}")
    print(f"Executed: {summary.get('executed', 0)}")
    print(f"Skip Rate: {summary.get('skip_rate', 0)*100:.1f}%")
    print()
    
    # Analysis of high-scoring skipped stocks
    print(f"🎯 HIGH-SCORING SKIPPED STOCKS")
    print("-" * 50)
    
    high_score_skipped = [d for d in skipped if d.overall_score >= 70]
    very_high_score_skipped = [d for d in skipped if d.overall_score >= 80]
    
    print(f"Skipped with Score ≥70: {len(high_score_skipped)}")
    print(f"Skipped with Score ≥80: {len(very_high_score_skipped)}")
    print()
    
    if very_high_score_skipped:
        print("🔥 VERY HIGH SCORE (≥80) SKIPPED STOCKS:")
        for decision in sorted(very_high_score_skipped, key=lambda x: x.overall_score, reverse=True):
            print(f"  {decision.symbol:12s} | Score: {decision.overall_score:5.1f} | "
                  f"Conf: {decision.confidence*100:3.0f}% | R:R: {decision.risk_reward_ratio:4.2f} | "
                  f"Reason: {decision.rejection_reason}")
        print()
    
    if high_score_skipped:
        print("⭐ HIGH SCORE (70-79) SKIPPED STOCKS:")
        for decision in sorted(high_score_skipped, key=lambda x: x.overall_score, reverse=True):
            if decision.overall_score < 80:
                print(f"  {decision.symbol:12s} | Score: {decision.overall_score:5.1f} | "
                      f"Conf: {decision.confidence*100:3.0f}% | R:R: {decision.risk_reward_ratio:4.2f} | "
                      f"Reason: {decision.rejection_reason}")
        print()
    
    # Rejection reasons analysis
    print(f"📋 REJECTION REASONS ANALYSIS")
    print("-" * 50)
    rejection_reasons = summary.get('rejection_reasons', {})
    for reason, count in sorted(rejection_reasons.items(), key=lambda x: x[1], reverse=True):
        percentage = (count / summary.get('skipped', 1)) * 100
        print(f"  {reason:30s}: {count:3d} ({percentage:5.1f}%)")
    print()
    
    # Specific analysis for mentioned stocks
    target_stocks = ['BHEL', 'BIOCON', 'BANDHANBNK', 'BPL', 'CONCOR', 'MCX', 'SBICARD']
    print(f"🎯 SPECIFIC STOCKS ANALYSIS")
    print("-" * 50)
    
    for stock in target_stocks:
        decision = next((d for d in skipped if d.symbol == stock), None)
        if decision:
            print(f"\n{stock}:")
            print(f"  Overall Score: {decision.overall_score:.1f}")
            print(f"  Confidence: {decision.confidence*100:.0f}%")
            print(f"  Technical Score: {decision.technical_score:.1f}")
            print(f"  News Sentiment: {decision.news_sentiment_score:.1f}")
            print(f"  Sector Strength: {decision.sector_strength:.1f}")
            print(f"  Market Regime: {decision.market_regime}")
            print(f"  Risk/Reward: {decision.risk_reward_ratio:.2f}")
            print(f"  Entry Price: ₹{decision.entry_price:.2f}")
            print(f"  Position Size: {decision.position_size_calculated}")
            print(f"  Available Cash: ₹{decision.available_cash:.0f}")
            print(f"  Portfolio Exposure: {decision.portfolio_exposure*100:.1f}%")
            print(f"  Open Positions: {len(decision.current_open_positions)}")
            print(f"  Existing Holdings: {len(decision.existing_holdings)}")
            print(f"  Cooldown Status: {decision.cooldown_status}")
            print(f"  Max Position Size: ₹{decision.max_position_size:.0f}")
            print(f"  🚫 REJECTION REASON: {decision.rejection_reason}")
        else:
            # Check if it was executed
            executed_decision = next((d for d in executed if d.symbol == stock), None)
            if executed_decision:
                print(f"\n{stock}: ✅ EXECUTED")
                print(f"  Overall Score: {executed_decision.overall_score:.1f}")
                print(f"  Confidence: {executed_decision.confidence*100:.0f}%")
                print(f"  Position Size: {executed_decision.position_size_calculated}")
            else:
                print(f"\n{stock}: ❌ NOT EVALUATED TODAY")
    
    print()
    
    # Market regime analysis
    print(f"🌍 MARKET CONTEXT ANALYSIS")
    print("-" * 50)
    
    # Get current market regime
    try:
        from market_regime import MarketRegimeDetector
        regime_detector = MarketRegimeDetector()
        regime = regime_detector.detect_regime()
        print(f"Current Market Regime: {regime}")
        
        # Analyze performance by regime
        regime_performance = {}
        for decision in skipped:
            regime = decision.market_regime
            if regime not in regime_performance:
                regime_performance[regime] = {'count': 0, 'avg_score': 0, 'avg_confidence': 0}
            regime_performance[regime]['count'] += 1
            regime_performance[regime]['avg_score'] += decision.overall_score
            regime_performance[regime]['avg_confidence'] += decision.confidence
        
        print(f"\nSkipped by Market Regime:")
        for regime, data in regime_performance.items():
            avg_score = data['avg_score'] / data['count'] if data['count'] > 0 else 0
            avg_conf = data['avg_confidence'] / data['count'] if data['count'] > 0 else 0
            print(f"  {regime:12s}: {data['count']:3d} stocks | "
                  f"Avg Score: {avg_score:5.1f} | Avg Conf: {avg_conf*100:3.0f}%")
        
    except Exception as e:
        print(f"Could not analyze market regime: {e}")
    
    print()
    
    # Capital constraints analysis
    print(f"💰 CAPITAL CONSTRAINTS ANALYSIS")
    print("-" * 50)
    
    capital_constraints = [d for d in skipped if 'capital' in d.rejection_reason.lower() or 
                          'position' in d.rejection_reason.lower() or
                          'exposure' in d.rejection_reason.lower()]
    
    if capital_constraints:
        print(f"Skipped due to Capital Constraints: {len(capital_constraints)}")
        avg_available_cash = sum(d.available_cash for d in capital_constraints) / len(capital_constraints)
        avg_position_size = sum(d.position_size_calculated for d in capital_constraints) / len(capital_constraints)
        avg_exposure = sum(d.portfolio_exposure for d in capital_constraints) / len(capital_constraints)
        
        print(f"  Average Available Cash: ₹{avg_available_cash:.0f}")
        print(f"  Average Position Size: {avg_position_size:.0f}")
        print(f"  Average Portfolio Exposure: {avg_exposure*100:.1f}%")
        print()
        
        for decision in sorted(capital_constraints, key=lambda x: x.overall_score, reverse=True)[:5]:
            print(f"  {decision.symbol:12s} | Score: {decision.overall_score:5.1f} | "
                  f"Pos Size: ₹{decision.position_size_calculated * decision.entry_price:.0f} | "
                  f"Available: ₹{decision.available_cash:.0f}")
    else:
        print("No stocks skipped due to capital constraints")
    
    print()
    
    # Risk/Reward analysis
    print(f"⚖️ RISK/REWARD ANALYSIS")
    print("-" * 50)
    
    poor_rr = [d for d in skipped if d.risk_reward_ratio < 1.5]
    good_rr = [d for d in skipped if d.risk_reward_ratio >= 2.0]
    
    print(f"Skipped with R:R < 1.5: {len(poor_rr)}")
    print(f"Skipped with R:R ≥ 2.0: {len(good_rr)}")
    
    if good_rr:
        print(f"\nHigh R:R (≥2.0) Skipped Stocks:")
        for decision in sorted(good_rr, key=lambda x: x.risk_reward_ratio, reverse=True)[:10]:
            print(f"  {decision.symbol:12s} | R:R: {decision.risk_reward_ratio:4.2f} | "
                  f"Score: {decision.overall_score:5.1f} | Reason: {decision.rejection_reason}")
    
    print()
    
    # Recommendations
    print(f"💡 RECOMMENDATIONS")
    print("-" * 50)
    
    if len(very_high_score_skipped) > 0:
        print("⚠️  HIGH-SCORING STOCKS SKIPPED:")
        print("   Review capital allocation strategy")
        print("   Consider increasing max position size for high-confidence trades")
        print("   Evaluate if risk parameters are too conservative")
        print()
    
    if len(poor_rr) > len(skipped) * 0.3:
        print("⚠️  MANY STOCKS SKIPPED DUE TO POOR R:R:")
        print("   Market conditions may not favor current strategy")
        print("   Consider adjusting risk parameters for current regime")
        print()
    
    if len(capital_constraints) > len(skipped) * 0.5:
        print("⚠️  CAPITAL CONSTRAINTS DOMINATE:")
        print("   Consider increasing overall capital allocation")
        print("   Review portfolio diversification requirements")
        print("   Evaluate if position sizing is too conservative")
        print()
    
    print("✅ ANALYSIS COMPLETE")
    print("=" * 70)
    print("Check the dashboard 'Skipped Opportunities' tab for real-time updates")

if __name__ == "__main__":
    analyze_today_skipped_opportunities()
