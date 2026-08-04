"""
Signal Generator Module
Generates trading signals based on AI research and risk parameters
"""
from typing import Dict, List, Optional
import logging
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
import time

from ai_research_agent import AIResearchAgent
from risk_manager import RiskManager
from enterprise_ai_decision_engine import EnterpriseAIDecisionEngine
from config import config
from decision_logger import DecisionLogger, create_decision_record

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class SignalGenerator:
    """Generates trading signals with entry, exit, and risk parameters"""
    
    def __init__(self):
        self.research_agent = AIResearchAgent()
        self._market_data = self.research_agent.market_data
        self.decision_logger = DecisionLogger()
        self.decision_engine = EnterpriseAIDecisionEngine(self._market_data)
    
    def generate_signal(self, symbol: str, risk_data: Dict = None) -> Dict:
        """
        Generate complete trading signal for a stock with detailed decision logging
        
        Args:
            symbol: Stock symbol
            risk_data: Risk management data for decision logging
        
        Returns:
            Dictionary with complete trading signal
        """
        logger.info(f"Generating signal for {symbol}")
        
        # Get research
        research = self.research_agent.research_stock(symbol)
        
        # Initialize decision data
        decision_data = {
            'symbol': symbol,
            'timestamp': datetime.now().isoformat(),
            'research': research,
            'final_decision': 'SKIP',
            'rejection_reason': ''
        }
        
        # Handle data errors
        if research['recommendation'] in ['NO_DATA', 'ERROR']:
            decision_data['rejection_reason'] = research.get('reason', 'Insufficient data')
            self._log_decision(decision_data, risk_data)
            return {
                'symbol': symbol,
                'action': 'SKIP',
                'reason': decision_data['rejection_reason'],
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

        # Fetch historical data once for ATR, decision engine and scoring
        hist = None
        try:
            hist = self._market_data.get_stock_data(symbol, period="3mo", interval="1d")
        except Exception:
            hist = None

        # ATR for dynamic SL and volatility-based position sizing
        atr = 0.0
        try:
            if hist is not None and not hist.empty:
                atr = RiskManager.calculate_atr(hist)
        except Exception:
            pass

        # ── Enterprise AI Decision Engine ─────────────────────────────────────
        ai_scores = self.decision_engine.compute_scores(symbol, current_price, hist, research)

        # Calculate position size based on risk
        position_size = self._calculate_position_size(current_price)

        # Calculate stop loss and target
        stop_loss, target = self._calculate_risk_parameters(
            current_price,
            research['technical_analysis'].get('support', 0),
            research['technical_analysis'].get('resistance', 0),
            ai_scores['recommendation']
        )

        # Determine action from the master decision engine
        action = ai_scores['action'] if ai_scores['action'] in ('BUY', 'SELL') else 'HOLD'
        
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
            'confidence': ai_scores['final_confidence'],
            'overall_score': ai_scores['final_score'],
            'threshold': ai_scores['threshold'],
            'reasoning': ai_scores['explain_text'],
            'ai_explain': ai_scores['explain'],
            'sub_scores': ai_scores['sub_scores'],
            'score_components': ai_scores['score_components'],
            'regime': ai_scores.get('regime_threshold'),
            'trend': research.get('technical_analysis', {}).get('trend', 'NEUTRAL'),
            'timestamp': datetime.now().isoformat(),
            '_research': research,  # full research for TradeScorer + news filter
        }
        
        # Log decision for transparency
        decision_data['research'].update({
            'overall_score': ai_scores['final_score'],
            'confidence': ai_scores['final_confidence'],
            'technical_score': ai_scores['sub_scores'].get('technical', 0),
            'news_sentiment_score': research.get('news_sentiment_score', 0),
            'sector_momentum': ai_scores['sub_scores'].get('sector', 0),
            'market_regime': research.get('market_regime', 'UNKNOWN'),
            'detailed_factors': {**ai_scores['sub_scores'], 'threshold': ai_scores['threshold']},
            'sector': research.get('sector', 'Unknown')
        })
        decision_data.update({
            'final_decision': action if action in ['BUY', 'SELL'] else 'SKIP',
            'signal': signal
        })
        self._log_decision(decision_data, risk_data)
        
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
            # but never place the stop below the identified support level.
            stop_loss = current_price * (1 - sl_pct)
            if support > 0:
                stop_loss = max(stop_loss, support)

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
    
    def _log_decision(self, decision_data: Dict, risk_data: Dict = None):
        """Log detailed decision for transparency"""
        try:
            research = decision_data.get('research', {})
            signal = decision_data.get('signal', {})
            
            # Create decision record
            decision_record = create_decision_record(
                symbol=decision_data['symbol'],
                research_data={
                    'overall_score': research.get('overall_score', 0),
                    'confidence': research.get('confidence', 0),
                    'technical_score': research.get('technical_score', 0),
                    'news_sentiment_score': research.get('news_sentiment_score', 0),
                    'sector_strength': research.get('sector_momentum', 0),
                    'market_regime': research.get('market_regime', 'UNKNOWN'),
                    'detailed_factors': research.get('detailed_factors', {}),
                    'sector': research.get('sector', 'Unknown')
                },
                signal_data={
                    'risk_reward_ratio': signal.get('risk_reward_ratio', 0),
                    'position_size': signal.get('position_size', 0),
                    'entry_price': signal.get('current_price', 0),
                    'stop_loss': signal.get('stop_loss', 0),
                    'target': signal.get('target', 0)
                },
                risk_data=risk_data or {
                    'available_cash': 0,
                    'open_positions': [],
                    'holdings': [],
                    'cooldown_status': False,
                    'portfolio_exposure': 0,
                    'max_position_size': 0
                },
                final_decision=decision_data['final_decision'],
                rejection_reason=decision_data.get('rejection_reason', '')
            )
            
            self.decision_logger.log_decision(decision_record)
            
        except Exception as e:
            logger.warning(f"Could not log decision for {decision_data.get('symbol', 'Unknown')}: {e}")
    
    # ── Pre-screening thresholds ──────────────────────────────────────────────
    _SCREEN_RSI_LOW  = 25.0   # oversold floor  (below = skip, likely falling knife)
    _SCREEN_RSI_HIGH = 78.0   # overbought ceil (above = skip, chasing top)
    _SCREEN_MIN_BARS = 22     # minimum daily candles needed for TA
    _SCREEN_MAX_PASS = 60     # max stocks that proceed to full AI analysis

    def _quick_screen(self, symbol: str) -> tuple:
        """
        Fast gate: fetch daily data ONCE and compute RSI + trend.
        Returns (passes: bool, hist: DataFrame | None, rsi: float)
        so the data can be reused in generate_signal, avoiding a second fetch.
        """
        try:
            hist = self._market_data.get_stock_data(symbol, period="3mo", interval="1d")
            if hist is None or hist.empty or len(hist) < self._SCREEN_MIN_BARS:
                return False, None, 0.0

            close = hist['Close']
            volume = hist['Volume'] if 'Volume' in hist.columns else None

            # ── RSI (14) ──────────────────────────────────────────────────────
            delta = close.diff()
            gain  = delta.clip(lower=0).rolling(14).mean()
            loss  = (-delta.clip(upper=0)).rolling(14).mean()
            rsi   = float((100 - 100 / (1 + gain / loss.replace(0, 1e-9))).iloc[-1])

            # ── Trend: price vs 20-DMA ────────────────────────────────────────
            dma20       = float(close.rolling(20).mean().iloc[-1])
            last_price  = float(close.iloc[-1])
            above_dma20 = last_price > dma20 * 0.98    # allow 2% below for near-breakout

            # ── Momentum: last 5 days positive ────────────────────────────────
            momentum_5d = float(close.iloc[-1] / close.iloc[-6] - 1) if len(close) >= 6 else 0.0

            # ── Volume: today > 50% of 10-day avg (avoid dead stocks) ─────────
            if volume is not None and len(volume) >= 10:
                vol_avg = float(volume.iloc[-11:-1].mean())
                vol_ok  = vol_avg > 10_000
            else:
                vol_ok = True

            passes = (
                self._SCREEN_RSI_LOW <= rsi <= self._SCREEN_RSI_HIGH
                and above_dma20
                and momentum_5d > -0.08    # not down >8% in 5 days
                and vol_ok
            )
            return passes, hist, rsi
        except Exception:
            return False, None, 0.0

    # Workers for parallel scan: 8 threads balance throughput vs Kite rate limits
    _SCAN_WORKERS = 8

    def generate_signals_for_watchlist(self, symbols: List[str], risk_data: Dict = None) -> List[Dict]:
        """
        Two-stage pipeline with parallel execution:
          Stage 1 — Parallel quick screen (8 workers): RSI, DMA20, momentum, volume
          Stage 2 — Parallel full AI + TA on survivors (8 workers, ≤60 stocks)

        Reduces wall-clock time from ~90s → ~20-25s on 50 stocks.
        """
        t0 = time.time()

        # ── Stage 1: parallel pre-screen ─────────────────────────────────────
        passed: List[tuple] = []   # (symbol, hist, rsi)
        skipped = 0

        def _screen(sym):
            return sym, *self._quick_screen(sym)

        with ThreadPoolExecutor(max_workers=self._SCAN_WORKERS) as pool:
            futures = {pool.submit(_screen, sym): sym for sym in symbols}
            for fut in as_completed(futures):
                try:
                    sym, ok, hist, rsi = fut.result(timeout=15)
                    if ok:
                        passed.append((sym, hist, rsi))
                    else:
                        skipped += 1
                except Exception as exc:
                    skipped += 1
                    logger.debug(f"Screen error {futures[fut]}: {exc}")

        # Cap at SCREEN_MAX_PASS
        passed = passed[:self._SCREEN_MAX_PASS]

        logger.info(
            f"Pre-screen: {len(passed)} passed / {skipped} skipped "
            f"out of {len(symbols)} in {time.time()-t0:.1f}s → full analysis on {len(passed)}"
        )

        # Warm detailed candles for the shortlist so full signal generation is cache-only
        try:
            _passed_syms = [sym for sym, _, _ in passed]
            self._market_data.prefetch_historical(_passed_syms, '5d', '1h')
            self._market_data.prefetch_historical(_passed_syms, '2d', '15m')
            self._market_data.prefetch_historical(_passed_syms, '5d', '15m')
        except Exception as _pfe:
            logger.warning(f"Detailed prefetch for survivors failed: {_pfe}")

        # ── Stage 2: parallel full signal generation on survivors ─────────────
        signals = []

        def _full_signal(sym):
            return self.generate_signal(sym)

        with ThreadPoolExecutor(max_workers=self._SCAN_WORKERS) as pool:
            futures = {pool.submit(_full_signal, sym): sym for sym, _, _ in passed}
            for fut in as_completed(futures):
                sym = futures[fut]
                try:
                    signal = fut.result(timeout=60)
                    if signal.get('action', 'SKIP') != 'SKIP':
                        signals.append(signal)
                except Exception as exc:
                    logger.error(f"Signal error {sym}: {exc}")

        logger.info(
            f"Signal scan complete: {len(signals)} signals in {time.time()-t0:.1f}s total"
        )

        # Sort: BUY first, then by confidence × overall_score × clamped R:R
        def _rank(s):
            action_rank = 1 if s.get('action') == 'BUY' else 0
            rr = min(s.get('risk_reward_ratio', 0), 5.0)
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
