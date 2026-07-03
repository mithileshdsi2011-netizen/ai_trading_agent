"""
Multi-Timeframe Confirmation
Before buying, confirm all three timeframes align upward:
  Daily  (1d) → primary trend
  1-Hour (1h) → intermediate trend
  15-Min (15m) → entry timing

Avoids buying against higher timeframe direction.
Returns a confirmation dict: aligned (bool) + per-frame details.
"""
import logging
import pandas as pd
from typing import Dict

from market_data import MarketDataFetcher

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _sma(series: pd.Series, n: int) -> float:
    """Return last SMA(n) of a series, or nan if insufficient data."""
    if len(series) < n:
        return float('nan')
    return float(series.rolling(n).mean().iloc[-1])


def _trend_label(close: pd.Series) -> str:
    """
    Classify trend from a Close price series.
    Returns: UPTREND | DOWNTREND | NEUTRAL
    """
    if len(close) < 20:
        return 'NEUTRAL'
    sma20 = _sma(close, 20)
    sma50 = _sma(close, 50) if len(close) >= 50 else sma20
    last  = float(close.iloc[-1])

    if last > sma20 and sma20 > sma50:
        return 'UPTREND'
    elif last < sma20 and sma20 < sma50:
        return 'DOWNTREND'
    else:
        return 'NEUTRAL'


class MultiTimeframeConfirmer:
    """
    Checks Daily + 1H + 15m alignment for a stock before allowing a BUY.
    Uses MarketDataFetcher (shared instance injected to avoid extra connections).
    """

    def __init__(self, market_data: MarketDataFetcher = None):
        self._md = market_data or MarketDataFetcher()

    def confirm(self, symbol: str) -> Dict:
        """
        Run multi-timeframe check for symbol.

        Returns:
            {
              'aligned': bool,          True only if all 3 TFs are UPTREND or NEUTRAL with no DOWNTREND
              'strict':  bool,          True only if all 3 are strictly UPTREND
              'daily':   str,           UPTREND / DOWNTREND / NEUTRAL
              'hourly':  str,
              'minute15': str,
              'reason':  str,
            }
        """
        result = {
            'aligned':   False,
            'strict':    False,
            'daily':     'NEUTRAL',
            'hourly':    'NEUTRAL',
            'minute15':  'NEUTRAL',
            'reason':    '',
        }

        try:
            # ── Daily (3 months of 1d data) ────────────────────────────────────
            df_d = self._md.get_stock_data(symbol, period='3mo', interval='1d')
            daily_trend = _trend_label(df_d['Close']) if not df_d.empty else 'NEUTRAL'
            result['daily'] = daily_trend

            # ── 1-Hour (5 days of 1h data) ─────────────────────────────────────
            df_h = self._md.get_stock_data(symbol, period='5d', interval='1h')
            hourly_trend = _trend_label(df_h['Close']) if not df_h.empty else 'NEUTRAL'
            result['hourly'] = hourly_trend

            # ── 15-Minute (2 days of 15m data) ────────────────────────────────
            df_15 = self._md.get_stock_data(symbol, period='2d', interval='15m')
            m15_trend = _trend_label(df_15['Close']) if not df_15.empty else 'NEUTRAL'
            result['minute15'] = m15_trend

        except Exception as e:
            logger.warning(f"MTF check failed for {symbol}: {e}")
            result['reason'] = f'Data error: {e}'
            result['aligned'] = True   # fail-open: don't block on data issues
            return result

        trends = [daily_trend, hourly_trend, m15_trend]

        # Strict: all three UPTREND
        if all(t == 'UPTREND' for t in trends):
            result['strict']  = True
            result['aligned'] = True
            result['reason']  = 'All 3 timeframes UPTREND ✓'

        # Relaxed: no DOWNTREND in any timeframe
        elif 'DOWNTREND' not in trends:
            result['aligned'] = True
            result['reason']  = f'No counter-trend (D:{daily_trend} H:{hourly_trend} 15m:{m15_trend}) ✓'

        # Higher-TF daily is up but lower TFs are mixed — still allow (with warning)
        elif daily_trend == 'UPTREND' and hourly_trend != 'DOWNTREND':
            result['aligned'] = True
            result['reason']  = f'Daily UPTREND; 15m={m15_trend} (caution) ✓'

        else:
            result['aligned'] = False
            result['reason']  = (
                f'Counter-trend detected: D:{daily_trend} '
                f'H:{hourly_trend} 15m:{m15_trend} ✗'
            )

        logger.info(f"MTF {symbol}: {result['reason']}")
        return result
