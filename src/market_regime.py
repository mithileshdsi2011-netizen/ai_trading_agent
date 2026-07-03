"""
Market Regime Detection
Determines whether the broader market is in BULL, BEAR, or SIDEWAYS regime
using NIFTY 50 daily moving averages (50 DMA and 200 DMA).
"""
import logging
from typing import Dict, Optional

from market_data import MarketDataFetcher

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class MarketRegimeDetector:
    """Detects market regime (bull/bear/sideways)"""

    def __init__(self, kite=None):
        self.market_data = MarketDataFetcher(kite=kite)

    def detect_regime(self) -> str:
        """
        Detect current market regime.

        Returns:
            'BULL', 'BEAR', or 'SIDEWAYS'
        """
        try:
            hist = self.market_data.get_stock_data(
                "NIFTY 50", period="1y", interval="1d"
            )
            if hist.empty or len(hist) < 200:
                logger.warning("Insufficient NIFTY data for regime detection")
                return "SIDEWAYS"

            close = hist["Close"]
            dma50 = close.rolling(window=50).mean().iloc[-1]
            dma200 = close.rolling(window=200).mean().iloc[-1]
            latest = close.iloc[-1]

            if latest > dma50 > dma200:
                regime = "BULL"
            elif latest < dma50 < dma200:
                regime = "BEAR"
            else:
                regime = "SIDEWAYS"

            logger.info(
                f"Market regime: {regime} | NIFTY: {latest:.2f} | "
                f"DMA50: {dma50:.2f} | DMA200: {dma200:.2f}"
            )
            return regime

        except Exception as e:
            logger.error(f"Market regime detection failed: {e}")
            return "SIDEWAYS"

    def get_summary(self) -> Dict:
        """Get regime summary with NIFTY levels"""
        regime = self.detect_regime()
        try:
            hist = self.market_data.get_stock_data(
                "NIFTY 50", period="1y", interval="1d"
            )
            close = hist["Close"]
            return {
                "regime": regime,
                "nifty": round(close.iloc[-1], 2),
                "dma50": round(close.rolling(window=50).mean().iloc[-1], 2),
                "dma200": round(close.rolling(window=200).mean().iloc[-1], 2),
            }
        except Exception:
            return {"regime": regime, "nifty": 0, "dma50": 0, "dma200": 0}
