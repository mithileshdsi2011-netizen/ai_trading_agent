"""
Sentiment Analysis Module
Analyzes market sentiment using news and social media
"""
import requests
from bs4 import BeautifulSoup
from typing import Dict, List
import logging
from datetime import datetime, timedelta
import re

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class SentimentAnalyzer:
    """Analyzes market sentiment from news sources"""
    
    def __init__(self):
        self.news_sources = [
            'https://www.moneycontrol.com/news/business/markets/',
            'https://www.economicstimes.indiatimes.com/markets'
        ]
    
    def get_news_headlines(self, symbol: str, days: int = 1) -> List[Dict]:
        """
        Get recent news headlines for a stock.
        Returns empty list when no real news source is available
        so that analysis is driven purely by technical data.
        
        Args:
            symbol: Stock symbol
            days: Number of days to look back
        
        Returns:
            List of news items with title, url, and date
        """
        return []
    
    def analyze_sentiment(self, text: str) -> Dict:
        """
        Analyze sentiment of text using OpenAI
        
        Args:
            text: Text to analyze
        
        Returns:
            Dictionary with sentiment score and label
        """
        try:
            from openai import OpenAI
            from config import config
            
            if not config.OPENAI_API_KEY:
                # Fallback to simple keyword-based sentiment
                return self._simple_sentiment_analysis(text)
            
            client = OpenAI(api_key=config.OPENAI_API_KEY)
            
            response = client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=[
                    {
                        "role": "system",
                        "content": "You are a financial sentiment analyzer. Analyze the sentiment of the given text and return a JSON with 'sentiment' (positive/negative/neutral) and 'score' (-1 to 1)."
                    },
                    {
                        "role": "user",
                        "content": text
                    }
                ],
                temperature=0.3
            )
            
            result = response.choices[0].message.content
            
            # Parse the result (simplified)
            if 'positive' in result.lower():
                sentiment = 'positive'
                score = 0.5
            elif 'negative' in result.lower():
                sentiment = 'negative'
                score = -0.5
            else:
                sentiment = 'neutral'
                score = 0.0
            
            return {
                'sentiment': sentiment,
                'score': score,
                'analysis': result
            }
        
        except Exception as e:
            logger.error(f"Error in AI sentiment analysis: {e}")
            return self._simple_sentiment_analysis(text)
    
    def _simple_sentiment_analysis(self, text: str) -> Dict:
        """
        Simple keyword-based sentiment analysis (fallback)
        
        Args:
            text: Text to analyze
        
        Returns:
            Dictionary with sentiment score and label
        """
        positive_words = [
            'strong', 'growth', 'profit', 'gain', 'rise', 'bullish',
            'buy', 'upgrade', 'positive', 'beat', 'exceed', 'rally'
        ]
        
        negative_words = [
            'weak', 'loss', 'fall', 'decline', 'bearish', 'sell',
            'downgrade', 'negative', 'miss', 'drop', 'crash', 'concern'
        ]
        
        text_lower = text.lower()
        
        positive_count = sum(1 for word in positive_words if word in text_lower)
        negative_count = sum(1 for word in negative_words if word in text_lower)
        
        total = positive_count + negative_count
        
        if total == 0:
            return {'sentiment': 'neutral', 'score': 0.0}
        
        if positive_count > negative_count:
            score = positive_count / total
            sentiment = 'positive'
        elif negative_count > positive_count:
            score = -negative_count / total
            sentiment = 'negative'
        else:
            score = 0.0
            sentiment = 'neutral'
        
        return {
            'sentiment': sentiment,
            'score': score
        }
    
    def get_market_sentiment(self, symbol: str) -> Dict:
        """
        Get overall market sentiment for a stock
        
        Args:
            symbol: Stock symbol
        
        Returns:
            Dictionary with sentiment analysis
        """
        news_items = self.get_news_headlines(symbol)
        
        if not news_items:
            return {
                'sentiment': 'neutral',
                'score': 0.0,
                'news_count': 0,
                'reason': 'No news data available'
            }
        
        total_score = 0.0
        analyzed_news = []
        
        for news in news_items:
            sentiment_result = self.analyze_sentiment(news['title'])
            total_score += sentiment_result['score']
            analyzed_news.append({
                'title': news['title'],
                'sentiment': sentiment_result['sentiment'],
                'score': sentiment_result['score']
            })
        
        avg_score = total_score / len(news_items)
        
        if avg_score > 0.2:
            overall_sentiment = 'positive'
        elif avg_score < -0.2:
            overall_sentiment = 'negative'
        else:
            overall_sentiment = 'neutral'
        
        return {
            'sentiment': overall_sentiment,
            'score': avg_score,
            'news_count': len(news_items),
            'news_items': analyzed_news,
            'reason': f'Based on {len(news_items)} news items'
        }
