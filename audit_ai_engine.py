#!/usr/bin/env python3
"""
AI Decision Engine Audit Script
Audits scoring components, calculations, and ranking logic for top gainers
"""
import sys
import logging
import json
from datetime import datetime

sys.path.insert(0, 'src')
logging.getLogger().setLevel(logging.WARNING)

from trade_scorer import TradeScorer, WEIGHTS
from market_regime import MarketRegimeDetector
from market_data import MarketDataFetcher
from ai_research_agent import AIResearchAgent
from technical_analysis import TechnicalAnalyzer
from sentiment_analysis import SentimentAnalyzer

def audit_top_gainers():
    """Audit AI scoring for today's top gainers"""
    
    print("=" * 80)
    print("AI DECISION ENGINE AUDIT REPORT")
    print("=" * 80)
    print(f"Timestamp: {datetime.now().isoformat()}")
    print()
    
    # Initialize components
    scorer = TradeScorer()
    md = MarketDataFetcher()
    regime = MarketRegimeDetector(kite=md.kite).detect_regime()
    ai = AIResearchAgent()
    
    print(f"Market Regime: {regime}")
    print(f"Trade Scorer Weights: {WEIGHTS}")
    print()
    
    # Test symbols from morning report top gainers
    symbols = ['KALYANKJIL', 'NEWGEN', 'ZENSARTECH', 'TCS', 'ROUTE', 'M&M', 'SHOPERSTOP']
    
    audit_results = []
    
    for sym in symbols:
        print(f"AUDITING: {sym}")
        print("-" * 40)
        
        try:
            # Get full research
            research = ai.research_stock(sym)
            tech = research['technical_analysis']
            senti = research['sentiment_analysis']
            
            # Get current price
            price = md.get_realtime_price(sym) or 0
            
            # Create signal for scoring
            signal = {
                'symbol': sym,
                'action': 'BUY' if research['recommendation'] in ['BUY','STRONG_BUY'] else 'HOLD',
                'current_price': price,
                'confidence': research['confidence'],
                'overall_score': research['overall_score'],
                'reasoning': research['reasoning']
            }
            
            # Score with TradeScorer
            score_res = scorer.score(
                signal=signal, 
                research=research, 
                regime=regime, 
                sector_momentum=research.get('sector_momentum',0.0)
            )
            
            # Compile audit data
            audit_data = {
                'symbol': sym,
                'price': price,
                'ai_recommendation': research['recommendation'],
                'ai_overall_score': round(research['overall_score'],3),
                'ai_confidence': round(research['confidence'],3),
                'trade_score': score_res['total_score'],
                'trade_score_components': score_res['components'],
                'size_fraction': score_res['size_fraction'],
                'grade': score_res['grade'],
                'skip': score_res['skip'],
                'technical_trend': tech.get('trend'),
                'technical_score': round(tech.get('technical_score',0),3),
                'rsi': round(tech.get('rsi',50),2),
                'macd_histogram': round(tech.get('macd_histogram',0),4) if tech.get('macd_histogram') is not None else None,
                'volume_ratio': round(tech.get('volume_ratio',1.0),2),
                'sentiment': senti.get('sentiment'),
                'sentiment_score': senti.get('score'),
                'news_count': senti.get('news_count',0),
                'sector_momentum': round(research.get('sector_momentum',0.0),3),
                'regime': regime,
                'full_reasoning': research['reasoning']
            }
            
            audit_results.append(audit_data)
            
            # Print detailed breakdown
            print(f"Price: ₹{price:.2f}")
            print(f"AI Recommendation: {research['recommendation']}")
            print(f"AI Overall Score: {research['overall_score']:.3f}")
            print(f"AI Confidence: {research['confidence']:.1%}")
            print(f"Trade Score: {score_res['total_score']}/100 ({score_res['grade']})")
            print(f"Size Fraction: {score_res['size_fraction']*100:.0f}%")
            print(f"Skip: {score_res['skip']}")
            print()
            print("Component Breakdown:")
            for comp, val in score_res['components'].items():
                weight = WEIGHTS.get(comp, 0)
                print(f"  {comp:12}: {val:2.0f}/ {weight:2.0f} pts")
            print()
            print("Technical Details:")
            print(f"  Trend: {tech.get('trend')}")
            print(f"  Technical Score: {tech.get('technical_score',0):.3f}")
            print(f"  RSI: {tech.get('rsi',50):.1f}")
            print(f"  MACD Histogram: {tech.get('macd_histogram')}")
            print(f"  Volume Ratio: {tech.get('volume_ratio',1.0):.2f}x")
            print()
            print("Sentiment Details:")
            print(f"  Sentiment: {senti.get('sentiment')}")
            print(f"  Sentiment Score: {senti.get('score',0):.3f}")
            print(f"  News Count: {senti.get('news_count',0)}")
            print()
            print(f"Full Reasoning: {research['reasoning']}")
            print()
            
        except Exception as e:
            print(f"ERROR: {e}")
            audit_results.append({'symbol': sym, 'error': str(e)})
        
        print()
    
    # Summary analysis
    print("=" * 80)
    print("SUMMARY ANALYSIS")
    print("=" * 80)
    
    valid_results = [r for r in audit_results if 'error' not in r]
    
    if valid_results:
        # Find why top gainers have low scores
        print("Why Top Gainers Have Low Scores:")
        for r in valid_results:
            if r['trade_score'] < 65:
                print(f"\n{r['symbol']} (Score: {r['trade_score']}):")
                comps = r['trade_score_components']
                if comps.get('trend', 0) == 0:
                    print("  - No trend points (trend not strong)")
                if comps.get('volume', 0) < 12:
                    print(f"  - Low volume score: {comps.get('volume',0)}/20")
                if comps.get('rsi', 0) < 10:
                    print(f"  - Poor RSI positioning: {comps.get('rsi',0)}/15")
                if comps.get('macd', 0) < 10:
                    print(f"  - Weak MACD: {comps.get('macd',0)}/15")
                if r['sentiment'] == 'neutral' and r['news_count'] == 0:
                    print("  - No news sentiment boost")
        
        print("\nScore Distribution:")
        scores = [r['trade_score'] for r in valid_results]
        print(f"  Min: {min(scores):.0f}")
        print(f"  Max: {max(scores):.0f}")
        print(f"  Avg: {sum(scores)/len(scores):.0f}")
        
        print("\nRecommendation Distribution:")
        rec_counts = {}
        for r in valid_results:
            rec = r['ai_recommendation']
            rec_counts[rec] = rec_counts.get(rec, 0) + 1
        for rec, count in rec_counts.items():
            print(f"  {rec}: {count}")
    
    # Save detailed results
    with open('/tmp/ai_engine_audit.json', 'w') as f:
        json.dump({
            'timestamp': datetime.now().isoformat(),
            'regime': regime,
            'scorer_weights': WEIGHTS,
            'results': audit_results
        }, f, indent=2, default=str)
    
    print(f"\nDetailed audit saved to: /tmp/ai_engine_audit.json")
    print("=" * 80)

if __name__ == "__main__":
    audit_top_gainers()
