"""
AI-Powered Sell Decision Engine for Professional Swing Trading
Implements intelligent HOLD vs SELL decisions based on multi-factor analysis
"""
import logging
import pandas as pd
import numpy as np
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timedelta
from dataclasses import dataclass

from market_data import MarketDataFetcher
from risk_manager import Position, PositionStatus
from trade_scorer import TradeScorer
from ai_research_agent import AIResearchAgent
from sentiment_analysis import SentimentAnalyzer
from market_regime import MarketRegimeDetector
from technical_analysis import TechnicalAnalyzer

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@dataclass
class SellDecision:
    """Represents a sell decision with detailed reasoning"""
    should_sell: bool
    confidence: float  # 0-1
    reason: str
    factors: Dict[str, float]
    recommendation: str  # 'HOLD', 'SELL', 'REDUCE_PARTIAL'


class SellDecisionAI:
    """
    AI-powered sell decision engine that evaluates multiple factors
    to make intelligent HOLD vs SELL decisions for swing trading
    """
    
    def __init__(self):
        self.market_data = MarketDataFetcher()
        self.trade_scorer = TradeScorer()
        self.research_agent = AIResearchAgent()
        self.sentiment_analyzer = SentimentAnalyzer()
        self.regime_detector = MarketRegimeDetector()
        self.technical_analyzer = TechnicalAnalyzer()
        
        # Weight factors for decision making
        self.weights = {
            'ai_score': 0.25,
            'technical_momentum': 0.20,
            'market_regime': 0.15,
            'sector_strength': 0.15,
            'news_sentiment': 0.10,
            'fundamental_outlook': 0.10,
            'recovery_probability': 0.05
        }
    
    def evaluate_position(self, position: Position, current_price: float) -> SellDecision:
        """
        Evaluate a position and return intelligent sell decision
        
        Args:
            position: Current position
            current_price: Current market price
            
        Returns:
            SellDecision with recommendation and reasoning
        """
        symbol = position.symbol
        pnl_pct = (current_price - position.entry_price) / position.entry_price
        
        # Gather all factors
        factors = self._analyze_all_factors(symbol, current_price, position, pnl_pct)
        
        # Calculate overall sell score
        sell_score = self._calculate_sell_score(factors, pnl_pct)
        
        # Make decision based on score and context
        decision = self._make_decision(sell_score, factors, pnl_pct, position)
        
        logger.info(
            f"SellDecisionAI for {symbol}: {decision.recommendation} "
            f"(confidence: {decision.confidence:.2f}, P&L: {pnl_pct:+.2%})"
        )
        
        return decision
    
    def _analyze_all_factors(self, symbol: str, current_price: float, 
                           position: Position, pnl_pct: float) -> Dict[str, float]:
        """Analyze all factors for sell decision"""
        factors = {}
        
        try:
            # 1. AI Overall Score
            factors['ai_score'] = self._analyze_ai_score(symbol, current_price)
            
            # 2. Technical Momentum
            factors['technical_momentum'] = self._analyze_technical_momentum(symbol, current_price)
            
            # 3. Market Regime
            factors['market_regime'] = self._analyze_market_regime()
            
            # 4. Sector Strength
            factors['sector_strength'] = self._analyze_sector_strength(symbol)
            
            # 5. News Sentiment
            factors['news_sentiment'] = self._analyze_news_sentiment(symbol)
            
            # 6. Fundamental Outlook
            factors['fundamental_outlook'] = self._analyze_fundamental_outlook(symbol)
            
            # 7. Recovery Probability
            factors['recovery_probability'] = self._analyze_recovery_probability(
                symbol, current_price, position, pnl_pct
            )
            
        except Exception as e:
            logger.warning(f"Error analyzing factors for {symbol}: {e}")
            # Set neutral scores if analysis fails
            for key in self.weights.keys():
                if key not in factors:
                    factors[key] = 0.5
        
        return factors
    
    def _analyze_ai_score(self, symbol: str, current_price: float) -> float:
        """Analyze current AI score for the symbol"""
        try:
            # Get current market data for scoring
            hist = self.market_data.get_stock_data(symbol, period='1mo', interval='1d')
            if hist is None or hist.empty:
                return 0.5
            
            latest = hist.iloc[-1]
            
            # Calculate technical indicators
            delta = hist['Close'].diff()
            gain = delta.clip(lower=0)
            loss = (-delta).clip(lower=0)
            avg_g = gain.rolling(14).mean()
            avg_l = loss.rolling(14).mean()
            rs = avg_g / avg_l.replace(0, 1e-9)
            rsi = 100 - (100 / (1 + rs))
            
            # MACD
            ema12 = hist['Close'].ewm(span=12).mean()
            ema26 = hist['Close'].ewm(span=26).mean()
            macd = ema12 - ema26
            signal_line = macd.ewm(span=9).mean()
            macd_histogram = macd - signal_line
            
            # Volume ratio
            volume_sma = hist['Volume'].rolling(20).mean()
            volume_ratio = hist['Volume'].iloc[-1] / volume_sma.iloc[-1] if volume_sma.iloc[-1] > 0 else 1.0
            
            # Get research data
            research = self.research_agent.research_stock(symbol)
            
            # Score the position
            score_data = {
                'price': current_price,
                'rsi': rsi.iloc[-1] if not rsi.empty else 50,
                'macd': macd_histogram.iloc[-1] if not macd_histogram.empty else 0,
                'volume_ratio': volume_ratio,
                'sentiment': 0.5,  # Neutral default
                'research': research
            }
            
            ai_score = self.trade_scorer.score(symbol, score_data)
            
            # Convert to 0-1 scale (higher score = stronger hold signal)
            normalized_score = min(ai_score / 100.0, 1.0)
            
            # For sell decision, invert the score (higher AI score = lower sell probability)
            return 1.0 - normalized_score
            
        except Exception as e:
            logger.debug(f"AI score analysis failed for {symbol}: {e}")
            return 0.5
    
    def _analyze_technical_momentum(self, symbol: str, current_price: float) -> float:
        """Analyze technical momentum indicators"""
        try:
            hist = self.market_data.get_stock_data(symbol, period='1mo', interval='1d')
            if hist is None or hist.empty or len(hist) < 20:
                return 0.5
            
            # Calculate technical indicators
            df = hist.copy()
            
            # RSI
            delta = df['Close'].diff()
            gain = delta.clip(lower=0)
            loss = (-delta).clip(lower=0)
            avg_g = gain.rolling(14).mean()
            avg_l = loss.rolling(14).mean()
            rs = avg_g / avg_l.replace(0, 1e-9)
            df['RSI'] = 100 - (100 / (1 + rs))
            
            # MACD
            ema12 = df['Close'].ewm(span=12).mean()
            ema26 = df['Close'].ewm(span=26).mean()
            macd = ema12 - ema26
            signal_line = macd.ewm(span=9).mean()
            df['MACD_Hist'] = macd - signal_line
            
            # Price momentum
            df['SMA_20'] = df['Close'].rolling(20).mean()
            df['SMA_50'] = df['Close'].rolling(50).mean()
            
            latest = df.iloc[-1]
            prev = df.iloc[-2]
            
            sell_signals = 0
            total_signals = 0
            
            # RSI overbought
            total_signals += 1
            if latest['RSI'] > 80:
                sell_signals += 1
            elif latest['RSI'] > 70:
                sell_signals += 0.5
            
            # MACD bearish crossover
            total_signals += 1
            if (prev['MACD_Hist'] >= 0 and latest['MACD_Hist'] < 0):
                sell_signals += 1
            elif latest['MACD_Hist'] < 0:
                sell_signals += 0.5
            
            # Price below moving averages
            total_signals += 1
            if latest['Close'] < latest['SMA_20']:
                sell_signals += 0.5
            if latest['Close'] < latest['SMA_50']:
                sell_signals += 0.5
            
            # Volume decline
            vol_sma = df['Volume'].rolling(20).mean()
            total_signals += 1
            if latest['Volume'] < 0.6 * vol_sma.iloc[-1]:
                sell_signals += 1
            elif latest['Volume'] < 0.8 * vol_sma.iloc[-1]:
                sell_signals += 0.5
            
            return sell_signals / total_signals if total_signals > 0 else 0.5
            
        except Exception as e:
            logger.debug(f"Technical momentum analysis failed for {symbol}: {e}")
            return 0.5
    
    def _analyze_market_regime(self) -> float:
        """Analyze current market regime"""
        try:
            regime = self.regime_detector.detect_regime()
            
            # Convert regime to sell probability
            regime_sell_prob = {
                'BULL': 0.1,      # Very low sell probability in bull market
                'BULL_VOLATILE': 0.2,
                'SIDEWAYS': 0.3,   # Moderate in sideways
                'SIDEWAYS_VOLATILE': 0.4,
                'BEAR': 0.7,       # High sell probability in bear market
                'BEAR_VOLATILE': 0.8
            }
            
            return regime_sell_prob.get(regime, 0.5)
            
        except Exception as e:
            logger.debug(f"Market regime analysis failed: {e}")
            return 0.5
    
    def _analyze_sector_strength(self, symbol: str) -> float:
        """Analyze sector strength relative to market"""
        try:
            research = self.research_agent.research_stock(symbol)
            sector_momentum = research.get('sector_momentum', 0)
            
            # Convert sector momentum to sell probability
            # Negative momentum = higher sell probability
            if sector_momentum > 0.05:  # Strong outperformance
                return 0.1
            elif sector_momentum > 0:   # Mild outperformance
                return 0.3
            elif sector_momentum > -0.05:  # Mild underperformance
                return 0.6
            else:  # Strong underperformance
                return 0.8
                
        except Exception as e:
            logger.debug(f"Sector strength analysis failed for {symbol}: {e}")
            return 0.5
    
    def _analyze_news_sentiment(self, symbol: str) -> float:
        """Analyze news sentiment for the symbol"""
        try:
            sentiment = self.sentiment_analyzer.analyze_stock(symbol)
            
            # Convert sentiment to sell probability
            if sentiment > 0.3:  # Very positive
                return 0.1
            elif sentiment > 0.1:  # Positive
                return 0.3
            elif sentiment > -0.1:  # Neutral
                return 0.5
            elif sentiment > -0.3:  # Negative
                return 0.7
            else:  # Very negative
                return 0.9
                
        except Exception as e:
            logger.debug(f"News sentiment analysis failed for {symbol}: {e}")
            return 0.5
    
    def _analyze_fundamental_outlook(self, symbol: str) -> float:
        """Analyze fundamental outlook (simplified)"""
        try:
            # For swing trading, we'll use recent price action as a proxy
            hist = self.market_data.get_stock_data(symbol, period='3mo', interval='1d')
            if hist is None or hist.empty:
                return 0.5
            
            # Calculate 3-month trend
            initial_price = hist['Close'].iloc[0]
            current_price = hist['Close'].iloc[-1]
            trend = (current_price - initial_price) / initial_price
            
            # Convert trend to sell probability
            if trend > 0.2:  # Strong uptrend
                return 0.1
            elif trend > 0.1:  # Moderate uptrend
                return 0.3
            elif trend > -0.1:  # Sideways
                return 0.5
            elif trend > -0.2:  # Moderate downtrend
                return 0.7
            else:  # Strong downtrend
                return 0.9
                
        except Exception as e:
            logger.debug(f"Fundamental outlook analysis failed for {symbol}: {e}")
            return 0.5
    
    def _analyze_recovery_probability(self, symbol: str, current_price: float,
                                    position: Position, pnl_pct: float) -> float:
        """Analyze probability of recovery from current position"""
        try:
            # If already profitable, recovery probability is less relevant
            if pnl_pct > 0:
                return 0.3  # Lower sell probability for profitable positions
            
            # For losing positions, analyze recovery potential
            hist = self.market_data.get_stock_data(symbol, period='3mo', interval='1d')
            if hist is None or hist.empty:
                return 0.5
            
            # Calculate volatility and recent bounce potential
            returns = hist['Close'].pct_change().dropna()
            volatility = returns.std()
            
            # Look for recent bottoming patterns
            recent_prices = hist['Close'].tail(10)
            is_bottoming = (
                recent_prices.min() == recent_prices.iloc[-1] or
                recent_prices.iloc[-1] > recent_prices.iloc[-2] > recent_prices.iloc[-3]
            )
            
            # Calculate support level strength
            support_level = hist['Close'].tail(30).quantile(0.1)
            support_distance = (current_price - support_level) / support_level
            
            # Recovery probability factors
            recovery_score = 0.5
            
            # Lower volatility = higher recovery probability
            if volatility < 0.02:
                recovery_score += 0.2
            elif volatility < 0.04:
                recovery_score += 0.1
            
            # Bottoming pattern = higher recovery probability
            if is_bottoming:
                recovery_score += 0.2
            
            # Close to support = higher recovery probability
            if support_distance < 0.05:
                recovery_score += 0.1
            
            # Not too deep in loss = higher recovery probability
            if pnl_pct > -0.1:  # Less than 10% loss
                recovery_score += 0.1
            
            return min(max(recovery_score, 0), 1.0)
            
        except Exception as e:
            logger.debug(f"Recovery probability analysis failed for {symbol}: {e}")
            return 0.5
    
    def _calculate_sell_score(self, factors: Dict[str, float], pnl_pct: float) -> float:
        """Calculate overall sell score from all factors"""
        weighted_score = 0.0
        total_weight = 0.0
        
        for factor, value in factors.items():
            if factor in self.weights:
                weighted_score += value * self.weights[factor]
                total_weight += self.weights[factor]
        
        base_score = weighted_score / total_weight if total_weight > 0 else 0.5
        
        # Adjust based on P&L
        if pnl_pct > 0.1:  # More than 10% profit
            base_score *= 0.7  # Reduce sell probability for big profits
        elif pnl_pct > 0.05:  # 5-10% profit
            base_score *= 0.8
        elif pnl_pct < -0.15:  # More than 15% loss
            base_score *= 1.3  # Increase sell probability for big losses
        elif pnl_pct < -0.1:  # 10-15% loss
            base_score *= 1.2
        
        return min(max(base_score, 0), 1.0)
    
    def _make_decision(self, sell_score: float, factors: Dict[str, float],
                      pnl_pct: float, position: Position) -> SellDecision:
        """Make final sell decision based on score and context"""
        
        # Decision thresholds
        if sell_score > 0.75:
            recommendation = 'SELL'
            should_sell = True
        elif sell_score > 0.6:
            recommendation = 'REDUCE_PARTIAL'
            should_sell = True
        else:
            recommendation = 'HOLD'
            should_sell = False
        
        # Special cases for profitable positions
        if pnl_pct > 0.15 and sell_score < 0.8:
            # Let big winners run
            recommendation = 'HOLD'
            should_sell = False
            confidence = 1.0 - sell_score
            reason = "Strong profit position - letting winner run"
        
        # Special cases for losing positions
        elif pnl_pct < -0.2 and sell_score > 0.4:
            # Cut losses early
            recommendation = 'SELL'
            should_sell = True
            confidence = sell_score
            reason = "Significant loss with poor recovery prospects"
        
        else:
            confidence = sell_score if should_sell else (1.0 - sell_score)
            
            # Build detailed reason
            reasons = []
            for factor, value in factors.items():
                if value > 0.7:
                    reasons.append(f"High {factor.replace('_', ' ')}")
                elif value < 0.3:
                    reasons.append(f"Low {factor.replace('_', ' ')}")
            
            reason = f"Score: {sell_score:.2f} - " + ", ".join(reasons[:3])
        
        return SellDecision(
            should_sell=should_sell,
            confidence=confidence,
            reason=reason,
            factors=factors,
            recommendation=recommendation
        )
