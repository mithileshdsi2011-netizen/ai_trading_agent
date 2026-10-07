"""
Intraday Market Scanner Module

Scans market for intraday trading opportunities.
Completely separate from Swing Trading scanner.
"""
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime

from .models import IntradayConfig, MarketRegime
from .features import IntradayFeatures

logger = logging.getLogger(__name__)


class IntradayScanner:
    """Scans market for intraday opportunities."""
    
    def __init__(self, market_data, instruments, config: IntradayConfig):
        """
        Initialize scanner.
        
        Args:
            market_data: AngelMarketData instance
            instruments: AngelInstruments instance
            config: IntradayConfig instance
        """
        self.market_data = market_data
        self.instruments = instruments
        self.config = config
        self.features = IntradayFeatures()
        self._last_scan_time = None
    
    def scan_universe(self) -> List[Dict[str, Any]]:
        """
        Scan the configured trading universe.
        
        Returns:
            List of symbols with basic market data
        """
        try:
            logger.info(f"[INTRADAY SCANNER] Scanning universe: {self.config.trading_universe}")
            
            # Get symbols based on universe
            if self.config.trading_universe == "NIFTY50":
                symbols = self.instruments.get_nifty50_symbols()
            else:
                symbols = self.instruments.get_all_symbols()
            
            # Fetch quotes for all symbols
            quotes = self.market_data.get_quotes(symbols)
            
            # Build scan results
            results = []
            for symbol in symbols:
                quote = quotes.get(symbol, {})
                if quote:
                    results.append({
                        'symbol': symbol,
                        'price': quote.get('ltp', 0),
                        'volume': quote.get('volume', 0),
                        'change': quote.get('change', 0),
                        'change_pct': quote.get('change_percent', 0)
                    })
            
            self._last_scan_time = datetime.now()
            logger.info(f"[INTRADAY SCANNER] Scanned {len(results)} symbols")
            
            return results
            
        except Exception as e:
            logger.error(f"[INTRADAY SCANNER] Scan error: {e}")
            return []
    
    def get_symbol_data(self, symbol: str) -> Optional[Dict[str, Any]]:
        """
        Get detailed data for a symbol including features.
        
        Args:
            symbol: Trading symbol
            
        Returns:
            Dict with symbol data and features
        """
        try:
            # Get instrument
            instrument = self.instruments.get_instrument(symbol)
            if not instrument:
                logger.warning(f"[INTRADAY SCANNER] Instrument not found: {symbol}")
                return None
            
            # Get historical data for features
            symbol_token = instrument.symbol_token
            candles = self.market_data.get_historical_candles(
                symbol_token=symbol_token,
                interval="ONE_MINUTE",
                days=5
            )
            
            if candles is None or candles.empty:
                logger.warning(f"[INTRADAY SCANNER] No historical data for {symbol}")
                return None
            
            # Calculate features
            features = self.features.calculate_all_features(candles, self.config)
            
            # Get current quote
            quote = self.market_data.get_quote(symbol)
            
            return {
                'symbol': symbol,
                'quote': quote,
                'features': features,
                'candles': candles
            }
            
        except Exception as e:
            logger.error(f"[INTRADAY SCANNER] Symbol data error for {symbol}: {e}")
            return None
    
    def detect_market_regime(self, index_symbol: str = "NIFTY 50") -> MarketRegime:
        """
        Detect current market regime from index data.

        Uses today's index candles for the configured benchmark:
          - price vs VWAP and day change drive BULLISH / BEARISH
          - an unusually wide intraday range flags HIGH_VOLATILITY
          - otherwise SIDEWAYS

        Falls back to SIDEWAYS when index data is unavailable.

        Args:
            index_symbol: Index symbol to analyze

        Returns:
            MarketRegime enum
        """
        try:
            logger.info(f"[INTRADAY SCANNER] Detecting market regime for {index_symbol}")

            index_token = None
            if hasattr(self.instruments, 'get_index_token'):
                index_token = self.instruments.get_index_token(index_symbol)
            if not index_token:
                logger.warning(f"[INTRADAY SCANNER] No index token for {index_symbol} — defaulting to SIDEWAYS")
                return MarketRegime.SIDEWAYS

            candles = self.market_data.get_historical_candles(
                symbol_token=index_token,
                interval="FIFTEEN_MINUTE",
                days=1
            )

            if candles is None or candles.empty:
                logger.warning(f"[INTRADAY SCANNER] No index candles for {index_symbol} — defaulting to SIDEWAYS")
                return MarketRegime.SIDEWAYS

            today = candles[candles.index.date == candles.index[-1].date()]
            if today.empty:
                today = candles.tail(25)  # fallback: last session's candles

            day_open = float(today['open'].iloc[0])
            price = float(today['close'].iloc[-1])
            day_high = float(today['high'].max())
            day_low = float(today['low'].min())

            vwap = self.features.calculate_vwap(today)
            change_pct = ((price - day_open) / day_open) * 100 if day_open else 0.0
            range_pct = ((day_high - day_low) / day_open) * 100 if day_open else 0.0

            # High volatility takes precedence over direction
            if range_pct >= 1.5:
                logger.info(f"[INTRADAY SCANNER] Regime=HIGH_VOLATILITY (range {range_pct:.2f}%)")
                return MarketRegime.HIGH_VOLATILITY

            above_vwap = vwap and price > vwap
            below_vwap = vwap and price < vwap

            if change_pct >= 0.4 and above_vwap:
                regime = MarketRegime.BULLISH
            elif change_pct <= -0.4 and below_vwap:
                regime = MarketRegime.BEARISH
            else:
                regime = MarketRegime.SIDEWAYS

            logger.info(
                f"[INTRADAY SCANNER] Regime={regime.value} "
                f"(chg {change_pct:+.2f}%, range {range_pct:.2f}%, "
                f"price vs VWAP {'above' if above_vwap else 'below' if below_vwap else 'at'})"
            )
            return regime

        except Exception as e:
            logger.error(f"[INTRADAY SCANNER] Regime detection error: {e}")
            return MarketRegime.SIDEWAYS
    
    def filter_by_liquidity(self, scan_results: List[Dict[str, Any]], 
                           min_volume: int = 100000) -> List[Dict[str, Any]]:
        """
        Filter scan results by liquidity.
        
        Args:
            scan_results: Scan results
            min_volume: Minimum volume threshold
            
        Returns:
            Filtered results
        """
        filtered = [r for r in scan_results if r.get('volume', 0) >= min_volume]
        
        logger.info(f"[INTRADAY SCANNER] Liquidity filter: {len(scan_results)} -> {len(filtered)}")
        
        return filtered
    
    def get_last_scan_time(self) -> Optional[datetime]:
        """Get last scan time."""
        return self._last_scan_time
