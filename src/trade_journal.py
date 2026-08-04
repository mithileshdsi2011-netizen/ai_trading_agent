"""
Trade Journal
Automatically logs every completed trade with full context.
Persists to data/trade_journal.json for lifetime analytics.
"""
import os
import logging
import threading
from datetime import datetime, date
from typing import Dict, List, Optional

from persistence import get_store

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
        self._store = get_store()
        os.makedirs(os.path.dirname(self._path), exist_ok=True)
        self._backfill_attribution()

    def _backfill_attribution(self):
        """One-time idempotent migration: compute attribution for closed trades without it."""
        try:
            entries = self._store.all_trades()
            backfilled = 0
            with self._file_lock:
                for e in entries:
                    if e.get('action') == 'BUY' and e.get('status') == 'CLOSED' and 'attribution' not in e:
                        net_pnl = float(e.get('net_pnl') or 0)
                        updates = {'attribution': self._compute_attribution(e, net_pnl)}
                        if 'id' in e and e['id'] is not None:
                            self._store.update_trade(e['id'], updates)
                            backfilled += 1
                if backfilled:
                    logger.info(f"Backfilled attribution for {backfilled} historical closed trade(s)")
        except Exception as e:
            logger.error(f"Attribution backfill failed: {e}")

    # ── Persistence ───────────────────────────────────────────────────────────

    def _load(self) -> List[Dict]:
        try:
            return self._store.all_trades()
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
        """No-op; store persists entries immediately."""
        pass

    # ── Write ─────────────────────────────────────────────────────────────────

    def log_entry(
        self,
        symbol: str,
        action: str,                        # 'BUY' | 'SELL'
        price: float,
        quantity: int,
        order_id: str = '',
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
            if action == 'BUY':
                # If an open position already exists for this symbol, merge the new buy
                # into it (scale-in / duplicate guard) instead of creating another open row.
                existing = self._store.get_trades(symbol=symbol, action='BUY', status='OPEN', limit=1)
                existing = existing[0] if existing else None
                if existing:
                    old_qty = int(existing.get('quantity', 0) or 0)
                    add_qty = int(quantity or 0)
                    total_qty = old_qty + add_qty
                    old_px = float(existing.get('entry_price', 0) or 0)
                    new_px = float(price or 0)
                    avg_px = round((old_px * old_qty + new_px * add_qty) / total_qty, 2) if total_qty else round(new_px, 2)
                    updates = {
                        'quantity': total_qty,
                        'entry_price': avg_px,
                        'invested': round(avg_px * total_qty, 2),
                    }
                    self._store.update_trade(existing['id'], updates)
                    existing.update(updates)
                    return existing

                entry = {
                    'date':            today,
                    'timestamp':       now,
                    'symbol':          symbol,
                    'action':          'BUY',
                    'order_id':        order_id or None,
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
                self._store.add_trade(entry)
            else:
                # Find the original BUY timestamp for accurate holding time
                _buy_entry = self._store.get_trades(symbol=symbol, action='BUY', status='OPEN', limit=1)
                _buy_entry = _buy_entry[0] if _buy_entry else None
                _now = datetime.now()
                if _buy_entry and _buy_entry.get('timestamp'):
                    try:
                        _buy_dt = datetime.fromisoformat(_buy_entry['timestamp'])
                        _delta = _now - _buy_dt
                        holding_hours = round(_delta.total_seconds() / 3600, 1)
                        holding_days = round(_delta.total_seconds() / 86400, 2)
                    except Exception:
                        holding_hours = 0
                        holding_days = 0
                elif _buy_entry and _buy_entry.get('date'):
                    try:
                        _buy_dt = datetime.fromisoformat(_buy_entry['date'])
                        _delta = _now - _buy_dt
                        holding_hours = round(_delta.total_seconds() / 3600, 1)
                        holding_days = round(_delta.total_seconds() / 86400, 2)
                    except Exception:
                        holding_hours = 0
                        holding_days = 0
                elif entry_date:
                    try:
                        entry_date_dt = datetime.fromisoformat(entry_date)
                        _delta = _now - entry_date_dt
                        holding_hours = round(_delta.total_seconds() / 3600, 1)
                        holding_days = round(_delta.total_seconds() / 86400, 2)
                    except Exception:
                        holding_hours = 0
                        holding_days = 0
                else:
                    holding_hours = 0
                    holding_days = 0

                # Preserve the snapshot from the BUY entry for post-trade analysis
                _snap = _buy_entry or {}
                _score = float(_snap.get('trade_score', 0) or trade_score)
                _conf = float(_snap.get('confidence', 0) or confidence)
                _regime = _snap.get('market_regime', 'UNKNOWN') or market_regime
                _sector = _snap.get('sector', 'Unknown') or sector
                _buy_reason_snap = _snap.get('buy_reason', '') or buy_reason
                _sentiment = _snap.get('sentiment', 'NEUTRAL')
                _sentiment_score = float(_snap.get('sentiment_score', 0) or 0)
                _news_count = int(_snap.get('news_count', 0) or 0)
                _rsi = float(_snap.get('rsi', 0) or 0)
                _macd = float(_snap.get('macd_histogram', 0) or 0)
                _vol = float(_snap.get('volume_ratio', 1) or 1)
                _trend = _snap.get('trend', 'NEUTRAL')
                _atr = float(_snap.get('atr', 0) or 0)
                _mtf = bool(_snap.get('mtf_aligned', False))
                _score_components = _snap.get('score_components', score_components or {})

                entry = {
                    'date':           today,
                    'timestamp':      now,
                    'symbol':         symbol,
                    'action':         'SELL',
                    'order_id':       order_id or None,
                    'entry_price':    round(entry_price, 2) if entry_price else 0,
                    'exit_price':     round(price, 2),
                    'quantity':       quantity,
                    'invested':       round(entry_price * quantity, 2) if entry_price else 0,
                    'entry_date':     _snap.get('date', entry_date) or entry_date,
                    'exit_date':      today,
                    'holding_hours':  holding_hours,
                    'holding_days':   holding_days,
                    'exit_reason':    exit_reason,
                    'gross_pnl':      round(gross_pnl, 2),
                    'net_pnl':        round(net_pnl, 2),
                    'charges':        round(charges, 2),
                    'market_regime':  _regime,
                    'sector':         _sector,
                    'buy_reason':     _buy_reason_snap,
                    'trade_score':    round(_score, 1),
                    'score_components': _score_components,
                    'sentiment':      _sentiment,
                    'sentiment_score': _sentiment_score,
                    'news_count':     _news_count,
                    'rsi':            _rsi,
                    'macd_histogram': _macd,
                    'volume_ratio':   _vol,
                    'trend':          _trend,
                    'atr':            _atr,
                    'mtf_aligned':    _mtf,
                    'confidence':     round(_conf, 3),
                    'status':         'CLOSED',
                    'day_of_week':    _dow,
                    'week_number':    _week,
                    'month':          _mon,
                }
                # Update the matching open BUY record so we have one complete row
                buy_updates = {
                    'exit_price':   entry['exit_price'],
                    'exit_date':    today,
                    'holding_hours': holding_hours,
                    'holding_days': holding_days,
                    'exit_reason':  exit_reason,
                    'gross_pnl':    entry['gross_pnl'],
                    'net_pnl':      entry['net_pnl'],
                    'charges':      entry['charges'],
                    'status':       'CLOSED'
                }
                if _buy_entry:
                    net_pnl_for_attr = float(entry['net_pnl'] or 0)
                    attribution = self._compute_attribution(_buy_entry, net_pnl_for_attr)
                    buy_updates['attribution'] = attribution
                    if 'id' in _buy_entry and _buy_entry['id'] is not None:
                        self._store.update_trade(_buy_entry['id'], buy_updates)

                self._store.add_trade(entry)

        logger.info(f"Journal: logged {action} {symbol} @ ₹{price} (score={trade_score})")
        return entry

    # ── Read / Analytics ──────────────────────────────────────────────────────

    def all_entries(self) -> List[Dict]:
        return self._store.all_trades()

    def closed_trades(self) -> List[Dict]:
        return self._store.get_trades(action='BUY', status='CLOSED')

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
        avg_hold    = sum(float(t.get('holding_days') or 1) for t in trades) / total if total else 0

        holds = [max(0.0, float(t.get('holding_days', 0) or 0)) for t in trades]
        holds_h = [max(0.0, float(t.get('holding_hours', 0) or 0)) for t in trades]
        wins_t = [t for t in trades if float(t.get('net_pnl', 0) or 0) > 0]
        loss_t = [t for t in trades if float(t.get('net_pnl', 0) or 0) < 0]
        holding_stats = {
            'longest_trade_days': round(max(holds), 2) if holds else 0,
            'longest_trade_hours': round(max(holds_h), 1) if holds_h else 0,
            'shortest_trade_days': round(min(holds), 2) if holds else 0,
            'shortest_trade_hours': round(min(holds_h), 1) if holds_h else 0,
            'avg_winner_hold_days': round(sum(max(0.0, float(t.get('holding_days', 0) or 0)) for t in wins_t) / len(wins_t), 2) if wins_t else 0,
            'avg_loser_hold_days': round(sum(max(0.0, float(t.get('holding_days', 0) or 0)) for t in loss_t) / len(loss_t), 2) if loss_t else 0,
        }

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
            reg = t.get('market_regime', 'UNKNOWN')
            if not isinstance(reg, str) or not reg or reg.replace('.', '', 1).isdigit():
                reg = 'UNKNOWN'
            by_regime.setdefault(reg, []).append(t.get('net_pnl', 0))
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
            'holding_stats': holding_stats,
        }
