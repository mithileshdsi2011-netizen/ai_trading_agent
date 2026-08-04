"""
Smart Exit AI
Replaces naive "sell after N days" with intelligent exit triggers:

  1. Target hit                    (handled by risk_manager)
  2. Trailing stop triggered        (handled by risk_manager)
  3. RSI overbought (>80)
  4. Bearish MACD crossover (histogram turns negative after being positive)
  5. Volume collapse (<40% of 20d avg) → momentum dying
  6. Market regime turns BEAR
  7. Bearish candlestick pattern (shooting star / bearish engulfing)

Returns an exit recommendation dict for each open position.
Called from order_executor.monitor_positions() every cycle.
"""
import logging
import pandas as pd
from typing import Dict, List, Optional

from market_data import MarketDataFetcher
from risk_manager import Position, PositionStatus
from config import config

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class SmartExitAI:
    """
    Evaluates each open position against smart exit criteria.
    Returns exit signals that order_executor will act on.
    """

    def __init__(self, market_data: MarketDataFetcher = None):
        self._md = market_data or MarketDataFetcher()

    def check_position(
        self,
        position: Position,
        current_price: float,
        regime: str = 'SIDEWAYS',
    ) -> Optional[Dict]:
        """
        Evaluate one position.  Returns an exit_signal dict if any trigger fires,
        or None if position should be held.

        exit_signal keys: symbol, action, price, quantity, reason, trigger, pnl, pnl_percentage
        """
        if position.status not in {PositionStatus.OPEN, PositionStatus.PARTIAL}:
            return None

        # Minimum Profit Rule: SmartExit is not a stop-loss; avoid tiny/noisy gains
        min_profit_pct = config.SMARTEXIT_MIN_PROFIT_PCT
        unrealized_pct = (current_price - position.entry_price) / position.entry_price
        if unrealized_pct < min_profit_pct:
            logger.debug(
                f"SmartExit suppressed for {position.symbol}: unrealized {unrealized_pct*100:.2f}% "
                f"below {min_profit_pct*100:.2f}% minimum"
            )
            return None

        symbol = position.symbol
        triggers_hit: List[str] = []

        # ── Fetch fresh indicator data ─────────────────────────────────────────
        try:
            df_raw = self._md.get_stock_data(symbol, period='1mo', interval='1d')
            if df_raw.empty or len(df_raw) < 10:
                return None

            df = df_raw.copy()
            # RSI
            delta = df['Close'].diff()
            gain  = delta.clip(lower=0)
            loss  = (-delta).clip(lower=0)
            avg_g = gain.rolling(14).mean()
            avg_l = loss.rolling(14).mean()
            rs    = avg_g / avg_l.replace(0, 1e-9)
            df['RSI'] = 100 - (100 / (1 + rs))

            # MACD histogram
            ema12 = df['Close'].ewm(span=12, adjust=False).mean()
            ema26 = df['Close'].ewm(span=26, adjust=False).mean()
            macd  = ema12 - ema26
            signal_line = macd.ewm(span=9, adjust=False).mean()
            df['MACD_Hist'] = macd - signal_line

            # Volume SMA
            df['Vol_SMA20'] = df['Volume'].rolling(20).mean()

        except Exception as e:
            logger.warning(f"SmartExit: data fetch failed for {symbol}: {e}")
            return None

        latest = df.iloc[-1]
        prev   = df.iloc[-2] if len(df) >= 2 else latest

        # ── Volume context (used by all price-based triggers) ──────────────────
        vol_sma = latest.get('Vol_SMA20', 0)
        vol_ratio = 0.0
        if pd.notna(vol_sma) and vol_sma > 0:
            vol_ratio = float(latest['Volume']) / float(vol_sma)
        vol_confirmed = vol_ratio >= config.SMARTEXIT_VOLUME_CONFIRMATION_RATIO

        # ── Trigger 1: RSI Overbought ──────────────────────────────────────────
        rsi = latest.get('RSI', 50)
        if pd.notna(rsi) and rsi > 80:
            triggers_hit.append(f"RSI overbought ({rsi:.1f})")

        # ── Trigger 2: Bearish MACD crossover ─────────────────────────────────
        macd_curr = latest.get('MACD_Hist')
        macd_prev = prev.get('MACD_Hist')
        if (macd_curr is not None and macd_prev is not None
                and pd.notna(macd_curr) and pd.notna(macd_prev)
                and float(macd_prev) >= 0 and float(macd_curr) < 0
                and vol_confirmed):
            triggers_hit.append("Bearish MACD crossover")

        # ── Trigger 3: Volume collapse ─────────────────────────────────────────
        if vol_ratio < 0.4:
            triggers_hit.append(f"Volume collapse ({vol_ratio:.2f}x avg)")

        # ── Trigger 4: Market turns BEAR ──────────────────────────────────────
        if str(regime).upper() == 'BEAR':
            pnl_pct = (current_price - position.entry_price) / position.entry_price
            # Only exit if we have profit; if already at loss, SL handles it
            if pnl_pct > 0:
                triggers_hit.append("Market regime turned BEAR")

        # ── Trigger 5: Bearish candlestick (last 2 bars) ──────────────────────
        if len(df) >= 2:
            o1, h1, l1, c1 = (float(prev['Open']),  float(prev['High']),
                               float(prev['Low']),   float(prev['Close']))
            o2, h2, l2, c2 = (float(latest['Open']), float(latest['High']),
                               float(latest['Low']),  float(latest['Close']))

            # Bearish engulfing: prev candle green, current red and body engulfs prev
            if (c1 > o1 and c2 < o2 and o2 >= c1 and c2 <= o1
                    and vol_confirmed):
                triggers_hit.append("Bearish engulfing candle")

            # Shooting star: small body at bottom, long upper wick
            body  = abs(c2 - o2)
            upper = h2 - max(c2, o2)
            lower = min(c2, o2) - l2
            if (body > 0 and upper > 2 * body and lower < body * 0.5
                    and vol_confirmed):
                triggers_hit.append("Shooting star candle")

        if not triggers_hit:
            return None

        # Build exit signal
        pnl     = (current_price - position.entry_price) * position.quantity
        pnl_pct = ((current_price - position.entry_price) / position.entry_price) * 100
        reason  = " | ".join(triggers_hit)

        logger.info(
            f"SmartExit triggered for {symbol}: {reason} "
            f"Price=₹{current_price:.2f} P&L=₹{pnl:.2f} ({pnl_pct:+.2f}%)"
        )
        logger.info(
            f"SELL_PIPELINE | origin=smart_exit.check_position "
            f"| symbol={symbol} | current_price={current_price} "
            f"| entry={position.entry_price} | pnl_pct={pnl_pct:+.2f}% "
            f"| reason='{reason}'"
        )

        return {
            'symbol':          symbol,
            'action':          'SELL',
            'price':           current_price,
            'quantity':        position.quantity,
            'reason':          reason,
            'trigger':         'SMART_EXIT',
            'pnl':             pnl,
            'pnl_percentage':  pnl_pct,
            'partial':         False,
        }

    def check_all(
        self,
        positions: List[Position],
        current_prices: Dict[str, float],
        regime: str = 'SIDEWAYS',
    ) -> List[Dict]:
        """Check all open positions. Returns list of exit signals."""
        exits = []
        for pos in positions:
            if pos.status not in {PositionStatus.OPEN, PositionStatus.PARTIAL}:
                continue
            price = current_prices.get(pos.symbol)
            if not price:
                continue
            sig = self.check_position(pos, price, regime)
            if sig:
                exits.append(sig)
        return exits
