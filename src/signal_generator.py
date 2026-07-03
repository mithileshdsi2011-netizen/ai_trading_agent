"""
Signal Generator Module
Generates trading signals based on AI research and risk parameters
"""
from typing import Dict, List, Optional
import logging
from datetime import datetime

from ai_research_agent import AIResearchAgent
from risk_manager import RiskManager
from config import config

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class SignalGenerator:
    """Generates trading signals with entry, exit, and risk parameters"""
    
    def __init__(self):
        self.research_agent = AIResearchAgent()
        self._market_data = self.research_agent.market_data
    
    def generate_signal(self, symbol: str) -> Dict:
        """
        Generate complete trading signal for a stock
        
        Args:
            symbol: Stock symbol
        
        Returns:
            Dictionary with complete trading signal
        """
        logger.info(f"Generating signal for {symbol}")
        
        # Get research
        research = self.research_agent.research_stock(symbol)
        
        if research['recommendation'] in ['NO_DATA', 'ERROR']:
            return {
                'symbol': symbol,
                'action': 'SKIP',
                'reason': research.get('reason', 'Insufficient data'),
                'timestamp': datetime.now().isoformat()
            }
        
        # Get current price (reuse shared MarketDataFetcher)
        current_price = self._market_data.get_realtime_price(symbol)
        
        if not current_price:
            return {
                'symbol': symbol,
                'action': 'SKIP',
                'reason': 'Could not get current price',
                'timestamp': datetime.now().isoformat()
            }

        # Fetch ATR for dynamic SL and volatility-based position sizing
        atr = 0.0
        try:
            hist = self._market_data.get_stock_data(symbol, period="1mo", interval="1d")
            if not hist.empty:
                atr = RiskManager.calculate_atr(hist)
        except Exception:
            pass
        
        # Calculate position size based on risk
        position_size = self._calculate_position_size(current_price)
        
        # Calculate stop loss and target
        stop_loss, target = self._calculate_risk_parameters(
            current_price,
            research['technical_analysis'].get('support', 0),
            research['technical_analysis'].get('resistance', 0),
            research['recommendation']
        )
        
        # Determine action
        action = self._determine_action(research['recommendation'])
        
        signal = {
            'symbol': symbol,
            'action': action,
            'current_price': current_price,
            'position_size': position_size,
            'investment_amount': position_size * current_price,
            'stop_loss': stop_loss,
            'target': target,
            'atr': atr,
            'risk_reward_ratio': self._calculate_risk_reward(current_price, stop_loss, target),
            'confidence': research['confidence'],
            'overall_score': research['overall_score'],
            'reasoning': research['reasoning'],
            'trend': research.get('technical_analysis', {}).get('trend', 'NEUTRAL'),
            'timestamp': datetime.now().isoformat(),
            '_research': research,  # full research for TradeScorer + news filter
        }
        
        logger.info(f"Signal generated for {symbol}: {action} at {current_price}")
        return signal
    
    def _calculate_position_size(self, current_price: float) -> int:
        """
        Calculate position size based on risk parameters
        
        Args:
            current_price: Current stock price
        
        Returns:
            Number of shares to buy
        """
        # Calculate maximum investment per trade
        max_investment = config.TRADING_AMOUNT / config.MAX_POSITIONS
        
        # Calculate shares based on price
        shares = int(max_investment / current_price)
        
        # Ensure at least 1 share
        return max(1, shares)
    
    def _calculate_risk_parameters(
        self, 
        current_price: float, 
        support: float, 
        resistance: float, 
        recommendation: str
    ) -> tuple:
        """
        Calculate stop loss and target prices
        
        Args:
            current_price: Current stock price
            support: Support level
            resistance: Resistance level
            recommendation: Trading recommendation
        
        Returns:
            Tuple of (stop_loss, target)
        """
        # Use swing or intraday parameters based on trading mode
        if config.TRADING_MODE == "swing":
            sl_pct = config.SWING_STOP_LOSS_PERCENTAGE
            tgt_pct = config.SWING_TARGET_PERCENTAGE
        else:
            sl_pct = config.STOP_LOSS_PERCENTAGE
            tgt_pct = config.TARGET_PERCENTAGE
        
        if recommendation in ['BUY', 'STRONG_BUY']:
            # For long positions - use percentage-based SL for consistent R:R
            # Support levels are often too tight for intraday, causing poor R:R
            stop_loss = current_price * (1 - sl_pct)

            # Use resistance if it provides better upside than percentage target
            pct_target = current_price * (1 + tgt_pct)
            if resistance > 0 and resistance > current_price and resistance > pct_target:
                target = resistance
            else:
                target = pct_target
        
        else:  # SELL or STRONG_SELL
            # For short positions (not implementing for now, focusing on long)
            stop_loss = current_price * (1 + sl_pct)
            target = current_price * (1 - tgt_pct)
        
        return stop_loss, target
    
    def _calculate_risk_reward(self, entry: float, stop_loss: float, target: float) -> float:
        """
        Calculate risk-reward ratio
        
        Args:
            entry: Entry price
            stop_loss: Stop loss price
            target: Target price
        
        Returns:
            Risk-reward ratio
        """
        risk = abs(entry - stop_loss)
        reward = abs(target - entry)
        
        if risk == 0:
            return 0.0
        
        return reward / risk
    
    def _determine_action(self, recommendation: str) -> str:
        """
        Determine trading action based on recommendation
        
        Args:
            recommendation: Research recommendation
        
        Returns:
            Trading action (BUY, SELL, HOLD)
        """
        if recommendation in ['STRONG_BUY', 'BUY']:
            return 'BUY'
        elif recommendation in ['STRONG_SELL', 'SELL']:
            return 'SELL'
        else:
            return 'HOLD'
    
    def generate_signals_for_watchlist(self, symbols: List[str]) -> List[Dict]:
        """
        Generate signals for all stocks in watchlist
        
        Args:
            symbols: List of stock symbols
        
        Returns:
            List of trading signals
        """
        signals = []
        for symbol in symbols:
            try:
                signal = self.generate_signal(symbol)
                if signal.get('action', 'SKIP') != 'SKIP':
                    signals.append(signal)
            except Exception as e:
                logger.error(f"Error generating signal for {symbol}: {e}")
        
        # Sort: BUY before SELL, then by confidence × overall_score × clamped R:R
        def _rank(s):
            action_rank = 1 if s.get('action') == 'BUY' else 0
            rr = min(s.get('risk_reward_ratio', 0), 5.0)   # cap to avoid outlier dominance
            return (action_rank, s.get('confidence', 0) * s.get('overall_score', 0) * (1 + rr))
        signals.sort(key=_rank, reverse=True)
        
        return signals
    
    def get_best_signal(self, symbols: List[str]) -> Optional[Dict]:
        """
        Get the best trading signal from watchlist
        
        Args:
            symbols: List of stock symbols
        
        Returns:
            Best signal or None
        """
        signals = self.generate_signals_for_watchlist(symbols)
        
        if not signals:
            return None
        
        # Return the top signal
        return signals[0]
