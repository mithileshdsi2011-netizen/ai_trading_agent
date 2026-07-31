"""
Trade Journal
Automatically logs every completed trade with full context.
Persists to data/trade_journal.json for lifetime analytics.
"""
import json
import os
import logging
import threading
from datetime import datetime, date
from typing import Dict, List, Optional

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

JOURNAL_FILE = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), 'data', 'trade_journal.json'
)


class TradeJournal:
    """Auto-logs every trade with full context for post-trade analysis."""

    # Class-level lock: prevents concurrent write corruption from parallel threads
    _file_lock = threading.Lock()

    def __init__(self, path: str = JOURNAL_FILE):
        self._path = path
        os.makedirs(os.path.dirname(self._path), exist_ok=True)
        self._backfill_attribution()

    def _backfill_attribution(self):
        """One-time idempotent migration: compute attribution for closed trades without it."""
        try:
            entries = self._load()
            backfilled = 0
            with self._file_lock:
                for e in entries:
                    if e.get('action') == 'BUY' and e.get('status') == 'CLOSED' and 'attribution' not in e:
                        net_pnl = float(e.get('net_pnl') or 0)
                        e['attribution'] = self._compute_attribution(e, net_pnl)
                        backfilled += 1
                if backfilled:
                    self._save(entries)
                    logger.info(f"Backfilled attribution for {backfilled} historical closed trade(s)")
        except Exception as e:
            logger.error(f"Attribution backfill failed: {e}")

    # ── Persistence ───────────────────────────────────────────────────────────

    def _load(self) -> List[Dict]:
        try:
            if os.path.exists(self._path):
                with open(self._path, 'r') as f:
                    return json.load(f)
        except Exception as e:
            logger.error(f"Journal load error: {e}")
        return []

    @staticmethod
    def _compute_attribution(buy_entry: Dict, net_pnl: float) -> Dict:
        """Derive strategy attribution from a closed BUY record."""
        components = buy_entry.get('score_components', {}) or {}
        positive = {k: v for k, v in components.items() if v > 0}
        dominant = max(positive, key=positive.get) if positive else 'none'
        net_pnl = float(net_pnl or 0)
        win = net_pnl > 0

        rsi = float(buy_entry.get('rsi', 50) or 0)
        if rsi < 30:
            rsi_bucket = '<30'
        elif rsi < 40:
            rsi_bucket = '30-40'
        elif rsi <= 60:
            rsi_bucket = '40-60'
        elif rsi <= 65:
            rsi_bucket = '60-65'
        elif rsi <= 70:
            rsi_bucket = '65-70'
        else:
            rsi_bucket = '>70'

        vol_ratio = float(buy_entry.get('volume_ratio', 1.0) or 1.0)
        if vol_ratio >= 2.5:
            volume_bucket = '>=2.5'
        elif vol_ratio >= 1.8:
            volume_bucket = '1.8-2.5'
        elif vol_ratio >= 1.3:
            volume_bucket = '1.3-1.8'
        elif vol_ratio >= 1.0:
            volume_bucket = '1.0-1.3'
        elif vol_ratio >= 0.7:
            volume_bucket = '0.7-1.0'
        else:
            volume_bucket = '<0.7'

        trend = buy_entry.get('trend', 'NEUTRAL')
        regime = buy_entry.get('market_regime', 'UNKNOWN')
        mtf = bool(buy_entry.get('mtf_aligned', False))

        if win:
            reason = f"Win driven by {dominant} and volume confirmation"
        else:
            mtf_note = " with MTF aligned" if mtf else " without MTF alignment"
            reason = f"Loss: {dominant} dominated but failed{mtf_note}"

        return {
            'win': win,
            'net_pnl': round(net_pnl, 2),
            'dominant_component': dominant,
            'trend': trend,
            'regime': regime,
            'mtf_aligned': mtf,
            'rsi_bucket': rsi_bucket,
            'volume_bucket': volume_bucket,
            'why': reason,
        }

    def _factor_stats(self, trades: List[Dict], key_func) -> Dict:
        """Helper: win rate and P&L by an arbitrary grouping key."""
        groups: Dict[str, List[float]] = {}
        for t in trades:
            attr = t.get('attribution', {})
            k = key_func(attr)
            if k is None:
                continue
            groups.setdefault(k, []).append(float(t.get('net_pnl') or 0))
        return {
            k: {
                'trades': len(v),
                'win_rate': round(len([p for p in v if p > 0]) / len(v) * 100, 1) if v else 0,
                'net_pnl': round(sum(v), 2),
            }
            for k, v in groups.items()
        }

    def _save(self, entries: List[Dict]):
        try:
            with open(self._path, 'w') as f:
                json.dump(entries, f, indent=2, default=str)
        except Exception as e:
            logger.error(f"Journal save error: {e}")

    # ── Write ─────────────────────────────────────────────────────────────────

    def log_entry(
        self,
        symbol: str,
        action: str,                        # 'BUY' | 'SELL'
        price: float,
        quantity: int,
        # Context from signal / research
        buy_reason: str = '',
        trade_score: float = 0,
        score_components: Optional[Dict] = None,
        market_regime: str = 'UNKNOWN',
        sector: str = '',
        sentiment: str = 'NEUTRAL',
        sentiment_score: float = 0.0,
        news_count: int = 0,
        # Technical indicators snapshot
        rsi: float = 0,
        macd_histogram: float = 0,
        volume_ratio: float = 1.0,
        trend: str = 'NEUTRAL',
        atr: float = 0,
        mtf_aligned: bool = False,
        mtf_strict: bool = False,
        confidence: float = 0,
        # Re-entry fields (filled when this BUY is a re-entry)
        is_reentry: bool = False,
        prev_exit_reason: str = '',
        prev_pnl: float = 0.0,
        time_since_exit_hours: float = 0.0,
        reentry_score: float = 0.0,
        reentry_confidence: float = 0.0,
        # Exit fields (filled on SELL)
        exit_reason: str = '',
        entry_price: float = 0,
        entry_date: str = '',
        gross_pnl: float = 0,
        net_pnl: float = 0,
        charges: float = 0,
    ) -> Dict:
        today = datetime.now().strftime('%Y-%m-%d')
        now   = datetime.now().isoformat(timespec='seconds')
        _dow  = datetime.now().strftime('%A')
        _week = int(datetime.now().strftime('%V'))
        _mon  = datetime.now().strftime('%B')

        with TradeJournal._file_lock:
            entries = self._load()

            if action == 'BUY':
                entry = {
                    'id':              len(entries) + 1,
                    'date':            today,
                    'timestamp':       now,
                    'symbol':          symbol,
                    'action':          'BUY',
                    'entry_price':     round(price, 2),
                    'quantity':        quantity,
                    'invested':        round(price * quantity, 2),
                    'buy_reason':      buy_reason,
                    'trade_score':     round(trade_score, 1),
                    'score_components': score_components or {},
                    'market_regime':   market_regime,
                    'sector':          sector,
                    'sentiment':       sentiment,
                    'sentiment_score': round(sentiment_score, 3),
                    'news_count':      news_count,
                    'rsi':             round(rsi, 1),
                    'macd_histogram':  round(macd_histogram, 4),
                    'volume_ratio':    round(volume_ratio, 2),
                    'trend':           trend,
                    'atr':             round(atr, 2),
                    'mtf_aligned':     mtf_aligned,
                    'mtf_strict':      mtf_strict,
                    'confidence':      round(confidence, 3),
                    'is_reentry':            is_reentry,
                    'prev_exit_reason':      prev_exit_reason,
                    'prev_pnl':              round(prev_pnl, 2),
                    'time_since_exit_hours': round(time_since_exit_hours, 2),
                    'reentry_score':         round(reentry_score, 1),
                    'reentry_confidence':    round(reentry_confidence, 3),
                    'exit_price':      None,
                    'exit_date':       None,
                    'holding_days':    None,
                    'exit_reason':     None,
                    'gross_pnl':       None,
                    'net_pnl':         None,
                    'charges':         None,
                    'status':          'OPEN',
                    'day_of_week':     _dow,
                    'week_number':     _week,
                    'month':           _mon,
                }
            else:
                # Find the original BUY date from existing journal entries for accurate holding_days
                _buy_entry = next(
                    (e for e in entries if e.get('symbol') == symbol
                     and e.get('action') == 'BUY' and e.get('status') == 'OPEN'),
                    None
                )
                if _buy_entry and _buy_entry.get('date'):
                    try:
                        _buy_dt = datetime.fromisoformat(_buy_entry['date'])
                        holding_days = (datetime.now() - _buy_dt).days
                    except Exception:
                        holding_days = 0
                elif entry_date:
                    try:
                        entry_date_dt = datetime.fromisoformat(entry_date)
                        holding_days = (datetime.now() - entry_date_dt).days
                    except Exception:
                        holding_days = 0
                else:
                    holding_days = 0
                entry = {
                    'id':             len(entries) + 1,
                    'date':           today,
                    'timestamp':      now,
                    'symbol':         symbol,
                    'action':         'SELL',
                    'entry_price':    round(entry_price, 2) if entry_price else 0,
                    'exit_price':     round(price, 2),
                    'quantity':       quantity,
                    'invested':       round(entry_price * quantity, 2) if entry_price else 0,
                    'entry_date':     entry_date,
                    'exit_date':      today,
                    'holding_days':   holding_days,
                    'exit_reason':    exit_reason,
                    'gross_pnl':      round(gross_pnl, 2),
                    'net_pnl':        round(net_pnl, 2),
                    'charges':        round(charges, 2),
                    'market_regime':  market_regime,
                    'sector':         sector,
                    'buy_reason':     buy_reason,
                    'trade_score':    round(trade_score, 1),
                    'confidence':     round(confidence, 3),
                    'status':         'CLOSED',
                    'day_of_week':    _dow,
                    'week_number':    _week,
                    'month':          _mon,
                }
                # Update the matching open BUY record so we have one complete row
                for e in entries:
                    if (e.get('symbol') == symbol
                            and e.get('action') == 'BUY'
                            and e.get('status') == 'OPEN'):
                        e['exit_price']   = entry['exit_price']
                        e['exit_date']    = today
                        e['holding_days'] = holding_days
                        e['exit_reason']  = exit_reason
                        e['gross_pnl']    = entry['gross_pnl']
                        e['net_pnl']      = entry['net_pnl']
                        e['charges']      = entry['charges']
                        e['status']       = 'CLOSED'
                        e['attribution']  = self._compute_attribution(e, float(entry['net_pnl'] or 0))
                        break

            entries.append(entry)
            self._save(entries)

        logger.info(f"Journal: logged {action} {symbol} @ ₹{price} (score={trade_score})")
        return entry

    # ── Read / Analytics ──────────────────────────────────────────────────────

    def all_entries(self) -> List[Dict]:
        return self._load()

    def closed_trades(self) -> List[Dict]:
        return [e for e in self._load()
                if e.get('status') == 'CLOSED' and e.get('action') == 'BUY']

    def attribution_report(self) -> Dict:
        """Win rate and P&L broken down by the factors that drove each trade."""
        trades = self.closed_trades()
        if not trades:
            return {'total_trades': 0}

        return {
            'total_trades': len(trades),
            'by_mtf': self._factor_stats(trades, lambda a: f"mtf_{'aligned' if a.get('mtf_aligned') else 'not_aligned'}"),
            'by_rsi_bucket': self._factor_stats(trades, lambda a: a.get('rsi_bucket')),
            'by_volume_bucket': self._factor_stats(trades, lambda a: a.get('volume_bucket')),
            'by_trend': self._factor_stats(trades, lambda a: a.get('trend')),
            'by_regime': self._factor_stats(trades, lambda a: a.get('regime')),
            'by_dominant_component': self._factor_stats(trades, lambda a: a.get('dominant_component')),
        }

    def analytics(self) -> Dict:
        """Compute all analytics over closed trades for the journal tab."""
        trades = self.closed_trades()
        if not trades:
            return {'total_trades': 0}

        pnls        = [float(t.get('net_pnl') or 0) for t in trades]
        scores      = [float(t['trade_score']) for t in trades if t.get('trade_score') is not None]
        wins        = [p for p in pnls if p > 0]
        losses      = [p for p in pnls if p < 0]
        total       = len(trades)
        win_rate    = len(wins) / total if total else 0
        avg_win     = sum(wins) / len(wins) if wins else 0
        avg_loss    = abs(sum(losses) / len(losses)) if losses else 0
        pf          = sum(wins) / abs(sum(losses)) if losses else 0
        total_net   = sum(pnls)
        avg_score   = sum(scores) / len(scores) if scores else 0
        avg_hold    = sum(float(t.get('holding_days') or 1) for t in trades) / total

        # By sector
        by_sector: Dict[str, List[float]] = {}
        for t in trades:
            sec = t.get('sector', 'Unknown') or 'Unknown'
            by_sector.setdefault(sec, []).append(t.get('net_pnl', 0))
        sector_stats = {
            sec: {
                'trades': len(v),
                'net_pnl': round(sum(v), 2),
                'win_rate': round(len([x for x in v if x > 0]) / len(v) * 100, 1) if v else 0
            }
            for sec, v in by_sector.items()
        }

        # By day of week
        by_dow: Dict[str, List[float]] = {}
        for t in trades:
            dow = t.get('day_of_week', 'Unknown')
            by_dow.setdefault(dow, []).append(t.get('net_pnl', 0))
        dow_stats = {
            dow: {
                'trades': len(v),
                'net_pnl': round(sum(v), 2),
                'win_rate': round(len([x for x in v if x > 0]) / len(v) * 100, 1) if v else 0
            }
            for dow, v in by_dow.items()
        }

        # By exit reason
        by_exit: Dict[str, List[float]] = {}
        for t in trades:
            reason = t.get('exit_reason', 'Unknown') or 'Unknown'
            # Simplify reason label
            label = (
                'Target Hit'         if 'target' in reason.lower() else
                'Stop Loss'          if 'stop' in reason.lower() or 'sl' in reason.lower() else
                'Partial Profit'     if 'partial' in reason.lower() else
                'Smart Exit'         if 'smart' in reason.lower() or 'rsi' in reason.lower()
                                        or 'macd' in reason.lower() or 'volume' in reason.lower() else
                'Weekly Rebalance'   if 'rebalance' in reason.lower() else
                'EOD Close'          if 'end of day' in reason.lower() or 'eod' in reason.lower() else
                'Circuit Breaker'    if 'circuit' in reason.lower() or 'breaker' in reason.lower() else
                'Manual'
            )
            by_exit.setdefault(label, []).append(t.get('net_pnl', 0))
        exit_stats = {
            label: {
                'trades': len(v),
                'net_pnl': round(sum(v), 2),
                'avg_pnl': round(sum(v) / len(v), 2) if v else 0
            }
            for label, v in by_exit.items()
        }

        # By regime
        by_regime: Dict[str, List[float]] = {}
        for t in trades:
            r = t.get('market_regime', 'UNKNOWN')
            by_regime.setdefault(r, []).append(t.get('net_pnl', 0))
        regime_stats = {
            r: {
                'trades': len(v),
                'net_pnl': round(sum(v), 2),
                'win_rate': round(len([x for x in v if x > 0]) / len(v) * 100, 1) if v else 0
            }
            for r, v in by_regime.items()
        }

        # Score bucket performance
        score_buckets = {'<70': [], '70-80': [], '80-90': [], '90+': []}
        for t in trades:
            sc = t.get('trade_score', 0)
            if sc >= 90:   score_buckets['90+'].append(t.get('net_pnl', 0))
            elif sc >= 80: score_buckets['80-90'].append(t.get('net_pnl', 0))
            elif sc >= 70: score_buckets['70-80'].append(t.get('net_pnl', 0))
            else:          score_buckets['<70'].append(t.get('net_pnl', 0))
        score_stats = {
            b: {
                'trades': len(v),
                'net_pnl': round(sum(v), 2),
                'win_rate': round(len([x for x in v if x > 0]) / len(v) * 100, 1) if v else 0
            }
            for b, v in score_buckets.items() if v
        }

        # Cumulative P&L curve (last 50 trades)
        cumulative = []
        running = 0
        for t in trades[-50:]:
            running += float(t.get('net_pnl') or 0)
            cumulative.append({'date': t.get('exit_date', ''), 'cumulative_pnl': round(running, 2)})

        return {
            'total_trades':  total,
            'win_rate':      round(win_rate, 4),
            'avg_win':       round(avg_win, 2),
            'avg_loss':      round(avg_loss, 2),
            'profit_factor': round(pf, 2),
            'total_net_pnl': round(total_net, 2),
            'net_pnl':       round(total_net, 2),
            'avg_score':     round(avg_score, 1),
            'avg_hold_days': round(avg_hold, 1),
            'by_sector':     sector_stats,
            'by_day_of_week': dow_stats,
            'by_exit_reason': exit_stats,
            'by_regime':     regime_stats,
            'by_score_bucket': score_stats,
            'cumulative_pnl': cumulative,
            'recent_trades': trades[-20:][::-1],   # last 20 reversed (newest first)
        }
