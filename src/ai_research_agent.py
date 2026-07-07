"""
AI Research Agent Module
Combines technical and sentiment analysis to generate comprehensive research
"""
from typing import Dict, List
import logging
from datetime import datetime

from market_data import MarketDataFetcher
from technical_analysis import TechnicalAnalyzer
from sentiment_analysis import SentimentAnalyzer
import numpy as np

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class AIResearchAgent:
    """AI-powered research agent for stock analysis"""
    
    def __init__(self):
        self.market_data = MarketDataFetcher()
        self.technical_analyzer = TechnicalAnalyzer()
        self.sentiment_analyzer = SentimentAnalyzer()
    
    def research_stock(self, symbol: str) -> Dict:
        """
        Perform comprehensive research on a stock
        
        Args:
            symbol: Stock symbol
        
        Returns:
            Dictionary with complete research analysis
        """
        logger.info(f"Starting research for {symbol}")
        
        research_result = {
            'symbol': symbol,
            'timestamp': datetime.now().isoformat(),
            'market_data': {},
            'technical_analysis': {},
            'sentiment_analysis': {},
            'overall_score': 0.0,
            'recommendation': 'HOLD',
            'confidence': 0.0
        }
        
        # Get market data
        stock_info = self.market_data.get_stock_info(symbol)
        historical_data = self.market_data.get_stock_data(symbol, period="3mo", interval="1d")
        intraday_data = self.market_data.get_intraday_data(symbol, days=5)
        
        research_result['market_data'] = {
            'info': stock_info,
            'historical_data_available': not historical_data.empty,
            'intraday_data_available': not intraday_data.empty
        }
        
        if historical_data.empty:
            logger.warning(f"No historical data available for {symbol}")
            research_result['recommendation'] = 'NO_DATA'
            return research_result
        
        # Perform technical analysis
        technical_signals = self.technical_analyzer.generate_signals(historical_data)
        research_result['technical_analysis'] = technical_signals

        # Sector momentum proxy: 5d return of stock vs Nifty
        try:
            nifty = self.market_data.get_stock_data('NIFTY 50', period='10d', interval='1d')
            if not nifty.empty and len(historical_data) >= 6:
                stock_ret  = (historical_data['Close'].iloc[-1] / historical_data['Close'].iloc[-6]) - 1
                nifty_ret  = (nifty['Close'].iloc[-1] / nifty['Close'].iloc[-6]) - 1
                # Clamp relative strength to −1..1
                rel        = float(np.clip((stock_ret - nifty_ret) * 10, -1, 1))
                research_result['sector_momentum'] = rel
            else:
                research_result['sector_momentum'] = 0.0
        except Exception:
            research_result['sector_momentum'] = 0.0
        
        # Perform sentiment analysis
        sentiment_result = self.sentiment_analyzer.get_market_sentiment(symbol)
        research_result['sentiment_analysis'] = sentiment_result
        
        # Calculate overall score
        overall_score = self._calculate_overall_score(
            technical_signals['technical_score'],
            sentiment_result['score']
        )
        research_result['overall_score'] = overall_score
        
        # Generate recommendation
        recommendation, confidence = self._generate_recommendation(
            overall_score,
            technical_signals['confidence'],
            len(sentiment_result.get('news_items', []))
        )
        
        research_result['recommendation'] = recommendation
        research_result['confidence'] = confidence
        
        # Add reasoning
        research_result['reasoning'] = self._generate_reasoning(
            technical_signals,
            sentiment_result,
            overall_score
        )
        
        logger.info(f"Research completed for {symbol}: {recommendation}")
        return research_result
    
    def _calculate_overall_score(self, technical_score: float, sentiment_score: float) -> float:
        """
        Calculate overall score combining technical and sentiment
        
        Args:
            technical_score: Technical analysis score (-1 to 1)
            sentiment_score: Sentiment analysis score (-1 to 1)
        
        Returns:
            Combined score (-1 to 1)
        """
        # Weight technical analysis more heavily (70%) than sentiment (30%)
        overall_score = (technical_score * 0.7) + (sentiment_score * 0.3)
        return overall_score
    
    def _generate_recommendation(self, overall_score: float, technical_confidence: float, news_count: int) -> tuple:
        """
        Generate trading recommendation
        
        Args:
            overall_score: Combined analysis score
            technical_confidence: Confidence from technical analysis
            news_count: Number of news items analyzed
        
        Returns:
            Tuple of (recommendation, confidence)
        """
        # Confidence derived from technical analysis
        # Real news gives a small boost; no penalty for missing news
        if news_count > 0:
            adjusted_confidence = min(technical_confidence + 0.05, 0.95)
        else:
            adjusted_confidence = technical_confidence
        
        if overall_score > 0.4:
            recommendation = 'STRONG_BUY'
        elif overall_score > 0.2:
            recommendation = 'BUY'
        elif overall_score < -0.4:
            recommendation = 'STRONG_SELL'
        elif overall_score < -0.2:
            recommendation = 'SELL'
        else:
            recommendation = 'HOLD'
        
        return recommendation, adjusted_confidence
    
    def _generate_reasoning(self, technical_signals: Dict, sentiment_result: Dict, overall_score: float) -> str:
        """
        Generate human-readable reasoning
        
        Args:
            technical_signals: Technical analysis results
            sentiment_result: Sentiment analysis results
            overall_score: Combined score
        
        Returns:
            Reasoning string
        """
        reasoning_parts = []
        
        # Technical reasoning
        reasoning_parts.append(
            f"Technical: {technical_signals['signal']} signal with {technical_signals['trend']} trend "
            f"(score: {technical_signals['technical_score']:.2f})"
        )
        
        # Sentiment reasoning
        reasoning_parts.append(
            f"Sentiment: {sentiment_result['sentiment']} ({sentiment_result['score']:.2f}) "
            f"based on {sentiment_result['news_count']} news items"
        )
        
        # Overall reasoning
        if overall_score > 0.3:
            reasoning_parts.append("Overall positive indicators suggest buying opportunity")
        elif overall_score < -0.3:
            reasoning_parts.append("Overall negative indicators suggest selling pressure")
        else:
            reasoning_parts.append("Mixed signals - wait for clearer direction")
        
        return " | ".join(reasoning_parts)
    
    def research_multiple_stocks(self, symbols: List[str]) -> List[Dict]:
        """
        Research multiple stocks
        
        Args:
            symbols: List of stock symbols
        
        Returns:
            List of research results
        """
        results = []
        for symbol in symbols:
            try:
                result = self.research_stock(symbol)
                results.append(result)
            except Exception as e:
                logger.error(f"Error researching {symbol}: {e}")
                results.append({
                    'symbol': symbol,
                    'error': str(e),
                    'recommendation': 'ERROR'
                })
        return results
    
    def get_top_opportunities(self, symbols: List[str], top_n: int = 5) -> List[Dict]:
        """
        Get top trading opportunities from watchlist
        
        Args:
            symbols: List of stock symbols
            top_n: Number of top opportunities to return
        
        Returns:
            List of top opportunities sorted by overall score
        """
        research_results = self.research_multiple_stocks(symbols)
        
        # Filter out errors and no-data results
        valid_results = [
            r for r in research_results 
            if r.get('recommendation') not in ['ERROR', 'NO_DATA', 'HOLD']
        ]
        
        # Sort by overall score
        sorted_results = sorted(
            valid_results,
            key=lambda x: x['overall_score'],
            reverse=True
        )
        
        return sorted_results[:top_n]
