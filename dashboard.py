"""
Personal Trading Dashboard
Run: ./run_with_venv.sh dashboard.py
Open: http://localhost:5001
"""
import os, sys, json, threading, logging, time, socket, subprocess
from datetime import datetime, timedelta
from flask import Flask, render_template_string, jsonify, request
import pytz
try:
    import psutil as _psutil
    _HAS_PSUTIL = True
except ImportError:
    _HAS_PSUTIL = False

sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))
sys.path.insert(0, os.path.dirname(__file__))

from config import config
try:
    from trade_scorer import SCORE_SKIP as _SCORE_SKIP_THRESHOLD
except ImportError:
    _SCORE_SKIP_THRESHOLD = 55

try:
    from trade_scorer import TradeScorer as _TradeScorer
except ImportError:
    _TradeScorer = None

try:
    from multi_timeframe import MultiTimeframeConfirmer as _MultiTimeframeConfirmer
except ImportError:
    _MultiTimeframeConfirmer = None

try:
    from persistence import get_store
except ImportError:
    get_store = None

try:
    from trade_journal import TradeJournal as _TradeJournal
except ImportError:
    _TradeJournal = None

logger = logging.getLogger(__name__)
app = Flask(__name__)
IST = pytz.timezone("Asia/Kolkata")

# ── System health metrics (updated by background heartbeat) ───────────────────
_HEALTH: dict = {
    "cpu_pct":        0.0,
    "mem_pct":        0.0,
    "mem_mb":         0.0,
    "disk_free_gb":   0.0,
    "api_latency_ms": 0.0,
    "kite_ok":        False,
    "errors_today":   0,
    "scan_time_s":    0.0,
    "last_heartbeat": None,
    "start_time":     datetime.now(pytz.timezone("Asia/Kolkata")).isoformat(),
}
_HEALTH_LOCK = threading.Lock()

# ── Token server auto-start helper ─────────────────────────────────────────────
_TOKEN_SERVER_PROC: subprocess.Popen | None = None
_TOKEN_SERVER_LOCK = threading.Lock()


def _is_port_open(port: int, host: str = "127.0.0.1", timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception:
        return False


def _ensure_token_server() -> dict:
    """
    Ensure the Kite token receiver server (get_kite_token.py) is running on port 8080.
    Returns a dict with the Kite login URL and status.
    """
    global _TOKEN_SERVER_PROC
    with _TOKEN_SERVER_LOCK:
        if _is_port_open(8080):
            return {"started": False, "already_running": True, "login_url": _kite_login_url()}

        proj_dir = os.path.dirname(os.path.abspath(__file__))
        python = os.path.join(proj_dir, "venv", "bin", "python3")
        script = os.path.join(proj_dir, "get_kite_token.py")
        env = os.environ.copy()
        env["PYTHONPATH"] = f"{proj_dir}:{proj_dir}/src"

        try:
            _TOKEN_SERVER_PROC = subprocess.Popen(
                [python, script],
                cwd=proj_dir,
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except Exception as e:
            logger.error(f"Failed to start token server: {e}")
            return {"started": False, "error": str(e), "login_url": _kite_login_url()}

        # Wait up to 5 seconds for the server to be listening
        deadline = time.time() + 5
        while time.time() < deadline:
            if _is_port_open(8080):
                return {"started": True, "already_running": False, "login_url": _kite_login_url()}
            time.sleep(0.2)

        return {
            "started": False,
            "error": "Token server did not start listening on port 8080 within 5 seconds",
            "login_url": _kite_login_url(),
        }


def _kite_login_url() -> str:
    """Build the Kite Connect login URL from config."""
    api_key = getattr(config, "KITE_API_KEY", "") or "veq6w4lv31v27ogd"
    return f"https://kite.trade/connect/login?api_key={api_key}&v=3"


def _safe_dt(value):
    """Parse a journal/broker date/timestamp into a naive datetime."""
    if not value:
        return None
    try:
        if isinstance(value, datetime):
            return value.replace(tzinfo=None)
        s = str(value)
        if 'T' in s:
            return datetime.fromisoformat(s.replace('Z', '+00:00')).replace(tzinfo=None)
        # Broker format: 'Fri, 07 Aug 2026 09:50:57 GMT'
        for fmt in ('%a, %d %b %Y %H:%M:%S %Z', '%Y-%m-%d %H:%M:%S'):
            try:
                return datetime.strptime(s, fmt).replace(tzinfo=None)
            except Exception:
                pass
        return datetime.strptime(s[:10], '%Y-%m-%d')
    except Exception:
        return None


def _safe_float(value, default=0.0):
    """Convert a value to float safely."""
    if value is None or value == '':
        return default
    try:
        return float(value)
    except Exception:
        return default


def _duration_text(start_dt, end_dt):
    """Return a human-readable holding duration (e.g. '1d 2h 15m')."""
    if not start_dt or not end_dt:
        return ''
    delta = end_dt - start_dt
    if delta.total_seconds() < 0:
        return ''
    days = delta.days
    hours, rem = divmod(delta.seconds, 3600)
    minutes = rem // 60
    parts = []
    if days:
        parts.append(f'{days}d')
    if hours:
        parts.append(f'{hours}h')
    if minutes:
        parts.append(f'{minutes}m')
    return ' '.join(parts) if parts else '0m'


def _build_trade_cards(journal_entries, positions, now_naive):
    """Pair BUY journal entries with their exits into professional trade cards.
    Also include any current open broker positions not already in the journal."""
    cards = []
    for e in journal_entries:
        if e.get('action') != 'BUY':
            continue
        entry_dt = _safe_dt(e.get('entry_date') or e.get('timestamp'))
        exit_dt = _safe_dt(e.get('exit_date'))
        if e.get('status') == 'OPEN':
            status_label = 'Open'
            exit_dt_for_duration = now_naive
        elif e.get('status') == 'CLOSED':
            status_label = 'Completed'
            if exit_dt and entry_dt:
                # exit_date is stored as date-only; inherit the entry time for a sensible duration/display
                exit_dt_for_duration = datetime.combine(exit_dt.date(), entry_dt.time())
            else:
                exit_dt_for_duration = exit_dt or now_naive
        else:
            status_label = str(e.get('status', 'Open'))
            exit_dt_for_duration = exit_dt or now_naive

        qty = int(e.get('quantity', 0) or 0)
        entry_price = float(e.get('entry_price', 0) or 0)
        exit_price = float(e.get('exit_price', 0)) if e.get('exit_price') is not None else None
        gross = e.get('gross_pnl')
        charges = e.get('charges', 0) or 0
        net = e.get('net_pnl')
        invested = round(entry_price * qty, 2) if qty and entry_price else 0.0

        return_pct = 0.0
        if invested and net is not None and invested > 0:
            return_pct = (float(net) / invested) * 100

        cards.append({
            'id': e.get('id'),
            'symbol': e.get('symbol', ''),
            'entry_date': entry_dt.isoformat() if entry_dt else e.get('timestamp'),
            'exit_date': exit_dt_for_duration.isoformat() if exit_dt_for_duration else None,
            'entry_price': round(entry_price, 2),
            'exit_price': round(exit_price, 2) if exit_price is not None else None,
            'quantity': qty,
            'invested': invested,
            'gross_pnl': round(float(gross), 2) if gross is not None else None,
            'charges': round(float(charges), 2),
            'net_pnl': round(float(net), 2) if net is not None else None,
            'exit_reason': e.get('exit_reason') or '',
            'trade_score': e.get('trade_score'),
            'confidence': e.get('confidence'),
            'sector': e.get('sector') or 'Unknown',
            'risk_reward_ratio': e.get('risk_reward_ratio'),
            'market_regime': e.get('market_regime'),
            'holding_time': _duration_text(entry_dt, exit_dt_for_duration) if entry_dt else '',
            'status': status_label,
            'return_pct': round(return_pct, 2),
            'buy_reason': e.get('buy_reason') or '',
        })

    # Pair SELL journal entries with BUY cards and add orphan SELL-only trades
    try:
        buy_lookup = {}
        for c in cards:
            if c.get('status') == 'Open':
                continue
            ed = _safe_dt(c.get('entry_date'))
            if ed:
                buy_lookup[(c['symbol'], ed.date().isoformat())] = c

        # Most recent exits first so the latest SELL is paired with the BUY
        sell_entries = sorted(
            [x for x in journal_entries if x.get('action') == 'SELL' and x.get('status') == 'CLOSED'],
            key=lambda x: str(x.get('exit_date', '')),
            reverse=True,
        )
        for e in sell_entries:
            sym = e.get('symbol', '')
            entry_dt = _safe_dt(e.get('entry_date') or e.get('timestamp'))
            if not entry_dt:
                continue
            ed_key = (sym, entry_dt.date().isoformat())
            if ed_key in buy_lookup:
                c = buy_lookup[ed_key]
                if c.get('_sell_merged'):
                    continue
                exit_price = float(e.get('exit_price', 0)) if e.get('exit_price') is not None else None
                if exit_price is None:
                    continue
                c['_sell_merged'] = True
                entry_price = float(e.get('entry_price', 0) or 0)
                if entry_price > 0:
                    c['entry_price'] = round(entry_price, 2)
                    c['invested'] = round(entry_price * c.get('quantity', 0), 2)
                c['exit_price'] = round(exit_price, 2)
                c['exit_date'] = e.get('exit_date')
                c['gross_pnl'] = round(float(e.get('gross_pnl', 0) or 0), 2) if e.get('gross_pnl') is not None else c.get('gross_pnl')
                c['charges'] = round(float(e.get('charges', 0) or 0), 2)
                c['net_pnl'] = round(float(e.get('net_pnl', 0) or 0), 2) if e.get('net_pnl') is not None else c.get('net_pnl')
                c['exit_reason'] = e.get('exit_reason') or c.get('exit_reason', '')
                if c.get('invested') and c.get('net_pnl') is not None:
                    c['return_pct'] = round((c['net_pnl'] / c['invested']) * 100, 2)
                if c.get('entry_date') or c.get('exit_date'):
                    c['holding_time'] = _duration_text(_safe_dt(c.get('entry_date')), _safe_dt(c.get('exit_date')))
            else:
                # Orphan SELL without a BUY record (e.g., a manually-held position sold)
                qty = int(e.get('quantity', 0) or 0)
                entry_price = float(e.get('entry_price', 0) or 0)
                exit_price = float(e.get('exit_price', 0)) if e.get('exit_price') is not None else None
                gross = e.get('gross_pnl')
                charges = e.get('charges', 0) or 0
                net = e.get('net_pnl')
                invested = round(entry_price * qty, 2) if qty and entry_price else 0.0
                return_pct = 0.0
                if invested and net is not None and invested > 0:
                    return_pct = (float(net) / invested) * 100
                new_card = {
                    'id': e.get('id'),
                    'symbol': e.get('symbol', ''),
                    'entry_date': entry_dt.isoformat(),
                    'exit_date': e.get('exit_date'),
                    'entry_price': round(entry_price, 2),
                    'exit_price': round(exit_price, 2) if exit_price is not None else None,
                    'quantity': qty,
                    'invested': invested,
                    'gross_pnl': round(float(gross), 2) if gross is not None else None,
                    'charges': round(float(charges), 2),
                    'net_pnl': round(float(net), 2) if net is not None else None,
                    'exit_reason': e.get('exit_reason') or '',
                    'trade_score': e.get('trade_score'),
                    'confidence': e.get('confidence'),
                    'sector': e.get('sector') or 'Unknown',
                    'risk_reward_ratio': e.get('risk_reward_ratio'),
                    'market_regime': e.get('market_regime'),
                    'holding_time': _duration_text(entry_dt, _safe_dt(e.get('exit_date'))) if entry_dt else '',
                    'status': 'Completed',
                    'return_pct': round(return_pct, 2),
                    'buy_reason': e.get('buy_reason') or '',
                    'source': 'Kite',
                }
                cards.append(new_card)
                buy_lookup[ed_key] = new_card
    except Exception as _sell_err:
        logger.warning(f"Could not pair SELL journal entries into trade cards: {_sell_err}")

    # Add live open positions from Kite/holdings that are not already represented by an open journal card
    try:
        open_symbols = {c['symbol'] for c in cards if c.get('status') == 'Open'}
        for p in (positions or []):
            sym = p.get('tradingsymbol') or p.get('symbol')
            qty = int(p.get('quantity', 0) or 0)
            if p.get('status') == 'CLOSED':
                continue
            if not sym or qty <= 0 or sym in open_symbols:
                continue
            entry_dt = _safe_dt(p.get('entry_date') or p.get('entry_time') or p.get('buy_datetime'))
            buy_price = float(p.get('average_price', 0) or 0) or float(p.get('first_entry_price', 0) or 0)
            cards.append({
                'id': None,
                'symbol': sym,
                'entry_date': entry_dt.isoformat() if entry_dt else now_naive.isoformat(),
                'exit_date': None,
                'entry_price': round(buy_price, 2),
                'exit_price': None,
                'quantity': qty,
                'invested': round(buy_price * qty, 2),
                'gross_pnl': None,
                'charges': 0,
                'net_pnl': None,
                'exit_reason': '',
                'trade_score': p.get('trade_score'),
                'confidence': p.get('confidence'),
                'sector': p.get('sector') or 'Unknown',
                'risk_reward_ratio': p.get('risk_reward_ratio'),
                'market_regime': p.get('market_regime'),
                'holding_time': _duration_text(entry_dt, now_naive) if entry_dt else '',
                'status': 'Open',
                'return_pct': 0.0,
                'buy_reason': '',
                'source': 'Holding' if p.get('_source') == 'holding' else 'Kite',
            })
    except Exception as _pos_err:
        logger.warning(f"Could not merge open positions into trade cards: {_pos_err}")

    return sorted(cards, key=lambda x: x['entry_date'] or '', reverse=True)


def _build_trade_events(trade_cards):
    """Flatten trade cards into BUY/SELL event rows for the history table."""
    events = []
    for c in trade_cards:
        invested = c.get('invested', 0) or 0
        pnl_pct = round((c.get('net_pnl', 0) or 0) / invested * 100, 2) if invested else 0.0
        events.append({
            'datetime': c.get('entry_date'),
            'symbol': c['symbol'],
            'type': 'BUY',
            'quantity': c.get('quantity', 0),
            'price': c.get('entry_price'),
            'buy_price': c.get('entry_price'),
            'sell_price': None,
            'total_value': c.get('invested', 0),
            'pnl': None,
            'pnl_pct': pnl_pct,
            'charges': c.get('charges', 0),
            'exit_reason': c.get('exit_reason') or '',
            'source': c.get('source') or 'Bot',
            'trade_id': c.get('id'),
            'order_id': c.get('id'),
        })
        if c.get('exit_price') is not None and c.get('exit_date'):
            entry_dt_ev = _safe_dt(c.get('entry_date'))
            exit_dt_ev = _safe_dt(c.get('exit_date'))
            if entry_dt_ev and exit_dt_ev:
                # Exit was stored as date-only; combine with entry time for a realistic SELL timestamp
                sell_dt = datetime.combine(exit_dt_ev.date(), entry_dt_ev.time())
                sell_dt_iso = sell_dt.isoformat()
            else:
                sell_dt_iso = c.get('exit_date')
            total_sell = round(c['exit_price'] * c['quantity'], 2) if c.get('exit_price') and c.get('quantity') else 0.0
            pnl = c.get('net_pnl')
            pnl_pct = round(pnl / invested * 100, 2) if invested and pnl is not None else 0.0
            events.append({
                'datetime': sell_dt_iso,
                'symbol': c['symbol'],
                'type': 'SELL',
                'quantity': c.get('quantity', 0),
                'price': c.get('exit_price'),
                'buy_price': c.get('entry_price'),
                'sell_price': c.get('exit_price'),
                'total_value': total_sell,
                'pnl': pnl,
                'pnl_pct': pnl_pct,
                'charges': c.get('charges', 0),
                'exit_reason': c.get('exit_reason') or '',
                'source': 'Bot',
                'trade_id': c.get('id'),
                'order_id': c.get('id'),
            })
    return sorted(events, key=lambda x: x['datetime'] or '', reverse=True)


def _build_order_events(orders):
    """Flatten raw broker/journal completed orders into BUY/SELL event rows."""
    events = []
    for o in (orders or []):
        if str(o.get('status', '')).upper() != 'COMPLETE':
            continue
        qty = int(o.get('quantity', 0) or 0)
        if qty <= 0:
            continue
        ttype = (o.get('transaction_type') or 'BUY').upper()
        if ttype not in ('BUY', 'SELL'):
            continue
        dt = _safe_dt(o.get('order_timestamp'))
        sym = o.get('tradingsymbol') or o.get('symbol') or '—'
        avg = _safe_float(o.get('average_price'), 0.0)
        buy_px = _safe_float(o.get('buy_price'), avg) if ttype == 'SELL' else avg
        sell_px = _safe_float(o.get('sell_price'), avg) if ttype == 'SELL' else None
        pnl = _safe_float(o.get('pnl'), None) if ttype == 'SELL' else None
        pnl_pct = 0.0
        if ttype == 'SELL' and buy_px and qty:
            pnl_pct = round(pnl / (buy_px * qty) * 100, 2) if pnl is not None and pnl != 0.0 else round((sell_px - buy_px) / buy_px * 100, 2)
        total = round(sell_px * qty, 2) if ttype == 'SELL' and sell_px else round(avg * qty, 2)
        source = 'Broker' if o.get('_source') != 'journal' else 'Journal'
        events.append({
            'datetime': dt.isoformat() if dt else str(o.get('order_timestamp', '')),
            'symbol': sym,
            'type': ttype,
            'quantity': qty,
            'price': sell_px if ttype == 'SELL' else avg,
            'buy_price': buy_px,
            'sell_price': sell_px if ttype == 'SELL' else None,
            'total_value': total,
            'pnl': pnl,
            'pnl_pct': pnl_pct,
            'charges': 0.0,
            'exit_reason': o.get('exit_reason') or '',
            'source': source,
            'trade_id': o.get('order_id') or o.get('id'),
            'order_id': o.get('order_id') or o.get('id') or '—',
        })
    return sorted(events, key=lambda x: x['datetime'] or '', reverse=True)


def _build_order_completed(orders):
    """Pair completed SELL orders with their BUY counterpart to form finished trades."""
    completed = []
    store = get_store() if get_store else None
    sell_exit_reasons = {}
    if store:
        for trade in (store.get_trades(action='SELL') or []):
            if trade.get('order_id'):
                sell_exit_reasons[trade.get('order_id')] = trade.get('exit_reason')
            if trade.get('symbol'):
                sell_exit_reasons[trade.get('symbol')] = trade.get('exit_reason')
    # Sort ascending by timestamp for FIFO pairing
    sorted_orders = sorted(
        [o for o in (orders or []) if str(o.get('status', '')).upper() == 'COMPLETE'],
        key=lambda x: _safe_dt(x.get('order_timestamp')) or datetime.min,
    )
    open_buys = {}
    for o in sorted_orders:
        ttype = (o.get('transaction_type') or 'BUY').upper()
        sym = o.get('tradingsymbol') or o.get('symbol') or '—'
        qty = int(o.get('quantity', 0) or 0)
        if ttype == 'BUY' and qty > 0:
            open_buys.setdefault(sym, []).append({
                'dt': _safe_dt(o.get('order_timestamp')),
                'price': _safe_float(o.get('average_price'), 0.0),
                'oid': o.get('order_id') or o.get('id') or '—',
                'qty': qty,
                'remaining': qty,
            })
        elif ttype == 'SELL' and qty > 0:
            sell_px = _safe_float(o.get('sell_price') or o.get('average_price'), 0.0)
            buy_px = _safe_float(o.get('buy_price'), 0.0)
            sell_dt = _safe_dt(o.get('order_timestamp'))
            match_oid = '—'
            match_buy_dt = None
            matched_qty = 0
            # Consume from open BUY queue
            if sym in open_buys:
                queue = open_buys[sym]
                i = 0
                while i < len(queue) and matched_qty < qty:
                    b = queue[i]
                    take = min(b['remaining'], qty - matched_qty)
                    if take > 0:
                        b['remaining'] -= take
                        matched_qty += take
                        if not match_buy_dt:
                            match_buy_dt = b['dt']
                            match_oid = b['oid']
                    i += 1
                open_buys[sym] = [b for b in queue if b['remaining'] > 0]
            if not buy_px and matched_qty > 0:
                buy_px = open_buys[sym][0]['price'] if (sym in open_buys and open_buys[sym]) else 0.0
            net = _safe_float(o.get('pnl'), 0.0)
            qty_for_pnl = qty if qty > 0 else matched_qty
            invested = round(buy_px * qty_for_pnl, 2) if buy_px and qty_for_pnl else 0.0
            pnl_pct = round(net / invested * 100, 2) if invested and net != 0.0 else 0.0
            # If pnl missing, compute gross
            if net == 0.0 and buy_px and sell_px and qty_for_pnl:
                net = round((sell_px - buy_px) * qty_for_pnl, 2)
                pnl_pct = round((sell_px - buy_px) / buy_px * 100, 2) if buy_px else 0.0
            date_str = sell_dt.date().isoformat() if sell_dt else str(o.get('order_timestamp', ''))[:10]
            time_str = sell_dt.time().isoformat()[:8] if sell_dt else '—'
            holding = _duration_text(match_buy_dt, sell_dt) if match_buy_dt and sell_dt else ''
            brokerage = round(((sell_px - buy_px) * qty_for_pnl - net), 2) if buy_px and qty_for_pnl and sell_px else 0.0
            order_id = o.get('order_id') or o.get('id')
            exit_reason = (
                o.get('exit_reason')
                or sell_exit_reasons.get(order_id)
                or sell_exit_reasons.get(sym)
                or '—'
            )
            completed.append({
                'date': date_str,
                'time': time_str,
                'symbol': sym,
                'buy_price': round(buy_px, 2) if buy_px else '—',
                'sell_price': round(sell_px, 2) if sell_px else '—',
                'qty': qty_for_pnl,
                'holding_hours': 0.0,
                'holding_time': holding or '—',
                'net_pnl': net,
                'pnl_pct': pnl_pct,
                'action': ttype,
                'exit_reason': exit_reason,
                'brokerage': brokerage,
                'buy_order_id': match_oid,
                'sell_order_id': order_id or '—',
                'regime': '—',
                'sector': '—',
            })
    return sorted(completed, key=lambda x: f"{x.get('date', '')} {x.get('time', '')}", reverse=True)


def _heartbeat_loop():
    """Background thread: update system metrics every 60 s."""
    import pytz as _pytz
    _ist = _pytz.timezone("Asia/Kolkata")
    while True:
        try:
            metrics: dict = {}
            if _HAS_PSUTIL:
                metrics["cpu_pct"]      = _psutil.cpu_percent(interval=1)
                mem = _psutil.virtual_memory()
                metrics["mem_pct"]      = mem.percent
                metrics["mem_mb"]       = round(mem.used / 1024 / 1024, 1)
                disk = _psutil.disk_usage('/')
                metrics["disk_free_gb"] = round(disk.free / 1024**3, 1)

            # Kite connectivity probe (latency)
            try:
                sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))
                from token_manager import TokenManager
                _tm = TokenManager()
                _kite = _tm.initialize_kite()
                if _kite:
                    t0 = time.time()
                    _kite.ltp(["NSE:NIFTY 50"])
                    metrics["api_latency_ms"] = round((time.time() - t0) * 1000, 1)
                    metrics["kite_ok"] = True
                else:
                    metrics["kite_ok"] = False
            except Exception:
                metrics["kite_ok"] = False

            metrics["last_heartbeat"] = datetime.now(_ist).isoformat()

            with _HEALTH_LOCK:
                _HEALTH.update(metrics)
        except Exception as _e:
            logger.debug(f"Heartbeat error: {_e}")
        time.sleep(60)

# ── IP address cache — refreshed every 5 min in background to avoid per-request HTTP calls ──
_IP_CACHE: dict = {"ipv4": "unknown", "ipv6": "Not available", "ts": None}
_IP_CACHE_TTL = timedelta(minutes=5)
_IP_CACHE_LOCK = threading.Lock()

def _refresh_ip_cache():
    """Fetch public IP in background thread and cache for 5 min."""
    import urllib.request as _ur
    import ssl as _ssl
    _ctx = _ssl._create_unverified_context()
    try:
        for _url in ('https://api.ipify.org', 'https://ifconfig.me/ip', 'https://icanhazip.com'):
            try:
                _raw = _ur.urlopen(_url, timeout=5, context=_ctx).read().decode().strip()
                if _raw and '.' in _raw and len(_raw) < 20:
                    with _IP_CACHE_LOCK:
                        _IP_CACHE["ipv4"] = _raw
                        _IP_CACHE["ts"]   = datetime.now(IST)
                    break
            except Exception:
                continue
        try:
            _v6 = _ur.urlopen('https://api6.ipify.org', timeout=4, context=_ctx).read().decode().strip()
            with _IP_CACHE_LOCK:
                _IP_CACHE["ipv6"] = _v6 if ':' in _v6 else 'Not available'
        except Exception:
            pass
    except Exception:
        pass

def _maybe_refresh_ip():
    """Trigger background IP refresh if cache is stale."""
    with _IP_CACHE_LOCK:
        ts = _IP_CACHE["ts"]
    age = (datetime.now(IST) - ts) if ts else timedelta.max
    if age > _IP_CACHE_TTL:
        threading.Thread(target=_refresh_ip_cache, daemon=True).start()

# ── Background signal cache ───────────────────────────────────────────────────
# Scan runs in a background thread every 15 min; dashboard reads from cache instantly
_SIGNAL_CACHE = {
    "signals": [], "recommendations": [], "stocks_scanned": 0,
    "scan_universe": [], "timestamp": None, "scanning": False,
}
_SIGNAL_CACHE_LOCK = threading.Lock()
_SIGNAL_CACHE_TTL  = timedelta(minutes=15)

def _run_background_scan():
    """Background thread: scan full 150-stock universe, cache top-50 for display."""
    _scan_t0 = time.time()
    try:
        with _SIGNAL_CACHE_LOCK:
            _SIGNAL_CACHE["scanning"] = True
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))
        from dynamic_universe import DynamicUniverse
        from signal_generator import SignalGenerator

        scanner = DynamicUniverse()
        sg      = SignalGenerator()

        # Step 1: get 150-stock scan universe + 50-stock diversified display list
        candidates = scanner.get_top_candidates(display_n=50, scan_n=150)
        scan_syms    = candidates["scan_universe"]    # 150 ranked by liquidity+momentum
        display_syms = candidates["top_display"]      # 50 sector-diversified for UI

        # ── Inject morning report top picks + real-time gainers ───────────────
        # These are the stocks identified by the morning intelligence as top movers.
        # They MUST be scanned even if DynamicUniverse didn't rank them in top-50.
        priority_syms: list = []

        # A) Morning report AI top picks (already signal-ranked)
        with _MORNING_CACHE_LOCK:
            mr = _MORNING_CACHE.get("report") or {}
        priority_syms += [p["symbol"] for p in mr.get("ai_top_picks", [])[:15]]

        # B) Morning report top gainers (real-time price movers)
        priority_syms += [g["symbol"] for g in mr.get("top_gainers", [])[:10]]

        # C) Gap-up stocks from morning report
        priority_syms += [g["symbol"] for g in mr.get("gap_up_stocks", [])[:8]]

        # D) Live batch-quote top gainers (current session, not just morning open)
        try:
            from dynamic_universe import _NIFTY500_PRIORITY as _prio
            from market_data import MarketDataFetcher as _MDF
            _mdf = _MDF()
            if _mdf.kite:
                _syms = list(_prio)[:200]
                _all_q: dict = {}
                for _i in range(0, len(_syms), 200):
                    try:
                        _q = _mdf.kite.quote([f"NSE:{s}" for s in _syms[_i:_i+200]]) or {}
                        _all_q.update(_q)
                    except Exception:
                        pass
                _live_movers = []
                for _key, _qv in _all_q.items():
                    _s = _key.replace("NSE:", "")
                    _lp = _qv.get("last_price", 0)
                    _pc = _qv.get("ohlc", {}).get("close", 0)
                    if _lp and _pc:
                        _chg = (_lp - _pc) / _pc * 100
                        if _chg >= 1.5:            # only stocks up ≥1.5% intraday
                            _live_movers.append((_s, _chg))
                _live_movers.sort(key=lambda x: x[1], reverse=True)
                priority_syms += [s for s, _ in _live_movers[:12]]
                logger.info(f"Live gainers ≥1.5%: {[s for s,_ in _live_movers[:8]]}")
        except Exception as _e:
            logger.debug(f"Live gainer fetch error: {_e}")

        # E) Force-include currently held/open positions so they always appear in the UI
        try:
            from broker_integration import BrokerIntegration as _BI
            _bh = _BI().get_holdings()
            _held_force = [
                p.get('tradingsymbol') for p in
                (_bh.get('positions', []) + _bh.get('holdings', []))
                if (p.get('quantity', 0) or p.get('opening_quantity', 0) or 0) > 0
                and p.get('tradingsymbol')
            ]
            priority_syms = _held_force + priority_syms  # held stocks always first
        except Exception:
            pass

        # Merge: priority first (force-included), then normal display list, dedup, cap at 60
        _seen: set = set()
        merged_display: list = []
        for _sym in priority_syms + display_syms:
            if _sym and _sym not in _seen:
                _seen.add(_sym)
                merged_display.append(_sym)
            if len(merged_display) >= 60:
                break

        logger.info(
            f"Scan list: {len(merged_display)} stocks "
            f"({len(priority_syms)} priority + {len(display_syms)} universe = merged to 60 cap)"
        )

        # Step 2: run full AI pipeline on merged list (priority stocks guaranteed entry)
        raw_signals = sg.generate_signals_for_watchlist(merged_display)
        display_syms = merged_display   # update display_syms so filter logic uses merged list

        # Step 3: get held symbols for bot_decision logic
        try:
            from broker_integration import BrokerIntegration
            _b = BrokerIntegration()
            _held = _b.get_holdings()
            _held_syms = {
                p.get('tradingsymbol')
                for p in (_held.get('positions', []) + _held.get('holdings', []))
                if (p.get('quantity', 0) or p.get('opening_quantity', 0)) > 0
            }
            _open_count = len([p for p in _held.get('positions', [])
                               if (p.get('quantity', 0) or 0) > 0])
        except Exception:
            _held_syms  = set()
            _open_count = 0

        sl_pct  = config.SWING_STOP_LOSS_PERCENTAGE if config.TRADING_MODE == 'swing' else config.STOP_LOSS_PERCENTAGE
        tgt_pct = config.SWING_TARGET_PERCENTAGE    if config.TRADING_MODE == 'swing' else config.TARGET_PERCENTAGE

        # Step 4: build enriched signal list, filter to display_syms for UI
        from dynamic_universe import SECTOR_MAP as _SMAP
        display_set = set(display_syms)
        all_signals  = []
        display_sigs = []
        recommendations = []

        _scorer = _TradeScorer() if _TradeScorer else None
        _mtf = _MultiTimeframeConfirmer() if _MultiTimeframeConfirmer else None

        for sig in raw_signals:
            sym   = sig.get('symbol', '')
            act   = sig.get('action', '')
            conf  = float(sig.get('confidence') or 0)
            raw_score = float(sig.get('overall_score') or sig.get('trade_score') or 0)
            # Normalize to 0-100: ai_research_agent returns 0-1 float; TradeScorer returns 0-100
            ai_score = round(raw_score * 100, 1) if raw_score <= 1.0 else round(raw_score, 1)
            rr    = float(sig.get('risk_reward_ratio') or 0)
            price = float(sig.get('current_price') or sig.get('price') or 0)
            tech_score = 0.0
            trade_score = 0.0
            trade_components = {}
            mtf_aligned = bool(sig.get('mtf_aligned', False))
            mtf_reason = ''
            trade_action = 'SKIP'
            rejection_reason = ''

            if act == 'BUY':
                # Technical score from the enterprise engine sub-scores
                tech_score = float((sig.get('score_components') or {}).get('technical', 0))

                # Multi-timeframe confirmation (relaxed: only D + H)
                if _mtf:
                    try:
                        mtf_result = _mtf.confirm(sym)
                    except Exception:
                        mtf_result = {'aligned': True, 'reason': 'MTF data unavailable — fail open'}
                else:
                    mtf_result = {'aligned': True, 'reason': 'MTF disabled'}
                mtf_aligned = mtf_result.get('aligned', False)
                mtf_reason = mtf_result.get('reason', '')

                # Trade scorer (conservative technical 0-100)
                if _scorer:
                    research = sig.get('_research') or {}
                    regime = (research.get('market_regime', 'UNKNOWN') or 'UNKNOWN').upper()
                    try:
                        score_result = _scorer.score(
                            signal=sig,
                            research=research,
                            regime=regime,
                            sector_momentum=research.get('sector_momentum', 0.0),
                            mtf_aligned=mtf_aligned,
                        )
                        trade_score = float(score_result.get('total_score', 0))
                        trade_components = score_result.get('components', {})
                        trade_action = 'BUY' if not score_result.get('skip', True) and mtf_aligned else 'SKIP'
                    except Exception:
                        trade_score = 0
                        trade_action = 'SKIP'

                if trade_action != 'BUY':
                    if trade_score < _SCORE_SKIP_THRESHOLD:
                        rejection_reason = f'Trade score {trade_score:.0f} below threshold ({_SCORE_SKIP_THRESHOLD})'
                    elif not mtf_aligned:
                        rejection_reason = f'MTF not aligned — {mtf_reason}'
                    else:
                        rejection_reason = 'Technical setup too weak'

            # Bot decision merges AI, risk and trade-score gates
            if act != 'BUY':
                bot_decision = 'SELL signal — not buying'
            elif sym in _held_syms:
                bot_decision = 'Already held'
            elif _open_count >= config.MAX_POSITIONS:
                bot_decision = 'Max positions reached'
            elif trade_action != 'BUY' and act == 'BUY':
                bot_decision = rejection_reason or 'Technical setup too weak'
            elif conf < config.MIN_CONFIDENCE * 100:
                bot_decision = f'Confidence {conf:.0f}% below min {config.MIN_CONFIDENCE*100:.0f}%'
            elif rr < config.MIN_RISK_REWARD:
                bot_decision = f'R:R {rr:.2f} below {config.MIN_RISK_REWARD} min'
            else:
                bot_decision = 'Will buy*'

            expected_hold = f"{config.SWING_MAX_HOLD_DAYS} days" if config.TRADING_MODE == 'swing' else 'Same day'
            position_size = int(sig.get('position_size', 0) or 0)
            investment_amount = float(sig.get('investment_amount', 0) or 0)

            sig_data = {
                'symbol':            sym,
                'action':            act,
                'price':             price,
                'current_price':     price,
                'target':            sig.get('target') or round(price * (1 + tgt_pct), 2),
                'stop_loss':         sig.get('stop_loss') or round(price * (1 - sl_pct), 2),
                'confidence':        conf,
                'overall_score':     ai_score,
                'ai_score':          ai_score,
                'technical_score':   tech_score,
                'trade_score':       trade_score,
                'trade_components':  trade_components,
                'risk_reward_ratio': round(rr, 2),
                'trend':             sig.get('trend', ''),
                'reasoning':         sig.get('reasoning', ''),
                'bot_decision':      bot_decision,
                'rejection_reason':  rejection_reason,
                'sector':            _SMAP.get(sym, 'Other'),
                'market_regime':     sig.get('market_regime', ''),
                'atr':               sig.get('atr', 0),
                'mtf_aligned':       mtf_aligned,
                'mtf_reason':        mtf_reason,
                'position_size':     position_size,
                'investment_amount': investment_amount,
                'expected_hold_days': expected_hold,
            }
            all_signals.append(sig_data)
            if sym in display_set:
                display_sigs.append(sig_data)
            if act == 'BUY' and bot_decision == 'Will buy*' and trade_action == 'BUY' and mtf_aligned:
                recommendations.append(sig_data)

        # Sort display signals: BUY first, then by score desc
        display_sigs.sort(key=lambda x: (x['action'] != 'BUY', -x['overall_score']))

        with _SIGNAL_CACHE_LOCK:
            _SIGNAL_CACHE.update({
                "signals":        display_sigs,
                "recommendations": recommendations[:5],
                "stocks_scanned": len(scan_syms),
                "scan_universe":  scan_syms,
                "timestamp":      datetime.now(IST),
                "scanning":       False,
            })
        _elapsed = round(time.time() - _scan_t0, 1)
        with _HEALTH_LOCK:
            _HEALTH["scan_time_s"] = _elapsed
        logger.info(f"BG scan complete: {len(scan_syms)} scanned, {len(display_sigs)} displayed, {len(recommendations)} actionable [{_elapsed}s]")
    except Exception as e:
        logger.error(f"Background scan error: {e}")
        with _SIGNAL_CACHE_LOCK:
            _SIGNAL_CACHE["scanning"] = False
        with _HEALTH_LOCK:
            _HEALTH["errors_today"] = _HEALTH.get("errors_today", 0) + 1

def _maybe_trigger_background_scan():
    """Trigger background scan if cache is stale and no scan already running."""
    with _SIGNAL_CACHE_LOCK:
        ts       = _SIGNAL_CACHE["timestamp"]
        scanning = _SIGNAL_CACHE["scanning"]
    age = (datetime.now(IST) - ts) if ts else timedelta.max
    if age > _SIGNAL_CACHE_TTL and not scanning:
        t = threading.Thread(target=_run_background_scan, daemon=True)
        t.start()


# ── Morning Intelligence Report cache ─────────────────────────────────────────
_MORNING_CACHE: dict = {"report": None, "date": None, "generating": False}
_MORNING_CACHE_LOCK = threading.Lock()

# Sector proxy symbols — used to judge sector strength from daily moves
_SECTOR_PROXIES = {
    "Defence":      ["HAL", "BEL", "BEML", "GRSE", "MAZDOCK"],
    "Banking":      ["HDFCBANK", "ICICIBANK", "SBIN", "KOTAKBANK", "AXISBANK"],
    "IT":           ["TCS", "INFY", "WIPRO", "HCLTECH", "TECHM"],
    "Pharma":       ["SUNPHARMA", "DRREDDY", "CIPLA", "DIVISLAB", "LUPIN"],
    "Energy":       ["RELIANCE", "ONGC", "NTPC", "POWERGRID", "TATAPOWER"],
    "Auto":         ["MARUTI", "TATAMOTORS", "M&M", "BAJAJ-AUTO", "EICHERMOT"],
    "Metals":       ["TATASTEEL", "JSWSTEEL", "HINDALCO", "COALINDIA", "VEDL"],
    "Infra":        ["LT", "SIEMENS", "ABB", "RVNL", "IRCON"],
    "FMCG":         ["HINDUNILVR", "ITC", "NESTLEIND", "BRITANNIA", "TATACONSUM"],
    "RealEstate":   ["GODREJPROP", "PRESTIGE", "OBEROIRLTY", "LODHA", "BRIGADE"],
    "Chemicals":    ["PIDILITIND", "DEEPAKNTR", "NAVINFLUOR", "ALKYLAMINE", "AARTIIND"],
    "Retail":       ["TRENT", "DMART", "ZOMATO", "NYKAA", "JUBLFOOD"],
}

# Stocks to flag for avoid-reasons (keyword → reason label)
_AVOID_KEYWORDS = [
    ("fraud", "Fraud / SEBI action"),
    ("sebi", "SEBI action"),
    ("promoter sell", "Promoter selling"),
    ("insider sell", "Insider selling"),
    ("loss", "Weak earnings"),
    ("negative result", "Negative results"),
    ("downgrade", "Analyst downgrade"),
    ("debt", "High debt concern"),
]


def _generate_morning_report():
    """
    Generate full Morning Market Intelligence Report.
    Runs once per trading day (cached). Covers:
      1. Market Overview  (NIFTY, BANKNIFTY, VIX, regime)
      2. Sector Strength  (ranked by avg 1-day move)
      3. Top Gainers      (pre-market movers, gap-up)
      4. Gap-Up Stocks    (gap > 1.5%, volume filter)
      5. Delivery Volume  (top delivery % stocks)
      6. Strong News      (positive news + AI confidence)
      7. AI Top Picks     (ranked, sector-diversified, ≤ 20)
      8. Stocks to Avoid  (negative signals)
      9. Trading Plan     (regime-aware playbook)
    """
    with _MORNING_CACHE_LOCK:
        _MORNING_CACHE["generating"] = True

    try:
        import sys as _sys
        _sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))
        from market_data import MarketDataFetcher
        from market_regime import MarketRegimeDetector
        from dynamic_universe import DynamicUniverse, SECTOR_MAP as _SMAP
        from broker_integration import BrokerIntegration
        import config as _cfg

        mdf = MarketDataFetcher()
        kite = mdf.kite

        report = {
            "generated_at": datetime.now(IST).isoformat(),
            "market_overview": {},
            "sector_strength": [],
            "top_gainers": [],
            "gap_up_stocks": [],
            "delivery_volume": [],
            "strong_news": [],
            "ai_top_picks": [],
            "stocks_to_avoid": [],
            "trading_plan": {},
        }

        # ── 1. Market Overview ────────────────────────────────────────────────
        try:
            regime_det = MarketRegimeDetector()
            regime_sum = regime_det.get_summary()
            regime     = regime_sum.get("regime", "SIDEWAYS")

            # NIFTY, BANKNIFTY, VIX via Kite LTP
            ltp_keys = ["NSE:NIFTY 50", "NSE:NIFTY BANK", "NSE:INDIA VIX"]
            ltp_data = {}
            if kite:
                try:
                    ltp_data = kite.ltp(ltp_keys) or {}
                except Exception:
                    pass

            def _ltp(key):
                return ltp_data.get(key, {}).get("last_price", 0)

            # Prev-close change for NIFTY
            nifty_hist = mdf.get_stock_data("NIFTY 50", period="5d", interval="1d")
            nifty_chg  = 0.0
            if not nifty_hist.empty and len(nifty_hist) >= 2:
                nifty_chg = float((nifty_hist["Close"].iloc[-1] / nifty_hist["Close"].iloc[-2] - 1) * 100)

            bnf_hist = mdf.get_stock_data("NIFTY BANK", period="5d", interval="1d")
            bnf_chg  = 0.0
            if not bnf_hist.empty and len(bnf_hist) >= 2:
                bnf_chg = float((bnf_hist["Close"].iloc[-1] / bnf_hist["Close"].iloc[-2] - 1) * 100)

            vix_val = _ltp("NSE:INDIA VIX")
            vix_label = "LOW" if vix_val < 15 else "MEDIUM" if vix_val < 20 else "HIGH"

            nifty_val  = regime_sum.get("nifty") or _ltp("NSE:NIFTY 50")
            report["market_overview"] = {
                "regime":        regime,
                "nifty":         round(nifty_val, 2),
                "nifty_chg":     round(nifty_chg, 2),
                "banknifty_chg": round(bnf_chg, 2),
                "vix":           round(vix_val, 2),
                "vix_label":     vix_label,
                "sentiment":     "Bullish" if nifty_chg > 0.3 else "Bearish" if nifty_chg < -0.3 else "Neutral",
                "dma50":         regime_sum.get("dma50", 0),
                "dma200":        regime_sum.get("dma200", 0),
            }
        except Exception as e:
            logger.warning(f"Morning report: market overview error: {e}")

        # ── 2. Sector Strength ────────────────────────────────────────────────
        try:
            sector_scores = {}
            for sector, syms in _SECTOR_PROXIES.items():
                moves = []
                for sym in syms:
                    try:
                        h = mdf.get_stock_data(sym, period="5d", interval="1d")
                        if not h.empty and len(h) >= 2:
                            chg = float((h["Close"].iloc[-1] / h["Close"].iloc[-2] - 1) * 100)
                            moves.append(chg)
                    except Exception:
                        pass
                if moves:
                    sector_scores[sector] = round(sum(moves) / len(moves), 2)
            sorted_sectors = sorted(sector_scores.items(), key=lambda x: x[1], reverse=True)
            report["sector_strength"] = [
                {"sector": s, "change_pct": c,
                 "direction": "↑↑" if c > 0.5 else "↑" if c > 0 else "↓" if c > -0.5 else "↓↓"}
                for s, c in sorted_sectors
            ]
        except Exception as e:
            logger.warning(f"Morning report: sector strength error: {e}")

        # ── 3 & 4. Top Gainers + Gap-Up ──────────────────────────────────────
        try:
            scanner = DynamicUniverse()
            if kite:
                # Batch quote priority universe
                from dynamic_universe import _NIFTY500_PRIORITY
                syms_list = list(_NIFTY500_PRIORITY)[:300]
                batch_size = 200
                all_quotes = {}
                for i in range(0, len(syms_list), batch_size):
                    batch = syms_list[i:i+batch_size]
                    try:
                        q = kite.quote([f"NSE:{s}" for s in batch]) or {}
                        all_quotes.update(q)
                    except Exception:
                        pass

                movers = []
                gap_ups = []
                for sym, q in all_quotes.items():
                    s = sym.replace("NSE:", "")
                    lp   = q.get("last_price", 0)
                    pc   = q.get("ohlc", {}).get("close", 0)
                    op   = q.get("ohlc", {}).get("open", 0)
                    vol  = q.get("volume", 0)
                    if not pc or not lp:
                        continue
                    chg_pct = (lp - pc) / pc * 100
                    gap_pct = (op - pc) / pc * 100 if pc else 0
                    movers.append({"symbol": s, "price": lp, "change_pct": round(chg_pct, 2),
                                   "volume": vol, "sector": _SMAP.get(s, "Other")})
                    if gap_pct >= 1.5 and vol > 50000:
                        gap_ups.append({"symbol": s, "price": lp, "gap_pct": round(gap_pct, 2),
                                        "volume": vol, "sector": _SMAP.get(s, "Other")})

                movers.sort(key=lambda x: x["change_pct"], reverse=True)
                gap_ups.sort(key=lambda x: x["gap_pct"], reverse=True)
                report["top_gainers"]   = movers[:10]
                report["gap_up_stocks"] = gap_ups[:10]
        except Exception as e:
            logger.warning(f"Morning report: gainers/gap-up error: {e}")

        # ── 5. Delivery Volume (top delivery % - approximated via high vol stocks) ──
        try:
            # Proxy: stocks with high volume relative to avg (institutional interest)
            high_vol = sorted(
                [m for m in report.get("top_gainers", []) if m.get("volume", 0) > 500000],
                key=lambda x: x.get("volume", 0), reverse=True
            )[:8]
            report["delivery_volume"] = [
                {"symbol": h["symbol"], "price": h["price"],
                 "volume": h["volume"], "sector": h.get("sector", "Other"),
                 "note": "High institutional volume"}
                for h in high_vol
            ]
        except Exception as e:
            logger.warning(f"Morning report: delivery volume error: {e}")

        # ── 6. Strong News + AI Confidence ───────────────────────────────────
        try:
            from sentiment_analysis import SentimentAnalyzer
            sa = SentimentAnalyzer()
            news_picks = []
            # Check top gainers + sector leaders for positive news
            check_syms = [m["symbol"] for m in report.get("top_gainers", [])[:6]]
            top_sector_syms = []
            for sec in (report.get("sector_strength") or [])[:3]:
                top_sector_syms += _SECTOR_PROXIES.get(sec["sector"], [])[:2]
            check_syms = list(dict.fromkeys(check_syms + top_sector_syms))[:10]

            for sym in check_syms:
                try:
                    sent = sa.get_market_sentiment(sym)
                    items = sent.get("news_items", [])
                    price_info = next(
                        (m for m in report.get("top_gainers", []) if m["symbol"] == sym), {}
                    )
                    if items and sent.get("score", 0) > 0.3:
                        headline = items[0].get("title", "") if isinstance(items[0], dict) else str(items[0])
                        news_picks.append({
                            "symbol":     sym,
                            "price":      price_info.get("price", 0),
                            "headline":   headline[:80],
                            "sentiment":  "Positive",
                            "confidence": round(sent.get("score", 0) * 100),
                            "sector":     _SMAP.get(sym, "Other"),
                        })
                except Exception:
                    pass
            report["strong_news"] = news_picks[:6]
        except Exception as e:
            logger.warning(f"Morning report: strong news error: {e}")

        # ── 7. AI Top Picks ───────────────────────────────────────────────────
        try:
            from signal_generator import SignalGenerator
            sg = SignalGenerator()
            # Build priority list: gap-ups + top gainers + top-sector leaders
            priority_syms = []
            priority_syms += [g["symbol"] for g in report.get("gap_up_stocks", [])]
            priority_syms += [g["symbol"] for g in report.get("top_gainers", [])[:8]]
            for sec in (report.get("sector_strength") or [])[:4]:
                priority_syms += _SECTOR_PROXIES.get(sec["sector"], [])[:3]
            # De-dup, keep order
            seen = set(); uniq = []
            for s in priority_syms:
                if s not in seen:
                    seen.add(s); uniq.append(s)
            priority_syms = uniq[:30]

            raw_sigs = sg.generate_signals_for_watchlist(priority_syms)

            # Apply live trading filters consistent with background scan
            sec_count: dict = {}
            top_picks = []
            watchlist = []
            avoid = []
            for sig in raw_sigs:
                sym  = sig.get("symbol", "")
                sec  = _SMAP.get(sym, "Other")
                act  = sig.get("action", "")
                price = float(sig.get("current_price") or 0)
                tgt   = float(sig.get("target") or 0)
                sl    = float(sig.get("stop_loss") or 0)
                exp_ret = round((tgt - price) / price * 100, 1) if price and tgt else 0

                # Normalize score to 0-100 (same as dashboard background scan)
                raw_score = float(sig.get('overall_score') or sig.get('trade_score') or 0)
                score = round(raw_score * 100, 1) if raw_score <= 1.0 else round(raw_score, 1)
                conf = float(sig.get('confidence', 0))
                rr = float(sig.get('risk_reward_ratio', 0))
                regime = sig.get('market_regime', '')

                def _pick(reason=''):
                    item = {
                        "rank":        len(top_picks) + 1,
                        "symbol":      sym,
                        "score":       score,
                        "confidence":  round(conf * 100),
                        "sector":      sec,
                        "action":      act,
                        "entry":       price,
                        "target":      tgt,
                        "stop_loss":   sl,
                        "rr":          round(rr, 2),
                        "exp_return":  exp_ret,
                        "trend":       sig.get("trend", ""),
                        "reason":      (sig.get("reasoning", "") or "").split(".")[0][:80],
                        "filter_note": reason,
                    }
                    return item

                if act != 'BUY':
                    watchlist.append(_pick(act or 'Not a BUY signal'))
                    continue
                if config.VOLATILE_BLOCK_BUYS and regime == 'VOLATILE':
                    watchlist.append(_pick('Volatile market regime'))
                    continue
                if exp_ret <= 0:
                    avoid.append(_pick(f'Negative expected return ({exp_ret}%)'))
                    continue
                if score < _SCORE_SKIP_THRESHOLD:
                    watchlist.append(_pick(f'Score {score:.0f} below {_SCORE_SKIP_THRESHOLD}'))
                    continue
                if conf < config.MIN_CONFIDENCE:
                    watchlist.append(_pick(f'Confidence {conf:.0%} below {config.MIN_CONFIDENCE:.0%}'))
                    continue
                if rr < config.MIN_RISK_REWARD:
                    watchlist.append(_pick(f'R:R {rr:.2f} below {config.MIN_RISK_REWARD}'))
                    continue

                # Tradeable pick — keep sector cap
                if sec_count.get(sec, 0) >= 3:
                    continue
                top_picks.append(_pick())
                sec_count[sec] = sec_count.get(sec, 0) + 1

                if len(top_picks) >= 20:
                    break

            report["ai_top_picks"]      = top_picks
            report["ai_watchlist"]     = watchlist[:20]
            report["ai_avoid"]         = avoid[:20]
            report["morning_scan_syms"] = [p["symbol"] for p in top_picks]
        except Exception as e:
            logger.warning(f"Morning report: AI top picks error: {e}")

        # ── 8. Stocks to Avoid ────────────────────────────────────────────────
        try:
            from sentiment_analysis import SentimentAnalyzer
            sa2 = SentimentAnalyzer()
            avoid_list = []
            # Check bottom movers
            bottom = sorted(report.get("top_gainers", []), key=lambda x: x.get("change_pct", 0))[:8]
            for item in bottom:
                sym = item["symbol"]
                try:
                    sent = sa2.get_market_sentiment(sym)
                    items = sent.get("news_items", [])
                    score = sent.get("score", 0)
                    if score < -0.1 or items:
                        reason = "Negative sentiment"
                        for kw, label in _AVOID_KEYWORDS:
                            all_text = " ".join(
                                str(n.get("title", "") if isinstance(n, dict) else n)
                                for n in items
                            ).lower()
                            if kw in all_text:
                                reason = label
                                break
                        avoid_list.append({
                            "symbol":     sym,
                            "price":      item.get("price", 0),
                            "change_pct": item.get("change_pct", 0),
                            "reason":     reason,
                            "sector":     _SMAP.get(sym, "Other"),
                        })
                except Exception:
                    pass

            # Add AI-flagged negative-expected-return / non-BUY signals
            for p in report.get("ai_avoid", []):
                avoid_list.append({
                    "symbol":     p.get("symbol", ""),
                    "price":      p.get("entry", 0),
                    "change_pct": p.get("exp_return", 0),
                    "reason":     p.get("filter_note", "AI filter"),
                    "sector":     p.get("sector", "Other"),
                })

            report["stocks_to_avoid"] = avoid_list[:8]
        except Exception as e:
            logger.warning(f"Morning report: avoid list error: {e}")

        # ── 9. Trading Plan ───────────────────────────────────────────────────
        try:
            regime = report["market_overview"].get("regime", "SIDEWAYS")
            top_secs = [s["sector"] for s in report.get("sector_strength", [])[:3] if s["change_pct"] > 0]
            bot_secs = [s["sector"] for s in report.get("sector_strength", []) if s["change_pct"] < -0.3]
            vix_val  = report["market_overview"].get("vix", 0)

            if regime == "BULL" and vix_val < 15:
                max_trades = "4–6"; risk_mode = "Aggressive"
            elif regime == "SIDEWAYS" or 15 <= vix_val < 20:
                max_trades = "2–4"; risk_mode = "Moderate"
            else:
                max_trades = "0–2"; risk_mode = "Defensive"

            report["trading_plan"] = {
                "regime":           regime,
                "risk_mode":        risk_mode,
                "expected_trades":  max_trades,
                "preferred_sectors": top_secs[:3],
                "avoid_sectors":     bot_secs[:3],
                "vix":              vix_val,
                "note": (
                    "Strong trending market — follow momentum" if regime == "BULL"
                    else "Choppy market — tighter SL, fewer trades" if regime == "SIDEWAYS"
                    else "Bear market — mostly cash, only defensive buys"
                ),
            }
        except Exception as e:
            logger.warning(f"Morning report: trading plan error: {e}")

        today = datetime.now(IST).strftime("%Y-%m-%d")
        with _MORNING_CACHE_LOCK:
            _MORNING_CACHE.update({
                "report":     report,
                "date":       today,
                "generating": False,
            })

        # Persist to disk so trading_orchestrator.py (separate process) can read it
        try:
            _cache_dir  = os.path.join(os.path.dirname(__file__), 'data')
            os.makedirs(_cache_dir, exist_ok=True)
            _cache_path = os.path.join(_cache_dir, 'morning_report_cache.json')
            with open(_cache_path, 'w') as _f:
                json.dump({"date": today, "report": report}, _f, default=str)
            logger.info(f"Morning report persisted → {_cache_path}")
        except Exception as _pe:
            logger.warning(f"Could not persist morning report: {_pe}")

        logger.info("Morning Intelligence Report generated successfully")
        return report

    except Exception as e:
        logger.error(f"Morning report generation failed: {e}")
        with _MORNING_CACHE_LOCK:
            _MORNING_CACHE["generating"] = False
        return {}


def _maybe_trigger_morning_report():
    """Generate morning report if not done today and time is ≥ 09:00 IST."""
    now = datetime.now(IST)
    today = now.strftime("%Y-%m-%d")
    with _MORNING_CACHE_LOCK:
        cached_date = _MORNING_CACHE["date"]
        generating  = _MORNING_CACHE["generating"]
    if cached_date == today or generating:
        return
    if now.hour >= 9:  # generate from 9:00 AM onwards
        t = threading.Thread(target=_generate_morning_report, daemon=True)
        t.start()

SECTOR_MAP = {
    "RELIANCE": "Energy/Oil",
    "TCS": "IT",
    "INFY": "IT",
    "WIPRO": "IT",
    "HDFCBANK": "Banking",
    "ICICIBANK": "Banking",
    "KOTAKBANK": "Banking",
    "AXISBANK": "Banking",
    "SBIN": "Banking",
    "BANKBARODA": "Banking",
    "INDUSINDBK": "Banking",
    "BANDHANBNK": "Banking",
    "BAJFINANCE": "Finance",
    "BAJAJFINSV": "Finance",
    "HDFCLIFE": "Insurance",
    "ICICIPRULI": "Insurance",
    "NIACL": "Insurance",
    "LICHSGFIN": "Finance",
    "HINDUNILVR": "FMCG",
    "ITC": "FMCG",
    "NESTLEIND": "FMCG",
    "BRITANNIA": "FMCG",
    "DABUR": "FMCG",
    "MARUTI": "Auto",
    "TATAMOTORS": "Auto",
    "M&M": "Auto",
    "ASHOKLEY": "Auto",
    "BAJAJ-AUTO": "Auto",
    "EICHERMOT": "Auto",
    "HEROMOTOCO": "Auto",
    "SUNPHARMA": "Pharma",
    "DRREDDY": "Pharma",
    "CIPLA": "Pharma",
    "AUROPHARMA": "Pharma",
    "DIVISLAB": "Pharma",
    "ASIANPAINT": "Paints",
    "BERGEPAINT": "Paints",
    "TITAN": "Consumer",
    "LT": "Infrastructure",
    "POWERGRID": "Infrastructure",
    "NTPC": "Power",
    "ADANIGREEN": "Power",
    "TATAPOWER": "Power",
    "BHARTIARTL": "Telecom",
    "IDEA": "Telecom",
    "BHEL": "Capital Goods",
    "SIEMENS": "Capital Goods",
    "ABB": "Capital Goods",
    "CUMMINSIND": "Capital Goods",
    "IRFC": "Finance",
    "PNB": "Banking",
    "BSE": "Exchange",
    "CANBK": "Banking",
    "IOB": "Banking",
    "UCOBANK": "Banking",
    "CENTRALBK": "Banking",
}

HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Cache-Control" content="no-store, no-cache, must-revalidate, max-age=0">
<meta http-equiv="Pragma" content="no-cache">
<title>AI Swing Trading Bot</title>
<script src="https://cdn.tailwindcss.com"></script>
<script src="/static/chart.umd.min.js?v=2"></script>
<script src="/static/ai_signals.js?v=2"></script>
<script>
// Report JS errors to server for debugging
window.onerror=function(msg, url, line, col, err){
  try{
    fetch('/api/js-error',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({message:String(msg), url:String(url), line:line, col:col, stack:(err&&err.stack)||''})});
  }catch(e){}
};
// Diagnostic: try a ping fetch immediately and display any error in the header
try{
  fetch('/api/data').then(function(r){return r.json();}).then(function(d){
    var el=document.getElementById('last-updated');
    if(el) el.textContent='API reachable — loading...';
  }).catch(function(e){
    var el=document.getElementById('last-updated');
    if(el) el.textContent='FETCH ERROR: '+e.message;
  });
}catch(e){
  var el=document.getElementById('last-updated');
  if(el) el.textContent='JS ERROR: '+e.message;
}
// If this cached copy fails to initialize within 4s, force a no-cache reload once
if(!location.search.includes('nocache=')){
  setTimeout(function(){
    var el=document.getElementById('last-updated');
    if(el && el.textContent.indexOf('Initializing')!==-1){
      location.href=location.href.split('?')[0]+'?nocache='+Date.now();
    }
  },4000);
}
</script>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{background:#0a0f1e;color:#e2e8f0;font-family:'Inter',system-ui,sans-serif;min-height:100vh}
.card{background:#111827;border-radius:12px;padding:18px;border:1px solid #1f2937}
.card-sm{background:#0f1724;border-radius:10px;padding:14px;border:1px solid #1f2937}
.green{color:#22c55e}.red{color:#ef4444}.yellow{color:#eab308}.blue{color:#60a5fa}.purple{color:#a78bfa}
.bg-green{background:#16a34a22;border:1px solid #22c55e44}
.bg-red{background:#dc262622;border:1px solid #ef444444}
.bg-yellow{background:#ca8a0422;border:1px solid #eab30844}
.badge{display:inline-block;padding:2px 10px;border-radius:99px;font-size:11px;font-weight:700}
.badge-buy{background:#16a34a33;color:#22c55e;border:1px solid #22c55e55}
.badge-sell{background:#dc262633;color:#ef4444;border:1px solid #ef444455}
.badge-hold{background:#ca8a0433;color:#eab308;border:1px solid #eab30855}
th{color:#4b5563;font-size:10px;text-transform:uppercase;letter-spacing:.08em;padding:8px 10px;border-bottom:1px solid #1f2937}
td{padding:9px 10px;border-bottom:1px solid #111827;font-size:13px;color:#d1d5db}
tr:hover td{background:#0f1724}
tr:last-child td{border:none}
.stat-label{color:#9ca3af;font-size:12px;font-weight:600;margin-bottom:4px;text-transform:uppercase;letter-spacing:.06em}
.stat-value{font-size:22px;font-weight:700;line-height:1.1}
.stat-value-sm{font-size:16px;font-weight:700}
.tab-btn{padding:8px 18px;border-radius:8px;font-size:13px;font-weight:600;cursor:pointer;border:none;transition:all .2s;color:#6b7280;background:transparent;white-space:nowrap}
.tab-btn.active{background:#1d4ed8;color:#fff}
.tab-btn:hover:not(.active){background:#1f2937;color:#e2e8f0}
.tab-content{display:none}
.tab-content.active{display:block}
.progress-bar{height:6px;background:#1f2937;border-radius:3px;overflow:hidden}
.progress-fill{height:100%;border-radius:3px;transition:width .5s}
.heatmap-item{padding:10px 14px;border-radius:8px;font-size:13px;font-weight:600;text-align:center;cursor:default}
.pulse{animation:pulse 2s infinite}
.spin{animation:spin 2s linear infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.4}}
@keyframes spin{to{transform:rotate(360deg)}}
.notif-item{padding:10px 0;border-bottom:1px solid #1f2937;display:flex;align-items:center;gap:10px;font-size:13px}
.notif-item:last-child{border:none}
.score-bar{display:inline-block;width:40px;height:6px;border-radius:3px;vertical-align:middle;margin-left:6px}
::-webkit-scrollbar{width:4px;height:4px}
::-webkit-scrollbar-track{background:#0a0f1e}
::-webkit-scrollbar-thumb{background:#1f2937;border-radius:2px}
.chat-wrap{display:flex;flex-direction:column;height:calc(100vh - 200px);min-height:500px}
.chat-msgs{flex:1;overflow-y:auto;padding:16px;display:flex;flex-direction:column;gap:14px}
.msg-user{align-self:flex-end;background:#1d4ed8;color:#fff;padding:10px 16px;border-radius:16px 16px 4px 16px;max-width:72%;font-size:14px;line-height:1.5}
.msg-ai{align-self:flex-start;background:#111827;border:1px solid #1f2937;color:#e2e8f0;padding:14px 18px;border-radius:4px 16px 16px 16px;max-width:82%;font-size:14px;line-height:1.6}
.msg-ai .ai-label{font-size:11px;color:#4b5563;margin-bottom:6px;text-transform:uppercase;letter-spacing:.06em}
.chip-row{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:14px}
.chip{padding:6px 14px;border-radius:99px;border:1px solid #374151;color:#9ca3af;font-size:12px;cursor:pointer;background:#111827;transition:all .2s}
.chip:hover{background:#1d4ed8;border-color:#1d4ed8;color:#fff}
.chat-input-row{display:flex;gap:10px;padding:14px;border-top:1px solid #1f2937;background:#0a0f1e}
.chat-input{flex:1;background:#111827;border:1px solid #374151;color:#e2e8f0;border-radius:10px;padding:10px 14px;font-size:14px;outline:none;transition:border .2s}
.chat-input:focus{border-color:#1d4ed8}
.chat-send{background:#1d4ed8;color:#fff;border:none;border-radius:10px;padding:10px 20px;font-size:14px;font-weight:600;cursor:pointer;transition:background .2s}
.chat-send:hover{background:#2563eb}.chat-send:disabled{background:#374151;cursor:not-allowed}
.ai-section{margin-bottom:8px}
.ai-section-title{font-size:11px;font-weight:700;color:#60a5fa;text-transform:uppercase;letter-spacing:.06em;margin-bottom:4px}
.ai-pill{display:inline-block;padding:3px 10px;border-radius:99px;font-size:12px;font-weight:600;margin:2px 3px}
.ai-pill-green{background:#16a34a22;color:#22c55e;border:1px solid #22c55e44}
.ai-pill-red{background:#dc262622;color:#ef4444;border:1px solid #ef444444}
.ai-pill-blue{background:#1d4ed822;color:#60a5fa;border:1px solid #1d4ed844}
.ai-pill-yellow{background:#ca8a0422;color:#eab308;border:1px solid #eab30844}
.ai-pill-gray{background:#1f293766;color:#9ca3af;border:1px solid #37415144}
.typing-dot{display:inline-block;width:7px;height:7px;border-radius:50%;background:#60a5fa;animation:blink 1.2s infinite}
.typing-dot:nth-child(2){animation-delay:.2s}.typing-dot:nth-child(3){animation-delay:.4s}
@keyframes blink{0%,80%,100%{opacity:.2}40%{opacity:1}}
</style>
</head>
<body>

<!-- HEADER -->
<div style="background:#111827;border-bottom:1px solid #1f2937;padding:12px 20px" class="flex items-center justify-between flex-wrap gap-3">
  <div class="flex items-center gap-3">
    <span style="font-size:22px">🤖</span>
    <div>
      <div style="font-size:16px;font-weight:700;color:#f9fafb">AI Swing Trading Bot</div>
      <div style="font-size:11px;color:#4b5563" id="last-updated">Initializing...</div>
    </div>
    <div id="health-badge" style="display:flex;align-items:center;gap:6px;padding:4px 10px;border-radius:20px;background:#1f2937;font-size:12px;font-weight:600">
      <span id="health-dot" style="width:10px;height:10px;border-radius:50%;background:#9ca3af"></span>
      <span id="health-text">Unknown</span>
    </div>
  </div>
  <div class="flex items-center gap-4 flex-wrap">
    <div class="text-center">
      <div class="stat-label">Market</div>
      <div id="hdr-market" style="font-size:13px;font-weight:700">—</div>
    </div>
    <div class="text-center">
      <div class="stat-label">Mode</div>
      <div id="hdr-mode" style="font-size:13px;font-weight:700;color:#60a5fa">—</div>
    </div>
    <div class="text-center">
      <div class="stat-label">Last Scan</div>
      <div id="hdr-last-scan" style="font-size:13px;font-weight:600">—</div>
    </div>
    <div class="text-center">
      <div class="stat-label">Next Scan</div>
      <div id="hdr-next-scan" style="font-size:13px;font-weight:600">—</div>
    </div>
    <div class="text-center">
      <div class="stat-label">Bot Status</div>
      <div id="hdr-status" style="font-size:13px;font-weight:700">—</div>
    </div>
    <div class="text-center">
      <div class="stat-label">Token</div>
      <div id="hdr-token" style="font-size:11px;color:#6b7280">—</div>
    </div>
  </div>
</div>

<!-- TAB NAV -->
<div style="background:#111827;border-bottom:1px solid #1f2937;padding:6px 16px;flex-wrap:wrap;" class="flex gap-2">
  <button class="tab-btn active" onclick="switchTab('dashboard',this)">🏠 Dashboard</button>
  <button class="tab-btn" onclick="switchTab('morning',this)" id="morning-tab-btn">🌅 Morning Intel</button>
  <button class="tab-btn" onclick="switchTab('portfolio',this)">📈 Portfolio</button>
  <button class="tab-btn" onclick="switchTab('positions',this)">📋 Positions</button>
  <button class="tab-btn" onclick="switchTab('lifecycle',this)">🔄 Trade Lifecycle</button>
  <button class="tab-btn" onclick="switchTab('history',this)">🕒 History</button>
  <button class="tab-btn" onclick="switchTab('signals',this)">🤖 AI Signals</button>
  <button class="tab-btn" onclick="switchTab('analytics',this)">📊 Analytics</button>
  <button class="tab-btn" onclick="switchTab('journal',this)">📓 Trade Journal</button>
  <button class="tab-btn" onclick="switchTab('skipped',this)">⚠️ Skipped Opportunities</button>
  <button class="tab-btn" onclick="switchTab('explain',this)">🔍 AI Explain</button>
  <button class="tab-btn" onclick="switchTab('askai',this)">💬 Ask AI</button>
  <button class="tab-btn" onclick="switchTab('market-intelligence',this)">🌐 Market Intelligence</button>
  <button class="tab-btn" onclick="switchTab('botstatus',this)">⚙️ Bot Status</button>
  <button class="tab-btn" id="ip-tab-btn" onclick="switchTab('ipstatus',this)">🌐 IP Status</button>
  <button class="tab-btn" onclick="switchTab('portfolio-optimizer',this)">📊 Portfolio Optimizer</button>
  <button class="tab-btn" onclick="switchTab('backtest',this)">📈 Backtest</button>
  <button class="tab-btn" onclick="switchTab('backtesting',this)">🧪 Backtesting</button>
  <button class="tab-btn" onclick="switchTab('ai-learning',this)">🧠 AI Learning</button>
  <button class="tab-btn" onclick="switchTab('monitoring',this)">🚨 Monitoring</button>
  <button class="tab-btn" onclick="switchTab('smart-execution',this)">⚡ Smart Execution</button>
</div>

<div style="padding:16px 20px;max-width:1800px;margin:0 auto">

<!-- ===== TAB: MORNING INTELLIGENCE ===== -->
<div id="tab-morning" class="tab-content">

  <!-- Header -->
  <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:16px">
    <div>
      <div style="font-size:20px;font-weight:800;color:#f9fafb">🌅 Morning Briefing</div>
      <div style="font-size:12px;color:#4b5563"><span id="mr-generated-at">—</span> · <span id="mr-last-updated">Last updated: —</span></div>
    </div>
    <div style="display:flex;gap:8px;align-items:center">
      <div id="mr-generating-badge" style="display:none;font-size:11px;color:#93c5fd;background:#1d4ed822;border:1px solid #3b82f644;padding:4px 10px;border-radius:6px">⏳ Generating…</div>
      <button onclick="refreshMorningReport()" style="background:#1d4ed8;color:#fff;border:none;border-radius:6px;padding:6px 14px;font-size:12px;cursor:pointer">↻ Regenerate</button>
    </div>
  </div>

  <!-- Today's Theme -->
  <div class="card mb-4" style="border:1px solid #1d4ed855;background:linear-gradient(135deg,#1e293b 0%,#0f172a 100%)">
    <div style="font-size:16px;font-weight:800;color:#f9fafb;margin-bottom:10px">🔶 Today's Theme</div>
    <div id="mr-theme" style="font-size:13px">—</div>
  </div>

  <!-- Section 1: Today's Trading Plan + Market Checklist -->
  <div class="grid grid-cols-1 lg:grid-cols-2 gap-4 mb-4">

    <!-- Today's Trading Plan -->
    <div class="card" style="border:1px solid #1d4ed855;background:linear-gradient(135deg,#1e293b 0%,#0f172a 100%)">
      <div style="font-size:15px;font-weight:800;color:#f9fafb;margin-bottom:14px">📋 Today's Trading Plan</div>
      <div id="mr-plan" style="font-size:13px">—</div>
    </div>

    <!-- Market Checklist -->
    <div class="card">
      <div style="font-size:15px;font-weight:800;color:#f9fafb;margin-bottom:14px">✅ Market Checklist</div>
      <div id="mr-checklist" style="font-size:13px">—</div>
    </div>

  </div>

  <!-- Section 2: Sector Rotation -->
  <div class="grid grid-cols-1 md:grid-cols-2 gap-4 mb-4">

    <div class="card">
      <div style="font-size:15px;font-weight:800;color:#f9fafb;margin-bottom:12px">📈 Top 3 Strongest</div>
      <div id="mr-strong" style="font-size:13px">—</div>
    </div>

    <div class="card">
      <div style="font-size:15px;font-weight:800;color:#f9fafb;margin-bottom:12px">📉 Top 3 Weakest</div>
      <div id="mr-weak" style="font-size:13px">—</div>
    </div>

  </div>

  <!-- Section 3: Institutional Activity + Important Events -->
  <div class="grid grid-cols-1 md:grid-cols-2 gap-4 mb-4">

    <div class="card">
      <div style="font-size:15px;font-weight:800;color:#f9fafb;margin-bottom:12px">🏦 Institutional Activity</div>
      <div id="mr-institutional" style="font-size:13px">—</div>
    </div>

    <div class="card" id="mr-events-card" style="display:none">
      <div style="font-size:15px;font-weight:800;color:#f9fafb;margin-bottom:12px">🗓️ Today's Important Events</div>
      <div id="mr-events" style="font-size:13px">—</div>
    </div>

  </div>

  <!-- Section 4: Today's Opportunity Summary -->
  <div class="card mb-4">
    <div style="font-size:15px;font-weight:800;color:#f9fafb;margin-bottom:12px">🎯 Today's Opportunity Summary</div>
    <div id="mr-summary" style="font-size:13px">—</div>
  </div>

  <!-- Section 5: Top 5 AI Opportunities -->
  <div class="card mb-4" style="border:1px solid #22c55e44">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px">
      <div style="font-size:15px;font-weight:800;color:#22c55e">🚀 Top 5 AI Opportunities</div>
      <div style="font-size:11px;color:#4b5563">Click a symbol to view full AI Signals</div>
    </div>
    <div id="mr-picks" style="font-size:13px">—</div>
  </div>

  <!-- Section 6: Market Notes -->
  <div class="card mb-4">
    <div style="font-size:15px;font-weight:800;color:#f9fafb;margin-bottom:12px">📝 Market Notes</div>
    <div id="mr-notes" style="font-size:13px;color:#d1d5db">—</div>
  </div>

  <!-- Section 7: Stocks to Avoid (hidden if empty) -->
  <div class="card mb-4" id="mr-avoid-card" style="display:none;border:1px solid #ef444444">
    <div style="font-size:15px;font-weight:800;color:#ef4444;margin-bottom:12px">🚫 Stocks to Avoid Today</div>
    <div id="mr-avoid" style="font-size:13px">—</div>
  </div>

</div><!-- /tab-morning -->


<!-- ===== TAB: DASHBOARD ===== -->
<div id="tab-dashboard" class="tab-content active">

  <!-- Row 1: Key Metrics -->
  <div class="grid grid-cols-2 md:grid-cols-4 gap-4 mb-4">
    <div class="card">
      <div class="stat-label">Portfolio Value</div>
      <div class="stat-value" id="d-portfolio-value">₹—</div>
      <div style="font-size:12px;margin-top:4px" id="d-portfolio-return">—</div>
    </div>
    <div class="card">
      <div class="stat-label">Today's P&amp;L</div>
      <div class="stat-value" id="d-daily-pnl">₹—</div>
      <div style="font-size:12px;margin-top:4px" id="d-daily-pnl-pct">—</div>
    </div>
    <div class="card">
      <div class="stat-label">Available Cash</div>
      <div class="stat-value green" id="d-cash">₹—</div>
      <div style="font-size:12px;margin-top:4px;color:#4b5563" id="d-cash-pct">— of budget</div>
    </div>
    <div class="card">
      <div class="stat-label">Open Positions</div>
      <div class="stat-value yellow" id="d-open-pos">—</div>
      <div style="font-size:12px;margin-top:4px;color:#4b5563" id="d-pos-detail">— / 3 max</div>
    </div>
  </div>

  <!-- Row 2: Trading Status + Capital Usage -->
  <div class="grid grid-cols-1 lg:grid-cols-2 gap-4 mb-4">

    <!-- Trading Status -->
    <div class="card">
      <div style="font-size:13px;font-weight:600;color:#9ca3af;margin-bottom:12px;text-transform:uppercase;letter-spacing:.06em">🚦 Trading Status</div>
      <div class="grid grid-cols-2 gap-3">
        <div>
          <div class="stat-label" style="margin-bottom:2px">Market Status</div>
          <div class="stat-value-sm" id="ts-market">—</div>
        </div>
        <div>
          <div class="stat-label" style="margin-bottom:2px">Open Positions</div>
          <div class="stat-value-sm" id="ts-open-pos">—</div>
        </div>
        <div>
          <div class="stat-label" style="margin-bottom:2px">Cash Available</div>
          <div class="stat-value-sm" id="ts-cash">—</div>
        </div>
        <div>
          <div class="stat-label" style="margin-bottom:2px">Risk Level</div>
          <div class="stat-value-sm" id="ts-risk">—</div>
        </div>
        <div style="grid-column: span 2">
          <div class="stat-label" style="margin-bottom:2px">Bot Health</div>
          <div class="stat-value-sm" id="ts-bot-health">—</div>
        </div>
      </div>
    </div>

    <!-- Capital Usage -->
    <div class="card">
      <div style="font-size:13px;font-weight:600;color:#9ca3af;margin-bottom:12px;text-transform:uppercase;letter-spacing:.06em">💰 Capital Usage</div>
      <div class="grid grid-cols-3 gap-4 mb-3">
        <div>
          <div class="stat-label" style="margin-bottom:2px">Used</div>
          <div class="stat-value-sm" id="cu-used">—</div>
        </div>
        <div>
          <div class="stat-label" style="margin-bottom:2px">Available</div>
          <div class="stat-value-sm" id="cu-available">—</div>
        </div>
        <div>
          <div class="stat-label" style="margin-bottom:2px">Reserved</div>
          <div class="stat-value-sm" id="cu-reserved">—</div>
        </div>
      </div>
      <div class="progress-bar" style="height:12px"><div class="progress-fill" id="cu-bar" style="width:0%;background:#3b82f6"></div></div>
      <div style="font-size:11px;color:#9ca3af;margin-top:4px" id="cu-pct">0% used</div>
    </div>

  </div>

  <!-- Row 3: Open Positions / Holdings -->
  <div class="card mb-4">
    <div style="font-size:13px;font-weight:600;color:#9ca3af;margin-bottom:12px;text-transform:uppercase;letter-spacing:.06em">
      📈 Open Positions / Holdings <span id="d-holdings-count" style="color:#3b82f6">(0)</span> <span class="pulse green" style="font-size:11px">● LIVE</span>
    </div>
    <div style="overflow-x:auto">
    <table style="width:100%;border-collapse:collapse;font-size:13px">
      <thead><tr style="background:#1f2937">
        <th style="text-align:left;padding:10px 8px">Symbol</th>
        <th style="text-align:right;padding:10px 8px">Qty</th>
        <th style="text-align:right;padding:10px 8px">Avg Price</th>
        <th style="text-align:right;padding:10px 8px">CMP</th>
        <th style="text-align:right;padding:10px 8px">Total P&amp;L</th>
        <th style="text-align:right;padding:10px 8px">Today %</th>
        <th style="text-align:right;padding:10px 8px">Trail SL</th>
        <th style="text-align:right;padding:10px 8px">Target</th>
        <th style="text-align:center;padding:10px 8px">Status</th>
      </tr></thead>
      <tbody id="d-positions"><tr><td colspan="9" style="text-align:center;color:#4b5563;padding:20px">No open positions</td></tr></tbody>
      <tfoot id="d-positions-total" style="display:none;background:#1f2937;font-weight:600">
        <tr>
          <td style="padding:10px 8px;text-align:left">Total</td>
          <td style="padding:10px 8px;text-align:right" id="d-total-qty">—</td>
          <td style="padding:10px 8px;text-align:right">—</td>
          <td style="padding:10px 8px;text-align:right">—</td>
          <td style="padding:10px 8px;text-align:right" id="d-total-pnl">—</td>
          <td style="padding:10px 8px;text-align:right" id="d-total-day-pct">—</td>
          <td style="padding:10px 8px;text-align:right">—</td>
          <td style="padding:10px 8px;text-align:right">—</td>
          <td style="padding:10px 8px;text-align:center">—</td>
        </tr>
      </tfoot>
    </table>
    </div>
  </div>

  <!-- Row 4: Global Markets -->
  <div class="card mb-4">
    <div style="font-size:13px;font-weight:600;color:#9ca3af;margin-bottom:12px;text-transform:uppercase;letter-spacing:.06em">🌍 Global Markets</div>
    <div class="grid grid-cols-1 md:grid-cols-3 gap-4">
      <div class="card-sm"><div class="stat-label">Overall Global Sentiment</div><div class="stat-value-sm" id="gm-sentiment">—</div></div>
      <div class="card-sm" style="grid-column: span 2"><div class="stat-label">Top 3 Market Drivers</div><div class="stat-value-sm" id="gm-drivers" style="font-size:12px">—</div></div>
    </div>
  </div>

  <!-- Row 4: Economic Events -->
  <div class="card mb-4">
    <div style="font-size:13px;font-weight:600;color:#9ca3af;margin-bottom:12px;text-transform:uppercase;letter-spacing:.06em">🗓️ Economic Events</div>
    <div class="grid grid-cols-2 md:grid-cols-4 gap-4">
      <div class="card-sm"><div class="stat-label">Next Event</div><div class="stat-value-sm" id="evt-name">—</div></div>
      <div class="card-sm"><div class="stat-label">Hours Away</div><div class="stat-value-sm" id="evt-hours">—</div></div>
      <div class="card-sm"><div class="stat-label">Size Factor</div><div class="stat-value-sm" id="evt-factor">—</div></div>
      <div class="card-sm"><div class="stat-label">New BUYs</div><div class="stat-value-sm" id="evt-buy">—</div></div>
    </div>
  </div>

  <!-- Row 4: Options Chain Intelligence -->
  <div class="card mb-4">
    <div style="font-size:13px;font-weight:600;color:#9ca3af;margin-bottom:12px;text-transform:uppercase;letter-spacing:.06em">📈 Options Intelligence</div>
    <div class="grid grid-cols-2 md:grid-cols-4 gap-4">
      <div class="card-sm"><div class="stat-label">PCR</div><div class="stat-value-sm" id="oi-pcr">—</div></div>
      <div class="card-sm"><div class="stat-label">Max Pain</div><div class="stat-value-sm" id="oi-max-pain">—</div></div>
      <div class="card-sm"><div class="stat-label">OI Build-up</div><div class="stat-value-sm" id="oi-buildup">—</div></div>
      <div class="card-sm"><div class="stat-label">Long Build-up</div><div class="stat-value-sm" id="oi-long">—</div></div>
      <div class="card-sm"><div class="stat-label">Short Build-up</div><div class="stat-value-sm" id="oi-short">—</div></div>
      <div class="card-sm"><div class="stat-label">Put Wall</div><div class="stat-value-sm" id="oi-put-wall">—</div></div>
      <div class="card-sm"><div class="stat-label">Strong OI Support</div><div class="stat-value-sm" id="oi-support">—</div></div>
      <div class="card-sm"><div class="stat-label">Conf Boost</div><div class="stat-value-sm" id="oi-conf-boost">—</div></div>
    </div>
  </div>

  <!-- Row 4: FII/DII Flow -->
  <div class="card mb-4">
    <div style="font-size:13px;font-weight:600;color:#9ca3af;margin-bottom:12px;text-transform:uppercase;letter-spacing:.06em">🏦 FII/DII Flow</div>
    <div class="grid grid-cols-2 md:grid-cols-4 gap-4">
      <div class="card-sm"><div class="stat-label">FII Net</div><div class="stat-value-sm" id="fii-net">—</div></div>
      <div class="card-sm"><div class="stat-label">DII Net</div><div class="stat-value-sm" id="dii-net">—</div></div>
      <div class="card-sm"><div class="stat-label">Net Flow</div><div class="stat-value-sm" id="fii-dii-net">—</div></div>
      <div class="card-sm"><div class="stat-label">Institutional Sentiment</div><div class="stat-value-sm" id="fii-dii-sentiment">—</div></div>
    </div>
  </div>

  <!-- Row 4: VIX Risk -->
  <div class="card mb-4">
    <div style="font-size:13px;font-weight:600;color:#9ca3af;margin-bottom:12px;text-transform:uppercase;letter-spacing:.06em">⚡ India VIX Risk</div>
    <div class="grid grid-cols-2 md:grid-cols-4 gap-4">
      <div class="card-sm"><div class="stat-label">VIX</div><div class="stat-value-sm" id="vix-value">—</div></div>
      <div class="card-sm"><div class="stat-label">Volatility Score</div><div class="stat-value-sm" id="vix-score">—</div></div>
      <div class="card-sm"><div class="stat-label">Risk Factor</div><div class="stat-value-sm" id="vix-factor">—</div></div>
      <div class="card-sm"><div class="stat-label">Risk Level</div><div class="stat-value-sm" id="vix-level">—</div></div>
    </div>
  </div>

  <!-- Row 4: Sector Rotation -->
  <div class="card mb-4">
    <div style="font-size:13px;font-weight:600;color:#9ca3af;margin-bottom:12px;text-transform:uppercase;letter-spacing:.06em">🔄 Sector Rotation</div>
    <div class="grid grid-cols-1 lg:grid-cols-2 gap-4">
      <div>
        <div style="font-size:12px;color:#94a3b8;margin-bottom:8px;font-weight:600">Top 5 Strongest</div>
        <div id="sr-strong" style="font-size:13px;color:#22c55e">—</div>
      </div>
      <div>
        <div style="font-size:12px;color:#94a3b8;margin-bottom:8px;font-weight:600">Top 5 Weakest</div>
        <div id="sr-weak" style="font-size:13px;color:#ef4444">—</div>
      </div>
    </div>
    <div class="grid grid-cols-2 md:grid-cols-4 gap-4 mt-4">
      <div class="card-sm"><div class="stat-label">NIFTY 7d</div><div class="stat-value-sm" id="sr-nifty-7d">—</div></div>
      <div class="card-sm"><div class="stat-label">NIFTY 30d</div><div class="stat-value-sm" id="sr-nifty-30d">—</div></div>
    </div>
  </div>

  <!-- Row 4: Risk Monitor -->
  <div class="card mb-4">
    <div style="font-size:13px;font-weight:600;color:#9ca3af;margin-bottom:12px;text-transform:uppercase;letter-spacing:.06em">⚡ Risk Monitor</div>
    <div class="grid grid-cols-2 md:grid-cols-5 gap-4">
      <div class="card-sm"><div class="stat-label">Exposure</div><div class="stat-value-sm yellow" id="d-exposure">₹—</div></div>
      <div class="card-sm"><div class="stat-label">Risk (SL)</div><div class="stat-value-sm red" id="d-risk">₹—</div></div>
      <div class="card-sm"><div class="stat-label">Reward (Tgt)</div><div class="stat-value-sm green" id="d-reward">₹—</div></div>
      <div class="card-sm"><div class="stat-label">Risk : Reward</div><div class="stat-value-sm" id="d-rr">—</div></div>
      <div class="card-sm"><div class="stat-label">Drawdown</div><div class="stat-value-sm" id="d-drawdown">—</div></div>
    </div>
  </div>

  <!-- Position Heatmap removed - details moved to Portfolio tab -->

  <!-- Portfolio Summary removed - key metrics now in top cards and Portfolio tab -->

  <!-- Row 5+6: AI Opportunities + Market -->
  <div class="grid grid-cols-1 lg:grid-cols-2 gap-4 mb-4">

    <!-- AI Opportunities -->
    <div class="card">
      <div style="font-size:13px;font-weight:600;color:#9ca3af;margin-bottom:12px;text-transform:uppercase;letter-spacing:.06em">🤖 Today's Best Opportunities</div>
      <div style="overflow-x:auto">
      <table style="width:100%;border-collapse:collapse">
        <thead><tr>
          <th style="text-align:left">Stock</th><th>Score</th><th>Trend</th>
          <th>Entry</th><th>Target</th><th>Required Capital</th><th>Can Buy?</th><th>Position Size</th>
        </tr></thead>
        <tbody id="d-opportunities"><tr><td colspan="8" style="text-align:center;color:#4b5563;padding:20px">Scanning...</td></tr></tbody>
      </table>
      </div>
    </div>

    <!-- AI Recommendation -->
    <div class="card">
      <div style="font-size:13px;font-weight:600;color:#9ca3af;margin-bottom:12px;text-transform:uppercase;letter-spacing:.06em">🧠 AI Recommendation</div>
      <div class="grid grid-cols-2 gap-3 mb-2">
        <div>
          <div class="stat-label" style="margin-bottom:2px">Market Regime</div>
          <div class="stat-value-sm" id="ai-rec-regime">—</div>
        </div>
        <div>
          <div class="stat-label" style="margin-bottom:2px">Current Action</div>
          <div class="stat-value-sm" id="ai-rec-action">—</div>
        </div>
        <div>
          <div class="stat-label" style="margin-bottom:2px">Existing Position Advice</div>
          <div class="stat-value-sm" id="ai-rec-advice">—</div>
        </div>
        <div>
          <div class="stat-label" style="margin-bottom:2px">New BUY Allowed</div>
          <div class="stat-value-sm" id="ai-rec-new-buy">—</div>
        </div>
      </div>
      <div>
        <div class="stat-label" style="margin-bottom:2px">Confidence</div>
        <div class="stat-value-sm" id="ai-rec-confidence">—</div>
      </div>
    </div>
  </div>

  <!-- Row 7: Recent Orders + Notifications -->
  <div class="grid grid-cols-1 lg:grid-cols-2 gap-4 mb-4">

    <!-- Recent Orders -->
    <div class="card">
      <div style="font-size:13px;font-weight:600;color:#9ca3af;margin-bottom:12px;text-transform:uppercase;letter-spacing:.06em">📋 Recent Orders (Last 10)</div>
      <div id="d-recent-orders">
        <div style="color:#4b5563;font-size:13px;padding:20px;text-align:center">No orders today</div>
      </div>
    </div>

    <!-- Notifications -->
    <div class="card" id="alerts-card" style="display:none">
      <div style="font-size:13px;font-weight:600;color:#9ca3af;margin-bottom:12px;text-transform:uppercase;letter-spacing:.06em">🔔 Recent Alerts</div>
      <div id="d-notifications">
        <div style="color:#4b5563;font-size:13px;padding:20px;text-align:center">No alerts yet</div>
      </div>
    </div>
  </div>

  <!-- Row 8: Daily Goals -->
  <div class="card mb-4">
    <div style="font-size:13px;font-weight:600;color:#9ca3af;margin-bottom:12px;text-transform:uppercase;letter-spacing:.06em">🎯 Daily Goals</div>
    <div class="grid grid-cols-1 md:grid-cols-3 gap-4">
      <div>
        <div class="flex justify-between mb-1"><span style="font-size:12px;color:#9ca3af">Current P&L / Daily Target</span><span style="font-size:12px" id="d-goal-profit-val">₹0 / ₹100</span></div>
        <div class="progress-bar"><div class="progress-fill" id="d-goal-profit-bar" style="width:0%;background:#22c55e"></div></div>
      </div>
      <div>
        <div class="flex justify-between mb-1"><span style="font-size:12px;color:#9ca3af">Remaining Target</span><span style="font-size:12px" id="d-goal-capital-val">₹0</span></div>
        <div class="progress-bar"><div class="progress-fill" id="d-goal-capital-bar" style="width:0%;background:#60a5fa"></div></div>
      </div>
      <div>
        <div class="flex justify-between mb-1"><span style="font-size:12px;color:#9ca3af">Daily Loss Limit</span><span style="font-size:12px" id="d-goal-loss-val">₹0 / ₹250</span></div>
        <div class="progress-bar"><div class="progress-fill" id="d-goal-loss-bar" style="width:0%;background:#ef4444"></div></div>
      </div>
    </div>
  </div>

</div><!-- /tab-dashboard -->


<!-- ===== TAB: PORTFOLIO ===== -->
<div id="tab-portfolio" class="tab-content">

  <!-- Top Summary Cards -->
  <div class="grid grid-cols-2 md:grid-cols-3 gap-4 mb-4" id="p-summary-cards">
    <div class="card"><div class="stat-label">Portfolio Value</div><div class="stat-value" id="p-portfolio-value">₹—</div></div>
    <div class="card"><div class="stat-label">Today P&L</div><div class="stat-value" id="p-today-pnl">₹—</div><div style="font-size:11px;color:#4b5563;margin-top:4px" id="p-today-pnl-pct">—</div></div>
    <div class="card"><div class="stat-label">Cash</div><div class="stat-value green" id="p-cash">₹—</div></div>
    <div class="card"><div class="stat-label">Capital Used</div><div class="stat-value" id="p-capital-used">—</div>
      <div style="width:100%;height:6px;background:#1f2937;border-radius:3px;margin-top:8px;overflow:hidden"><div id="p-capital-bar" style="width:0%;height:100%;background:#3b82f6;border-radius:3px;transition:width .3s"></div></div>
    </div>
    <div class="card"><div class="stat-label">Open Positions</div><div class="stat-value" id="p-open-positions">—</div></div>
    <div class="card"><div class="stat-label">Portfolio Health</div><div class="stat-value" id="p-header-health">—</div></div>
  </div>

  <!-- AI Recommendation -->
  <div class="card mb-4" id="p-ai-rec-card" style="display:none;border:1px solid #1d4ed855;background:linear-gradient(135deg,#1e293b 0%,#0f172a 100%)">
    <div style="font-size:13px;font-weight:600;color:#9ca3af;margin-bottom:12px;text-transform:uppercase;letter-spacing:.06em">🧠 AI Portfolio Recommendation</div>
    <div id="p-ai-rec"></div>
  </div>

  <!-- Allocation + Largest Holdings -->
  <div class="grid grid-cols-1 lg:grid-cols-2 gap-4 mb-4">
    <div class="card">
      <div style="font-size:13px;font-weight:600;color:#9ca3af;margin-bottom:16px;text-transform:uppercase;letter-spacing:.06em">💰 Capital Allocation</div>
      <div id="p-capital-legend" style="font-size:12px;color:#d1d5db;margin-bottom:12px"></div>
      <div style="max-height:260px;display:flex;justify-content:center">
        <canvas id="chart-allocation" style="max-height:250px;max-width:250px"></canvas>
      </div>
    </div>
    <div class="card">
      <div style="font-size:13px;font-weight:600;color:#9ca3af;margin-bottom:16px;text-transform:uppercase;letter-spacing:.06em">🏭 Sector Allocation</div>
      <div id="p-sector-legend" style="font-size:12px;color:#d1d5db;margin-bottom:12px"></div>
      <div style="max-height:260px;display:flex;justify-content:center">
        <canvas id="chart-sector" style="max-height:250px;max-width:250px"></canvas>
      </div>
    </div>
  </div>

  <div class="card mb-4">
    <div style="font-size:13px;font-weight:600;color:#9ca3af;margin-bottom:12px;text-transform:uppercase;letter-spacing:.06em">🏆 Largest Holdings</div>
    <div class="grid grid-cols-2 md:grid-cols-5 gap-4" id="p-largest"></div>
  </div>

  <!-- Current Holdings Table -->
  <div class="card mb-4">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px">
      <div style="font-size:13px;font-weight:600;color:#9ca3af;text-transform:uppercase;letter-spacing:.06em">📈 Current Holdings <span id="p-holdings-count" style="color:#3b82f6">(0)</span></div>
      <div style="font-size:11px;color:#6b7280">Click a row to view details</div>
    </div>
    <div style="overflow-x:auto">
      <table style="width:100%;border-collapse:collapse;font-size:13px">
        <thead><tr style="background:#1f2937">
          <th style="text-align:left;padding:10px 8px">Symbol</th>
          <th style="text-align:right;padding:10px 8px">Qty</th>
          <th style="text-align:right;padding:10px 8px">Avg</th>
          <th style="text-align:right;padding:10px 8px">CMP</th>
          <th style="text-align:right;padding:10px 8px">P&L</th>
          <th style="text-align:center;padding:10px 8px">Status</th>
          <th style="text-align:center;padding:10px 8px">Action</th>
        </tr></thead>
        <tbody id="p-holdings"><tr><td colspan="7" style="text-align:center;color:#4b5563;padding:20px">No open positions</td></tr></tbody>
      </table>
    </div>
  </div>

  <!-- Position Details Panel -->
  <div id="p-detail-panel" class="card mb-4" style="display:none;border:1px solid #1d4ed855;background:linear-gradient(135deg,#1e293b 0%,#0f172a 100%)">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px">
      <div style="font-size:15px;font-weight:800;color:#f9fafb" id="p-detail-title">Position Details</div>
      <button onclick="document.getElementById('p-detail-panel').style.display='none'" style="background:transparent;border:none;color:#9ca3af;cursor:pointer;font-size:16px">✕</button>
    </div>
    <div id="p-detail-content"></div>
  </div>

  <!-- Portfolio Health -->
  <div class="card mb-4">
    <div style="font-size:13px;font-weight:600;color:#9ca3af;margin-bottom:12px;text-transform:uppercase;letter-spacing:.06em">🏥 Portfolio Health</div>
    <div class="grid grid-cols-2 md:grid-cols-4 gap-4" id="p-health"></div>
  </div>

  <!-- Risk Analysis -->
  <div class="card mb-4">
    <div style="font-size:13px;font-weight:600;color:#9ca3af;margin-bottom:12px;text-transform:uppercase;letter-spacing:.06em">⚠️ Portfolio Risk</div>
    <div class="grid grid-cols-1 md:grid-cols-3 gap-4" id="p-risk"></div>
    <div style="margin-top:12px" id="p-risk-meter"></div>
  </div>

  <!-- Performance -->
  <div class="card mb-4">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px">
      <div style="font-size:13px;font-weight:600;color:#9ca3af;text-transform:uppercase;letter-spacing:.06em">📈 Performance</div>
      <div id="p-perf-btns" style="display:flex;gap:6px"></div>
    </div>
    <canvas id="chart-portfolio" style="max-height:220px"></canvas>
    <div id="p-perf-note" style="font-size:12px;color:#6b7280;margin-top:8px"></div>
  </div>

  <!-- Top Winner / Loser -->
  <div class="grid grid-cols-1 md:grid-cols-2 gap-4 mb-4">
    <div class="card" id="p-winner"></div>
    <div class="card" id="p-loser"></div>
  </div>

  <!-- Buying Power -->
  <div class="card mb-4" id="p-buying-power"></div>

</div><!-- /tab-portfolio -->


<!-- ===== TAB: POSITIONS ===== -->
<div id="tab-positions" class="tab-content">

  <!-- 1. Portfolio Summary -->
  <div class="card mb-4" id="pos-portfolio-summary">
    <div style="font-size:13px;font-weight:600;color:#9ca3af;margin-bottom:12px;text-transform:uppercase;letter-spacing:.06em">🏦 Portfolio Summary</div>
    <div class="grid grid-cols-2 md:grid-cols-4 gap-4">
      <div class="card-sm"><div class="stat-label">Cash</div><div class="stat-value" id="pos-cash">₹—</div></div>
      <div class="card-sm"><div class="stat-label">Holdings Value</div><div class="stat-value" id="pos-holdings-value">₹—</div></div>
      <div class="card-sm"><div class="stat-label">Total Portfolio</div><div class="stat-value" id="pos-total-value">₹—</div></div>
      <div class="card-sm"><div class="stat-label">Today's P&amp;L</div><div class="stat-value" id="pos-day-pnl">₹—</div></div>
    </div>
  </div>

  <!-- 2. Current Holdings -->
  <div class="card mb-4">
    <div style="font-size:13px;font-weight:600;color:#9ca3af;margin-bottom:12px;text-transform:uppercase;letter-spacing:.06em">💼 Current Holdings</div>
    <div style="overflow-x:auto">
      <table style="width:100%;border-collapse:collapse;font-size:13px">
        <thead><tr style="background:#1f2937">
          <th style="text-align:left;padding:10px 8px">Symbol</th>
          <th style="text-align:right;padding:10px 8px">Qty</th>
          <th style="text-align:right;padding:10px 8px">First Entry</th>
          <th style="text-align:right;padding:10px 8px">Avg Cost</th>
          <th style="text-align:right;padding:10px 8px">CMP</th>
          <th style="text-align:right;padding:10px 8px">Invested</th>
          <th style="text-align:right;padding:10px 8px">Unrealised P&amp;L</th>
          <th style="text-align:right;padding:10px 8px">SL</th>
          <th style="text-align:right;padding:10px 8px">Target</th>
          <th style="text-align:center;padding:10px 8px">Action</th>
        </tr></thead>
        <tbody id="pos-holdings-table"><tr><td colspan="10" style="text-align:center;color:#4b5563;padding:20px">No open positions</td></tr></tbody>
      </table>
    </div>
  </div>

  <!-- 3. Pending Orders -->
  <div class="card mb-4" id="pos-pending-orders-card" style="display:none">
    <div style="font-size:13px;font-weight:600;color:#9ca3af;margin-bottom:12px;text-transform:uppercase;letter-spacing:.06em">⏳ Pending Orders</div>
    <div id="pos-pending-orders">No pending orders</div>
  </div>

  <!-- 4. Current Week Completed Trades -->
  <div class="card mb-4">
    <div style="font-size:13px;font-weight:600;color:#9ca3af;margin-bottom:12px;text-transform:uppercase;letter-spacing:.06em">✅ Current Week Completed Trades</div>
    <div style="overflow-x:auto">
      <table style="width:100%;border-collapse:collapse;font-size:13px">
        <thead><tr style="background:#1f2937">
          <th style="text-align:left;padding:10px 8px">Date</th>
          <th style="text-align:left;padding:10px 8px">Time</th>
          <th style="text-align:left;padding:10px 8px">Symbol</th>
          <th style="text-align:center;padding:10px 8px">Action</th>
          <th style="text-align:right;padding:10px 8px">BUY Price</th>
          <th style="text-align:right;padding:10px 8px">SELL Price</th>
          <th style="text-align:right;padding:10px 8px">Qty</th>
          <th style="text-align:right;padding:10px 8px">Holding Time</th>
          <th style="text-align:right;padding:10px 8px">Net P&amp;L</th>
          <th style="text-align:right;padding:10px 8px">P&amp;L %</th>
          <th style="text-align:left;padding:10px 8px">Exit Reason</th>
          <th style="text-align:right;padding:10px 8px">Brokerage</th>
          <th style="text-align:right;padding:10px 8px">Order IDs</th>
        </tr></thead>
        <tbody id="pos-completed-trades"><tr><td colspan="13" style="text-align:center;color:#4b5563;padding:20px">No completed trades this week</td></tr></tbody>
      </table>
    </div>
  </div>

  <!-- 5. Performance Statistics -->
  <div class="card mb-4">
    <div style="font-size:13px;font-weight:600;color:#9ca3af;margin-bottom:12px;text-transform:uppercase;letter-spacing:.06em">📈 Performance Statistics</div>
    <div class="grid grid-cols-2 md:grid-cols-3 gap-4" id="pos-performance-stats">
      <div class="card-sm"><div class="stat-label">Win Rate</div><div class="stat-value" id="pos-win-rate">—</div></div>
      <div class="card-sm"><div class="stat-label">Avg Profit</div><div class="stat-value" id="pos-avg-profit">₹—</div></div>
      <div class="card-sm"><div class="stat-label">Avg Loss</div><div class="stat-value" id="pos-avg-loss">₹—</div></div>
      <div class="card-sm"><div class="stat-label">Expectancy</div><div class="stat-value" id="pos-expectancy">₹—</div></div>
      <div class="card-sm"><div class="stat-label">Profit Factor</div><div class="stat-value" id="pos-profit-factor">—</div></div>
      <div class="card-sm"><div class="stat-label">Best / Worst</div><div class="stat-value" id="pos-best-worst">—</div></div>
    </div>
  </div>

  <!-- 6. Trade Analytics -->
  <div class="card mb-4">
    <div style="font-size:13px;font-weight:600;color:#9ca3af;margin-bottom:12px;text-transform:uppercase;letter-spacing:.06em">🧮 Trade Analytics</div>
    <div class="grid grid-cols-1 lg:grid-cols-2 gap-4" id="pos-trade-analytics">
      <div class="card-sm" id="pos-sector-pnl"><div class="stat-label">Sector-wise P&amp;L</div><div class="stat-value">—</div></div>
      <div class="card-sm" id="pos-score-vs-result"><div class="stat-label">AI Score vs Actual Result</div><div class="stat-value">—</div></div>
    </div>
  </div>

  <!-- Position Detail Drawer -->
  <div id="pos-detail-overlay" style="display:none;position:fixed;top:0;left:0;width:100%;height:100%;background:#00000066;z-index:40" onclick="closePosDrawer()"></div>
  <div id="pos-detail-drawer" style="position:fixed;top:0;right:-430px;width:400px;max-width:95vw;height:100vh;background:#0f172a;border-left:1px solid #1f2937;z-index:50;overflow-y:auto;padding:20px;transition:right .3s ease">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px">
      <div style="font-size:16px;font-weight:800;color:#f9fafb" id="pos-drawer-title">Position Details</div>
      <button onclick="closePosDrawer()" style="background:transparent;border:none;color:#9ca3af;font-size:18px;cursor:pointer">✕</button>
    </div>
    <div id="pos-drawer-content"></div>
  </div>
</div>


<!-- ===== TAB: HISTORY ===== -->
<div id="tab-history" class="tab-content">

  <!-- Wallet Breakdown -->
  <div class="grid grid-cols-2 md:grid-cols-4 gap-4 mb-4">
    <div class="card"><div class="stat-label">Available Cash</div><div class="stat-value green" id="h-cash">₹—</div><div style="font-size:11px;color:#4b5563;margin-top:4px">Free to trade</div></div>
    <div class="card"><div class="stat-label">Invested in Stocks</div><div class="stat-value blue" id="h-invested">₹—</div><div style="font-size:11px;color:#4b5563;margin-top:4px">Current positions</div></div>
    <div class="card"><div class="stat-label">Holdings Value</div><div class="stat-value" id="h-holdings-val">₹—</div><div style="font-size:11px;color:#4b5563;margin-top:4px">At market price</div></div>
    <div class="card"><div class="stat-label">Total Portfolio</div><div class="stat-value yellow" id="h-total">₹—</div><div style="font-size:11px;color:#4b5563;margin-top:4px">Cash + stocks</div></div>
  </div>

  <!-- Stock-wise Breakdown -->
  <div class="card mb-4">
    <div style="font-size:13px;font-weight:600;color:#9ca3af;margin-bottom:12px;text-transform:uppercase;letter-spacing:.06em">💼 Where Your Money Is</div>
    <div style="overflow-x:auto">
    <table style="width:100%;border-collapse:collapse">
      <thead><tr>
        <th style="text-align:left">Stock</th><th>Qty</th><th>Avg Buy</th>
        <th>Invested</th><th>Current Value</th><th>P&amp;L</th><th>Return</th>
      </tr></thead>
      <tbody id="h-stock-breakdown"><tr><td colspan="7" style="text-align:center;color:#4b5563;padding:20px">Loading...</td></tr></tbody>
    </table>
    </div>
  </div>

  <!-- Open Positions -->
  <div class="card mb-4">
    <div style="font-size:13px;font-weight:600;color:#9ca3af;margin-bottom:12px;text-transform:uppercase;letter-spacing:.06em">📌 Open Positions</div>
    <div id="h-open-positions"><div style="color:#4b5563;padding:20px;text-align:center">No open positions</div></div>
  </div>

  <!-- Automatic Retry Queue -->
  <div class="card mb-4">
    <div style="font-size:13px;font-weight:600;color:#9ca3af;margin-bottom:12px;text-transform:uppercase;letter-spacing:.06em">Automatic Retry Queue</div>
    <div style="overflow-x:auto">
    <table style="width:100%;border-collapse:collapse">
      <thead><tr>
        <th style="text-align:left">Symbol</th>
        <th>Next Retry</th>
        <th>Retry Count</th>
        <th>Last Error</th>
      </tr></thead>
      <tbody id="h-retry-queue"><tr><td colspan="4" style="text-align:center;color:#4b5563;padding:20px">No queued retries</td></tr></tbody>
    </table>
    </div>
  </div>

  <!-- All Time Buy/Sell History -->
  <div class="card mb-4">
    <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:12px">
      <div style="font-size:13px;font-weight:600;color:#9ca3af;text-transform:uppercase;letter-spacing:.06em">📋 Complete Buy / Sell History</div>
      <div style="display:flex;gap:8px">
        <button onclick="filterHistory('ALL')" id="hf-all" style="padding:4px 12px;border-radius:6px;font-size:12px;font-weight:600;background:#1d4ed8;color:#fff;border:none;cursor:pointer">All</button>
        <button onclick="filterHistory('BUY')" id="hf-buy" style="padding:4px 12px;border-radius:6px;font-size:12px;font-weight:600;background:#1f2937;color:#9ca3af;border:none;cursor:pointer">Buys</button>
        <button onclick="filterHistory('SELL')" id="hf-sell" style="padding:4px 12px;border-radius:6px;font-size:12px;font-weight:600;background:#1f2937;color:#9ca3af;border:none;cursor:pointer">Sells</button>
      </div>
    </div>
    <div style="overflow-x:auto">
    <table style="width:100%;border-collapse:collapse">
      <thead><tr>
        <th style="text-align:left">Date</th>
        <th style="text-align:left">Time</th>
        <th style="text-align:left">Symbol</th>
        <th>Action</th>
        <th>Qty</th>
        <th>Buy Price</th>
        <th>Sell Price</th>
        <th>Value</th>
        <th>P&amp;L</th>
        <th>P&amp;L %</th>
        <th>Source</th>
        <th>Order ID</th>
      </tr></thead>
      <tbody id="h-history-table"><tr><td colspan="12" style="text-align:center;color:#4b5563;padding:20px">No history yet</td></tr></tbody>
    </table>
    </div>
  </div>

</div><!-- /tab-history -->


<!-- ===== TAB: TRADE LIFECYCLE ===== -->
<div id="tab-lifecycle" class="tab-content">
  <div class="card mb-4">
    <div style="font-size:13px;font-weight:600;color:#e2e8f0;margin-bottom:12px;text-transform:uppercase;letter-spacing:.06em">🔄 Trade Lifecycle Cockpit</div>
    <div id="lifecycle-container" style="display:grid;grid-template-columns:repeat(auto-fit,minmax(360px,1fr));gap:14px"></div>
  </div>
</div><!-- /tab-lifecycle -->


<!-- ===== TAB: AI SIGNALS ===== -->
<div id="tab-signals" class="tab-content">

<style>
#ais-summary-cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:14px;margin-bottom:18px}
.ais-card{background:linear-gradient(135deg,#111827 0%,#0f1724 100%);border-radius:14px;padding:16px;border:1px solid #1f2937;box-shadow:0 4px 12px rgba(0,0,0,.25)}
.ais-card-title{color:#9ca3af;font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.06em;margin-bottom:8px}
.ais-card-value{font-size:24px;font-weight:800;line-height:1}
.ais-section-title{color:#f9fafb;font-size:14px;font-weight:700;margin-bottom:12px}
.ais-pipeline-row{display:flex;gap:10px;align-items:flex-end;justify-content:space-between}
.ais-pipeline-step{display:flex;flex-direction:column;align-items:center;gap:4px;flex:1}
.ais-funnel-bar{width:100%;border-radius:6px 6px 0 0;min-height:8px}
.ais-funnel-count{font-size:18px;font-weight:800}
.ais-funnel-label{font-size:10px;color:#9ca3af;text-align:center}
.ais-funnel-arrow{color:#4b5563;font-size:20px}
.ais-closest-card{border-radius:10px;padding:12px;margin-bottom:10px}
.ais-table-wrap{max-height:420px;overflow:auto;border-radius:8px;border:1px solid #1f2937}
#ais-candidate-thead{position:sticky;top:0;background:#0f1724;z-index:1}
#ais-candidate-thead th{padding:10px 8px;text-align:left;border-bottom:1px solid #1f2937;font-size:10px;color:#9ca3af}
.ais-candidate-row{cursor:pointer}
.ais-candidate-row:hover td{background:#1f2937}
.ais-sort-btn{cursor:pointer;font-size:11px;margin-left:4px}
.ais-score-pill{border-radius:6px;padding:3px 8px;font-size:12px;font-weight:700;border:1px solid}
.ais-decision-pill{border-radius:99px;padding:3px 8px;font-size:11px;font-weight:700}
.ais-stats-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:10px}
.ais-stat-chip{background:#0f1724;border:1px solid #1f2937;border-radius:10px;padding:10px 14px;display:flex;justify-content:space-between;align-items:center}
.ais-stat-chip b{font-size:18px}
.ais-toolbar{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:12px}
.ais-toolbar input,.ais-toolbar select{background:#0f1724;border:1px solid #374151;color:#e2e8f0;border-radius:8px;padding:8px 12px;font-size:12px;outline:none}
.ais-toolbar input:focus,.ais-toolbar select:focus{border-color:#60a5fa}
.ais-modal{display:none;position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,.7);z-index:999;align-items:center;justify-content:center;padding:20px}
.ais-modal-content{background:#111827;border:1px solid #1f2937;border-radius:16px;max-width:640px;width:100%;max-height:85vh;overflow-y:auto;padding:24px;box-shadow:0 20px 50px rgba(0,0,0,.5)}
.ais-detail-metric{background:#0f1724;border:1px solid #1f2937;border-radius:10px;padding:10px 12px}
.ais-detail-label{font-size:10px;color:#9ca3af;text-transform:uppercase;letter-spacing:.06em;margin-bottom:4px}
.ais-detail-section{margin-top:12px;background:#0f1724;border:1px solid #1f2937;border-radius:10px;padding:12px}
.bg-green-gradient{background:linear-gradient(135deg,#111827,#0f1724);border-color:#22c55e44}
.bg-red-gradient{background:linear-gradient(135deg,#111827,#1f1010);border-color:#ef444444}
.bg-yellow-gradient{background:linear-gradient(135deg,#111827,#1a160a);border-color:#eab30844}
.bg-blue-gradient{background:linear-gradient(135deg,#111827,#0f1724);border-color:#3b82f644}
</style>

  <!-- Why No Trade Today? -->
  <div id="ais-why-no-trade" class="card mb-4" style="display:none"></div>

  <!-- Top summary cards -->
  <div id="ais-summary-cards"></div>

  <!-- Pipeline -->
  <div class="card mb-4">
    <div class="ais-section-title">Trading Pipeline Funnel</div>
    <div id="ais-pipeline" class="ais-pipeline-row">Scanning…</div>
  </div>

  <div class="grid grid-cols-1 lg:grid-cols-2 gap-4 mb-4">
    <!-- Rejection analysis -->
    <div class="card">
      <div class="ais-section-title">Rejection Analysis</div>
      <div id="ais-rejection-bars">—</div>
    </div>

    <!-- Closest BUY opportunities -->
    <div class="card">
      <div class="ais-section-title">Closest BUY Opportunities</div>
      <div id="ais-closest">—</div>
    </div>
  </div>

  <!-- Enhanced Candidate Table -->
  <div class="card mb-4">
    <div class="ais-section-title" style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:8px">
      <span>Enhanced Candidate Table</span>
      <div class="ais-toolbar">
        <input id="ais-search" type="text" placeholder="Search symbol…">
        <select id="ais-filter-sector"><option value="">All Sectors</option></select>
        <select id="ais-filter-decision"><option value="">All Decisions</option>
          <option value="BUY">BUY</option>
          <option value="SELL">SELL</option>
          <option value="WATCH">Watch / Hold</option>
          <option value="SKIP">Skip</option>
        </select>
      </div>
    </div>
    <div class="ais-table-wrap">
      <table style="width:100%;border-collapse:collapse">
        <thead id="ais-candidate-thead"></thead>
        <tbody id="ais-candidate-body"></tbody>
      </table>
    </div>
  </div>

  <!-- Today's Statistics -->
  <div class="card mb-4">
    <div class="ais-section-title">Today's Statistics</div>
    <div id="ais-today-stats" class="ais-stats-grid">—</div>
  </div>

  <!-- Missed Opportunities (optional, after market close) -->
  <div class="card mb-4" id="ais-missed" style="display:none">
    <div class="ais-section-title">Missed Opportunities</div>
    <table style="width:100%;border-collapse:collapse">
      <thead><tr><th>Symbol</th><th>Skip Reason</th><th>Highest Gain After Skip</th><th>Result</th></tr></thead>
      <tbody id="ais-missed-body"></tbody>
    </table>
  </div>

  <!-- Detail modal -->
  <div id="ais-detail-modal" class="ais-modal" onclick="if(event.target===this) closeSignalDetail()">
    <div class="ais-modal-content">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:14px">
        <div class="ais-section-title" style="margin:0">Signal Details</div>
        <button onclick="closeSignalDetail()" style="background:transparent;border:none;color:#9ca3af;font-size:18px;cursor:pointer">✕</button>
      </div>
      <div id="ais-detail-content"></div>
    </div>
  </div>

</div><!-- /tab-signals -->


<!-- ===== TAB: ANALYTICS ===== -->
<div id="tab-analytics" class="tab-content">

  <!-- Performance Summary -->
  <div class="grid grid-cols-2 md:grid-cols-4 gap-4 mb-4">
    <div class="card"><div class="stat-label">Win Rate</div><div class="stat-value" id="a-win-rate">—</div><div style="font-size:11px;color:#6b7280" id="a-win-basis">0 closed trades</div></div>
    <div class="card"><div class="stat-label">Total Closed Trades</div><div class="stat-value" id="a-total-trades">—</div></div>
    <div class="card"><div class="stat-label">Net P&amp;L</div><div class="stat-value" id="a-net-pnl">—</div></div>
    <div class="card"><div class="stat-label">AI Accuracy</div><div class="stat-value" id="a-ai-accuracy">—</div></div>
  </div>

  <!-- Trading + Risk Stats -->
  <div class="grid grid-cols-2 md:grid-cols-4 gap-4 mb-4">
    <div class="card"><div class="stat-label" title="Profit Factor = Gross Profit / Gross Loss. It can be &#x221e; (infinity) when there are no losing trades; high values mean wins are much larger than losses.">Profit Factor <span style="cursor:help;color:#94a3b8">&#9432;</span></div><div class="stat-value green" id="a-profit-factor">—</div></div>
    <div class="card"><div class="stat-label">Average Win</div><div class="stat-value green" id="a-avg-win">—</div></div>
    <div class="card"><div class="stat-label">Average Loss</div><div class="stat-value red" id="a-avg-loss">—</div></div>
    <div class="card"><div class="stat-label">Expectancy</div><div class="stat-value" id="a-expectancy">—</div></div>
  </div>

  <div class="grid grid-cols-2 md:grid-cols-4 gap-4 mb-4">
    <div class="card"><div class="stat-label">Avg Hold (days)</div><div class="stat-value" id="a-avg-hold">—</div></div>
    <div class="card"><div class="stat-label">Sharpe Ratio</div><div class="stat-value" id="a-sharpe">—</div></div>
    <div class="card"><div class="stat-label">Sortino Ratio</div><div class="stat-value" id="a-sortino">—</div></div>
    <div class="card"><div class="stat-label">Max Drawdown</div><div class="stat-value red" id="a-max-drawdown">—</div></div>
  </div>

  <!-- Portfolio Growth + Win Rate Gauge -->
  <div class="grid grid-cols-1 lg:grid-cols-2 gap-4 mb-4">
    <div class="card">
      <div style="font-size:13px;font-weight:600;color:#9ca3af;margin-bottom:16px;text-transform:uppercase;letter-spacing:.06em">📈 Portfolio Growth</div>
      <canvas id="chart-portfolio-growth" style="max-height:220px"></canvas>
    </div>
    <div class="card">
      <div style="font-size:13px;font-weight:600;color:#9ca3af;margin-bottom:16px;text-transform:uppercase;letter-spacing:.06em">📊 Win Rate Gauge</div>
      <canvas id="chart-winrate" style="max-height:220px"></canvas>
    </div>
  </div>

  <!-- Trade Calendar -->
  <div class="card mb-4">
    <div style="font-size:13px;font-weight:600;color:#9ca3af;margin-bottom:12px;text-transform:uppercase;letter-spacing:.06em">📅 Trade Calendar (This Week)</div>
    <div id="a-calendar" class="flex gap-2 justify-around"></div>
  </div>

  <!-- Completed Trades -->
  <div class="card mb-4">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px">
      <div style="font-size:13px;font-weight:600;color:#9ca3af;text-transform:uppercase;letter-spacing:.06em">✅ Completed Trades</div>
      <div style="font-size:11px;color:#6b7280">BUY &rarr; SELL pairs only</div>
    </div>
    <div style="overflow-x:auto">
    <table style="width:100%;border-collapse:collapse;font-size:12px">
      <thead><tr style="background:#1f2937">
        <th style="text-align:left;padding:8px">Symbol</th>
        <th style="text-align:left;padding:8px">Buy</th>
        <th style="text-align:left;padding:8px">Sell</th>
        <th style="text-align:center;padding:8px">Qty</th>
        <th style="text-align:right;padding:8px">P&amp;L</th>
        <th style="text-align:center;padding:8px">Days</th>
        <th style="text-align:left;padding:8px">Exit</th>
      </tr></thead>
      <tbody id="a-completed-trades"><tr><td colspan="7" style="text-align:center;color:#4b5563;padding:20px">No completed trades</td></tr></tbody>
    </table>
    </div>
  </div>

  <!-- Sector & Exit Performance -->
  <div class="grid grid-cols-1 lg:grid-cols-2 gap-4 mb-4">
    <div class="card">
      <div style="font-size:13px;font-weight:600;color:#9ca3af;margin-bottom:12px;text-transform:uppercase;letter-spacing:.06em">🏭 Performance by Sector</div>
      <div id="a-sector-table">—</div>
    </div>
    <div class="card">
      <div style="font-size:13px;font-weight:600;color:#9ca3af;margin-bottom:12px;text-transform:uppercase;letter-spacing:.06em">🚪 Performance by Exit Reason</div>
      <div id="a-exit-table">—</div>
    </div>
  </div>

  <!-- Order History (collapsible rejected) -->
  <div class="card mb-4">
    <div style="font-size:13px;font-weight:600;color:#9ca3af;margin-bottom:12px;text-transform:uppercase;letter-spacing:.06em">📋 Order History</div>
    <div style="overflow-x:auto">
    <table style="width:100%;border-collapse:collapse;font-size:12px">
      <thead><tr style="background:#1f2937">
        <th style="text-align:left;padding:8px">Time</th>
        <th style="text-align:left;padding:8px">Symbol</th>
        <th style="text-align:center;padding:8px">Side</th>
        <th style="text-align:center;padding:8px">Qty</th>
        <th style="text-align:center;padding:8px">Status</th>
      </tr></thead>
      <tbody id="a-order-history"><tr><td colspan="5" style="text-align:center;color:#4b5563;padding:20px">No orders</td></tr></tbody>
    </table>
    </div>
    <details style="margin-top:16px" id="a-exceptions-wrap">
      <summary style="cursor:pointer;color:#f59e0b;font-size:13px;font-weight:600">⚠️ Order Exceptions (<span id="a-rejected-count">0</span>)</summary>
      <div style="margin-top:10px;overflow-x:auto">
      <table style="width:100%;border-collapse:collapse;font-size:12px">
        <thead><tr style="background:#1f2937">
          <th style="text-align:left;padding:8px">Time</th>
          <th style="text-align:left;padding:8px">Symbol</th>
          <th style="text-align:center;padding:8px">Side</th>
          <th style="text-align:center;padding:8px">Qty</th>
          <th style="text-align:center;padding:8px">Status</th>
        </tr></thead>
        <tbody id="a-rejected-orders"><tr><td colspan="5" style="text-align:center;color:#4b5563;padding:20px">No rejected orders</td></tr></tbody>
      </table>
      </div>
    </details>
  </div>

</div><!-- /tab-analytics -->


<!-- ===== TAB: TRADE JOURNAL ===== -->
<div id="tab-journal" class="tab-content">

  <!-- Top KPIs -->
  <div class="grid grid-cols-2 md:grid-cols-4 gap-4 mb-4">
    <div class="card"><div class="stat-label">Total Trades</div><div class="stat-value blue" id="j-total">—</div></div>
    <div class="card"><div class="stat-label">Win Rate</div><div class="stat-value" id="j-winrate">—</div></div>
    <div class="card"><div class="stat-label">Net P&amp;L (All Time)</div><div class="stat-value" id="j-netpnl">—</div></div>
    <div class="card"><div class="stat-label" title="Profit Factor = Gross Profit / Gross Loss. It can be &#x221e; (infinity) when there are no losing trades; high values mean wins are much larger than losses.">Profit Factor <span style="cursor:help;color:#94a3b8">&#9432;</span></div><div class="stat-value green" id="j-pf">—</div></div>
  </div>
  <div class="grid grid-cols-2 md:grid-cols-4 gap-4 mb-4">
    <div class="card"><div class="stat-label">Avg Win</div><div class="stat-value green" id="j-avgwin">—</div></div>
    <div class="card"><div class="stat-label">Avg Loss</div><div class="stat-value red" id="j-avgloss">—</div></div>
    <div class="card"><div class="stat-label">Avg Score</div><div class="stat-value yellow" id="j-avgscore">—</div></div>
    <div class="card"><div class="stat-label">Avg Hold Days</div><div class="stat-value" id="j-avghold">—</div></div>
  </div>
  <div class="grid grid-cols-2 md:grid-cols-4 gap-4 mb-4">
    <div class="card"><div class="stat-label">Longest Trade</div><div class="stat-value" id="j-longest">—</div></div>
    <div class="card"><div class="stat-label">Shortest Trade</div><div class="stat-value" id="j-shortest">—</div></div>
    <div class="card"><div class="stat-label">Avg Winner Hold</div><div class="stat-value green" id="j-avgwinhold">—</div></div>
    <div class="card"><div class="stat-label">Avg Loser Hold</div><div class="stat-value red" id="j-avglosehold">—</div></div>
  </div>

  <!-- Charts row 1: Cumulative P&L + By Score Bucket -->
  <div class="grid grid-cols-1 lg:grid-cols-2 gap-4 mb-4">
    <div class="card">
      <div style="font-size:13px;font-weight:600;color:#9ca3af;margin-bottom:16px;text-transform:uppercase;letter-spacing:.06em">📈 Cumulative Net P&amp;L</div>
      <canvas id="j-chart-cumulative" style="max-height:200px"></canvas>
    </div>
    <div class="card">
      <div style="font-size:13px;font-weight:600;color:#9ca3af;margin-bottom:16px;text-transform:uppercase;letter-spacing:.06em">🏆 Win Rate by Score Bucket</div>
      <canvas id="j-chart-scorebucket" style="max-height:200px"></canvas>
    </div>
  </div>

  <!-- Charts row 2: By Sector + By Exit Reason -->
  <div class="grid grid-cols-1 lg:grid-cols-2 gap-4 mb-4">
    <div class="card">
      <div style="font-size:13px;font-weight:600;color:#9ca3af;margin-bottom:16px;text-transform:uppercase;letter-spacing:.06em">🏭 P&amp;L by Sector</div>
      <canvas id="j-chart-sector" style="max-height:200px"></canvas>
    </div>
    <div class="card">
      <div style="font-size:13px;font-weight:600;color:#9ca3af;margin-bottom:16px;text-transform:uppercase;letter-spacing:.06em">🚪 P&amp;L by Exit Reason</div>
      <canvas id="j-chart-exit" style="max-height:200px"></canvas>
    </div>
  </div>

  <!-- Charts row 3: By Day of Week + By Regime -->
  <div class="grid grid-cols-1 lg:grid-cols-2 gap-4 mb-4">
    <div class="card">
      <div style="font-size:13px;font-weight:600;color:#9ca3af;margin-bottom:16px;text-transform:uppercase;letter-spacing:.06em">📅 P&amp;L by Day of Week</div>
      <canvas id="j-chart-dow" style="max-height:200px"></canvas>
    </div>
    <div class="card">
      <div style="font-size:13px;font-weight:600;color:#9ca3af;margin-bottom:16px;text-transform:uppercase;letter-spacing:.06em">🌍 Win Rate by Market Regime</div>
      <canvas id="j-chart-regime" style="max-height:200px"></canvas>
    </div>
  </div>

  <!-- Recent Trades Table -->
  <div class="card mb-4">
    <div style="font-size:13px;font-weight:600;color:#9ca3af;margin-bottom:12px;text-transform:uppercase;letter-spacing:.06em">📋 Trade Log (Last 20)</div>
    <div style="overflow-x:auto">
    <table style="width:100%;border-collapse:collapse">
      <thead><tr>
        <th style="text-align:left">Date</th>
        <th style="text-align:left">Symbol</th>
        <th>Status</th>
        <th>Score</th>
        <th>Regime</th>
        <th>Sector</th>
        <th>Entry</th>
        <th>Exit</th>
        <th>Holding</th>
        <th>Sentiment</th>
        <th>RSI</th>
        <th>Trend</th>
        <th>Exit Reason</th>
        <th>Net P&amp;L</th>
      </tr></thead>
      <tbody id="j-trade-log"><tr><td colspan="14" style="text-align:center;color:#4b5563;padding:20px">Loading journal…</td></tr></tbody>
    </table>
    </div>
  </div>

</div><!-- /tab-journal -->


<!-- ===== TAB: SKIPPED OPPORTUNITIES ===== -->
<div id="tab-skipped" class="tab-content">

  <!-- Top KPIs -->
  <div class="grid grid-cols-2 md:grid-cols-4 gap-4 mb-4">
    <div class="card"><div class="stat-label">Total Evaluated</div><div class="stat-value blue" id="s-total-evaluated">—</div></div>
    <div class="card"><div class="stat-label">Skipped</div><div class="stat-value orange" id="s-skipped">—</div></div>
    <div class="card"><div class="stat-label">BUYs Executed Today</div><div class="stat-value green" id="s-executed">—</div></div>
    <div class="card"><div class="stat-label">Skip Rate</div><div class="stat-value red" id="s-skip-rate">—</div></div>
  </div>

  <!-- Rejection Reasons Summary -->
  <div class="card mb-4">
    <h3 style="color:#f9fafb;font-size:16px;margin-bottom:12px">📊 Rejection Reasons Summary</h3>
    <div id="s-rejection-reasons" style="font-size:13px;color:#9ca3af">Loading...</div>
  </div>

  <!-- Skipped Opportunities Table -->
  <div class="card">
    <h3 style="color:#f9fafb;font-size:16px;margin-bottom:12px">⚠️ Skipped Opportunities</h3>
    <div style="overflow-x:auto">
      <table style="width:100%;border-collapse:collapse;font-size:12px">
        <thead>
          <tr style="background:#1f2937">
            <th style="padding:8px;text-align:left;color:#f9fafb">Symbol</th>
            <th style="padding:8px;text-align:left;color:#f9fafb">Score</th>
            <th style="padding:8px;text-align:left;color:#f9fafb">Confidence</th>
            <th style="padding:8px;text-align:left;color:#f9fafb">R:R</th>
            <th style="padding:8px;text-align:left;color:#f9fafb">Rejection Reason</th>
            <th style="padding:8px;text-align:left;color:#f9fafb">Entry Price</th>
            <th style="padding:8px;text-align:left;color:#f9fafb">Sector</th>
            <th style="padding:8px;text-align:left;color:#f9fafb">Time</th>
          </tr>
        </thead>
        <tbody id="s-skipped-table">
          <tr><td colspan="8" style="text-align:center;color:#4b5563;padding:20px">Loading skipped opportunities...</td></tr>
        </tbody>
      </table>
    </div>
  </div>

  <!-- Detailed Decision View -->
  <div class="card mt-4">
    <h3 style="color:#f9fafb;font-size:16px;margin-bottom:12px">🔍 Detailed Decision Analysis</h3>
    <div id="s-detailed-decisions" style="font-size:13px;color:#9ca3af">Click on a stock to see detailed analysis...</div>
  </div>

</div><!-- /tab-skipped -->


<!-- ===== TAB: AI EXPLAINABILITY ===== -->
<div id="tab-explain" class="tab-content">
  <div class="card mb-4">
    <h3 style="color:#f9fafb;font-size:16px;margin-bottom:12px">🔍 AI Explainability — Why the bot acted</h3>
    <p style="color:#9ca3af;font-size:13px;margin:0">Latest BUY, SELL, HOLD and SKIP decisions with the reason recorded by the engine.</p>
  </div>

  <div class="card">
    <div style="overflow-x:auto">
      <table style="width:100%;border-collapse:collapse;font-size:12px">
        <thead>
          <tr style="background:#1f2937">
            <th style="padding:8px;text-align:left;color:#f9fafb">Time</th>
            <th style="padding:8px;text-align:left;color:#f9fafb">Symbol</th>
            <th style="padding:8px;text-align:left;color:#f9fafb">Action</th>
            <th style="padding:8px;text-align:left;color:#f9fafb">Reason</th>
            <th style="padding:8px;text-align:left;color:#f9fafb">Score</th>
            <th style="padding:8px;text-align:left;color:#f9fafb">MIS</th>
            <th style="padding:8px;text-align:left;color:#f9fafb">Conf.</th>
            <th style="padding:8px;text-align:left;color:#f9fafb">P&L</th>
            <th style="padding:8px;text-align:left;color:#f9fafb">Price</th>
            <th style="padding:8px;text-align:left;color:#f9fafb">Qty</th>
            <th style="padding:8px;text-align:left;color:#f9fafb">Sector</th>
          </tr>
        </thead>
        <tbody id="explain-table">
          <tr><td colspan="11" style="text-align:center;color:#4b5563;padding:20px">Loading AI explanations...</td></tr>
        </tbody>
      </table>
    </div>
  </div>
</div><!-- /tab-explain -->


<!-- ===== TAB: ASK AI ===== -->
<div id="tab-askai" class="tab-content">
  <div class="card chat-wrap" style="padding:0;overflow:hidden">

    <!-- Header -->
    <div style="padding:16px 20px;border-bottom:1px solid #1f2937;display:flex;align-items:center;gap:12px">
      <span style="font-size:22px">🧠</span>
      <div>
        <div style="font-size:15px;font-weight:700;color:#f9fafb">AI Trade Assistant</div>
        <div style="font-size:12px;color:#4b5563">Ask anything about your trades, positions, signals, or strategy</div>
      </div>
      <div id="ai-status-dot" style="margin-left:auto;width:9px;height:9px;border-radius:50%;background:#22c55e" title="Ready"></div>
    </div>

    <!-- Suggested questions -->
    <div style="padding:12px 16px;border-bottom:1px solid #1f2937;background:#0a0f1e">
      <div style="font-size:11px;color:#4b5563;margin-bottom:8px;text-transform:uppercase;letter-spacing:.06em">Quick Questions</div>
      <div class="chip-row" id="ai-chips">
        <span class="chip" onclick="chipAsk(this)">Why did we buy this stock?</span>
        <span class="chip" onclick="chipAsk(this)">Why did we sell Reliance?</span>
        <span class="chip" onclick="chipAsk(this)">What is our current market regime?</span>
        <span class="chip" onclick="chipAsk(this)">Which sector is performing best?</span>
        <span class="chip" onclick="chipAsk(this)">What is my win rate?</span>
        <span class="chip" onclick="chipAsk(this)">Why was my last trade skipped?</span>
        <span class="chip" onclick="chipAsk(this)">Show recent P&L summary</span>
        <span class="chip" onclick="chipAsk(this)">Which indicators are working?</span>
      </div>
    </div>

    <!-- Message area -->
    <div class="chat-msgs" id="chat-msgs">
      <div class="msg-ai">
        <div class="ai-label">AI Assistant</div>
        <div>Hello! I can explain every trade decision this bot makes. Ask me <b>why we bought or sold any stock</b>, what the <b>market regime</b> is, how <b>indicators</b> influenced a trade, or get a <b>P&amp;L summary</b>. I have full access to your trade journal, open positions, and the latest signals.</div>
      </div>
    </div>

    <!-- Input row -->
    <div class="chat-input-row">
      <input class="chat-input" id="chat-input" type="text" placeholder="e.g. Why did we buy BEL? or Why was INFY skipped?" autocomplete="off"
        onkeydown="if(event.key==='Enter')sendChat()"/>
      <button class="chat-send" id="chat-send-btn" onclick="sendChat()">Send ↑</button>
    </div>

  </div>
</div><!-- /tab-askai -->


<!-- ===== TAB: MARKET INTELLIGENCE ===== -->
<div id="tab-market-intelligence" class="tab-content">
  <div class="card mb-4">
    <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:12px">
      <h3 style="color:#f9fafb;font-size:16px;margin:0">🌐 Unified Market Intelligence</h3>
      <div id="mi-score" style="font-size:22px;font-weight:800;color:#60a5fa">—</div>
    </div>
    <div class="grid grid-cols-2 md:grid-cols-5 gap-3" id="mi-grid">
      <!-- Populated by JS -->
    </div>
  </div>
</div><!-- /tab-market-intelligence -->


<!-- ===== TAB: BOT STATUS ===== -->
<div id="tab-botstatus" class="tab-content">

  <div class="grid grid-cols-2 md:grid-cols-3 gap-4 mb-4">
    <div class="card"><div class="stat-label">Kite Connected</div><div class="stat-value" id="bs-kite">—</div></div>
    <div class="card"><div class="stat-label">Trading Mode</div><div class="stat-value blue" id="bs-mode">—</div></div>
    <div class="card"><div class="stat-label">Token Expiry</div><div class="stat-value" id="bs-token">—</div></div>
    <div class="card"><div class="stat-label">Paper Trading</div><div class="stat-value" id="bs-paper">—</div></div>
    <div class="card"><div class="stat-label">Market Regime</div><div class="stat-value" id="bs-regime">—</div></div>
    <div class="card"><div class="stat-label">Budget</div><div class="stat-value yellow" id="bs-budget">—</div></div>
  </div>

  <!-- Bot Activity -->
  <div class="card mb-4">
    <div style="font-size:13px;font-weight:600;color:#9ca3af;margin-bottom:12px;text-transform:uppercase;letter-spacing:.06em">⚡ Bot Activity</div>
    <div class="grid grid-cols-2 md:grid-cols-3 gap-4">
      <div class="card-sm flex items-center gap-3">
        <span style="font-size:20px">🕐</span>
        <div><div class="stat-label">Last Scan</div><div style="font-size:14px;font-weight:600" id="bs-last-scan">—</div></div>
      </div>
      <div class="card-sm flex items-center gap-3">
        <span style="font-size:20px">🔍</span>
        <div><div class="stat-label">Stocks Scanned</div><div style="font-size:14px;font-weight:600" id="bs-scanned">—</div></div>
      </div>
      <div class="card-sm flex items-center gap-3">
        <span style="font-size:20px">🧠</span>
        <div><div class="stat-label">AI Signals</div><div style="font-size:14px;font-weight:600" id="bs-ai-signals">—</div></div>
      </div>
      <div class="card-sm flex items-center gap-3">
        <span style="font-size:20px">✅</span>
        <div><div class="stat-label">Orders Executed</div><div style="font-size:14px;font-weight:600" id="bs-orders-exec">—</div></div>
      </div>
      <div class="card-sm flex items-center gap-3">
        <span style="font-size:20px">📅</span>
        <div><div class="stat-label">Token Expiry</div><div style="font-size:14px;font-weight:600" id="bs-token2">—</div></div>
      </div>
      <div class="card-sm flex items-center gap-3">
        <span style="font-size:20px">⏭️</span>
        <div><div class="stat-label">Next Scan</div><div style="font-size:14px;font-weight:600" id="bs-next-scan">—</div></div>
      </div>
    </div>
  </div>

  <!-- Config -->
  <div class="card mb-4">
    <div style="font-size:13px;font-weight:600;color:#9ca3af;margin-bottom:12px;text-transform:uppercase;letter-spacing:.06em">⚙️ Configuration</div>
    <div class="grid grid-cols-2 md:grid-cols-4 gap-3">
      <div class="card-sm"><div class="stat-label">Trading Amount</div><div style="font-size:14px;font-weight:600" id="bs-cfg-amount">—</div></div>
      <div class="card-sm"><div class="stat-label">Max Positions</div><div style="font-size:14px;font-weight:600" id="bs-cfg-maxpos">—</div></div>
      <div class="card-sm"><div class="stat-label">Min Confidence</div><div style="font-size:14px;font-weight:600" id="bs-cfg-conf">—</div></div>
      <div class="card-sm"><div class="stat-label">Risk Per Trade</div><div style="font-size:14px;font-weight:600" id="bs-cfg-risk">—</div></div>
      <div class="card-sm"><div class="stat-label">Stop Loss %</div><div style="font-size:14px;font-weight:600" id="bs-cfg-sl">—</div></div>
      <div class="card-sm"><div class="stat-label">Target %</div><div style="font-size:14px;font-weight:600" id="bs-cfg-tgt">—</div></div>
      <div class="card-sm"><div class="stat-label">Max Capital Use</div><div style="font-size:14px;font-weight:600" id="bs-cfg-cap">—</div></div>
      <div class="card-sm"><div class="stat-label">Daily Loss Limit</div><div style="font-size:14px;font-weight:600" id="bs-cfg-loss">—</div></div>
      <div class="card-sm"><div class="stat-label">Max Hold Days</div><div style="font-size:14px;font-weight:600" id="bs-cfg-holddays">—</div></div>
      <div class="card-sm"><div class="stat-label">Re-entry Cooldown</div><div style="font-size:14px;font-weight:600" id="bs-cfg-reentry">—</div></div>
      <div class="card-sm"><div class="stat-label">Scan Interval</div><div style="font-size:14px;font-weight:600">15 min</div></div>
    </div>
  </div>

  <!-- Health Monitor -->
  <div class="card mb-4">
    <div style="font-size:13px;font-weight:600;color:#9ca3af;margin-bottom:12px;text-transform:uppercase;letter-spacing:.06em">🩺 Health Monitor</div>
    <div class="grid grid-cols-2 md:grid-cols-4 gap-3">
      <div class="card-sm"><div class="stat-label">Today's P&amp;L</div><div style="font-size:15px;font-weight:700" id="bs-daily-pnl">—</div></div>
      <div class="card-sm"><div class="stat-label">Drawdown</div><div style="font-size:15px;font-weight:700" id="bs-drawdown">—</div></div>
      <div class="card-sm"><div class="stat-label">Errors Today</div><div style="font-size:15px;font-weight:700" id="bs-errors">—</div></div>
      <div class="card-sm"><div class="stat-label">Circuit Breaker</div><div style="font-size:15px;font-weight:700" id="bs-circuit">—</div></div>
      <div class="card-sm"><div class="stat-label">API Latency</div><div style="font-size:15px;font-weight:700" id="bs-latency">—</div></div>
      <div class="card-sm"><div class="stat-label">Memory Usage</div><div style="font-size:15px;font-weight:700" id="bs-mem">—</div></div>
      <div class="card-sm"><div class="stat-label">CPU</div><div style="font-size:15px;font-weight:700" id="bs-cpu">—</div></div>
      <div class="card-sm"><div class="stat-label">Win Rate (Journal)</div><div style="font-size:15px;font-weight:700 green" id="bs-winrate">—</div></div>
    </div>
  </div>

  <!-- Positions Capacity -->
  <div class="card mb-4">
    <div style="font-size:13px;font-weight:600;color:#9ca3af;margin-bottom:10px;text-transform:uppercase;letter-spacing:.06em">📊 Position Capacity</div>
    <div style="display:flex;align-items:center;gap:12px">
      <div style="flex:1;background:#1f2937;border-radius:8px;height:18px;overflow:hidden">
        <div id="bs-pos-bar" style="height:100%;background:#3b82f6;border-radius:8px;transition:width .4s"></div>
      </div>
      <div id="bs-pos-label" style="font-size:14px;font-weight:700;color:#f9fafb;min-width:60px;text-align:right">—</div>
    </div>
    <div style="display:flex;justify-content:space-between;font-size:11px;color:#4b5563;margin-top:6px">
      <span>0</span><span id="bs-pos-max">— max</span>
    </div>
  </div>

  <!-- Reconciliation Monitor -->
  <div class="card mb-4">
    <div style="font-size:13px;font-weight:600;color:#9ca3af;margin-bottom:12px;text-transform:uppercase;letter-spacing:.06em">🔄 Reconciliation Status</div>
    <div class="grid grid-cols-2 md:grid-cols-4 gap-3">
      <div class="card-sm"><div class="stat-label">Health</div><div style="font-size:15px;font-weight:700" id="rs-healthy">—</div></div>
      <div class="card-sm"><div class="stat-label">Last Sync</div><div style="font-size:14px;font-weight:600" id="rs-last-sync">—</div></div>
      <div class="card-sm"><div class="stat-label">Objects Checked</div><div style="font-size:14px;font-weight:600" id="rs-objects">—</div></div>
      <div class="card-sm"><div class="stat-label">Repairs</div><div style="font-size:14px;font-weight:600" id="rs-repairs">—</div></div>
      <div class="card-sm"><div class="stat-label">Mismatches</div><div style="font-size:14px;font-weight:600" id="rs-mismatches">—</div></div>
      <div class="card-sm"><div class="stat-label">Duration</div><div style="font-size:14px;font-weight:600" id="rs-duration">—</div></div>
      <div class="card-sm"><div class="stat-label">SQLite</div><div style="font-size:14px;font-weight:600" id="rs-sqlite">—</div></div>
      <div class="card-sm"><div class="stat-label">Broker Status</div><div style="font-size:14px;font-weight:600" id="rs-broker">—</div></div>
      <div class="card-sm"><div class="stat-label">Broker/SQLite Sync</div><div style="font-size:15px;font-weight:700" id="rs-sync">—</div></div>
    </div>
  </div>

  <div style="text-align:right;font-size:11px;color:#374151;padding:8px 0">
    <a href="/api/data" style="color:#374151;text-decoration:underline">Raw API JSON</a> &nbsp;|
    <a href="/api/health" style="color:#374151;text-decoration:underline">Health JSON</a> &nbsp;|
    <a href="/api/reconciliation/status" style="color:#374151;text-decoration:underline">Reconciliation JSON</a>
  </div>

</div><!-- /tab-botstatus -->

<!-- ═══ IP STATUS TAB ═══════════════════════════════════════════════════════ -->
<div id="tab-ipstatus" class="tab-content">
  <div style="max-width:920px;margin:0 auto;padding:8px 0">

    <!-- ── Status Banner ─────────────────────────────────────────────────── -->
    <div id="ip-banner" style="border-radius:12px;padding:20px 24px;margin-bottom:20px;background:#1e293b;border:2px solid #334155;display:flex;align-items:center;gap:16px">
      <div id="ip-banner-icon" style="font-size:36px">🔄</div>
      <div style="flex:1">
        <div id="ip-banner-title" style="font-size:18px;font-weight:700;color:#f1f5f9;margin-bottom:4px">Checking Network Status…</div>
        <div id="ip-banner-sub" style="font-size:13px;color:#94a3b8">Please wait</div>
      </div>
      <div style="text-align:right">
        <div style="color:#64748b;font-size:11px;margin-bottom:6px">TRADING STATUS</div>
        <div id="ip-trading-status" style="font-size:15px;font-weight:800;letter-spacing:.03em">—</div>
      </div>
      <button onclick="refreshIpStatus()" style="background:#3b82f6;color:#fff;border:none;border-radius:8px;padding:10px 18px;font-size:13px;font-weight:600;cursor:pointer;white-space:nowrap">🔄 Refresh</button>
    </div>

    <!-- ── Kite Authentication Status Banner ───────────────────────────── -->
    <div id="auth-status-card" style="border-radius:12px;padding:18px 24px;margin-bottom:20px;border:2px solid #334155;background:#1e293b;display:flex;align-items:center;gap:16px;flex-wrap:wrap">
      <div id="auth-status-icon" style="font-size:32px">⏳</div>
      <div style="flex:1;min-width:180px">
        <div style="font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.08em;color:#64748b;margin-bottom:4px">Kite Authentication</div>
        <div id="auth-status-text" style="font-size:17px;font-weight:700;color:#f1f5f9">Checking…</div>
        <div id="auth-status-sub" style="font-size:12px;color:#64748b;margin-top:3px">—</div>
      </div>
      <div style="text-align:right;min-width:140px">
        <div style="font-size:10px;color:#64748b;margin-bottom:4px">TOKEN EXPIRES</div>
        <div id="auth-token-expiry" style="font-size:13px;font-weight:600;color:#94a3b8;font-family:monospace">—</div>
        <div id="auth-token-ttl" style="font-size:11px;color:#64748b;margin-top:2px">—</div>
      </div>
      <a id="auth-reauth-btn" href="#" onclick="startKiteAuth(this);return false;"
         style="display:none;background:#3b82f6;color:#fff;border-radius:8px;padding:10px 18px;font-size:13px;font-weight:700;text-decoration:none;white-space:nowrap">
        🔐 Re-authenticate Now
      </a>
    </div>

    <!-- ── 6-Card Grid ───────────────────────────────────────────────────── -->
    <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:14px;margin-bottom:20px">

      <!-- Current Public IPv4 -->
      <div style="background:#1e293b;border-radius:12px;padding:18px;border:1px solid #334155">
        <div style="color:#94a3b8;font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.08em;margin-bottom:8px">Current Public IPv4</div>
        <div id="ip-current" style="font-size:20px;font-weight:700;color:#f1f5f9;font-family:monospace;word-break:break-all">—</div>
        <div id="ip-current-status" style="font-size:12px;margin-top:6px"></div>
        <div id="ip-verified-at" style="font-size:11px;color:#475569;margin-top:3px"></div>
      </div>

      <!-- Current Public IPv6 -->
      <div style="background:#1e293b;border-radius:12px;padding:18px;border:1px solid #334155">
        <div style="color:#94a3b8;font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.08em;margin-bottom:8px">Current Public IPv6</div>
        <div id="ip-v6" style="font-size:14px;font-weight:600;color:#94a3b8;font-family:monospace;word-break:break-all">—</div>
        <div style="font-size:11px;color:#475569;margin-top:6px">Not used by Kite (IPv4 only)</div>
      </div>

      <!-- Kite Resolution -->
      <div style="background:#1e293b;border-radius:12px;padding:18px;border:1px solid #334155">
        <div style="color:#94a3b8;font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.08em;margin-bottom:8px">Kite Resolution</div>
        <div id="ip-kite-res" style="font-size:20px;font-weight:700;color:#22c55e">—</div>
        <div style="font-size:11px;color:#475569;margin-top:6px">api.kite.trade → AF_INET</div>
      </div>

      <!-- Network -->
      <div style="background:#1e293b;border-radius:12px;padding:18px;border:1px solid #334155">
        <div style="color:#94a3b8;font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.08em;margin-bottom:8px">Network</div>
        <div id="ip-network" style="font-size:20px;font-weight:700;color:#60a5fa">—</div>
        <div id="ip-latency" style="font-size:11px;color:#475569;margin-top:6px">Kite latency: —</div>
      </div>

      <!-- Last Successful Order -->
      <div style="background:#1e293b;border-radius:12px;padding:18px;border:1px solid #334155">
        <div style="color:#94a3b8;font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.08em;margin-bottom:8px">Last Successful Order</div>
        <div id="ip-last-order" style="font-size:16px;font-weight:700;color:#f1f5f9;font-family:monospace">—</div>
        <div style="font-size:11px;color:#475569;margin-top:6px">From trade journal</div>
      </div>

      <!-- Last Successful API Call -->
      <div style="background:#1e293b;border-radius:12px;padding:18px;border:1px solid #334155">
        <div style="color:#94a3b8;font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.08em;margin-bottom:8px">Last Successful API Call</div>
        <div id="ip-last-api" style="font-size:16px;font-weight:700;color:#f1f5f9;font-family:monospace">—</div>
        <div style="font-size:11px;color:#475569;margin-top:6px">Heartbeat check</div>
      </div>

    </div><!-- /6-card grid -->

    <!-- ── Quick Action Links (always visible) ──────────────────────────── -->
    <div style="background:#1e293b;border-radius:12px;padding:18px 20px;border:1px solid #334155;margin-bottom:20px;display:flex;align-items:center;gap:16px;flex-wrap:wrap">
      <div style="font-size:13px;font-weight:600;color:#94a3b8;flex:1 1 160px">🔧 Kite Quick Actions</div>
      <a href="https://developers.kite.trade/profile" target="_blank"
         style="display:inline-block;background:#f59e0b;color:#000;border-radius:8px;padding:9px 20px;font-size:13px;font-weight:700;text-decoration:none;white-space:nowrap">
        🔗 Update IP Whitelist
      </a>
      <a href="#" onclick="startKiteAuth(this); return false;"
         style="display:inline-block;background:#3b82f6;color:#fff;border-radius:8px;padding:9px 20px;font-size:13px;font-weight:700;text-decoration:none;white-space:nowrap">
        🔐 Re-authenticate Kite
      </a>
    </div>

    <!-- ── IP Changed Alert Box ──────────────────────────────────────────── -->
    <div id="ip-action-box" style="background:#1a1206;border-radius:12px;padding:22px;border:2px solid #f59e0b;margin-bottom:20px;display:none">
      <div style="font-size:16px;font-weight:700;color:#f59e0b;margin-bottom:6px">⚠️ Public IPv4 Changed — Orders May Fail!</div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;margin:16px 0">
        <div style="background:#0f172a;border-radius:8px;padding:14px">
          <div style="color:#64748b;font-size:11px;margin-bottom:4px">OLD (whitelisted)</div>
          <div id="ip-action-old" style="color:#ef4444;font-size:18px;font-weight:700;font-family:monospace"></div>
        </div>
        <div style="background:#0f172a;border-radius:8px;padding:14px">
          <div style="color:#64748b;font-size:11px;margin-bottom:4px">NEW (current)</div>
          <div id="ip-action-new" style="color:#34d399;font-size:18px;font-weight:700;font-family:monospace"></div>
        </div>
      </div>
      <div style="font-size:13px;color:#cbd5e1;margin-bottom:16px">Add the new IP to Kite Developer Console → all orders will resume automatically.</div>
      <div style="display:flex;gap:12px;flex-wrap:wrap">
        <a href="https://developers.kite.trade/profile" target="_blank"
           style="display:inline-block;background:#f59e0b;color:#000;border-radius:8px;padding:10px 22px;font-size:14px;font-weight:700;text-decoration:none">
          🔗 Open Kite Console &rarr;
        </a>
        <a href="#" onclick="startKiteAuth(this); return false;"
           style="display:inline-block;background:#3b82f6;color:#fff;border-radius:8px;padding:10px 22px;font-size:14px;font-weight:700;text-decoration:none">
          🔐 Re-authenticate Kite &rarr;
        </a>
      </div>
      <div style="font-size:11px;color:#64748b;margin-top:12px">After whitelisting → click Refresh above to confirm.</div>
    </div>

    <!-- ── Instructions ──────────────────────────────────────────────────── -->
    <div style="background:#1e293b;border-radius:12px;padding:20px;border:1px solid #334155;margin-bottom:16px">
      <div style="font-size:14px;font-weight:600;color:#f1f5f9;margin-bottom:14px">📋 How to Update IP in Kite (30 seconds)</div>
      <ol style="color:#94a3b8;font-size:13px;line-height:2.2;padding-left:20px;margin:0">
        <li>Click <b style="color:#f59e0b">Open Kite Console</b> in the alert box above</li>
        <li>Go to your app → <b style="color:#f1f5f9">IP Whitelist</b> section</li>
        <li>Remove old IP, paste new IP, press <b style="color:#f1f5f9">Enter</b> → click <b style="color:#34d399">Save</b></li>
        <li>If orders still fail → click <b style="color:#3b82f6">Re-authenticate Kite</b> to get a fresh token</li>
        <li>Come back here → click <b style="color:#3b82f6">Refresh</b></li>
      </ol>
      <div style="margin-top:14px;padding:12px;background:#0f172a;border-radius:8px;font-size:12px;color:#64748b">
        💡 <b style="color:#94a3b8">Why does IP change?</b> Your ISP assigns a dynamic IP — it can change on router restart or randomly. Consider asking your ISP for a static IP to avoid this permanently.
      </div>
    </div>

    <!-- ── History Table ─────────────────────────────────────────────────── -->
    <div style="background:#1e293b;border-radius:12px;padding:20px;border:1px solid #334155">
      <div style="font-size:12px;font-weight:700;color:#94a3b8;text-transform:uppercase;letter-spacing:.06em;margin-bottom:12px">IP Change History</div>
      <div id="ip-history" style="font-size:13px;color:#64748b">Loading…</div>
    </div>

  </div>
</div><!-- /tab-ipstatus -->

<!-- ═══ BACKTEST TAB ════════════════════════════════════════════════════════ -->
<div id="tab-backtest" class="tab-content">
  <div style="max-width:1100px;margin:0 auto;padding:12px 0">

    <!-- Header -->
    <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:20px">
      <div>
        <div style="font-size:22px;font-weight:700;color:#f1f5f9">📈 Backtesting Engine</div>
        <div style="font-size:13px;color:#64748b;margin-top:4px">Replay live signal logic on historical Kite OHLCV data — no external data sources</div>
      </div>
      <div id="bt-status-badge" style="font-size:12px;color:#64748b;background:#1e293b;padding:6px 14px;border-radius:20px">Idle</div>
    </div>

    <!-- Controls -->
    <div style="background:#1e293b;border-radius:12px;padding:20px;margin-bottom:20px;border:1px solid #334155">
      <div style="display:grid;grid-template-columns:1fr 160px 160px;gap:16px;align-items:end">
        <div>
          <div style="color:#94a3b8;font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.08em;margin-bottom:6px">Symbols (comma-separated)</div>
          <input id="bt-symbols" type="text" placeholder="RELIANCE,INFY,TCS,HDFCBANK,ICICIBANK"
            style="width:100%;background:#0f172a;border:1px solid #334155;border-radius:8px;padding:10px 12px;color:#f1f5f9;font-size:13px;font-family:monospace;box-sizing:border-box">
        </div>
        <div>
          <div style="color:#94a3b8;font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.08em;margin-bottom:6px">Look-back</div>
          <select id="bt-years" style="width:100%;background:#0f172a;border:1px solid #334155;border-radius:8px;padding:10px 12px;color:#f1f5f9;font-size:13px">
            <option value="1">1 Year</option>
            <option value="2" selected>2 Years</option>
            <option value="3">3 Years</option>
            <option value="5">5 Years</option>
          </select>
        </div>
        <div>
          <button id="bt-run-btn" onclick="runBacktest()"
            style="width:100%;background:#3b82f6;color:#fff;border:none;border-radius:8px;padding:10px 16px;font-size:14px;font-weight:700;cursor:pointer">
            ▶ Run Backtest
          </button>
        </div>
      </div>
      <div style="margin-top:12px">
        <div style="color:#94a3b8;font-size:11px;margin-bottom:4px">Quick sets:</div>
        <div style="display:flex;gap:8px;flex-wrap:wrap">
          <button onclick="btQuick('RELIANCE,INFY,TCS,HDFCBANK,ICICIBANK,WIPRO,HDFC,AXISBANK,BAJFINANCE,KOTAKBANK')" style="background:#1a2744;border:1px solid #334155;color:#93c5fd;padding:4px 10px;border-radius:6px;font-size:11px;cursor:pointer">Nifty Large-cap 10</button>
          <button onclick="btQuick('BEL,ANDHRSUGAR,BRIGADE,METROPOLIS,RELIANCE,KALYANKJIL,IFGLEXPOR')" style="background:#1a2744;border:1px solid #334155;color:#93c5fd;padding:4px 10px;border-radius:6px;font-size:11px;cursor:pointer">My Current Holdings</button>
          <button onclick="btQuick('TATAMOTORS,BAJAJ-AUTO,MARUTI,HEROMOTOCO,EICHERMOT,TATASTEEL,JSWSTEEL,HINDALCO,VEDL,SAIL')" style="background:#1a2744;border:1px solid #334155;color:#93c5fd;padding:4px 10px;border-radius:6px;font-size:11px;cursor:pointer">Auto + Metals</button>
        </div>
      </div>
    </div>

    <!-- Progress bar (hidden until run) -->
    <div id="bt-progress-wrap" style="display:none;margin-bottom:20px">
      <div style="color:#94a3b8;font-size:12px;margin-bottom:6px" id="bt-progress-msg">Fetching data…</div>
      <div style="background:#1e293b;border-radius:4px;height:8px">
        <div id="bt-progress-bar" style="background:#3b82f6;height:8px;border-radius:4px;width:0%;transition:width .3s"></div>
      </div>
    </div>

    <!-- Summary cards (hidden until result) -->
    <div id="bt-summary" style="display:none">

      <!-- KPI row 1 -->
      <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:12px">
        <div style="background:#1e293b;border-radius:10px;padding:16px;border:1px solid #334155;text-align:center">
          <div style="color:#94a3b8;font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.08em">CAGR</div>
          <div id="bt-cagr" style="font-size:26px;font-weight:800;margin-top:6px">—</div>
        </div>
        <div style="background:#1e293b;border-radius:10px;padding:16px;border:1px solid #334155;text-align:center">
          <div style="color:#94a3b8;font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.08em">Win Rate</div>
          <div id="bt-winrate" style="font-size:26px;font-weight:800;margin-top:6px">—</div>
        </div>
        <div style="background:#1e293b;border-radius:10px;padding:16px;border:1px solid #334155;text-align:center">
          <div style="color:#94a3b8;font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.08em">Max Drawdown</div>
          <div id="bt-mdd" style="font-size:26px;font-weight:800;margin-top:6px">—</div>
        </div>
        <div style="background:#1e293b;border-radius:10px;padding:16px;border:1px solid #334155;text-align:center">
          <div style="color:#94a3b8;font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.08em">Profit Factor</div>
          <div id="bt-pf" style="font-size:26px;font-weight:800;margin-top:6px">—</div>
        </div>
      </div>

      <!-- KPI row 2 -->
      <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:20px">
        <div style="background:#1e293b;border-radius:10px;padding:14px;border:1px solid #334155;text-align:center">
          <div style="color:#94a3b8;font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.08em">Sharpe</div>
          <div id="bt-sharpe" style="font-size:20px;font-weight:700;color:#f1f5f9;margin-top:4px">—</div>
        </div>
        <div style="background:#1e293b;border-radius:10px;padding:14px;border:1px solid #334155;text-align:center">
          <div style="color:#94a3b8;font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.08em">Sortino</div>
          <div id="bt-sortino" style="font-size:20px;font-weight:700;color:#f1f5f9;margin-top:4px">—</div>
        </div>
        <div style="background:#1e293b;border-radius:10px;padding:14px;border:1px solid #334155;text-align:center">
          <div style="color:#94a3b8;font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.08em">Avg Hold Days</div>
          <div id="bt-hold" style="font-size:20px;font-weight:700;color:#f1f5f9;margin-top:4px">—</div>
        </div>
        <div style="background:#1e293b;border-radius:10px;padding:14px;border:1px solid #334155;text-align:center">
          <div style="color:#94a3b8;font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.08em">Total Trades</div>
          <div id="bt-trades" style="font-size:20px;font-weight:700;color:#f1f5f9;margin-top:4px">—</div>
        </div>
      </div>

      <!-- Capital summary -->
      <div style="background:#1e293b;border-radius:10px;padding:16px;border:1px solid #334155;margin-bottom:20px;display:flex;gap:32px;align-items:center">
        <div><div style="color:#64748b;font-size:11px">Initial Capital</div><div id="bt-initial" style="font-size:16px;font-weight:700;color:#f1f5f9;font-family:monospace">—</div></div>
        <div style="font-size:24px;color:#334155">→</div>
        <div><div style="color:#64748b;font-size:11px">Final Capital</div><div id="bt-final" style="font-size:16px;font-weight:700;font-family:monospace">—</div></div>
        <div style="margin-left:auto"><div style="color:#64748b;font-size:11px">Total P&amp;L</div><div id="bt-pnl" style="font-size:20px;font-weight:800;font-family:monospace">—</div></div>
        <div><div style="color:#64748b;font-size:11px">Period</div><div id="bt-period" style="font-size:13px;color:#94a3b8">—</div></div>
      </div>

      <!-- Equity curve chart -->
      <div style="background:#1e293b;border-radius:10px;padding:16px;border:1px solid #334155;margin-bottom:20px">
        <div style="color:#94a3b8;font-size:11px;font-weight:700;text-transform:uppercase;margin-bottom:12px">Equity Curve</div>
        <canvas id="bt-equity-chart" height="80"></canvas>
      </div>

      <!-- Breakdown grids -->
      <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:16px;margin-bottom:20px">

        <!-- By Regime -->
        <div style="background:#1e293b;border-radius:10px;padding:16px;border:1px solid #334155">
          <div style="color:#94a3b8;font-size:11px;font-weight:700;text-transform:uppercase;margin-bottom:10px">By Market Regime</div>
          <div id="bt-by-regime"></div>
        </div>

        <!-- By Confidence -->
        <div style="background:#1e293b;border-radius:10px;padding:16px;border:1px solid #334155">
          <div style="color:#94a3b8;font-size:11px;font-weight:700;text-transform:uppercase;margin-bottom:10px">By Confidence Bucket</div>
          <div id="bt-by-conf"></div>
        </div>

        <!-- By Exit Reason -->
        <div style="background:#1e293b;border-radius:10px;padding:16px;border:1px solid #334155">
          <div style="color:#94a3b8;font-size:11px;font-weight:700;text-transform:uppercase;margin-bottom:10px">By Exit Reason</div>
          <div id="bt-by-exit"></div>
        </div>
      </div>

      <!-- Trade log -->
      <div style="background:#1e293b;border-radius:10px;padding:16px;border:1px solid #334155">
        <div style="color:#94a3b8;font-size:11px;font-weight:700;text-transform:uppercase;margin-bottom:10px">All Simulated Trades</div>
        <div style="overflow-x:auto;max-height:340px;overflow-y:auto">
          <table style="width:100%;border-collapse:collapse;font-size:12px">
            <thead><tr style="color:#64748b;font-size:10px;text-transform:uppercase;position:sticky;top:0;background:#1e293b">
              <th style="text-align:left;padding:5px 8px">Symbol</th>
              <th style="text-align:left;padding:5px 8px">Entry</th>
              <th style="text-align:left;padding:5px 8px">Exit</th>
              <th style="text-align:right;padding:5px 8px">Entry ₹</th>
              <th style="text-align:right;padding:5px 8px">Exit ₹</th>
              <th style="text-align:right;padding:5px 8px">P&amp;L ₹</th>
              <th style="text-align:right;padding:5px 8px">P&amp;L %</th>
              <th style="text-align:right;padding:5px 8px">Days</th>
              <th style="text-align:left;padding:5px 8px">Regime</th>
              <th style="text-align:left;padding:5px 8px">Exit Reason</th>
            </tr></thead>
            <tbody id="bt-trade-log"></tbody>
          </table>
        </div>
      </div>

    </div><!-- /bt-summary -->

    <!-- Error box -->
    <div id="bt-error" style="display:none;background:#1a0f0f;border:1px solid #ef4444;border-radius:10px;padding:16px;color:#ef4444;font-size:13px"></div>

  </div>
</div><!-- /tab-backtest -->

<!-- ===== TAB: PORTFOLIO OPTIMIZER ===== -->
<div id="tab-portfolio-optimizer" class="tab-content">
  <div class="card mb-4">
    <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:12px">
      <h3 style="color:#f9fafb;font-size:16px;margin:0">📊 Portfolio Optimizer</h3>
      <div id="po-div" style="font-size:22px;font-weight:800">—</div>
    </div>
    <div class="grid grid-cols-2 md:grid-cols-5 gap-3" id="po-grid">
      <!-- Populated by JS -->
    </div>
  </div>
  <div class="card mb-4">
    <div style="font-size:13px;font-weight:600;color:#9ca3af;margin-bottom:12px;text-transform:uppercase;letter-spacing:.06em">Suggested Allocation</div>
    <div id="po-allocation" style="color:#f9fafb;font-size:13px">—</div>
  </div>
  <div class="card mb-4">
    <div style="font-size:13px;font-weight:600;color:#9ca3af;margin-bottom:12px;text-transform:uppercase;letter-spacing:.06em">Correlation Heatmap</div>
    <div id="po-corr" style="overflow-x:auto;font-size:12px">—</div>
  </div>
</div><!-- /tab-portfolio-optimizer -->

<!-- ===== TAB: BACKTESTING ===== -->
<div id="tab-backtesting" class="tab-content">
  <div class="card mb-4">
    <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:12px">
      <h3 style="color:#f9fafb;font-size:16px;margin:0">🧪 Enterprise Backtesting</h3>
      <button class="btn" onclick="refreshBacktesting()">Refresh</button>
    </div>
    <div class="grid grid-cols-2 md:grid-cols-4 gap-3" id="bt-kpi">
      <!-- Populated by JS -->
    </div>
  </div>

  <div class="grid grid-cols-1 lg:grid-cols-2 gap-4">
    <div class="card mb-4">
      <div style="font-size:13px;font-weight:600;color:#9ca3af;margin-bottom:12px;text-transform:uppercase;letter-spacing:.06em">Equity Curve</div>
      <div id="bt-equity-curve" style="overflow-x:auto;font-size:11px;color:#f9fafb">—</div>
    </div>
    <div class="card mb-4">
      <div style="font-size:13px;font-weight:600;color:#9ca3af;margin-bottom:12px;text-transform:uppercase;letter-spacing:.06em">Drawdown Curve</div>
      <div id="bt-drawdown-curve" style="overflow-x:auto;font-size:11px;color:#f9fafb">—</div>
    </div>
  </div>

  <div class="grid grid-cols-1 lg:grid-cols-2 gap-4">
    <div class="card mb-4">
      <div style="font-size:13px;font-weight:600;color:#9ca3af;margin-bottom:12px;text-transform:uppercase;letter-spacing:.06em">Monthly Returns</div>
      <div id="bt-monthly" style="overflow-x:auto;font-size:11px;color:#f9fafb">—</div>
    </div>
    <div class="card mb-4">
      <div style="font-size:13px;font-weight:600;color:#9ca3af;margin-bottom:12px;text-transform:uppercase;letter-spacing:.06em">Walk-Forward Results</div>
      <div id="bt-walkforward" style="overflow-x:auto;font-size:11px;color:#f9fafb">—</div>
    </div>
  </div>

  <div class="card mb-4">
    <div style="font-size:13px;font-weight:600;color:#9ca3af;margin-bottom:12px;text-transform:uppercase;letter-spacing:.06em">Monte Carlo Distribution</div>
    <div id="bt-monte" style="overflow-x:auto;font-size:11px;color:#f9fafb">—</div>
  </div>

  <div class="card mb-4">
    <div style="font-size:13px;font-weight:600;color:#9ca3af;margin-bottom:12px;text-transform:uppercase;letter-spacing:.06em">Strategy Comparison</div>
    <div id="bt-compare" style="overflow-x:auto;font-size:11px;color:#f9fafb">—</div>
  </div>
</div><!-- /tab-backtesting -->

<!-- ===== TAB: AI LEARNING ===== -->
<div id="tab-ai-learning" class="tab-content">
  <div class="card mb-4">
    <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:12px">
      <h3 style="color:#f9fafb;font-size:16px;margin:0">🧠 AI Learning Engine</h3>
      <div id="ai-last-retrain" style="font-size:12px;color:#9ca3af">—</div>
    </div>
    <div class="grid grid-cols-2 md:grid-cols-4 gap-3" id="ai-metrics">
      <!-- Populated by JS -->
    </div>
  </div>

  <div class="grid grid-cols-1 lg:grid-cols-2 gap-4">
    <div class="card mb-4">
      <div style="font-size:13px;font-weight:600;color:#9ca3af;margin-bottom:12px;text-transform:uppercase;letter-spacing:.06em">Top Predictive Indicators</div>
      <div id="ai-top" style="overflow-x:auto;font-size:11px;color:#f9fafb">—</div>
    </div>
    <div class="card mb-4">
      <div style="font-size:13px;font-weight:600;color:#9ca3af;margin-bottom:12px;text-transform:uppercase;letter-spacing:.06em">Worst Indicators</div>
      <div id="ai-worst" style="overflow-x:auto;font-size:11px;color:#f9fafb">—</div>
    </div>
  </div>

  <div class="grid grid-cols-1 lg:grid-cols-2 gap-4">
    <div class="card mb-4">
      <div style="font-size:13px;font-weight:600;color:#9ca3af;margin-bottom:12px;text-transform:uppercase;letter-spacing:.06em">Feature Importance</div>
      <div id="ai-importance" style="overflow-x:auto;font-size:11px;color:#f9fafb">—</div>
    </div>
    <div class="card mb-4">
      <div style="font-size:13px;font-weight:600;color:#9ca3af;margin-bottom:12px;text-transform:uppercase;letter-spacing:.06em">Model Weights</div>
      <div id="ai-weights" style="overflow-x:auto;font-size:11px;color:#f9fafb">—</div>
    </div>
  </div>

  <div class="card mb-4">
    <div style="font-size:13px;font-weight:600;color:#9ca3af;margin-bottom:12px;text-transform:uppercase;letter-spacing:.06em">Learning Curve</div>
    <div id="ai-curve" style="overflow-x:auto;font-size:11px;color:#f9fafb">—</div>
  </div>
</div><!-- /tab-ai-learning -->

<!-- ===== TAB: MONITORING ===== -->
<div id="tab-monitoring" class="tab-content">
  <div class="card mb-4">
    <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:12px">
      <h3 style="color:#f9fafb;font-size:16px;margin:0">🚨 System Monitoring</h3>
      <div id="mon-score" style="font-size:22px;font-weight:700">—</div>
    </div>
    <div class="grid grid-cols-2 md:grid-cols-4 gap-3" id="mon-kpi">
      <!-- Populated by JS -->
    </div>
  </div>

  <div class="grid grid-cols-1 lg:grid-cols-2 gap-4">
    <div class="card mb-4">
      <div style="font-size:13px;font-weight:600;color:#9ca3af;margin-bottom:12px;text-transform:uppercase;letter-spacing:.06em">Component Status</div>
      <div id="mon-status" style="font-size:12px;color:#f9fafb">—</div>
    </div>
    <div class="card mb-4">
      <div style="font-size:13px;font-weight:600;color:#9ca3af;margin-bottom:12px;text-transform:uppercase;letter-spacing:.06em">Heartbeat Log</div>
      <div id="mon-heartbeat" style="overflow-x:auto;font-size:11px;color:#f9fafb">—</div>
    </div>
  </div>

  <div class="card mb-4">
    <div style="font-size:13px;font-weight:600;color:#9ca3af;margin-bottom:12px;text-transform:uppercase;letter-spacing:.06em">Alerts</div>
    <div id="mon-alerts" style="overflow-x:auto;font-size:11px;color:#f9fafb">—</div>
  </div>
</div><!-- /tab-monitoring -->

<!-- ===== TAB: SMART EXECUTION ===== -->
<div id="tab-smart-execution" class="tab-content">
  <div class="card mb-4">
    <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:12px">
      <h3 style="color:#f9fafb;font-size:16px;margin:0">⚡ Smart Execution Engine</h3>
      <div id="exec-quality" style="font-size:22px;font-weight:700">—</div>
    </div>
    <div class="grid grid-cols-2 md:grid-cols-4 gap-3" id="exec-kpi">
      <!-- Populated by JS -->
    </div>
  </div>

  <div class="grid grid-cols-1 lg:grid-cols-2 gap-4">
    <div class="card mb-4">
      <div style="font-size:13px;font-weight:600;color:#9ca3af;margin-bottom:12px;text-transform:uppercase;letter-spacing:.06em">Order Queue</div>
      <div id="exec-queue" style="overflow-x:auto;font-size:11px;color:#f9fafb">—</div>
    </div>
    <div class="card mb-4">
      <div style="font-size:13px;font-weight:600;color:#9ca3af;margin-bottom:12px;text-transform:uppercase;letter-spacing:.06em">Today's Orders</div>
      <div id="exec-orders" style="overflow-x:auto;font-size:11px;color:#f9fafb">—</div>
    </div>
  </div>

  <div class="card mb-4">
    <div style="font-size:13px;font-weight:600;color:#9ca3af;margin-bottom:12px;text-transform:uppercase;letter-spacing:.06em">Execution Analytics</div>
    <div id="exec-analytics" style="overflow-x:auto;font-size:11px;color:#f9fafb">—</div>
  </div>
</div><!-- /tab-smart-execution -->

</div><!-- /main container -->

<script>
// ─── Utilities ────────────────────────────────────────────────────────────────
function rupee(v){return '₹'+parseFloat(v||0).toLocaleString('en-IN',{minimumFractionDigits:2,maximumFractionDigits:2});}
function pct(v,dec=2){let n=parseFloat(v||0);return (n>=0?'+':'')+n.toFixed(dec)+'%';}
function pnlStr(v){let n=parseFloat(v||0);return (n>=0?'+':'-')+rupee(Math.abs(n));}
function pnlClass(v){return parseFloat(v)>=0?'green':'red';}
function _toDate(iso){if(!iso)return null;const d=new Date(iso);return isNaN(d)?null:d;}
function fmtDateTime(iso,short){
  const d=_toDate(iso); if(!d)return '—';
  const days=['Sun','Mon','Tue','Wed','Thu','Fri','Sat'];
  const day=days[d.getDay()];
  const dd=String(d.getDate()).padStart(2,'0');
  const mm=String(d.getMonth()+1).padStart(2,'0');
  const yy=d.getFullYear();
  const h=String(d.getHours()).padStart(2,'0');
  const m=String(d.getMinutes()).padStart(2,'0');
  return short?`${day} ${dd}-${mm}-${yy} ${h}:${m}`:`${day} ${dd}-${mm}-${yy}, ${h}:${m}`;
}
function fmtHoldDate(iso){
  const d=_toDate(iso); if(!d)return '—';
  const months=['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
  const dd=String(d.getDate()).padStart(2,'0');
  const mmm=months[d.getMonth()];
  const yy=d.getFullYear();
  return `${dd}-${mmm}-${yy}`;
}
function fmtTime(iso){
  const d=_toDate(iso); if(!d)return '—';
  const h=String(d.getHours()).padStart(2,'0');
  const m=String(d.getMinutes()).padStart(2,'0');
  return `${h}:${m}`;
}
function fmtDayShort(name){const m={'Monday':'Mon','Tuesday':'Tue','Wednesday':'Wed','Thursday':'Thu','Friday':'Fri','Saturday':'Sat','Sunday':'Sun'};return m[name]||String(name||'').slice(0,3)||'—';}
function scoreColor(s){if(s>=80)return '#22c55e';if(s>=60)return '#eab308';return '#ef4444';}
function riskLabel(rr){if(rr>=2)return '<span class="green">Low</span>';if(rr>=1)return '<span class="yellow">Medium</span>';return '<span class="red">High</span>';}
function toggleRejected(){
  const body=document.getElementById('s-rejected-body');
  const chev=document.getElementById('s-rejected-chevron');
  if(!body) return;
  const open=body.style.display!=='none';
  body.style.display=open?'none':'block';
  if(chev) chev.style.transform=open?'':'rotate(180deg)';
}
function switchTab(id,btn){
  document.querySelectorAll('.tab-content').forEach(t=>{t.classList.remove('active');t.style.display=''});
  document.querySelectorAll('.tab-btn').forEach(b=>b.classList.remove('active'));
  const tabEl=document.getElementById('tab-'+id);
  if(tabEl){tabEl.classList.add('active');tabEl.style.display='block';}
  if(btn)btn.classList.add('active');
  if(id==='ipstatus') refreshIpStatus();
  if(id==='portfolio') load();
  if(id==='positions') load();
  if(id==='lifecycle') load();
  if(id==='history'){
    if(window._lastData){ window._historyData=window._lastData.trade_events||[]; renderHistory(window._historyFilter||'ALL'); }
    load();
  }
  if(id==='analytics') load();
  if(id==='signals'){ if (typeof renderAiSignals === 'function') renderAiSignals(window._lastData); }
  if(id==='morning') loadMorningReport();
  if(id==='journal') loadJournal();
  if(id==='skipped') loadSkippedOpportunities();
  if(id==='explain') loadExplainability();
  if(id==='market-intelligence') loadMarketIntelligence();
  if(id==='portfolio-optimizer') loadPortfolioOptimizer();
  if(id==='backtesting') loadBacktesting();
  if(id==='backtest') loadBacktesting();
  if(id==='ai-learning') loadAiLearning();
  if(id==='botstatus') loadMonitoring();
  if(id==='monitoring') loadMonitoring();
  if(id==='smart-execution') loadSmartExecution();
}

// ── Morning Intelligence Report ───────────────────────────────────────────────
let morningDataCache=null;

async function loadMorningReport(){
  try{
    const [mr,live]=await Promise.all([
      fetch('/api/morning-report').then(r=>r.json()),
      fetch('/api/data').then(r=>r.json())
    ]);
    const merged={...mr,live:live,lastUpdated:new Date()};
    morningDataCache=merged;
    renderMorningReport(merged);
  }catch(e){console.error('Morning report load error:',e);}
}

async function refreshMorningReport(){
  document.getElementById('mr-generating-badge').style.display='inline-block';
  try{
    await fetch('/api/morning-report/refresh',{method:'POST'});
    await loadMorningReport();
  }catch(e){console.error('Refresh morning report error:',e);}
  finally{document.getElementById('mr-generating-badge').style.display='none';}
}

function renderMorningReport(d){
  const rpt=d.report||{};
  const live=d.live||{};
  const generating=d.generating;
  const mo=live.market_overview||rpt.market_overview||{};
  const p=rpt.trading_plan||{};

  const badge=document.getElementById('mr-generating-badge');
  if(badge) badge.style.display=generating?'inline-block':'none';

  const genAt=document.getElementById('mr-generated-at');
  if(genAt){
    genAt.textContent=rpt.generated_at?'Generated: '+new Date(rpt.generated_at).toLocaleTimeString('en-IN',{hour:'2-digit',minute:'2-digit'}):'Generated: —';
  }
  const lastUp=document.getElementById('mr-last-updated');
  if(lastUp){
    lastUp.textContent=d.lastUpdated?'Last updated: '+new Date(d.lastUpdated).toLocaleTimeString('en-IN',{hour:'2-digit',minute:'2-digit'}):'Last updated: —';
  }

  const vixLevel=(live.vix_risk||{}).risk_level||mo.vix_label||'Moderate';
  const regime=live.market_regime||mo.regime||'SIDEWAYS';
  const riskMode=p.risk_mode||'Selective';
  const rm=riskMode.toLowerCase();
  const dayCall=rm==='aggressive'?'Aggressive Buy Day':(rm==='selective'||rm==='moderate')?'Selective Buy Day':(rm==='defensive'||rm==='conservative')?'Defensive Day':'No Trade Day';
  const dayColor=dayCall.includes('Aggressive')?'#22c55e':dayCall.includes('Defensive')?'#ef4444':'#eab308';

  // ── 0. Today's Theme
  const themeEl=document.getElementById('mr-theme');
  if(themeEl){
    const vixLevel=(live.vix_risk||{}).risk_level||mo.vix_label||'Moderate';
    const regime=live.market_regime||mo.regime||'SIDEWAYS';
    const bullets=[];
    bullets.push(`Market Bias: ${regime}`);
    bullets.push(`${vixLevel} volatility`);
    const mb=live.market_breadth||{};
    if(mb.breadth_score!=null) bullets.push(mb.breadth_score>=50?'Healthy breadth':mb.breadth_score>=30?'Mixed breadth':'Weak breadth');
    bullets.push(rm==='aggressive'?'Aggressive momentum preferred':(rm==='defensive'||rm==='conservative')?'Protect capital — reduce size':rm==='moderate'?'Moderate momentum with selectivity':'Momentum with selectivity');
    if(p.avoid_sectors&&p.avoid_sectors.length) bullets.push(`Avoid ${p.avoid_sectors.join(' · ')}`);
    else bullets.push('Avoid weak sectors');
    themeEl.innerHTML=`
      <div style="font-size:22px;font-weight:800;color:${dayColor};margin-bottom:8px">${dayCall}</div>
      <div style="display:flex;flex-wrap:wrap;gap:8px">
        ${bullets.map(b=>`<span style="background:#1f2937;color:#d1d5db;padding:4px 10px;border-radius:6px;font-size:12px">${b}</span>`).join('')}
      </div>`;
  }

  // ── 1. Today's Trading Plan (biggest card)
  const planEl=document.getElementById('mr-plan');
  if(planEl){
    const bias=mo.sentiment||p.regime||regime||'Sideways';
    const exposure=rm==='aggressive'?'80%':(rm==='selective'||rm==='moderate')?'60%':(rm==='defensive'||rm==='conservative')?'30%':'0%';
    const riskLevel=(vixLevel||'Moderate').toUpperCase();
    const expTrades=p.expected_trades||'2-4';
    const strategy=rm==='aggressive'?'Momentum Breakout':(rm==='defensive'||rm==='conservative')?'Protect Capital / Small Size':(rm==='moderate'?'Moderate momentum with selectivity':'Momentum with Selectivity');
    const avoid=p.avoid_sectors&&p.avoid_sectors.length?p.avoid_sectors.join(' · '):'None';
    const capDep=rm==='aggressive'?'Full':(rm==='selective'||rm==='moderate')?'Moderate':(rm==='defensive'||rm==='conservative')?'Minimal':'None';
    const execRecs=(live.recommendations||[]).filter(s=>s.action==='BUY');
    const picks=execRecs.length?execRecs:(rpt.ai_top_picks||[]).slice(0,5);
    const confAvg=picks.length?Math.min(100,Math.round(picks.reduce((s,x)=>{const c=parseFloat(x.confidence||0); const conf=c>100?c/100:c; return s+conf;},0)/picks.length)):'N/A';

    planEl.innerHTML=`
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">
        <div style="font-size:24px;font-weight:800;color:${dayColor}">${dayCall}</div>
        <div style="font-size:13px;color:#9ca3af">AI Confidence <b style="color:#f9fafb">${confAvg}%</b></div>
      </div>
      <div style="display:grid;grid-template-columns:repeat(2,1fr);gap:8px;margin-bottom:10px">
        <div class="card-sm"><div class="stat-label">Market Bias</div><b style="color:#f9fafb">${bias}</b></div>
        <div class="card-sm"><div class="stat-label">Recommended Exposure</div><b style="color:#f9fafb">${exposure}</b></div>
        <div class="card-sm"><div class="stat-label">Risk Level</div><b style="color:${riskLevel==='LOW'?'#22c55e':riskLevel==='HIGH'?'#ef4444':'#eab308'}">${riskLevel}</b></div>
        <div class="card-sm"><div class="stat-label">Expected Trades</div><b style="color:#f9fafb">${expTrades}</b></div>
        <div class="card-sm"><div class="stat-label">Best Strategy</div><b style="color:#f9fafb">${strategy}</b></div>
        <div class="card-sm"><div class="stat-label">Avoid</div><b style="color:#ef4444">${avoid}</b></div>
        <div class="card-sm" style="grid-column:span 2"><div class="stat-label">Capital Deployment</div><b style="color:#f9fafb">${capDep}</b></div>
      </div>
      ${p.preferred_sectors&&p.preferred_sectors.length?`<div style="margin-bottom:6px"><span style="color:#4b5563;font-size:11px">✅ Preferred Sectors: </span><b style="color:#22c55e;font-size:12px">${p.preferred_sectors.join(' · ')}</b></div>`:''}
      <div style="font-size:12px;color:#9ca3af;border-top:1px solid #1f2937;padding-top:8px;margin-top:4px">${p.note||mo.note||'Pre-market analysis complete. Trade selectively.'}</div>`;
  }

  // ── 2. Market Checklist
  const checkEl=document.getElementById('mr-checklist');
  if(checkEl){
    const items=[];
    const mb=live.market_breadth||{};
    const ad=parseFloat(mb.ad_ratio);
    if(!isNaN(ad)){
      if(ad>1.5) items.push({icon:'✅',name:'Market Breadth',reason:`A/D ratio ${ad.toFixed(2)} is healthy`});
      else if(ad>1) items.push({icon:'⚠',name:'Market Breadth',reason:`A/D ratio ${ad.toFixed(2)} is neutral`});
      else items.push({icon:'❌',name:'Market Breadth',reason:`A/D ratio ${ad.toFixed(2)} is weak`});
    } else { items.push({icon:'❌',name:'Market Breadth',reason:'No breadth data'}); }

    const vix=parseFloat((live.vix_risk||{}).vix||mo.vix);
    if(!isNaN(vix)){
      if(vix<15) items.push({icon:'✅',name:'India VIX',reason:`VIX ${vix.toFixed(2)} is low`});
      else if(vix<20) items.push({icon:'⚠',name:'India VIX',reason:`VIX ${vix.toFixed(2)} is moderate`});
      else items.push({icon:'❌',name:'India VIX',reason:`VIX ${vix.toFixed(2)} is elevated`});
    } else { items.push({icon:'❌',name:'India VIX',reason:'No VIX data'}); }

    const fd=live.fii_dii||{};
    const fiiNet=parseFloat(fd.fii_net);
    if(!isNaN(fiiNet)){
      if(fiiNet>0) items.push({icon:'✅',name:'FII/DII',reason:`FII net +${fiiNet.toFixed(0)} Cr`});
      else items.push({icon:'⚠',name:'FII/DII',reason:`FII net ${fiiNet.toFixed(0)} Cr`});
    } else { items.push({icon:'❌',name:'FII/DII',reason:'Awaiting data'}); }

    const gSent=parseFloat((live.global_markets||{}).sentiment_score);
    if(!isNaN(gSent)){
      if(gSent>=60) items.push({icon:'✅',name:'Global Markets',reason:'Positive global sentiment'});
      else if(gSent>=40) items.push({icon:'⚠',name:'Global Markets',reason:'Neutral global sentiment'});
      else items.push({icon:'❌',name:'Global Markets',reason:'Negative global sentiment'});
    } else { items.push({icon:'❌',name:'Global Markets',reason:'No global data'}); }

    const oi=live.options_intelligence||{};
    const pcr=parseFloat(oi.pcr);
    if(!isNaN(pcr)){
      if(pcr>=0.8 && pcr<=1.2) items.push({icon:'✅',name:'Options',reason:`PCR ${pcr.toFixed(2)} is balanced`});
      else items.push({icon:'⚠',name:'Options',reason:`PCR ${pcr.toFixed(2)} is skewed`});
    } else { items.push({icon:'❌',name:'Options',reason:'No options data'}); }

    const aiReady=(live.recommendations||[]).filter(s=>s.action==='BUY').length>0;
    if(aiReady) items.push({icon:'✅',name:'AI Ready',reason:`${(live.recommendations||[]).filter(s=>s.action==='BUY').length} executable BUYs`});
    else items.push({icon:'❌',name:'AI Ready',reason:'No executable BUYs yet'});

    checkEl.innerHTML=items.map(i=>`
      <div style="display:flex;align-items:flex-start;gap:8px;padding:6px 0;border-bottom:1px solid #1f293744">
        <span style="font-size:16px">${i.icon}</span>
        <div>
          <div style="font-size:13px;color:#f9fafb;font-weight:700">${i.name}</div>
          <div style="font-size:11px;color:#9ca3af">${i.reason}</div>
        </div>
      </div>`).join('');
  }

  // ── 3. Sector Rotation (Top 3 Strong / Top 3 Weak)
  const strongEl=document.getElementById('mr-strong');
  const weakEl=document.getElementById('mr-weak');
  const sr=live.sector_rotation||{};
  const strong=(sr.top5_strong||[]).slice(0,3);
  const weakSet=new Set(strong.map(s=>s.sector));
  const weak=(sr.top5_weak||[]).filter(s=>!weakSet.has(s.sector)).slice(0,3);

  const rotLabel=(s)=>{
    const m=parseFloat(s.momentum_score||0);
    const r=parseFloat(s.return_30d_pct||0);
    if(m>=70 || r>=2) return 'Increasing';
    if(m>=40 || r>=-1) return 'Stable';
    return 'Weakening';
  };

  const renderSec=(arr)=>arr.length?arr.map(s=>{
    const label=rotLabel(s);
    const col=label==='Increasing'?'#22c55e':label==='Stable'?'#eab308':'#ef4444';
    return `<div style="display:flex;justify-content:space-between;align-items:center;padding:8px 0;border-bottom:1px solid #1f293744">
      <span style="font-weight:700;color:#f9fafb">${s.sector||'—'}</span>
      <span style="color:${col};font-weight:700;font-size:12px">${label}</span>
    </div>`;
  }).join(''):'<span style="color:#4b5563;font-size:12px">No sector data available</span>';

  if(strongEl) strongEl.innerHTML=renderSec(strong);
  if(weakEl) weakEl.innerHTML=renderSec(weak);

  // ── 4. Institutional Activity
  const instEl=document.getElementById('mr-institutional');
  if(instEl){
    const fd=live.fii_dii||{};
    const fii=parseFloat(fd.fii_net);
    const dii=parseFloat(fd.dii_net);
    if(!isNaN(fii) || !isNaN(dii)){
      const fmt=(v)=>{const n=parseFloat(v); return isNaN(n)?'—':(n>=0?'+':'')+n.toFixed(0)+' Cr';};
      const dv=(rpt.delivery_volume||[]).slice(0,3);
      const activity=[
        {symbol:'FII Net', note:fmt(fii), color:fii>=0?'#22c55e':'#ef4444'},
        {symbol:'DII Net', note:fmt(dii), color:dii>=0?'#22c55e':'#ef4444'}
      ];
      dv.forEach(s=>{
        const vol=parseInt(s.volume||0);
        const note=vol>500000?'Huge Volume':vol>200000?'Delivery Buying':'Volume Spike';
        activity.push({symbol:s.symbol, note, color:'#60a5fa'});
      });
      instEl.innerHTML=activity.map(a=>`
        <div style="display:flex;justify-content:space-between;align-items:center;padding:7px 0;border-bottom:1px solid #1f293744">
          <span style="font-weight:700;color:#f9fafb">${a.symbol}</span>
          <span style="color:${a.color};font-size:12px;font-weight:600">${a.note}</span>
        </div>`).join('');
    } else {
      instEl.innerHTML='<span style="color:#4b5563;font-size:12px">No significant institutional flow detected</span>';
    }
  }

  // ── 5. Important Events (hide if empty)
  const eventsCard=document.getElementById('mr-events-card');
  const eventsEl=document.getElementById('mr-events');
  const events=live.economic_events||rpt.important_events||rpt.economic_events||[];
  if(eventsCard&&eventsEl){
    if(events.length){
      eventsCard.style.display='block';
      eventsEl.innerHTML=events.slice(0,5).map(e=>`
        <div style="display:grid;grid-template-columns:80px 1fr auto;gap:10px;padding:7px 0;border-bottom:1px solid #1f293744;align-items:center">
          <span style="color:#9ca3af;font-size:12px">${e.time||'—'}</span>
          <span style="color:#f9fafb;font-size:13px">${e.name||e.event_type||'—'}</span>
          <span style="color:${(e.impact||'').toUpperCase()==='HIGH'?'#ef4444':(e.impact||'').toUpperCase()==='MEDIUM'?'#eab308':'#22c55e'};font-size:12px;font-weight:600">${e.impact||'—'}</span>
        </div>`).join('');
    } else {
      eventsCard.style.display='block';
      eventsEl.innerHTML='<span style="color:#4b5563;font-size:13px">No major events today</span>';
    }
  }

  // ── 6. Opportunity Summary from AI engine
  const sumEl=document.getElementById('mr-summary');
  if(sumEl){
    const allCandidates=(live.signals||[]).filter(s=>s.action==='BUY');
    const executable=(live.recommendations||[]).filter(s=>s.action==='BUY');
    const high=executable.filter(x=>(x.overall_score||x.score||0)>=80).length;
    const best=executable.sort((a,b)=>(b.overall_score||b.score||0)-(a.overall_score||a.score||0))[0];
    sumEl.innerHTML=`
      <div style="display:flex;gap:20px;flex-wrap:wrap;align-items:center">
        <div style="text-align:center;min-width:80px"><div style="font-size:22px;font-weight:800;color:#f9fafb">${allCandidates.length}</div><div style="font-size:11px;color:#9ca3af">BUY Candidates</div></div>
        <div style="text-align:center;min-width:80px"><div style="font-size:22px;font-weight:800;color:#22c55e">${high}</div><div style="font-size:11px;color:#9ca3af">High Conviction</div></div>
        <div style="text-align:center;min-width:80px"><div style="font-size:22px;font-weight:800;color:#22c55e">${executable.length}</div><div style="font-size:11px;color:#9ca3af">Executable</div></div>
        <div style="text-align:center;min-width:80px"><div style="font-size:22px;font-weight:800;color:#ef4444">${Math.max(0,allCandidates.length-executable.length)}</div><div style="font-size:11px;color:#9ca3af">Rejected by Risk</div></div>
        ${best?`<div style="flex:1;min-width:140px;text-align:right"><div style="font-size:11px;color:#9ca3af">Best Opportunity</div><div style="font-size:18px;font-weight:800;color:#22c55e">${best.symbol}</div><div style="font-size:12px;color:#9ca3af">Score ${Math.min(100,Math.round(parseFloat(best.overall_score||best.score||0)))}</div></div>`:''}
      </div>`;
  }

  // ── 7. Top 5 AI Opportunities from live recommendations
  const picksEl=document.getElementById('mr-picks');
  if(picksEl){
    const picks=(live.recommendations||[]).filter(s=>s.action==='BUY').sort((a,b)=>(b.overall_score||b.score||0)-(a.overall_score||a.score||0)).slice(0,5);
    if(picks.length){
      picksEl.innerHTML=picks.map(p=>{
        const score=Math.min(100,Math.round(parseFloat(p.overall_score||p.score||0)));
        return `<div style="display:grid;grid-template-columns:2fr 1fr 1.5fr 2fr;gap:8px;align-items:center;padding:9px 6px;border-bottom:1px solid #1f293744;cursor:pointer" onclick="window._selectedMorningSymbol='${p.symbol}';switchTab('signals',null)">
          <div style="font-weight:800;color:#22c55e;font-size:14px">${p.symbol}</div>
          <div style="text-align:center"><span style="background:${score>=80?'#16a34a33':'#ca8a0433'};color:${score>=80?'#22c55e':'#eab308'};padding:2px 8px;border-radius:4px;font-weight:700;font-size:13px">${score}</span></div>
          <div style="color:#9ca3af;font-size:12px">${p.sector||'—'}</div>
          <div style="color:#d1d5db;font-size:12px">${p.reasoning||p.reason||p.setup||'—'}</div>
        </div>`;
      }).join('');
    } else if((rpt.ai_top_picks||[]).length){
      const fallback=(rpt.ai_top_picks||[]).slice(0,5);
      picksEl.innerHTML=fallback.map(p=>{
        const score=Math.min(100,Math.round(parseFloat(p.score||0)));
        return `<div style="display:grid;grid-template-columns:2fr 1fr 1.5fr 2fr;gap:8px;align-items:center;padding:9px 6px;border-bottom:1px solid #1f293744;cursor:pointer" onclick="window._selectedMorningSymbol='${p.symbol}';switchTab('signals',null)">
          <div style="font-weight:800;color:#22c55e;font-size:14px">${p.symbol}</div>
          <div style="text-align:center"><span style="background:${score>=80?'#16a34a33':'#ca8a0433'};color:${score>=80?'#22c55e':'#eab308'};padding:2px 8px;border-radius:4px;font-weight:700;font-size:13px">${score}</span></div>
          <div style="color:#9ca3af;font-size:12px">${p.sector||'—'}</div>
          <div style="color:#d1d5db;font-size:12px">${p.reason||'—'}</div>
        </div>`;
      }).join('');
    } else {
      picksEl.innerHTML='<span style="color:#4b5563;font-size:13px">No tradeable opportunities today</span>';
    }
  }

  // ── 8. Market Notes
  const notesEl=document.getElementById('mr-notes');
  if(notesEl){
    const generated=[];
    if(!isNaN(parseFloat((live.vix_risk||{}).vix||mo.vix))){
      const v=parseFloat((live.vix_risk||{}).vix||mo.vix);
      generated.push(`• VIX at ${v.toFixed(2)} (${(live.vix_risk||{}).risk_level||mo.vix_label||'Moderate'}).`);
    }
    const mb=live.market_breadth||{};
    if(mb.breadth_score!=null) generated.push(`• Market breadth ${mb.breadth_score>=50?'healthy':mb.breadth_score>=30?'mixed':'weak'} (score ${Math.round(mb.breadth_score)}).`);
    else if(mb.ad_ratio!=null) generated.push(`• A/D ratio ${mb.ad_ratio.toFixed(2)}.`);
    if(strong.length) generated.push(`• ${strong[0].sector} is the strongest sector.`);
    if(weak.length) generated.push(`• ${weak[0].sector} is the weakest sector.`);
    const gSent=parseFloat((live.global_markets||{}).sentiment_score);
    if(!isNaN(gSent)) generated.push(`• Global sentiment ${gSent>=60?'positive':gSent>=40?'neutral':'negative'} (${gSent.toFixed(1)}).`);
    const exCount=(live.recommendations||[]).filter(s=>s.action==='BUY').length;
    generated.push(`• AI recommends ${(p.risk_mode||'Selective').toLowerCase()} buying — ${exCount} executable BUY setups.`);
    if(p.avoid_sectors&&p.avoid_sectors.length) generated.push(`• Avoid: ${p.avoid_sectors.join(' · ')}.`);
    notesEl.innerHTML=generated.map(n=>`<div style="margin-bottom:4px">${n}</div>`).join('');
  }

  // ── 9. Stocks to Avoid (hide if empty)
  const avoidCard=document.getElementById('mr-avoid-card');
  const avoidEl=document.getElementById('mr-avoid');
  if(avoidCard&&avoidEl){
    const items=rpt.stocks_to_avoid||[];
    if(items.length){
      avoidCard.style.display='block';
      avoidEl.innerHTML=`<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:8px">`+
        items.map(s=>`<div style="background:#dc262611;border:1px solid #ef444433;border-radius:6px;padding:8px 10px">
          <div style="font-weight:700;color:#ef4444">${s.symbol}</div>
          <div style="font-size:11px;color:#f87171;margin-top:2px">${s.reason||'Negative signal'}</div>
        </div>`).join('')+'</div>';
    } else {
      avoidCard.style.display='none';
    }
  }
}

function renderPositionsTab(d){
  const setText=(id,v)=>{const e=document.getElementById(id);if(e)e.textContent=v;};
  const positions = (d.positions||[]).filter(p=>parseInt(p.quantity||0)>0);
  const cash = parseFloat(d.cash||0);
  const holdingsValue = parseFloat(d.holdings_value||positions.reduce((s,p)=>s+parseFloat(p.last_price||0)*parseInt(p.quantity||0),0));
  const totalPortfolio = parseFloat(d.net_portfolio_value||d.account_balance||cash+holdingsValue);
  const dayPnl = parseFloat(d.daily_pnl||0);

  // 1. Portfolio Summary
  setText('pos-cash', rupee(cash));
  const hvEl=document.getElementById('pos-holdings-value'); if(hvEl){ hvEl.textContent=rupee(holdingsValue); }
  setText('pos-total-value', rupee(totalPortfolio));
  const dpEl=document.getElementById('pos-day-pnl'); if(dpEl){ dpEl.textContent=pnlStr(dayPnl); dpEl.style.color=dayPnl>=0?'#22c55e':'#ef4444'; }

  // 2. Current Holdings (aggregated one row per symbol)
  const htEl=document.getElementById('pos-holdings-table');
    if(!positions.length){
    if(htEl) htEl.innerHTML='<tr><td colspan="10" style="text-align:center;color:#4b5563;padding:20px">No open positions</td></tr>';
  } else if(htEl){
    htEl.innerHTML = positions.map(p => {
      const sym=p.tradingsymbol;
      const qty=parseInt(p.quantity||0);
      const avg=parseFloat(p.average_price||0);
      const first=parseFloat(p.first_entry_price||avg);
      const ltp=parseFloat(p.last_price||avg);
      const invested=avg*qty;
      const pnl=parseFloat(p.pnl||(ltp-avg)*qty);
      const sl=parseFloat(p.trailing_stop||p.stop_loss||0);
      const tgt=parseFloat(p.target||0);
      return `<tr style="border-bottom:1px solid #1f293744">
        <td style="font-weight:700;color:#f9fafb;padding:10px 8px">${sym}</td>
        <td style="text-align:right;padding:10px 8px">${qty}</td>
        <td style="text-align:right;padding:10px 8px">${rupee(first)}</td>
        <td style="text-align:right;padding:10px 8px">${rupee(avg)}</td>
        <td style="text-align:right;font-weight:600;padding:10px 8px">${rupee(ltp)}</td>
        <td style="text-align:right;padding:10px 8px">${rupee(invested)}</td>
        <td style="text-align:right;padding:10px 8px"><span class="${pnlClass(pnl)}">${pnlStr(pnl)}</span></td>
        <td style="text-align:right;padding:10px 8px;color:#ef4444">${sl>0?rupee(sl):'—'}</td>
        <td style="text-align:right;padding:10px 8px;color:#22c55e">${tgt>0?rupee(tgt):'—'}</td>
        <td style="text-align:center;padding:10px 8px">
          <button onclick="openPosDrawer('${sym}')" style="background:#3b82f6;color:#fff;border:none;border-radius:4px;padding:3px 8px;font-size:11px;cursor:pointer">View</button>
          <button onclick="manualExit('${sym}')" style="background:#dc2626;color:#fff;border:none;border-radius:4px;padding:3px 8px;font-size:11px;cursor:pointer">Exit</button>
        </td>
      </tr>`;
    }).join('');
  }

  // 3. Pending Orders (open, partial, retry)
  const pendingEl=document.getElementById('pos-pending-orders');
  const pendingCard=document.getElementById('pos-pending-orders-card');
  const pendingSells=(d.pending_sells||[]).filter(p=>!p.completed && !p.resolved);
  const openText = `Open: ${positions.length} holding(s)`;
  const hasPending = positions.length || pendingSells.length || (d.pending_orders||0)>0;
  if(pendingCard) pendingCard.style.display=hasPending?'block':'none';
  if(pendingEl){
    if(!hasPending){
      pendingEl.innerHTML='No pending orders';
    } else {
      let html=`<div style="margin-bottom:8px;font-size:12px;color:#9ca3af">${openText} · Kite pending orders: ${d.pending_orders||0}</div>`;
      if(pendingSells.length){
        html+=`<table style="width:100%;border-collapse:collapse;font-size:13px"><thead><tr style="background:#1f2937">
          <th style="text-align:left;padding:8px">Symbol</th><th style="padding:8px">Retry</th><th style="padding:8px">Last Error</th>
        </tr></thead><tbody>`;
        html+=pendingSells.map(p=>`<tr style="border-bottom:1px solid #1f293744">
          <td style="padding:8px;font-weight:700">${p.symbol||'—'}</td>
          <td style="padding:8px;text-align:center">${p.retry_count||0}</td>
          <td style="padding:8px;font-size:11px;color:#f97316">${p.last_error||'—'}</td>
        </tr>`).join('');
        html+='</tbody></table>';
      }
      pendingEl.innerHTML=html;
    }
  }

  // 4. Current Week Completed Trades
  const ctEl=document.getElementById('pos-completed-trades');
  if(ctEl){
    const now=new Date();
    const day=now.getDay();
    const monOffset=day===0 ? -6 : 1-day;
    const weekStart=new Date(now.getFullYear(), now.getMonth(), now.getDate()+monOffset);
    const weekTrades=(d.completed_trades_full||[]).filter(t=>{
      if(!t.date) return false;
      const td=new Date(t.date+'T00:00:00');
      return td>=weekStart && td<=now;
    });
    if(!weekTrades.length){
      ctEl.innerHTML='<tr><td colspan="13" style="text-align:center;color:#4b5563;padding:20px">No completed trades this week</td></tr>';
    } else {
      ctEl.innerHTML = weekTrades.map(t => {
        const sym=t.symbol;
        const qty=parseInt(t.qty||0);
        const buy=parseFloat(t.buy_price||0);
        const sell=parseFloat(t.sell_price||0);
        const pnl=parseFloat(t.net_pnl||0);
        const pct=parseFloat(t.pnl_pct||0);
        const buyOid=t.buy_order_id?String(t.buy_order_id).slice(-12):'—';
        const sellOid=t.sell_order_id?String(t.sell_order_id).slice(-12):'—';
        const oids=(buyOid!=='—'||sellOid!=='—')?(buyOid+' / '+sellOid).replace(/— \/|\/ —/g,'—'):'—';
        return `<tr style="border-bottom:1px solid #1f293744">
          <td style="font-size:12px;color:#9ca3af;padding:10px 8px;white-space:nowrap">${t.date||'—'}</td>
          <td style="font-size:12px;color:#9ca3af;padding:10px 8px;white-space:nowrap">${t.time||'—'}</td>
          <td style="font-weight:700;color:#f9fafb;padding:10px 8px">${sym}</td>
          <td style="text-align:center;padding:10px 8px">${t.action||'—'}</td>
          <td style="text-align:right;padding:10px 8px">${rupee(buy)}</td>
          <td style="text-align:right;padding:10px 8px">${rupee(sell)}</td>
          <td style="text-align:right;padding:10px 8px">${qty}</td>
          <td style="text-align:right;padding:10px 8px;color:#9ca3af">${t.holding_time||'—'}</td>
          <td style="text-align:right;padding:10px 8px"><span class="${pnlClass(pnl)}">${pnlStr(pnl)}</span></td>
          <td style="text-align:right;padding:10px 8px;color:#9ca3af">${pct.toFixed(2)}%</td>
          <td style="padding:10px 8px;color:#9ca3af;font-size:12px">${t.exit_reason||'—'}</td>
          <td style="text-align:right;padding:10px 8px;color:#9ca3af">${rupee(t.brokerage||0)}</td>
          <td style="text-align:right;padding:10px 8px;font-size:11px;color:#6b7280;font-family:monospace">${oids}</td>
        </tr>`;
      }).join('');
    }
  }

  // 5. Performance Statistics
  const completedSells=(d.trade_events||[]).filter(t=>t.type==='SELL');
  const pnls=completedSells.map(t=>parseFloat(t.pnl||0));
  const wins=pnls.filter(x=>x>0);
  const losses=pnls.filter(x=>x<0);
  const winRate=pnls.length?wins.length/pnls.length*100:0;
  const avgProfit=wins.length?wins.reduce((a,b)=>a+b,0)/wins.length:0;
  const avgLoss=losses.length?losses.reduce((a,b)=>a+b,0)/losses.length:0;
  const grossProfit=wins.reduce((a,b)=>a+b,0);
  const grossLoss=Math.abs(losses.reduce((a,b)=>a+b,0));
  const profitFactor=grossLoss>0?grossProfit/grossLoss:0;
  const expectancy=(winRate/100*avgProfit) - ((1-winRate/100)*Math.abs(avgLoss));
  const best=Math.max(0,...pnls);
  const worst=Math.min(0,...pnls);
  const ss=d.strategy_stats||{};
  setText('pos-win-rate', winRate.toFixed(0)+'%');
  setText('pos-avg-profit', rupee(ss.avg_win || avgProfit));
  setText('pos-avg-loss', rupee(ss.avg_loss || Math.abs(avgLoss)));
  setText('pos-expectancy', rupee(ss.expectancy!=null ? ss.expectancy : expectancy));
  setText('pos-profit-factor', (ss.profit_factor || profitFactor).toFixed(2));
  setText('pos-best-worst', `${rupee(best)} / ${rupee(worst)}`);

  // 6. Trade Analytics
  // Sector-wise P&L from signals/recommendations mapping
  const secMap={};
  (d.signals||[]).forEach(s=>{if(s.symbol)secMap[s.symbol]=s.sector;}); 
  (d.recommendations||[]).forEach(s=>{if(s.symbol)secMap[s.symbol]=s.sector;});
  const sectorPnl={};
  completedSells.forEach(t=>{
    const sec=secMap[t.symbol]||'Unknown';
    sectorPnl[sec]=(sectorPnl[sec]||0)+parseFloat(t.pnl||0);
  });
  const secHtml=Object.keys(sectorPnl).length
    ? Object.entries(sectorPnl).sort((a,b)=>b[1]-a[1]).map(([s,v])=>`<div style="display:flex;justify-content:space-between;font-size:13px"><span style="color:#9ca3af">${s}</span><span style="color:${v>=0?'#22c55e':'#ef4444'};font-weight:700">${pnlStr(v)}</span></div>`).join('')
    : '<div style="color:#6b7280;font-size:13px">No sector data available</div>';
  const sectorEl=document.getElementById('pos-sector-pnl'); if(sectorEl) sectorEl.innerHTML=`<div class="stat-label">Sector-wise P&amp;L</div><div class="stat-value" style="font-weight:400">${secHtml}</div>`;
  const scoreEl=document.getElementById('pos-score-vs-result');
  if(scoreEl) scoreEl.innerHTML=`<div class="stat-label">AI Score vs Actual Result</div><div class="stat-value" style="font-weight:400"><div style="color:#6b7280;font-size:13px">Requires trade journal enrichment</div></div>`;
}

function openPosDrawer(sym){
  const d=window._lastData||{};
  const p=(d.positions||[]).find(x=>x.tradingsymbol===sym);
  if(!p) return;
  const qty=parseInt(p.quantity||0);
  const avg=parseFloat(p.average_price||0);
  const ltp=parseFloat(p.last_price||avg);
  const first=parseFloat(p.first_entry_price||avg);
  const sl=parseFloat(p.trailing_stop||p.stop_loss||0);
  const tgt=parseFloat(p.target||0);
  const pnl=(ltp-avg)*qty;
  const pnlPct=avg>0?((ltp-avg)/avg*100):0;
  const activeSl=Math.max(parseFloat(p.stop_loss||0),parseFloat(p.trailing_stop||0));
  const minGain=(activeSl-avg)*qty;
  const openRisk=(sl>0 && ltp>sl)?(ltp-sl)*qty:0;
  const potReward=(tgt>0 && tgt>ltp)?(tgt-ltp)*qty:0;
  const rr=openRisk>0?potReward/openRisk:0;
  const trend=pnlPct>=5?'Strong Uptrend':pnlPct>=2?'Uptrend':pnlPct>=-2?'Sideways':'Weak';
  const nextReview=d.next_scan?fmtDateTime(d.next_scan,true):'15 minutes';
  const entryDate=p.entry_date?fmtDateTime(p.entry_date,true):'—';
  const lp=(d.lifecycle_positions||[]).find(x=>x.symbol===sym);
  const rec=lp?lp.recommendation:'HOLD';
  const reason=lp?lp.reason:'Hold and trail SL as per lifecycle.';
  const timeline=[`<div style="display:flex;gap:10px;margin-bottom:6px"><div style="color:#6b7280;width:60px">${entryDate}</div><div>Entry at ${rupee(first)}</div></div>`,
    `<div style="display:flex;gap:10px;margin-bottom:6px"><div style="color:#6b7280;width:60px">Now</div><div>Current ${rupee(ltp)}</div></div>`,
    `<div style="display:flex;gap:10px;margin-bottom:6px"><div style="color:#6b7280;width:60px">Next</div><div>Review in ${nextReview}</div></div>`].join('');
  const html=`<div style="font-size:13px;color:#d1d5db;line-height:1.5">
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:16px">
      <div class="card-sm"><div style="font-size:11px;color:#9ca3af">Qty</div><div style="font-weight:700;color:#f9fafb">${qty}</div></div>
      <div class="card-sm"><div style="font-size:11px;color:#9ca3af">Avg Price</div><div style="font-weight:700;color:#f9fafb">${rupee(avg)}</div></div>
      <div class="card-sm"><div style="font-size:11px;color:#9ca3af">First Entry</div><div style="font-weight:700;color:#f9fafb">${rupee(first)}</div></div>
      <div class="card-sm"><div style="font-size:11px;color:#9ca3af">P&L</div><div style="font-weight:700;color:${pnl>=0?'#22c55e':'#ef4444'}">${pnlStr(pnl)}</div></div>
      <div class="card-sm"><div style="font-size:11px;color:#9ca3af">Return</div><div style="font-weight:700;color:${pnl>=0?'#22c55e':'#ef4444'}">${pnlPct>=0?'+':''}${pnlPct.toFixed(2)}%</div></div>
      <div class="card-sm"><div style="font-size:11px;color:#9ca3af">Day Change</div><div style="font-weight:700;color:#f9fafb">${p.day_change_percentage?p.day_change_percentage.toFixed(2)+'%':'—'}</div></div>
    </div>
    <div style="font-size:13px;font-weight:700;color:#f9fafb;margin-bottom:8px">Lifecycle Recommendation</div>
    <div style="background:#1f2937;border-radius:6px;padding:10px;margin-bottom:16px;font-size:13px;color:#22c55e;font-weight:700">
      ${rec}
    </div>
    <div style="font-size:12px;color:#d1d5db;margin-bottom:16px">${reason}</div>
    <div style="font-size:13px;font-weight:700;color:#f9fafb;margin-bottom:8px">Indicators</div>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:16px">
      <div class="card-sm"><div style="font-size:11px;color:#9ca3af">ATR</div><div style="font-weight:700;color:#f9fafb">${p.atr?p.atr.toFixed(2):'—'}</div></div>
      <div class="card-sm"><div style="font-size:11px;color:#9ca3af">RSI</div><div style="font-weight:700;color:#f9fafb">${p.rsi?p.rsi.toFixed(0):'—'}</div></div>
      <div class="card-sm"><div style="font-size:11px;color:#9ca3af">Trend</div><div style="font-weight:700;color:#f9fafb">${trend}</div></div>
      <div class="card-sm"><div style="font-size:11px;color:#9ca3af">Volume</div><div style="font-weight:700;color:#f9fafb">${p.volume_ratio?p.volume_ratio.toFixed(2)+'×':'—'}</div></div>
      <div class="card-sm"><div style="font-size:11px;color:#9ca3af">Stop Loss</div><div style="font-weight:700;color:#f9fafb">${p.stop_loss?rupee(p.stop_loss):'—'}</div></div>
      <div class="card-sm"><div style="font-size:11px;color:#9ca3af">Target</div><div style="font-weight:700;color:#f9fafb">${p.target?rupee(p.target):'—'}</div></div>
    </div>
    <div style="font-size:13px;font-weight:700;color:#f9fafb;margin-bottom:8px">Trade Timeline</div>
    <div style="background:#1f2937;border-radius:6px;padding:10px;margin-bottom:16px;font-size:12px">
      ${timeline}
    </div>
    <div style="font-size:13px;font-weight:700;color:#f9fafb;margin-bottom:8px">Risk / Reward</div>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:16px">
      <div class="card-sm"><div style="font-size:11px;color:#9ca3af">Open Risk</div><div style="font-weight:700;color:#f9fafb">${rupee(openRisk)}</div></div>
      <div class="card-sm"><div style="font-size:11px;color:#9ca3af">Potential Reward</div><div style="font-weight:700;color:#22c55e">${rupee(potReward)}</div></div>
      <div class="card-sm"><div style="font-size:11px;color:#9ca3af">R:R</div><div style="font-weight:700;color:#f9fafb">1 : ${rr.toFixed(1)}</div></div>
      <div class="card-sm"><div style="font-size:11px;color:#9ca3af">Min Gain if SL</div><div style="font-weight:700;color:${minGain>=0?'#22c55e':'#ef4444'}">${minGain>=0?rupee(minGain):pnlStr(minGain)}</div></div>
    </div>
    <div style="display:flex;gap:10px;flex-wrap:wrap">
      <button onclick="modifyTrailSL('${sym}')" style="background:#1f2937;color:#d1d5db;border:1px solid #374151;border-radius:4px;padding:6px 12px;font-size:12px;cursor:pointer">Move SL</button>
      <button onclick="partialExit('${sym}')" style="background:#ca8a04;color:#fff;border:none;border-radius:4px;padding:6px 12px;font-size:12px;cursor:pointer">Partial Exit</button>
      <button onclick="manualExit('${sym}')" style="background:#dc2626;color:#fff;border:none;border-radius:4px;padding:6px 12px;font-size:12px;cursor:pointer">Exit Position</button>
    </div>
  </div>`;
  document.getElementById('pos-drawer-title').textContent=sym+' Details';
  document.getElementById('pos-drawer-content').innerHTML=html;
  document.getElementById('pos-detail-overlay').style.display='block';
  document.getElementById('pos-detail-drawer').style.right='0';
}

function closePosDrawer(){
  document.getElementById('pos-detail-overlay').style.display='none';
  document.getElementById('pos-detail-drawer').style.right='-430px';
}

function renderPortfolio(d){
  const get=(id)=>document.getElementById(id);
  const set=(id,v)=>{const e=get(id);if(e)e.textContent=v;};
  const setH=(id,h)=>{const e=get(id);if(e)e.innerHTML=h;};
  const setW=(id,pct)=>{const e=get(id);if(e){e.style.width=Math.max(0,Math.min(100,pct))+'%';e.style.background=pct>=85?'#ef4444':pct>=60?'#eab308':'#22c55e';}};
  const setCol=(id,cls)=>{const e=get(id);if(e)e.className='stat-value '+(cls||'');};

  const positions=(d.positions||[]).filter(p=>parseInt(p.quantity||0)>0);
  const holdings=(d.holdings||[]).filter(h=>parseInt(h.quantity||0)>0);
  // merge positions + holdings, positions take precedence
  const seen=new Map();
  positions.forEach(p=>{if(!seen.has(p.tradingsymbol)) seen.set(p.tradingsymbol,p);});
  holdings.forEach(h=>{if(!seen.has(h.tradingsymbol)) seen.set(h.tradingsymbol,h);});
  const items=Array.from(seen.values());

  const ph=d.portfolio_health||{};
  const an=d.analytics||{};
  const cash=parseFloat(d.cash||0);
  const margin=Math.abs(parseFloat(d.margin_blocked||0));
  const invested=items.reduce((s,p)=>s+parseFloat(p.average_price||0)*parseInt(p.quantity||0),0);
  const holdingsVal=items.reduce((s,p)=>s+parseFloat(p.last_price||p.average_price||0)*parseInt(p.quantity||0),0);
  const totalValue=parseFloat(ph.account_value||d.account_balance||d.net_portfolio_value||cash+holdingsVal);
  const totalCapital=Math.max(1,parseFloat(d.budget||d.cfg_trading_amount||d.account_balance||d.net_portfolio_value||cash+invested));
  const dailyPnl=parseFloat(d.daily_pnl||0);
  const unrealized=items.reduce((s,p)=>s+parseFloat(p.unrealised||p.unrealized||p.pnl||(parseFloat(p.last_price||0)-parseFloat(p.average_price||0))*parseInt(p.quantity||0)),0);
  const realized=parseFloat(d.realized_pnl||0);
  const used=totalCapital>0?((invested)/totalCapital*100):0;
  const availableCash=cash;

  // Diversification & concentration
  const largestPct=totalValue>0?Math.max(0,...items.map(p=>{const v=parseFloat(p.last_price||p.average_price||0)*parseInt(p.quantity||0); return v/totalValue*100;})):0;
  const divScore=Math.max(0,Math.min(100,Math.round(100-largestPct)));
  const divLabel=divScore>=70?'Good':divScore>=40?'Fair':'Poor';
  const divColor=divScore>=70?'#22c55e':divScore>=40?'#eab308':'#ef4444';

  // Top summary
  set('p-portfolio-value',rupee(totalValue));
  const tdp=totalValue>0?Math.abs(dailyPnl/totalValue*100).toFixed(2):'0.00';
  set('p-today-pnl',(dailyPnl>=0?'':'')+pnlStr(dailyPnl)); setCol('p-today-pnl',dailyPnl>=0?'green':'red');
  set('p-today-pnl-pct',(dailyPnl>=0?'+':'-')+tdp+'% today');
  set('p-cash',rupee(cash));
  setH('p-capital-used','<div>'+used.toFixed(0)+'%</div><div style="font-size:11px;color:#9ca3af;font-weight:400;margin-top:4px">'+rupee(invested)+' / '+rupee(totalCapital)+'</div>');
  setW('p-capital-bar',used);
  const maxPos=parseInt(d.cfg_max_positions||7);
  set('p-open-positions',items.length+' / '+maxPos);

  // AI Portfolio Recommendation
  const aiCard=get('p-ai-rec-card');
  if(items.length && aiCard){
    const p=items[0];
    const sig=(d.signals||[]).find(s=>s.symbol===p.tradingsymbol)||{};
    const rec=sig.action||'HOLD';
    const recEmoji=rec==='HOLD'?'✅':rec==='BUY'?'🟢':'🚨';
    const sl=parseFloat(p.trailing_stop||p.stop_loss||0);
    const tgt=parseFloat(p.target||0);
    const qty=parseInt(p.quantity||0);
    const ltp=parseFloat(p.last_price||p.average_price||0);
    const riskRem=sl>0?Math.max(0,(ltp-sl)*qty):0;
    const rewardRem=tgt>0?Math.max(0,(tgt-ltp)*qty):0;
    let rawConf=sig.confidence!=null?sig.confidence:(p.trade_score!=null?p.trade_score:null);
    if((rawConf==null || Number(rawConf)<=0) && (riskRem+rewardRem)>0) rawConf=rewardRem/(riskRem+rewardRem)*100;
    const conf=(rawConf!=null && Number(rawConf)>0)?Number(rawConf).toFixed(0):'—';
    const nextReview=d.next_scan?fmtDateTime(d.next_scan,true):'after next scan';
    const expectedHold=(d.cfg_swing_max_hold_days||15)>=10?'3–7 days':'1–3 days';
    const reasonLines=[];
    if(p.last_price>parseFloat(p.average_price||0)) reasonLines.push('Trend remains strong');
    if(!p.partial_count) reasonLines.push('No averaging');
    if(parseFloat(p.trailing_stop||0)>0) reasonLines.push('Trail SL active');
    if(parseFloat(p.target||0)>0) reasonLines.push('Target intact');
    const reasonHtml=reasonLines.length?'<ul style="list-style:none;padding:0;margin:6px 0 0 0;font-size:12px;color:#d1d5db">'+reasonLines.map(r=>'<li>• '+r+'</li>').join('')+'</ul>':'';
    aiCard.style.display='block';
    setH('p-ai-rec','<div style="display:flex;flex-wrap:wrap;gap:12px;align-items:center;margin-bottom:10px"><div style="font-size:18px;font-weight:800;color:#22c55e">'+recEmoji+' '+rec+' '+p.tradingsymbol+'</div><div style="font-size:12px;color:#9ca3af">Expected hold: <b style="color:#f9fafb">'+expectedHold+'</b> &nbsp;|&nbsp; Review: <b style="color:#f9fafb">'+nextReview+'</b></div></div><div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:12px;margin-bottom:10px"><div><div style="font-size:11px;color:#9ca3af">Market Regime</div><div style="font-size:14px;font-weight:700;color:#f9fafb">'+(d.market_regime||'—').toUpperCase()+'</div></div><div><div style="font-size:11px;color:#9ca3af">Current Exposure</div><div style="font-size:14px;font-weight:700;color:#f9fafb">'+used.toFixed(0)+'%</div></div><div><div style="font-size:11px;color:#9ca3af">Confidence</div><div style="font-size:14px;font-weight:700;color:#f9fafb">'+(conf==='—'?'—':conf+'%')+'</div></div><div><div style="font-size:11px;color:#9ca3af">Trail SL</div><div style="font-size:14px;font-weight:700;color:#f9fafb">'+(sl>0?rupee(sl):'—')+'</div></div><div><div style="font-size:11px;color:#9ca3af">Target</div><div style="font-size:14px;font-weight:700;color:#f9fafb">'+(tgt>0?rupee(tgt):'—')+'</div></div></div><div style="font-size:12px;color:#9ca3af;margin-bottom:4px">Reason</div>'+reasonHtml);
  } else if(aiCard){ aiCard.style.display='none'; }

  // Sync status -> Bot Status
  const openCount=parseInt(d.open_positions||0);
  const posCount=items.length;
  const syncOk=posCount===openCount;
  setH('rs-sync','<span style="color:'+(syncOk?'#22c55e':'#ef4444')+'">'+(syncOk?'✅ in sync ('+openCount+')':'⚠️ mismatch: broker '+openCount+', local '+posCount)+'</span>');

  // Symbol → sector map
  const secMap={'MANYAVAR':'Retail'};
  (d.signals||[]).forEach(s=>{if(s.symbol)secMap[s.symbol]=s.sector;});
  (d.recommendations||[]).forEach(s=>{if(s.symbol)secMap[s.symbol]=s.sector;});

  // Sector allocation
  const sectorAlloc={};
  let posTotalVal=0;
  items.forEach(p=>{
    const sym=p.tradingsymbol;
    const s=secMap[sym]||'Other';
    const v=parseFloat(p.last_price||p.average_price||0)*parseInt(p.quantity||0);
    if(v>0){ sectorAlloc[s]=(sectorAlloc[s]||0)+v; posTotalVal+=v; }
  });
  const secPct={};
  if(posTotalVal>0){ for(const k in sectorAlloc) secPct[k]=sectorAlloc[k]/posTotalVal*100; }
  // Clean Unknown -> Other
  if(secPct['Unknown']!=null){ secPct['Other']=(secPct['Other']||0)+secPct['Unknown']; delete secPct['Unknown']; }
  const secData=Object.entries(secPct).sort((a,b)=>b[1]-a[1]);
  const secConc=secData.length?secData[0][1]:0;
  const divReasons=[];
  if(items.length===1) divReasons.push('Only 1 stock');
  if(secData.length===1) divReasons.push('One sector');
  if(largestPct>=50) divReasons.push(largestPct.toFixed(0)+'% concentration');
  if(cash/totalCapital>=0.2) divReasons.push('Cash healthy');
  const divReasonsHtml=divReasons.length?'<ul style="list-style:none;padding:0;margin:4px 0 0 0;font-size:11px;color:#9ca3af">'+divReasons.map(r=>'<li>• '+r+'</li>').join('')+'</ul>':'';
  setH('p-div-score','<div><span style="font-size:18px;font-weight:700;color:'+divColor+'">'+divScore+' / 100</span> <span style="font-size:13px;color:'+divColor+'">'+divLabel+'</span></div>'+divReasonsHtml);

  // Capital allocation: cash vs current portfolio value, no margin
  const capAlloc=[];
  if(cash>0.01) capAlloc.push({label:'Cash',val:cash,color:'#22c55e'});
  if(holdingsVal>0.01) capAlloc.push({label:'Portfolio Value',val:holdingsVal,color:'#a78bfa'});
  if(capAlloc.length===0) capAlloc.push({label:'Cash',val:cash,color:'#22c55e'});

  // Charts
  if(typeof Chart!=='undefined'){
    const allocCtx=get('chart-allocation');
    if(allocCtx){
      const allocCfg={type:'doughnut',data:{labels:capAlloc.map(x=>x.label),datasets:[{data:capAlloc.map(x=>x.val),backgroundColor:capAlloc.map(x=>x.color),borderWidth:0}]},options:{plugins:{legend:{labels:{color:'#9ca3af',font:{size:11}}}},cutout:'65%',maintainAspectRatio:false}};
      if(chartAlloc){chartAlloc.data=allocCfg.data;chartAlloc.update();}else{chartAlloc=new Chart(allocCtx,allocCfg);}
    }
    const secCtx=get('chart-sector');
    if(secCtx){
      const secCfg={type:'doughnut',data:{labels:secData.map(s=>s[0]),datasets:[{data:secData.map(s=>s[1]),backgroundColor:CHART_COLORS,borderWidth:0}]},options:{plugins:{legend:{labels:{color:'#9ca3af',font:{size:11}}}},cutout:'55%',maintainAspectRatio:false}};
      if(chartSector){chartSector.data=secCfg.data;chartSector.update();}else{chartSector=new Chart(secCtx,secCfg);}
    }
    const portCtx=get('chart-portfolio');
    if(portCtx){
      const labels=['9:30','10:00','10:30','11:00','11:30','12:00','12:30','1:00','1:30','Now'];
      const base=totalValue-dailyPnl;
      const data=[base,base+dailyPnl*0.1,base+dailyPnl*0.05,base+dailyPnl*0.15,base+dailyPnl*0.25,base+dailyPnl*0.4,base+dailyPnl*0.55,base+dailyPnl*0.7,base+dailyPnl*0.85,totalValue];
      if(chartPortfolio){chartPortfolio.data.datasets[0].data=data;chartPortfolio.update();}else{
        chartPortfolio=new Chart(portCtx,{type:'line',data:{labels:labels,datasets:[{label:'Portfolio',data:data,borderColor:'#3b82f6',backgroundColor:'#3b82f611',fill:true,tension:0.4,pointRadius:2}]},options:{scales:{x:{ticks:{color:'#4b5563',font:{size:10}}},y:{ticks:{color:'#4b5563',font:{size:10},callback:v=>'₹'+v.toLocaleString('en-IN')}}},plugins:{legend:{display:false}},maintainAspectRatio:false}});
      }
    }
  }
  setH('p-capital-legend','<span style="color:#22c55e">●</span> Cash '+rupee(cash)+' &nbsp; <span style="color:#3b82f6">●</span> Invested '+rupee(invested)+' &nbsp; <span style="color:#a78bfa">●</span> Portfolio Value '+rupee(totalValue));
  setH('p-sector-legend',secData.slice(0,4).map(s=>`<span style="color:#d1d5db">${s[0]} ${s[1].toFixed(1)}%</span>`).join(' &nbsp;'));

  // Largest holdings
  const top5=items.slice().sort((a,b)=>{
    const av=parseFloat(a.last_price||a.average_price||0)*parseInt(a.quantity||0);
    const bv=parseFloat(b.last_price||b.average_price||0)*parseInt(b.quantity||0);
    return bv-av;
  }).slice(0,5);
  const largestWarning=largestPct>50?`<div style="grid-column:1/-1;background:#7f1d1d22;border:1px solid #ef444455;border-radius:6px;padding:8px 12px;margin-bottom:10px;font-size:12px;color:#fca5a5"><b>⚠ Concentration Risk</b> — Recommended below 35%, current ${largestPct.toFixed(0)}%</div>`:'';
  setH('p-largest',(largestWarning)+'<div style="display:contents">'+(top5.length?top5.map(p=>{
    const v=parseFloat(p.last_price||p.average_price||0)*parseInt(p.quantity||0);
    const pct=totalValue>0?v/totalValue*100:0;
    return `<div><div style="font-weight:700;color:#f9fafb">${p.tradingsymbol}</div><div style="font-size:11px;color:#d1d5db">${pct.toFixed(1)}%</div><div style="font-size:11px;color:#9ca3af">${rupee(v)}</div></div>`;
  }).join(''):'<div style="color:#6b7280;font-size:12px">No positions</div>')+'</div>');

  // Status helper
  const getStatus=(p)=>{
    const ltp=parseFloat(p.last_price||0), sl=parseFloat(p.trailing_stop||p.stop_loss||0), tgt=parseFloat(p.target||0);
    if(sl>0 && ltp<=sl) return '⚫ EXIT';
    if(sl>0 && ltp<=sl*1.03) return '🔴 EXIT WATCH';
    if(p.partial_count && p.partial_count>0) return '🟠 PARTIAL BOOK';
    if(p.trailing_stop && p.stop_loss && p.trailing_stop>p.stop_loss) return '🟡 TRAILING';
    if(tgt>0 && ltp>=tgt*0.95) return '🟡 TRAILING';
    return '🟢 HOLD';
  };

  // Holdings table
  const hldEl=get('p-holdings');
  const hCount=get('p-holdings-count');
  if(hCount) hCount.textContent=`(${items.length})`;
  if(items.length){
    hldEl.innerHTML=items.map(p=>{
      const qty=parseInt(p.quantity||0);
      const avg=parseFloat(p.average_price||0);
      const ltp=parseFloat(p.last_price||avg);
      const invested=avg*qty;
      const cur=ltp*qty;
      const pnl=parseFloat(p.unrealised||p.unrealized||p.pnl||cur-invested);
      const retPct=avg>0?((ltp-avg)/avg*100):0;
      const status=getStatus(p);
      return `<tr style="cursor:pointer;border-bottom:1px solid #1f293744" onclick="viewPositionDetails('${p.tradingsymbol}')">
        <td style="font-weight:700;color:#f9fafb;padding:10px 8px">${p.tradingsymbol}</td>
        <td style="text-align:right;padding:10px 8px">${qty}</td>
        <td style="text-align:right;padding:10px 8px">${rupee(avg)}</td>
        <td style="text-align:right;padding:10px 8px">${rupee(ltp)}</td>
        <td style="text-align:right;padding:10px 8px"><div class="${pnlClass(pnl)}" style="font-weight:700">${pnlStr(pnl)}</div><div class="${pnlClass(retPct)}" style="font-size:11px">${retPct.toFixed(2)}%</div></td>
        <td style="text-align:center;padding:10px 8px"><span style="font-size:11px;background:#1f2937;color:#d1d5db;padding:3px 8px;border-radius:4px">${status}</span></td>
        <td style="text-align:center;padding:10px 8px">
          <button style="background:#1f2937;color:#d1d5db;border:1px solid #374151;border-radius:4px;padding:3px 8px;font-size:11px;cursor:pointer;margin-right:3px" onclick="event.stopPropagation();viewPositionDetails('${p.tradingsymbol}')">View</button>
          <button style="background:#1d4ed8;color:#fff;border:none;border-radius:4px;padding:3px 8px;font-size:11px;cursor:pointer;margin-right:3px" onclick="event.stopPropagation();modifyTrailSL('${p.tradingsymbol}')">SL</button>
          <button style="background:#ca8a04;color:#fff;border:none;border-radius:4px;padding:3px 8px;font-size:11px;cursor:pointer;margin-right:3px" onclick="event.stopPropagation();partialExit('${p.tradingsymbol}')">Exit ½</button>
          <button style="background:#dc2626;color:#fff;border:none;border-radius:4px;padding:3px 8px;font-size:11px;cursor:pointer" onclick="event.stopPropagation();manualExit('${p.tradingsymbol}')">Exit</button>
        </td>
      </tr>`;
    }).join('');
  } else {
    hldEl.innerHTML='<tr><td colspan="7" style="text-align:center;color:#4b5563;padding:20px">No open positions</td></tr>';
  }

  // Position-level open risk / reward
  const openRisk=items.reduce((s,p)=>{
    const ltp=parseFloat(p.last_price||0), sl=parseFloat(p.trailing_stop||p.stop_loss||0), qty=parseInt(p.quantity||0);
    return s+((sl>0 && ltp>sl)?(ltp-sl)*qty:0);
  },0);
  const potReward=items.reduce((s,p)=>{
    const ltp=parseFloat(p.last_price||0), tgt=parseFloat(p.target||0), qty=parseInt(p.quantity||0);
    return s+((tgt>0 && tgt>ltp)?(tgt-ltp)*qty:0);
  },0);
  const portRR=openRisk>0?potReward/openRisk:0;
  const cashBuffer=totalCapital>0?cash/totalCapital*100:0;

  // Portfolio Health
  let healthScore=80;
  if(items.length===1) healthScore-=20;
  if(secData.length===1) healthScore-=10;
  if(largestPct>50) healthScore-=20;
  if(cashBuffer>=20) healthScore+=10;
  if(portRR>=1.5) healthScore+=10;
  const healthGrade=healthScore>=80?'HEALTHY':healthScore>=50?'FAIR':'POOR';
  const healthColor=healthScore>=80?'#22c55e':healthScore>=50?'#eab308':'#ef4444';
  const healthReasons=[];
  if(cashBuffer>=20) healthReasons.push('✓ Healthy cash');
  else if(cashBuffer<10) healthReasons.push('⚠ Low cash');
  if(items.length===1) healthReasons.push('⚠ Single stock concentration');
  if(secData.length===1) healthReasons.push('⚠ One sector only');
  if(largestPct>50) healthReasons.push('⚠ '+largestPct.toFixed(0)+'% concentration');
  if(portRR>=1.5) healthReasons.push('✓ Risk acceptable');
  else if(portRR>0) healthReasons.push('⚠ Risk elevated');
  const healthReasonsHtml=healthReasons.length?'<ul style="list-style:none;padding:0;margin:6px 0 0 0;font-size:12px;color:#d1d5db">'+healthReasons.map(r=>'<li style="margin-bottom:3px">'+r+'</li>').join('')+'</ul>':'';
  setH('p-health','<div style="grid-column:1/-1" class="card-sm"><div class="stat-label">Portfolio Health</div><div style="font-size:22px;font-weight:800;color:'+healthColor+'">'+healthGrade+'</div>'+healthReasonsHtml+'</div>');
  setH('p-header-health','<span style="color:'+healthColor+'">'+healthGrade+'</span>');

  // Portfolio Risk
  const riskLevel=portRR>=3?'Low':portRR>=1.5?'Medium':'High';
  const riskLevelColor=portRR>=3?'#22c55e':portRR>=1.5?'#eab308':'#ef4444';
  const dailyLossLimit=totalValue*0.05;
  const remDailyRisk=Math.max(0,dailyLossLimit-Math.abs(dailyPnl));
  const rewardRemainingPct=potReward+openRisk>0?potReward/(potReward+openRisk)*100:0;
  const trailOn=items.some(p=>parseFloat(p.trailing_stop||0)>0)?'ON':'OFF';
  const worst=items.length?items.slice().map(p=>{const avg=parseFloat(p.average_price||0); const ltp=parseFloat(p.last_price||avg); const qty=parseInt(p.quantity||0); return {s:p.tradingsymbol,pnl:(ltp-avg)*qty};}).sort((a,b)=>a.pnl-b.pnl)[0]:null;
  const worstName=worst?worst.s:'—';
  setH('p-risk','<div style="grid-column:1/-1" class="card-sm"><div class="stat-label" style="font-size:13px;font-weight:600;text-transform:uppercase;letter-spacing:.06em;color:#9ca3af;margin-bottom:10px">Portfolio Risk</div><div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;font-size:13px"><div><div style="font-size:11px;color:#9ca3af">Open Risk</div><div style="font-size:16px;font-weight:700;color:#f9fafb">'+rupee(openRisk)+'</div></div><div><div style="font-size:11px;color:#9ca3af">Potential Reward</div><div style="font-size:16px;font-weight:700;color:#22c55e">'+rupee(potReward)+'</div></div><div><div style="font-size:11px;color:#9ca3af">Reward Remaining</div><div style="font-size:16px;font-weight:700;color:#f9fafb">'+rewardRemainingPct.toFixed(0)+'%</div></div><div><div style="font-size:11px;color:#9ca3af">Trail Protection</div><div style="font-size:16px;font-weight:700;color:'+(trailOn==='ON'?'#22c55e':'#6b7280')+'">'+trailOn+'</div></div><div><div style="font-size:11px;color:#9ca3af">Portfolio R:R</div><div style="font-size:16px;font-weight:700;color:'+riskLevelColor+'">1 : '+portRR.toFixed(1)+'</div></div><div><div style="font-size:11px;color:#9ca3af">Risk Level</div><div style="font-size:16px;font-weight:700;color:'+riskLevelColor+'">'+riskLevel+'</div></div><div><div style="font-size:11px;color:#9ca3af">Remaining Daily Risk</div><div style="font-size:16px;font-weight:700;color:#f9fafb">'+rupee(remDailyRisk)+'</div></div><div><div style="font-size:11px;color:#9ca3af">Worst Position</div><div style="font-size:16px;font-weight:700;color:#f9fafb">'+worstName+'</div></div></div></div>');
  setH('p-risk-meter','');

  // Performance time-range buttons
  setH('p-perf-btns',['1D','1W','1M','3M','YTD'].map(t=>`<button style="background:#1f2937;color:#9ca3af;border:none;border-radius:4px;padding:4px 10px;font-size:11px;cursor:default">${t}</button>`).join(''));
  setH('p-perf-note',`Live portfolio value: ${rupee(totalValue)} · Today's move: ${pnlStr(dailyPnl)} · Unrealized: ${pnlStr(unrealized)}`);

  // Portfolio performance (hide top loser until multiple holdings)
  const portfolioReturn=invested>0?(unrealized/invested*100):0;
  setH('p-winner',`<div style="font-size:13px;color:#9ca3af;margin-bottom:6px">Current Portfolio Return</div>
    <div style="font-size:26px;font-weight:800;color:${portfolioReturn>=0?'#22c55e':'#ef4444'}">${portfolioReturn>=0?'+':''}${portfolioReturn.toFixed(2)}%</div>
    <div style="font-size:13px;color:#d1d5db;margin-top:4px">Today's Gain: <span style="color:${dailyPnl>=0?'#22c55e':'#ef4444'}">${pnlStr(dailyPnl)}</span></div>`);
  if(items.length<2){ setH('p-loser',''); if(get('p-loser')) get('p-loser').style.display='none'; }
  else { setH('p-loser',`<div style="font-size:13px;color:#9ca3af;margin-bottom:6px">Overall Gain</div>
    <div style="font-size:26px;font-weight:800;color:${unrealized>=0?'#22c55e':'#ef4444'}">${pnlStr(unrealized)}</div>
    <div style="font-size:13px;color:#d1d5db;margin-top:4px">Unrealized across ${items.length} holdings</div>`); if(get('p-loser')) get('p-loser').style.display=''; }

  // Buying Power
  const maxNew=Math.max(0,maxPos-items.length);
  const maxAllowed=Math.max(0,totalCapital*parseFloat(d.cfg_max_capital||0.9)-invested);
  const riskAmount=totalValue*parseFloat(d.cfg_risk_per_trade||0.02);
  const riskBased=parseFloat(d.cfg_sl_pct||0.05)>0?riskAmount/parseFloat(d.cfg_sl_pct||0.05):availableCash;
  let suggested=Math.min(availableCash*0.6, maxAllowed, riskBased);
  suggested=Math.max(0,Math.round(suggested/100)*100);
  const buyChecks=[];
  if(suggested>0) buyChecks.push('Cash available');
  if(maxNew>0) buyChecks.push('Position limit available');
  if(used<parseFloat(d.cfg_max_capital||0.9)*100) buyChecks.push('Capital utilization OK');
  if(Math.abs(dailyPnl)<totalValue*parseFloat(d.cfg_daily_loss||0.05)) buyChecks.push('Risk filter OK');
  const regime=(d.market_regime||'').toUpperCase();
  if(regime!=='BEAR') buyChecks.push('Market regime OK');
  const canBuy=buyChecks.length>=5;
  const noReason=(regime==='BEAR')?'Market regime unfavorable':(suggested<=0?'Insufficient cash / risk buffer':(used>=parseFloat(d.cfg_max_capital||0.9)*100)?'Capital utilization too high':(maxNew<=0)?'Max positions reached':'Daily loss limit proximity');
  const reasonList=(canBuy?buyChecks:[noReason]).map(r=>(canBuy?'✓ ':'• ')+r).join('<br>');
  setH('p-buying-power',`
    <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:12px">
      <div style="font-size:13px;font-weight:600;color:#9ca3af;text-transform:uppercase;letter-spacing:.06em">💪 Buying Power</div>
      <div style="display:flex;gap:20px;flex-wrap:wrap;align-items:center">
        <div><div style="font-size:11px;color:#9ca3af">Available Cash</div><div style="font-size:16px;font-weight:700;color:#f9fafb">${rupee(availableCash)}</div></div>
        <div><div style="font-size:11px;color:#9ca3af">Suggested Position</div><div style="font-size:16px;font-weight:700;color:#f9fafb">${rupee(suggested)}</div></div>
        <div><div style="font-size:11px;color:#9ca3af">Can Buy</div><div style="font-size:16px;font-weight:700;color:${canBuy?'#22c55e':'#ef4444'}">${canBuy?'YES':'NO'}</div></div>
        <div style="font-size:12px;color:#d1d5db;line-height:1.4">${reasonList}</div>
      </div>
    </div>`);

  // Store for details
  window._portfolioItems=items;
}

function viewPositionDetails(sym){
  const d=window._lastData||{};
  const items=window._portfolioItems||[];
  const p=items.find(x=>x.tradingsymbol===sym);
  const panel=document.getElementById('p-detail-panel');
  const content=document.getElementById('p-detail-content');
  const title=document.getElementById('p-detail-title');
  if(!p||!panel||!content||!title){ if(panel) panel.style.display='none'; return; }
  const qty=parseInt(p.quantity||0);
  const avg=parseFloat(p.average_price||0);
  const ltp=parseFloat(p.last_price||avg);
  const first=parseFloat(p.first_entry_price||avg);
  const high=parseFloat(p.highest_price||ltp);
  const low=parseFloat(p.lowest_price||avg);
  const sl=parseFloat(p.trailing_stop||p.stop_loss||0);
  const tgt=parseFloat(p.target||0);
  const atr=parseFloat(p.atr||0);
  const riskRem=sl>0?Math.max(0,(ltp-sl)*qty):0;
  const rewardRem=tgt>0?Math.max(0,(tgt-ltp)*qty):0;
  const rr=riskRem>0?rewardRem/riskRem:0;
  // sector + signal
  const secMap={'MANYAVAR':'Retail'};
  (d.signals||[]).forEach(s=>{if(s.symbol)secMap[s.symbol]=s.sector;});
  (d.recommendations||[]).forEach(s=>{if(s.symbol)secMap[s.symbol]=s.sector;});
  const sector=secMap[sym]||'Other';
  const sig=(d.signals||[]).find(s=>s.symbol===sym);
  const rec=(sig&&sig.action)||'HOLD';
  let rawConf=sig?(sig.confidence||null):(p.trade_score!=null?p.trade_score:null);
  if((rawConf==null || Number(rawConf)<=0) && (riskRem+rewardRem)>0) rawConf=rewardRem/(riskRem+rewardRem)*100;
  const conf=(rawConf!=null && Number(rawConf)>0)?Number(rawConf).toFixed(0):'—';
  const reason=sig?((sig.reasoning||'').split(String.fromCharCode(10))[0]||'AI recommendation available'):'No live AI signal for this symbol';
  const status=()=>{
    if(sl>0 && ltp<=sl) return '⚫ EXIT';
    if(sl>0 && ltp<=sl*1.03) return '🔴 EXIT WATCH';
    if(p.partial_count && p.partial_count>0) return '🟠 PARTIAL BOOK';
    if(p.trailing_stop && p.stop_loss && p.trailing_stop>p.stop_loss) return '🟡 TRAILING';
    if(tgt>0 && ltp>=tgt*0.95) return '🟡 TRAILING';
    return '🟢 HOLD';
  };
  title.textContent=sym+' — '+status();
  content.innerHTML=`
    <div style="display:grid;grid-template-columns:repeat(2,1fr);gap:10px;margin-bottom:14px;font-size:12px">
      <div class="card-sm"><div class="stat-label">Avg Price</div><b style="color:#f9fafb">${rupee(avg)}</b></div>
      <div class="card-sm"><div class="stat-label">Current Price</div><b style="color:#f9fafb">${rupee(ltp)}</b></div>
      <div class="card-sm"><div class="stat-label">First Entry</div><b style="color:#f9fafb">${rupee(first)}</b></div>
      <div class="card-sm"><div class="stat-label">Highest Since Entry</div><b style="color:#f9fafb">${rupee(high)}</b></div>
      <div class="card-sm"><div class="stat-label">Lowest Since Entry</div><b style="color:#f9fafb">${rupee(low)}</b></div>
      <div class="card-sm"><div class="stat-label">Current Stop Loss</div><b style="color:#ef4444">${sl>0?rupee(sl):'—'}</b></div>
      <div class="card-sm"><div class="stat-label">Current Target</div><b style="color:#22c55e">${tgt>0?rupee(tgt):'—'}</b></div>
      <div class="card-sm"><div class="stat-label">ATR</div><b style="color:#f9fafb">${atr>0?rupee(atr):'—'}</b></div>
    </div>
    <div style="display:grid;grid-template-columns:repeat(2,1fr);gap:10px;margin-bottom:14px;font-size:12px">
      <div class="card-sm"><div class="stat-label">Risk Remaining</div><b style="color:#ef4444">${rupee(riskRem)}</b></div>
      <div class="card-sm"><div class="stat-label">Reward Remaining</div><b style="color:#22c55e">${rupee(rewardRem)}</b></div>
      <div class="card-sm"><div class="stat-label">Current R:R</div><b style="color:#f9fafb">${rr.toFixed(2)}</b></div>
      <div class="card-sm"><div class="stat-label">Holding Days</div><b style="color:#f9fafb">${p.days_held!=null?p.days_held:'—'}</b></div>
    </div>
    <div class="card-sm" style="margin-bottom:14px">
      <div class="stat-label">AI Recommendation</div>
      <div style="font-size:16px;font-weight:800;color:${rec==='BUY'?'#22c55e':rec==='SELL'?'#ef4444':'#eab308'}">${rec}</div>
      <div style="font-size:12px;color:#9ca3af">Confidence ${isNaN(Number(conf))?conf:conf+'%'}</div>
      <div style="font-size:12px;color:#d1d5db;margin-top:4px">${reason}</div>
    </div>
    <div style="display:flex;gap:8px;flex-wrap:wrap">
      <button onclick="modifyTarget('${sym}')" style="background:#1d4ed8;color:#fff;border:none;border-radius:6px;padding:6px 12px;font-size:12px;cursor:pointer">Modify Target</button>
      <button onclick="modifyTrailSL('${sym}')" style="background:#1d4ed8;color:#fff;border:none;border-radius:6px;padding:6px 12px;font-size:12px;cursor:pointer">Modify Trail SL</button>
      <button onclick="partialExit('${sym}')" style="background:#ca8a04;color:#fff;border:none;border-radius:6px;padding:6px 12px;font-size:12px;cursor:pointer">Partial Exit</button>
      <button onclick="manualExit('${sym}')" style="background:#dc2626;color:#fff;border:none;border-radius:6px;padding:6px 12px;font-size:12px;cursor:pointer">Manual Exit</button>
    </div>
  `;
  panel.style.display='block';
  panel.scrollIntoView({behavior:'smooth',block:'nearest'});
}

function modifyTarget(sym){
  const t=prompt('New target price for '+sym);
  if(t===null) return;
  alert('Set new target for '+sym+': '+t+String.fromCharCode(10)+'(Wiring to backend not enabled — execute via Kite if urgent)');
}
function modifyTrailSL(sym){
  const s=prompt('New trailing stop-loss for '+sym);
  if(s===null) return;
  alert('Set new trailing SL for '+sym+': '+s+String.fromCharCode(10)+'(Wiring to backend not enabled — execute via Kite if urgent)');
}
function partialExit(sym){
  const q=prompt('Quantity to partially exit for '+sym);
  if(!q) return;
  alert('Partial exit '+sym+' x '+q+' requested.'+String.fromCharCode(10)+'(Wiring to backend not enabled — execute manually on Kite)');
}
function manualExit(sym){
  if(confirm('Confirm manual exit for '+sym+'?')){
    alert('Manual exit '+sym+' requested.'+String.fromCharCode(10)+'(Wiring to backend not enabled — execute on Kite)');
  }
}

function filterHistory(type){
  window._historyFilter=type;
  ['ALL','BUY','SELL'].forEach(t=>{
    const id='hf-'+t.toLowerCase();
    const el=document.getElementById(id);
    if(el){ el.style.background=t===type?'#1d4ed8':'#1f2937'; el.style.color=t===type?'#fff':'#9ca3af'; }
  });
  renderHistory(type);
}

function renderHistory(filter){
  const rows=window._historyData||[];
  const filtered=filter==='ALL'?rows:rows.filter(o=>(o.type||'').toUpperCase()===filter);
  const el=document.getElementById('h-history-table');
  if(!el) return;
  if(!filtered.length){
    el.innerHTML='<tr><td colspan="12" style="text-align:center;color:#4b5563;padding:20px">No history yet</td></tr>';
    return;
  }
  el.innerHTML=filtered.map(o=>{
    const isBuy=(o.type||'').toUpperCase()==='BUY';
    const buyP=parseFloat(o.buy_price||0);
    const sellP=o.sell_price===null||o.sell_price===undefined?null:parseFloat(o.sell_price);
    const qty=parseInt(o.quantity||0);
    const val=parseFloat(o.total_value||0);
    const dt=fmtDateTime(o.datetime);
    const [dateStr, timeStr]=dt.includes(' ')?dt.split(' '):[dt,'—'];
    const pnlVal=o.pnl===null||o.pnl===undefined?null:parseFloat(o.pnl);
    const pnlPct=parseFloat(o.pnl_pct||0);
    const pnlText=pnlVal===null?'—':pnlStr(pnlVal);
    const pnlClassName=pnlVal===null?'':pnlClass(pnlVal);
    const src='<span style="font-size:10px;color:#a78bfa;background:#1f2937;padding:2px 6px;border-radius:4px">'+String(o.source||'Bot')+'</span>';
    const oid=o.order_id?'#'+o.order_id:'—';
    return `<tr>
      <td style="font-size:12px;color:#9ca3af;white-space:nowrap">${dateStr}</td>
      <td style="font-size:12px;color:#9ca3af;white-space:nowrap">${timeStr}</td>
      <td style="font-weight:700;color:#f9fafb">${o.symbol||'—'}</td>
      <td><span class="badge ${isBuy?'badge-buy':'badge-sell'}">${isBuy?'BUY':'SELL'}</span></td>
      <td style="text-align:center">${qty}</td>
      <td style="color:#60a5fa;font-family:monospace">${rupee(buyP)}</td>
      <td style="font-family:monospace">${sellP===null?'<span style="color:#4b5563">—</span>':rupee(sellP)}</td>
      <td style="font-weight:600">${rupee(val)}</td>
      <td class="${pnlClassName}">${pnlText}</td>
      <td style="font-size:12px;color:#9ca3af">${pnlPct?pnlPct.toFixed(2)+'%':''}</td>
      <td>${src}</td>
      <td style="font-size:11px;color:#6b7280;font-family:monospace">${oid}</td>
    </tr>`;
  }).join('');
}

function renderTradeCards(cards){
  const el=document.getElementById('h-trade-cards');
  if(!el) return;
  if(!cards || !cards.length){ el.innerHTML='<div style="color:#4b5563;padding:24px;text-align:center;font-size:14px">No open positions</div>'; return; }
  el.innerHTML=cards.map(c=>{
    const entryDate=c.entry_date?fmtDateTime(c.entry_date):'—';
    const invested='₹'+(Number(c.invested)||0).toLocaleString('en-IN',{maximumFractionDigits:2});
    const entryPrice='₹'+(Number(c.entry_price)||0).toFixed(2);
    const qty=Number(c.quantity)||0;
    const conf=c.confidence!=null?(Number(c.confidence)*100).toFixed(0)+'%':'—';
    const score=c.trade_score!=null?c.trade_score:'—';
    const rr=c.risk_reward_ratio!=null?Number(c.risk_reward_ratio).toFixed(2):'—';
    const source=c.source||'Bot';
    return `<div style="background:#1e293b;border:1px solid #334155;border-radius:14px;padding:18px;color:#e5e7eb">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:14px">
        <div>
          <div style="font-size:20px;font-weight:800;color:#f9fafb;letter-spacing:-0.02em">${c.symbol||'—'}</div>
          <div style="font-size:11px;color:#94a3b8;margin-top:2px">Entry: ${entryDate}</div>
        </div>
        <span style="font-size:11px;font-weight:700;padding:5px 12px;border-radius:999px;background:#3b82f622;color:#60a5fa;text-transform:uppercase">Open</span>
      </div>
      <div style="display:grid;grid-template-columns:repeat(2,1fr);gap:14px;margin-bottom:16px">
        <div>
          <div style="font-size:11px;color:#9ca3af;text-transform:uppercase">Entry Price</div>
          <div style="font-size:18px;font-weight:700;color:#22c55e">${entryPrice}</div>
        </div>
        <div>
          <div style="font-size:11px;color:#9ca3af;text-transform:uppercase">Quantity</div>
          <div style="font-size:18px;font-weight:700;color:#f9fafb">${qty}</div>
        </div>
        <div>
          <div style="font-size:11px;color:#9ca3af;text-transform:uppercase">Invested</div>
          <div style="font-size:18px;font-weight:700;color:#f9fafb">${invested}</div>
        </div>
        <div>
          <div style="font-size:11px;color:#9ca3af;text-transform:uppercase">Holding Time</div>
          <div style="font-size:18px;font-weight:700;color:#f9fafb">${c.holding_time||'—'}</div>
        </div>
      </div>
      <div style="display:flex;flex-wrap:wrap;gap:6px">
        <span style="background:#1f2937;padding:5px 10px;border-radius:6px;font-size:11px;color:#94a3b8">Score <strong style="color:#e5e7eb">${score}</strong></span>
        <span style="background:#1f2937;padding:5px 10px;border-radius:6px;font-size:11px;color:#94a3b8">Conf <strong style="color:#e5e7eb">${conf}</strong></span>
        <span style="background:#1f2937;padding:5px 10px;border-radius:6px;font-size:11px;color:#94a3b8">Sector <strong style="color:#e5e7eb">${c.sector||'—'}</strong></span>
        <span style="background:#1f2937;padding:5px 10px;border-radius:6px;font-size:11px;color:#94a3b8">R:R <strong style="color:#e5e7eb">${rr}</strong></span>
        <span style="background:#1f2937;padding:5px 10px;border-radius:6px;font-size:11px;color:#94a3b8">Regime <strong style="color:#e5e7eb">${c.market_regime||'—'}</strong></span>
        <span style="background:#1f2937;padding:5px 10px;border-radius:6px;font-size:11px;color:#94a3b8">Source <strong style="color:#e5e7eb">${source}</strong></span>
      </div>
    </div>`;
  }).join('');
}

function renderHistoryOpenPositions(d){
  const el=document.getElementById('h-open-positions');
  if(!el) return;
  const positions=(d.positions||[]).filter(p=>parseInt(p.quantity||0)>0);
  if(!positions.length){ el.innerHTML='<div style="color:#4b5563;padding:20px;text-align:center">No open positions</div>'; return; }
  const secMap={}; const confMap={};
  (d.signals||[]).forEach(s=>{ if(s.symbol){ secMap[s.symbol]=s.sector; if(s.confidence!=null && Number(s.confidence)>0) confMap[s.symbol]=Number(s.confidence); } });
  (d.recommendations||[]).forEach(s=>{ if(s.symbol){ if(!secMap[s.symbol]) secMap[s.symbol]=s.sector; if(!confMap[s.symbol] && s.confidence!=null && Number(s.confidence)>0) confMap[s.symbol]=Number(s.confidence); } });
  el.innerHTML='<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:12px">'+positions.map(p=>{
    const sym=p.tradingsymbol;
    const qty=parseInt(p.quantity||0);
    const avg=parseFloat(p.average_price||0);
    const ltp=parseFloat(p.last_price||avg);
    const pnl=(ltp-avg)*qty;
    const pnlPct=avg>0?((ltp-avg)/avg*100):0;
    const score=Number(p.trade_score||0);
    const sector=secMap[sym]||'—';
    const confidence=confMap[sym]?confMap[sym].toFixed(0)+'%':'—';
    const scoreStr=score>0?score.toFixed(0):'—';
    return `<div class="card-sm" style="padding:14px">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
        <div style="font-size:16px;font-weight:800;color:#f9fafb">${sym}</div>
        <div style="font-size:14px;font-weight:700;color:${pnl>=0?'#22c55e':'#ef4444'}">${pnlStr(pnl)}</div>
      </div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;font-size:12px;color:#d1d5db;margin-bottom:8px">
        <div><span style="color:#9ca3af">Qty:</span> ${qty}</div>
        <div><span style="color:#9ca3af">Buy:</span> ${rupee(avg)}</div>
        <div><span style="color:#9ca3af">CMP:</span> ${rupee(ltp)}</div>
        <div><span style="color:#9ca3af">P/L%:</span> <span class="${pnlClass(pnlPct)}">${pnlPct.toFixed(2)}%</span></div>
      </div>
      <div style="display:flex;gap:12px;font-size:11px;color:#9ca3af;flex-wrap:wrap">
        <span><b>Score:</b> ${scoreStr}</span>
        <span><b>Sector:</b> ${sector}</span>
        <span><b>Confidence:</b> ${confidence}</span>
      </div>
    </div>`;
  }).join('')+'</div>';
}

function renderRetryQueue(items){
  const el=document.getElementById('h-retry-queue');
  if(!el) return;
  const pending=(items||[]).filter(p=>!p.completed && !p.resolved);
  if(!pending.length){ el.innerHTML='<tr><td colspan="4" style="text-align:center;color:#4b5563;padding:20px">No queued retries</td></tr>'; return; }
  el.innerHTML=pending.map(p=>{
    const next=p.next_retry?fmtDateTime(p.next_retry):'Waiting';
    return `<tr style="border-bottom:1px solid #334155">
      <td style="padding:10px 0;font-weight:700">${p.symbol||'—'}</td>
      <td style="padding:10px 0;text-align:center">${next}</td>
      <td style="padding:10px 0;text-align:center">${p.retry_count||0}</td>
      <td style="padding:10px 0">${p.last_error||'—'}</td>
    </tr>`;
  }).join('');
}

// ─── Chart Instances ──────────────────────────────────────────────────────────
let chartAlloc=null, chartSector=null, chartPortfolio=null, chartPnl=null, chartWinrate=null, chartPortfolioGrowth=null;
let jChartCumulative=null, jChartScoreBucket=null, jChartSector=null, jChartExit=null, jChartDow=null, jChartRegime=null;
const CHART_COLORS=['#3b82f6','#22c55e','#eab308','#a78bfa','#ef4444','#06b6d4','#f97316'];

function makeOrUpdate(ref, ctx, cfg){
  if(typeof Chart==='undefined') return null;
  if(ref){ref.data=cfg.data;ref.update();return ref;}
  return new Chart(ctx,cfg);
}

// ─── Notification Store ───────────────────────────────────────────────────────
const notifs=[];
function pushNotif(icon,msg,cls=''){
  notifs.unshift({icon,msg,cls,time:new Date().toLocaleTimeString('en-IN',{hour:'2-digit',minute:'2-digit'})});
  if(notifs.length>15)notifs.pop();
  renderNotifs();
}
function renderNotifs(){
  const el=document.getElementById('d-notifications');
  const card=document.getElementById('alerts-card');
  if(!el)return;
  if(!notifs.length){if(card)card.style.display='none';el.innerHTML='<div style="color:#4b5563;font-size:13px;padding:20px;text-align:center">No alerts yet</div>';return;}
  if(card)card.style.display='block';
  el.innerHTML=notifs.slice(0,8).map(n=>`
    <div class="notif-item">
      <span style="font-size:16px">${n.icon}</span>
      <div style="flex:1"><div style="font-weight:600;font-size:13px ${n.cls?';color:'+n.cls:''}">${n.msg}</div></div>
      <div style="font-size:11px;color:#4b5563">${n.time}</div>
    </div>`).join('');
}

// ─── Data Store ───────────────────────────────────────────────────────────────
let prevData=null;
let _loading=false;

async function load(){
  if(_loading) return;
  _loading=true;
  try{
    const d=await fetch('/api/data',{cache:'no-store'}).then(r=>r.json());
    window._lastData=d;
    const now=new Date();
    const nowStr=now.toLocaleTimeString('en-IN',{hour:'2-digit',minute:'2-digit'});
    const nextMin=new Date(now.getTime()+900000);
    const nextStr=nextMin.toLocaleTimeString('en-IN',{hour:'2-digit',minute:'2-digit'});

    // ── HEADER ────────────────────────────────────────────────────────────────
    document.getElementById('last-updated').textContent='Last updated: '+now.toLocaleTimeString('en-IN');
    const mktEl=document.getElementById('hdr-market');
    if(d.market_open){mktEl.innerHTML='<span class="green">🟢 OPEN</span>';}
    else{mktEl.innerHTML='<span class="red">🔴 CLOSED</span>';}
    const bMode = (d.broker_mode || (d.paper_trading ? 'PAPER' : 'UNKNOWN')).toUpperCase();
    const bStart = d.broker_startup_timestamp && d.broker_startup_timestamp !== '—'
        ? ' · ' + new Date(d.broker_startup_timestamp).toLocaleTimeString('en-IN',{hour:'2-digit',minute:'2-digit',second:'2-digit'})
        : '';
    document.getElementById('hdr-mode').textContent=(d.trading_mode||'swing').toUpperCase()+' '+bMode+bStart;
    const scanActive=d.scan_running?'Scanning now':(d.market_open?'Scanning soon':'Market closed');
    document.getElementById('hdr-last-scan').textContent=(d.last_scan&&d.last_scan!=='—')?d.last_scan:scanActive;
    document.getElementById('hdr-next-scan').textContent=(d.next_scan&&d.next_scan!=='—')?d.next_scan:(d.market_open?'Awaiting schedule':'Market closed');
    const stEl=document.getElementById('hdr-status');
    stEl.innerHTML=d.kite_ok?'<span class="green">🟢 Running</span>':'<span class="red">🔴 Offline</span>';
    document.getElementById('hdr-token').textContent=fmtDateTime(d.token_expiry,true)||'—';

    // ── TAB: DASHBOARD ────────────────────────────────────────────────────────
    const ph=d.portfolio_health||{};
    const portVal=parseFloat(ph.account_value||0);
    const budget=parseFloat(d.budget||5000);
    const dpnl=parseFloat(d.daily_pnl||0);
    document.getElementById('d-portfolio-value').textContent=rupee(portVal);
    const dret=portVal>0?(dpnl/portVal*100).toFixed(2):0;
    const dpnlEl=document.getElementById('d-daily-pnl');
    dpnlEl.textContent=pnlStr(dpnl);dpnlEl.className='stat-value '+(dpnl>=0?'green':'red');
    document.getElementById('d-daily-pnl-pct').innerHTML='<span class="'+(dpnl>=0?'green':'red')+'">'+pct(dret)+'</span>';
    const retEl=document.getElementById('d-portfolio-return');
    retEl.innerHTML='<span class="'+(dpnl>=0?'green':'red')+'">'+pct(dret)+' today</span>';
    const cashEl=document.getElementById('d-cash');
    cashEl.textContent=rupee(d.cash||0);
    document.getElementById('d-cash-pct').textContent=Math.round((d.cash||0)/(d.cfg_trading_amount||15000)*100)+'% of budget';
    const op=parseInt(d.open_positions||0);
    document.getElementById('d-open-pos').textContent=op;
    document.getElementById('d-pos-detail').textContent=op+' / '+(d.cfg_max_positions||5)+' max';

    // Risk monitor
    const an=d.analytics||{};
    document.getElementById('d-exposure').textContent=rupee(an.exposure||0);
    document.getElementById('d-risk').textContent=rupee(an.risk||0);
    document.getElementById('d-reward').textContent=rupee(an.potential_profit||0);
    const rr=parseFloat(an.risk_reward||0);
    const rrEl=document.getElementById('d-rr');
    rrEl.textContent='1 : '+(rr>0?rr.toFixed(2):'—');
    rrEl.className='stat-value-sm '+(rr>=2?'green':rr>=1?'yellow':'red');
    const dd=parseFloat(ph.drawdown_pct||0);
    const ddEl2=document.getElementById('d-drawdown');
    ddEl2.textContent=dd.toFixed(2)+'%';ddEl2.className='stat-value-sm '+(dd<=1?'green':dd<=3?'yellow':'red');

    // Positions table (simplified for the dashboard)
    const pb=document.getElementById('d-positions');
    const totalEl=document.getElementById('d-positions-total');
    const positions=d.positions||[];

    // Update holdings count
    document.getElementById('d-holdings-count').textContent = `(${positions.length})`;

    if(positions.length){
      let totalPnl = 0;
      let totalDayPctAcc = 0;
      let totalQty = 0;

      const positionsRows = positions.map(p=>{
        const qty=parseInt(p.quantity||0);
        const avg=parseFloat(p.average_price||p.entry_price||0);
        const ltp=parseFloat(p.last_price||avg);
        const closePrice=parseFloat(p.close_price||avg);
        const pnl=(ltp-avg)*qty;
        const dayPct=closePrice>0?((ltp-closePrice)/closePrice*100):0;

        const trailSLRaw = (p.trailing_stop != null ? p.trailing_stop : (p.stop_loss || null));
        const trailSL = trailSLRaw > 0 ? rupee(trailSLRaw) : '—';
        const target = p.target ? rupee(p.target) : '—';

        // Current AI recommendation status
        let status='HOLDING', statusColor='#9ca3af';
        if(target !== '—' && p.target > 0 && ltp >= p.target*0.98){
          status='EXIT WATCH'; statusColor='#f59e0b';
        } else if(trailSLRaw > 0 && ltp <= trailSLRaw*1.01){
          status='TRAIL SL'; statusColor='#ef4444';
        } else if(pnl > 0 && avg > 0 && (ltp-avg)/avg > 0.03){
          status='TRAILING'; statusColor='#22c55e';
        }

        totalPnl += pnl;
        totalDayPctAcc += dayPct;
        totalQty += qty;

        const rowBg = pnl > 0 ? 'rgba(34, 197, 94, 0.05)' : pnl < 0 ? 'rgba(239, 68, 68, 0.05)' : '';

        return `<tr style="background:${rowBg}">
          <td style="font-weight:700;color:#f9fafb;padding:10px 8px">${p.tradingsymbol||p.symbol}</td>
          <td style="text-align:right;padding:10px 8px">${qty}</td>
          <td style="text-align:right;padding:10px 8px">${rupee(avg)}</td>
          <td style="text-align:right;padding:10px 8px">${rupee(ltp)}</td>
          <td style="text-align:right;padding:10px 8px" class="${pnlClass(pnl)}">${pnlStr(pnl)}</td>
          <td style="text-align:right;padding:10px 8px" class="${pnlClass(dayPct)}">${pct(dayPct.toFixed(2))}</td>
          <td style="text-align:right;padding:10px 8px">${trailSL}</td>
          <td style="text-align:right;padding:10px 8px">${target}</td>
          <td style="text-align:center;padding:10px 8px"><span style="font-weight:700;color:${statusColor}">${status}</span></td>
        </tr>`;
      }).join('');

      pb.innerHTML = positionsRows;

      const avgDayPct = positions.length ? (totalDayPctAcc / positions.length).toFixed(2) : '0.00';

      document.getElementById('d-total-qty').textContent = totalQty;
      document.getElementById('d-total-pnl').textContent = pnlStr(totalPnl);
      document.getElementById('d-total-pnl').className = pnlClass(totalPnl);
      document.getElementById('d-total-day-pct').textContent = pct(avgDayPct);
      document.getElementById('d-total-day-pct').className = pnlClass(avgDayPct);

      totalEl.style.display = 'table-footer-group';

    } else {
      pb.innerHTML='<tr><td colspan="9" style="text-align:center;color:#4b5563;padding:20px">No open positions</td></tr>';
      totalEl.style.display = 'none';
    }

    // AI Opportunities — tradeable BUY recommendations only
    const opp=(d.recommendations||[]).filter(s=>s.action==='BUY');
    const oppEl=document.getElementById('d-opportunities');
    const maxPos=parseInt(d.cfg_max_positions||5);
    const openPos=parseInt(d.open_positions||0);
    const cash=parseFloat(d.cash||0);
    if(opp.length){
      oppEl.innerHTML=opp.slice(0,5).map(s=>{
        const score=Math.round(s.overall_score||0);
        const price=parseFloat(s.price||0);
        const qty=Math.max(1,parseInt(s.position_size||1));
        const capital=parseFloat(s.investment_amount||qty*price);
        const scoreMin = parseInt(d.cfg_sideways_buy_score_min || 58);
        const canBuy=openPos<maxPos && cash>=capital && (s.trade_score||0)>=scoreMin;
        const rr2=s.stop_loss&&s.target&&s.price?Math.abs(s.target-s.price)/Math.abs(s.price-s.stop_loss):0;
        const trend=s.trend||(score>=70?'Bullish':score>=50?'Neutral':'Bearish');
        const trendStyle=trend==='Bullish'?'color:#22c55e':trend==='Bearish'?'color:#ef4444':'color:#eab308';
        const buyClass=canBuy?'green':'red';
        const buyText=canBuy?'Yes':'No';
        return `<tr>
          <td style="font-weight:700;color:#f9fafb">${s.symbol}</td>
          <td><span style="font-size:16px;font-weight:800;color:${scoreColor(score)}">${score}</span><span class="score-bar" style="background:${scoreColor(score)};width:${score*0.4}px"></span></td>
          <td style="${trendStyle}">${trend}</td>
          <td>${rupee(s.price)}</td>
          <td class="green">${rupee(s.target)}</td>
          <td>${rupee(capital)}</td>
          <td style="color:${canBuy?'#22c55e':'#ef4444'};font-weight:700">${buyText}</td>
          <td>${qty}</td>
        </tr>`;
      }).join('');
    } else {
      oppEl.innerHTML='<tr><td colspan="8" style="text-align:center;color:#4b5563;padding:20px">No tradeable opportunities right now</td></tr>';
    }

    // Trading Status + Capital Usage
    const tsMarket=document.getElementById('ts-market');
    if(tsMarket){ tsMarket.textContent=d.market_open?'OPEN':'CLOSED'; tsMarket.className='stat-value-sm '+(d.market_open?'green':'red'); }
    const tsOpenPos=document.getElementById('ts-open-pos');
    if(tsOpenPos){ tsOpenPos.textContent=openPos+' / '+maxPos; tsOpenPos.className='stat-value-sm '+(openPos>=maxPos?'red':'green'); }
    const tsCash=document.getElementById('ts-cash');
    if(tsCash){ tsCash.textContent=rupee(cash); }
    const tsRisk=document.getElementById('ts-risk');
    const riskLevel=(d.vix_risk||{}).risk_level||'UNKNOWN';
    if(tsRisk){ tsRisk.textContent=riskLevel.toUpperCase(); tsRisk.className='stat-value-sm '+(riskLevel.toUpperCase()==='LOW'?'green':riskLevel.toUpperCase()==='MODERATE'?'yellow':riskLevel.toUpperCase()==='HIGH'?'orange':'red'); }
    const tsHealth=document.getElementById('ts-bot-health');
    const healthOk=d.kite_ok && !(d.health||{}).errors_today;
    if(tsHealth){ tsHealth.textContent=healthOk?'HEALTHY':'ATTENTION'; tsHealth.className='stat-value-sm '+(healthOk?'green':'red'); }

    const investedVal=parseFloat(d.invested||0);
    const used=Math.max(0,investedVal);
    const available=Math.max(0,cash);
    const reserved=Math.max(0,parseFloat(d.margin_blocked||0));
    const totalCap=used+available+reserved;
    const usedPct=totalCap>0?(used/totalCap*100):0;
    document.getElementById('cu-used').textContent=rupee(used);
    document.getElementById('cu-available').textContent=rupee(available);
    document.getElementById('cu-reserved').textContent=rupee(reserved);
    document.getElementById('cu-bar').style.width=Math.min(100,usedPct)+'%';
    document.getElementById('cu-pct').textContent=usedPct.toFixed(1)+'% used';

    // AI Recommendation widget
    const regime=(d.market_regime||'—').toUpperCase();
    document.getElementById('ai-rec-regime').textContent=regime;
    document.getElementById('ai-rec-regime').className='stat-value-sm '+(regime==='BULL'?'green':regime==='BEAR'?'red':'yellow');
    const canBuyNew=!(d.economic_event_risk||{}).no_new_buy && openPos<maxPos && (d.market_open||false);
    const confidence=Math.min(100,Math.round(parseFloat(d.confidence||(d.portfolio_health||{}).confidence||70)));
    const action=canBuyNew?'LOOK FOR BUY SETUPS':regime==='BEAR'?'STAY DEFENSIVE':'HOLD / WATCH';
    const advice=(dpnl||0)>=0?'Let winners run with trailing SLs':(dpnl||0)<-250?'Daily loss rising - reduce risk':'Hold existing positions';
    document.getElementById('ai-rec-action').textContent=action;
    document.getElementById('ai-rec-action').className='stat-value-sm '+(canBuyNew?'green':'yellow');
    document.getElementById('ai-rec-advice').textContent=advice;
    document.getElementById('ai-rec-new-buy').textContent=canBuyNew?'Yes':'No';
    document.getElementById('ai-rec-new-buy').className='stat-value-sm '+(canBuyNew?'green':'red');
    document.getElementById('ai-rec-confidence').textContent=confidence+'%';

    // Global Markets (simplified)
    const gm=d.global_markets||{};
    const gmAssets=gm.assets||{};
    const gmSent=parseFloat(gm.sentiment_score||50);
    const gmSentEl=document.getElementById('gm-sentiment');
    gmSentEl.textContent=(gmSent>=70?'BULLISH':gmSent>=40?'NEUTRAL':'BEARISH')+' ('+gmSent.toFixed(1)+')';
    gmSentEl.className='stat-value-sm '+(gmSent>=70?'green':gmSent>=40?'yellow':'red');
    const drivers=Object.entries(gmAssets).filter(([,a])=>a.return_5d_pct!=null).sort((a,b)=>Math.abs(b[1].return_5d_pct)-Math.abs(a[1].return_5d_pct)).slice(0,3).map(([name,a])=>{
      const v=parseFloat(a.return_5d_pct);
      const col=v>=0?'#22c55e':'#ef4444';
      return `<div><span style="color:#f9fafb">${name}</span> <span style="color:${col};font-weight:700">${v>=0?'+':''}${v.toFixed(1)}%</span></div>`;
    }).join('');
    document.getElementById('gm-drivers').innerHTML=drivers||'<span style="color:#4b5563">No data</span>';

    // Economic Events
    const ev=d.economic_event_risk||{};
    const next=ev.next_event||{};
    if(next.event_type && ev.hours_to_event!=null){
      document.getElementById('evt-name').textContent=next.event_type;
      document.getElementById('evt-hours').textContent=ev.hours_to_event.toFixed(1);
      const evtFactor=parseFloat(ev.size_factor||1);
      const evtFactorEl=document.getElementById('evt-factor');
      evtFactorEl.textContent=evtFactor.toFixed(2);
      evtFactorEl.className='stat-value-sm '+(evtFactor>=1?'green':evtFactor>=0.5?'yellow':'red');
      document.getElementById('evt-buy').textContent=ev.no_new_buy?'Blocked':'Allowed';
      document.getElementById('evt-buy').className='stat-value-sm '+(ev.no_new_buy?'red':'green');
    } else {
      document.getElementById('evt-name').textContent='No major economic events today';
      document.getElementById('evt-hours').textContent='—';
      const evtFactor=parseFloat(ev.size_factor||1);
      const evtFactorEl=document.getElementById('evt-factor');
      evtFactorEl.textContent=evtFactor.toFixed(2);
      evtFactorEl.className='stat-value-sm '+(evtFactor>=1?'green':evtFactor>=0.5?'yellow':'red');
      document.getElementById('evt-buy').textContent='Allowed';
      document.getElementById('evt-buy').className='stat-value-sm green';
    }

    // Options Intelligence
    const oi=d.options_intelligence||{};
    const oiPcr=parseFloat(oi.pcr);
    const oiPcrEl=document.getElementById('oi-pcr');
    if(!isNaN(oiPcr) && oiPcrEl){ oiPcrEl.textContent=oiPcr.toFixed(2); oiPcrEl.className='stat-value-sm '+(oiPcr>=0.8 && oiPcr<=1.2?'green':oiPcr>1.2?'red':'yellow'); }
    const oiMax=document.getElementById('oi-max-pain');
    if(oiMax) oiMax.textContent=oi.max_pain!=null?rupee(oi.max_pain):'—';
    const oiBuild=document.getElementById('oi-buildup');
    if(oiBuild) oiBuild.textContent=oi.oi_build_up!=null?Number(oi.oi_build_up).toLocaleString('en-IN',{maximumFractionDigits:0}):'—';
    const oiLong=document.getElementById('oi-long');
    if(oiLong){ oiLong.textContent=oi.long_buildup===true?'YES':(oi.long_buildup===false?'No':'—'); oiLong.className='stat-value-sm '+(oi.long_buildup===true?'green':(oi.long_buildup===false?'red':'yellow')); }
    const oiShort=document.getElementById('oi-short');
    if(oiShort){ oiShort.textContent=oi.short_buildup===true?'YES':(oi.short_buildup===false?'No':'—'); oiShort.className='stat-value-sm '+(oi.short_buildup===true?'red':(oi.short_buildup===false?'green':'yellow')); }
    const oiPut=document.getElementById('oi-put-wall');
    if(oiPut) oiPut.textContent=oi.put_wall_strike!=null?rupee(oi.put_wall_strike):'—';
    const oiSupport=document.getElementById('oi-support');
    if(oiSupport) oiSupport.textContent=oi.put_wall_oi!=null?Number(oi.put_wall_oi).toLocaleString('en-IN'):'—';
    const oiConf=document.getElementById('oi-conf-boost');
    if(oiConf){ oiConf.textContent=oi.confidence_boost!=null?('+'+oi.confidence_boost.toFixed(0)+'%'):'—'; oiConf.className='stat-value-sm green'; }

    // FII/DII Flow
    const fd=d.fii_dii||{};
    const hasFii=fd.fii_net!=null || fd.dii_net!=null;
    if(hasFii){
      const fmtCr=(v)=>{const n=parseFloat(v); return isNaN(n)?'—':(n>=0?'+':'')+n.toFixed(0)+' Cr';};
      const fiiNet=parseFloat(fd.fii_net);
      const diiNet=parseFloat(fd.dii_net);
      const netFlow=parseFloat(fd.net_flow);
      const fiiNetEl=document.getElementById('fii-net');
      fiiNetEl.textContent=fmtCr(fiiNet);
      fiiNetEl.className='stat-value-sm '+(fiiNet>=0?'green':'red');
      const diiNetEl=document.getElementById('dii-net');
      diiNetEl.textContent=fmtCr(diiNet);
      diiNetEl.className='stat-value-sm '+(diiNet>=0?'green':'red');
      const netFlowEl=document.getElementById('fii-dii-net');
      netFlowEl.textContent=fmtCr(netFlow);
      netFlowEl.className='stat-value-sm '+(netFlow>=0?'green':'red');
      const sent=(fd.sentiment||'NEUTRAL').toUpperCase();
      document.getElementById('fii-dii-sentiment').textContent=sent;
      document.getElementById('fii-dii-sentiment').className='stat-value-sm '+(sent==='POSITIVE'?'green':sent==='NEGATIVE'?'red':'yellow');
    } else {
      const status=fd.status||'Awaiting market data';
      ['fii-net','dii-net','fii-dii-net','fii-dii-sentiment'].forEach(id=>{
        const el=document.getElementById(id);
        if(el){ el.textContent=status; el.className='stat-value-sm'; }
      });
    }

    // VIX Risk
    const vix=d.vix_risk||{};
    const vixStatus=vix.status;
    document.getElementById('vix-value').textContent=vix.vix!=null?vix.vix.toFixed(2):(vixStatus||'—');
    document.getElementById('vix-score').textContent=vix.volatility_score!=null?vix.volatility_score.toFixed(1):'—';
    const vixFactorEl=document.getElementById('vix-factor');
    vixFactorEl.textContent=vix.risk_factor!=null?parseFloat(vix.risk_factor).toFixed(2):'—';
    const vixFactor=parseFloat(vix.risk_factor||0);
    vixFactorEl.className='stat-value-sm '+(vixFactor>=0.8?'green':vixFactor>=0.5?'yellow':'red');
    const vixLevelEl=document.getElementById('vix-level');
    vixLevelEl.textContent=vix.risk_level!=null?(vix.risk_level||'UNKNOWN').toUpperCase():'—';
    const vixLevel=(vix.risk_level||'UNKNOWN').toUpperCase();
    vixLevelEl.className='stat-value-sm '+(vixLevel==='LOW'?'green':vixLevel==='MODERATE'?'yellow':vixLevel==='HIGH'?'orange':vixLevel==='EXTREME'?'red':'');

    // Sector Rotation (dedupe strong from weak)
    const sr=d.sector_rotation||{};
    const strongSet=new Set((sr.top5_strong||[]).map(s=>s.sector));
    const strong=(sr.top5_strong||[]).slice(0,5);
    const weak=(sr.top5_weak||[]).filter(s=>!strongSet.has(s.sector)).slice(0,5);
    document.getElementById('sr-strong').innerHTML=strong.length?strong.map(s=>`<div>${s.sector||'—'} <span style="color:#f9fafb">${(s.momentum_score||0).toFixed(0)}</span> <span style="color:#9ca3af;font-size:11px">(${s.return_30d_pct!=null?s.return_30d_pct.toFixed(1):'—'}%)</span></div>`).join(''):'—';
    document.getElementById('sr-weak').innerHTML=weak.length?weak.map(s=>`<div>${s.sector||'—'} <span style="color:#f9fafb">${(s.momentum_score||0).toFixed(0)}</span> <span style="color:#9ca3af;font-size:11px">(${s.return_30d_pct!=null?s.return_30d_pct.toFixed(1):'—'}%)</span></div>`).join(''):'—';

    // Recent orders - show only completed broker orders
    const ordEl=document.getElementById('d-recent-orders');
    const completed=(d.orders||[]).filter(o=>o.status==='COMPLETE'||(o.filled_quantity>0&&o.status!=='REJECTED')).reverse().slice(0,10);
    if(completed.length){
      ordEl.innerHTML=completed.map(o=>{
        const isBuy=o.transaction_type==='BUY';
        const t=fmtTime(o.order_timestamp)||'—';
        const pnl=parseFloat(o.pnl||0);
        const pnlPart=!isBuy?`<span class="${pnlClass(pnl)}" style="font-size:11px">${pnlStr(pnl)}</span>`:'';
        return `<div class="notif-item">
          <span class="badge ${isBuy?'badge-buy':'badge-sell'}">${o.transaction_type}</span>
          <div style="flex:1">
            <span style="font-weight:700;color:#f9fafb">${o.tradingsymbol}</span>
            <span style="color:#4b5563;font-size:12px"> × ${o.quantity} @ ${rupee(o.average_price||o.price||0)}</span>
          </div>
          ${pnlPart}
          <div style="font-size:11px;color:#4b5563">${t}</div>
        </div>`;
      }).join('');
    } else {
      ordEl.innerHTML='<div style="color:#4b5563;font-size:13px;padding:20px;text-align:center">No completed orders today</div>';
    }

    // Daily goals
    const tradeBudget=parseFloat(d.cfg_trading_amount||d.budget||15000);
    const dailyTarget=Math.round(tradeBudget*(d.cfg_tgt_pct||0.10));
    const dailyLossLimit=Math.round(tradeBudget*(d.cfg_daily_loss||0.05));
    const currentPnl=dpnl;
    const remaining=Math.max(0,dailyTarget-currentPnl);
    document.getElementById('d-goal-profit-val').textContent=rupee(currentPnl)+' / '+rupee(dailyTarget);
    document.getElementById('d-goal-profit-bar').style.width=Math.min(100,Math.max(0,currentPnl/dailyTarget*100))+'%';
    document.getElementById('d-goal-capital-val').textContent=rupee(remaining)+' remaining';
    document.getElementById('d-goal-capital-bar').style.width=Math.min(100,Math.max(0,(1-remaining/dailyTarget)*100))+'%';
    document.getElementById('d-goal-loss-val').textContent=rupee(Math.abs(Math.min(0,currentPnl)))+' / '+rupee(dailyLossLimit);
    document.getElementById('d-goal-loss-bar').style.width=Math.min(100,Math.abs(Math.min(0,currentPnl))/dailyLossLimit*100)+'%';

    // Notifications: detect changes
    if(prevData){
      if((d.open_positions||0)>(prevData.open_positions||0)) pushNotif('🟢','New position opened','#22c55e');
      if((d.open_positions||0)<(prevData.open_positions||0)) pushNotif('🏁','Position closed','#60a5fa');
      if((d.market_regime||'')!==(prevData.market_regime||'')) pushNotif('📊','Market regime changed to '+d.market_regime,'#eab308');
      if(d.ip_changed && !prevData.ip_changed) pushNotif('⚠️','IP changed! Update Kite whitelist now','#f59e0b');
    }

    // ── IP STATUS TAB ─────────────────────────────────────────────────────────
    updateIpStatus(d);

    // ── TAB: PORTFOLIO ────────────────────────────────────────────────────────
    renderPortfolio(d);

    // ── TAB: POSITIONS ───────────────────────────────────────────
    renderPositionsTab(d);

    // ── TAB: HISTORY ─────────────────────────────────────────────
    const hCash=parseFloat(d.cash||0);
    const hHeld=parseFloat(d.holdings_value||0);
    const hPositions=d.positions||[];
    const hInvested=hPositions.reduce((s,p)=>s+parseFloat(p.average_price||0)*parseInt(p.quantity||0),0);
    const hCurrentVal=hPositions.reduce((s,p)=>s+parseFloat(p.last_price||p.average_price||0)*parseInt(p.quantity||0),0);
    const hTotal=hCash+hCurrentVal;
    const hCashEl=document.getElementById('h-cash');
    if(hCashEl)hCashEl.textContent=rupee(hCash);
    const hInvEl=document.getElementById('h-invested');
    if(hInvEl)hInvEl.textContent=rupee(hInvested);
    const hHValEl=document.getElementById('h-holdings-val');
    if(hHValEl)hHValEl.textContent=rupee(hCurrentVal);
    const hTotEl=document.getElementById('h-total');
    if(hTotEl)hTotEl.textContent=rupee(hTotal);

    // Stock breakdown
    const sbEl=document.getElementById('h-stock-breakdown');
    if(sbEl){
      if(hPositions.length){
        sbEl.innerHTML=hPositions.map(p=>{
          const avg=parseFloat(p.average_price||0);
          const ltp=parseFloat(p.last_price||avg);
          const qty=parseInt(p.quantity||0);
          const invested=avg*qty;
          const curVal=ltp*qty;
          const pnl=curVal-invested;
          const retPct=avg>0?((ltp-avg)/avg*100):0;
          return `<tr>
            <td style="font-weight:700;color:#f9fafb">${p.tradingsymbol}</td>
            <td style="text-align:center">${qty}</td>
            <td>${rupee(avg)}</td>
            <td>${rupee(invested)}</td>
            <td>${rupee(curVal)}</td>
            <td class="${pnlClass(pnl)}">${pnlStr(pnl)}</td>
            <td class="${pnlClass(retPct)}">${retPct.toFixed(2)}%</td>
          </tr>`;
        }).join('');
      } else {
        sbEl.innerHTML='<tr><td colspan="7" style="text-align:center;color:#4b5563;padding:20px">No open positions</td></tr>';
      }
    }

    // Open positions + Retry queue + event history
    renderHistoryOpenPositions(d);
    renderRetryQueue(d.pending_sells || []);
    window._historyData=d.trade_events||[];
    window._completedTrades=d.completed_trades_full||[];
    window._allOrders=d.all_orders||[];
    renderHistory(window._historyFilter||'ALL');

    // ── TAB: TRADE LIFECYCLE ─────────────────────────────────────────────────
    const lcEl=document.getElementById('lifecycle-container');
    const lcPositions=d.lifecycle_positions||[];
    if(lcPositions.length && lcEl){
      lcEl.innerHTML=lcPositions.map(lp=>{
        const rec=lp.recommendation||'HOLD';
        const recCol=rec==='SELL'?'#ef4444':rec==='PARTIAL EXIT'?'#f97316':rec==='MOVE SL TO BREAK-EVEN'?'#eab308':'#22c55e';
        const recIcon=rec==='SELL'?'🔴':rec==='PARTIAL EXIT'?'🟠':rec==='MOVE SL TO BREAK-EVEN'?'🟡':'🟢';
        const steps=['Break-even','Partial 1','Partial 2','Final Exit'];
        const prog=(lp.partial_progress||[]);
        const stepHtml=steps.map((s,i)=>{
          const done=prog[i]?true:false;
          const bg=done?'#22c55e':(i===Math.max(0,lp.partial_count||0)?'#3b82f6':'#1f2937');
          const col=done?'#fff':'#9ca3af';
          return `<div style="text-align:center"><div style="width:32px;height:32px;border-radius:50%;background:${bg};color:${col};display:flex;align-items:center;justify-content:center;font-weight:700;margin:0 auto 4px">${done?'✓':(i+1)}</div><div style="font-size:10px;color:#9ca3af">${s}</div></div>`;
        }).join('');
        const trig=(lp.active_triggers||[]).filter(t=>['EMA','ATR','GAP','SWING LOW','TIME EXIT','STOP','BREAK-EVEN','TARGET'].includes(t.name));
        const trigHtml=trig.map(t=>{
          const st=t.status||'';
          const col=st==='TRIGGERED'||st==='due'?'#ef4444':st==='REACHED'||st==='monitoring'||st==='locked'?'#22c55e':st==='ready'||st==='watch'?'#eab308':'#9ca3af';
          const level=t.name==='TIME EXIT'?(t.level!=null?t.level+'d':''):rupee(t.level||0);
          return `<div style="background:#111827;border:1px solid #1f2937;border-radius:8px;padding:8px"><div style="font-size:10px;color:#9ca3af">${t.name}</div><div style="font-size:12px;font-weight:700;color:#f9fafb">${level}</div><div style="font-size:10px;color:${col};text-transform:uppercase">${st}</div></div>`;
        }).join('');
        return `<div class="card" style="background:linear-gradient(135deg,#111827 0%,#0f1724 100%)">
          <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:12px">
            <div>
              <div style="font-size:17px;font-weight:800;color:#f9fafb">${lp.symbol}</div>
              <div style="font-size:12px;color:#9ca3af;margin-top:2px">Qty ${lp.quantity} · RR ${(lp.current_rr||0).toFixed(2)} · Day ${lp.days_held}</div>
            </div>
            <div style="text-align:right">
              <div style="font-size:14px;font-weight:800;color:${recCol};background:${recCol}11;padding:4px 10px;border-radius:20px">${recIcon} ${rec}</div>
            </div>
          </div>
          <div style="background:#0f172a;border:1px solid #1f2937;border-radius:8px;padding:10px;margin-bottom:12px">
            <div style="font-size:12px;color:#9ca3af;margin-bottom:4px">AI Reason</div>
            <div style="font-size:13px;color:#e2e8f0">${lp.reason||'—'}</div>
          </div>
          <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-bottom:14px;font-size:12px">
            <div class="card-sm" style="text-align:center"><div style="color:#9ca3af;font-size:11px">First Entry</div><div style="font-weight:700;color:#f9fafb">${rupee(lp.first_entry||0)}</div></div>
            <div class="card-sm" style="text-align:center"><div style="color:#9ca3af;font-size:11px">Avg Cost</div><div style="font-weight:700;color:#f9fafb">${rupee(lp.average_price||0)}</div></div>
            <div class="card-sm" style="text-align:center"><div style="color:#9ca3af;font-size:11px">Current Price</div><div style="font-weight:700;color:#f9fafb">${rupee(lp.current_price||0)}</div></div>
            <div class="card-sm" style="text-align:center"><div style="color:#9ca3af;font-size:11px">Highest</div><div style="font-weight:700;color:#22c55e">${rupee(lp.highest_price||0)}</div></div>
            <div class="card-sm" style="text-align:center"><div style="color:#9ca3af;font-size:11px">Drawdown</div><div style="font-weight:700;color:${(lp.drawdown_pct||0)>5?'#ef4444':'#f9fafb'}">${(lp.drawdown_pct||0).toFixed(2)}%</div></div>
            <div class="card-sm" style="text-align:center"><div style="color:#9ca3af;font-size:11px">ATR</div><div style="font-weight:700;color:#f9fafb">${rupee(lp.atr_at_entry||0)}</div></div>
          </div>
          <div style="font-size:12px;color:#9ca3af;margin-bottom:8px">Lifecycle Progress</div>
          <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:14px">${stepHtml}</div>
          <div style="font-size:12px;color:#9ca3af;margin-bottom:8px">Exit Triggers</div>
          <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(90px,1fr));gap:8px;margin-bottom:12px">${trigHtml}</div>
          <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:10px;font-size:11px;color:#9ca3af">
            <div>Initial SL <b style="color:#ef4444">${rupee(lp.initial_sl||0)}</b></div>
            <div>Trail SL <b style="color:#f59e0b">${rupee(lp.trailing_sl||lp.effective_sl||0)}</b></div>
            <div>Target <b style="color:#22c55e">${rupee(lp.target||0)}</b></div>
          </div>
        </div>`;
      }).join('');
    } else if(lcEl){
      lcEl.innerHTML='<div class="card" style="color:#4b5563;text-align:center;padding:40px">No open positions to manage</div>';
    }

    // ── TAB: AI SIGNALS ───────────────────────────────────────────────────────
    if (typeof renderAiSignals === 'function') renderAiSignals(d);

    // ── TAB: ANALYTICS ────────────────────────────────────────────────────────
    const strat=d.strategy_stats||{};
    const wr=parseFloat(d.win_rate||0)*100;
    const totalClosed=parseInt(d.closed_trade_count||d.total_trades||0);

    const wrEl=document.getElementById('a-win-rate');
    wrEl.textContent=wr.toFixed(0)+'%';wrEl.className='stat-value '+(wr>=60?'green':wr>=40?'yellow':'red');
    document.getElementById('a-win-basis').textContent=`Based on ${totalClosed} closed ${totalClosed===1?'trade':'trades'}`;
    document.getElementById('a-total-trades').textContent=totalClosed;
    const netPnlEl=document.getElementById('a-net-pnl');
    const netPnl=parseFloat(strat.total_net_pnl||0);
    netPnlEl.textContent=pnlStr(netPnl);netPnlEl.className='stat-value '+(netPnl>=0?'green':'red');
    const aiAccEl=document.getElementById('a-ai-accuracy');
    if(d.ai_accuracy!=null){
      aiAccEl.textContent=d.ai_accuracy.toFixed(1)+'%';aiAccEl.className='stat-value '+(d.ai_accuracy>=60?'green':d.ai_accuracy>=40?'yellow':'red');
    } else {
      aiAccEl.textContent='—';aiAccEl.className='stat-value';
    }

    // Profit factor: ∞ when no losses exist
    const apfVal=parseFloat(strat.profit_factor||0);
    const aNoLosses=(strat.avg_loss||0)===0&&(strat.avg_win||0)>0;
    document.getElementById('a-profit-factor').textContent=aNoLosses?'∞':apfVal.toFixed(2);
    document.getElementById('a-avg-win').textContent=rupee(strat.avg_win||0);
    document.getElementById('a-avg-loss').textContent=rupee(strat.avg_loss||0);
    document.getElementById('a-expectancy').textContent=rupee(strat.expectancy||0);
    document.getElementById('a-avg-hold').textContent=(strat.avg_hold_days||0).toFixed(1);
    const sharpe=parseFloat(d.sharpe_ratio||0), sortino=parseFloat(d.sortino_ratio||0);
    document.getElementById('a-sharpe').textContent=sharpe?sharpe.toFixed(2):'—';
    document.getElementById('a-sortino').textContent=sortino?sortino.toFixed(2):'—';
    const ddEl=document.getElementById('a-max-drawdown');
    ddEl.textContent=parseFloat(ph.drawdown_pct||0).toFixed(2)+'%';

    // Win rate gauge (doughnut)
    if(typeof Chart!=='undefined'){
    const wrCtx=document.getElementById('chart-winrate');
    if(wrCtx){
      const wrVal=Math.round(wr);
      const wrCfg={type:'doughnut',data:{labels:['Win','Loss'],datasets:[{data:[wrVal,Math.max(0,100-wrVal)],backgroundColor:[wrVal>=60?'#22c55e':wrVal>=40?'#eab308':'#ef4444','#1f2937'],borderWidth:0}]},options:{plugins:{legend:{display:false},tooltip:{enabled:false},beforeDraw(chart){const {ctx,chartArea:{top,left,width,height}}=chart;ctx.save();ctx.font='bold 28px Inter';ctx.fillStyle='#f9fafb';ctx.textAlign='center';ctx.textBaseline='middle';ctx.fillText(wrVal+'%',left+width/2,top+height/2);ctx.restore();}},cutout:'70%',maintainAspectRatio:false}};
      chartWinrate=makeOrUpdate(chartWinrate,wrCtx,wrCfg);
    }

    // Portfolio growth (line)
    const pgCtx=document.getElementById('chart-portfolio-growth');
    if(pgCtx){
      const pg=d.portfolio_growth||[];
      if(pg.length>1){
        const labels=pg.map(p=>p.date);
        const vals=pg.map(p=>p.equity);
        const pgCfg={type:'line',data:{labels:labels,datasets:[{label:'Portfolio Value',data:vals,borderColor:'#22c55e',backgroundColor:'rgba(34,197,94,0.15)',fill:true,tension:0.3,pointRadius:3}]},options:{responsive:true,maintainAspectRatio:false,scales:{x:{ticks:{color:'#6b7280',font:{size:10}},grid:{color:'#1f2937'}},y:{ticks:{color:'#6b7280',font:{size:10},callback:v=>'₹'+v.toLocaleString()},grid:{color:'#1f2937'}}},plugins:{legend:{display:false}}}};
        chartPortfolioGrowth=makeOrUpdate(chartPortfolioGrowth,pgCtx,pgCfg);
      } else {
        pgCtx.parentElement.innerHTML='<div style="color:#6b7280;text-align:center;padding:40px 0">Not enough closed trades for portfolio growth chart</div>';
      }
    }
    } // end Chart guard

    // Trade Calendar (realized P&L per weekday, from backend)
    const calEl=document.getElementById('a-calendar');
    const wc=(d.weekly_calendar||[0,0,0,0,0]).map(v=>parseFloat(v||0));
    const weekDays=[{d:'Mon',v:wc[0]},{d:'Tue',v:wc[1]},{d:'Wed',v:wc[2]},{d:'Thu',v:wc[3]},{d:'Fri',v:wc[4]}];
    if(calEl) calEl.innerHTML=weekDays.map(({d:day,v})=>`
      <div style="text-align:center;flex:1">
        <div style="font-size:11px;color:#4b5563;margin-bottom:4px;white-space:nowrap">${day}</div>
        <div style="padding:8px 4px;border-radius:8px;font-size:13px;font-weight:700;background:${v>=0?'#16a34a22':'#dc262622'};color:${v>=0?'#22c55e':'#ef4444'}">${v>=0?'+':''}${v.toFixed(0)}</div>
      </div>`).join('');

    // Completed Trades (BUY -> SELL pairs)
    const ctEl=document.getElementById('a-completed-trades');
    if(d.closed_trades&&d.closed_trades.length){
      ctEl.innerHTML=d.closed_trades.map(t=>`
        <tr style="border-bottom:1px solid #1f2937">
          <td style="padding:8px;font-weight:700;color:#f9fafb">${t.symbol}</td>
          <td style="padding:8px;color:#9ca3af">${rupee(t.buy)}</td>
          <td style="padding:8px;color:#9ca3af">${rupee(t.sell)}</td>
          <td style="padding:8px;text-align:center">${t.qty}</td>
          <td style="padding:8px;text-align:right;font-weight:700" class="${pnlClass(t.pnl)}">${pnlStr(t.pnl)}</td>
          <td style="padding:8px;text-align:center">${t.days}</td>
          <td style="padding:8px;color:#9ca3af;font-size:11px">${t.exit}</td>
        </tr>`).join('');
    } else {
      ctEl.innerHTML='<tr><td colspan="7" style="text-align:center;color:#4b5563;padding:20px">No completed trades</td></tr>';
    }

    // Sector & Exit tables
    function renderFactorTable(el, obj){
      if(!el) return;
      const rows=Object.entries(obj||{}).sort((a,b)=>b[1].net_pnl-a[1].net_pnl);
      if(!rows.length){el.innerHTML='<div style="color:#6b7280;font-size:13px">No data yet.</div>';return;}
      el.innerHTML=`<table style="width:100%;border-collapse:collapse;font-size:12px"><tbody>`+rows.map(([k,v])=>`
        <tr style="border-bottom:1px solid #1f2937">
          <td style="padding:8px;color:#f9fafb">${k}</td>
          <td style="padding:8px;text-align:center;color:#9ca3af">${v.trades}</td>
          <td style="padding:8px;text-align:right;font-weight:700;color:${v.net_pnl>=0?'#22c55e':'#ef4444'}">${pnlStr(v.net_pnl)}</td>
          <td style="padding:8px;text-align:center;color:${(v.win_rate||0)>=50?'#22c55e':'#ef4444'}">${(v.win_rate||0).toFixed(0)}%</td>
        </tr>`).join('')+'</tbody></table>';
    }
    renderFactorTable(document.getElementById('a-sector-table'), strat.by_sector);
    renderFactorTable(document.getElementById('a-exit-table'), strat.by_exit_reason);

    // Order History
    const orderRows=(arr)=>arr.map(o=>{
      const t=fmtTime(o.order_timestamp)||'—';
      const side=(o.transaction_type||'').toUpperCase();
      const cls=side==='BUY'?'#22c55e':side==='SELL'?'#ef4444':'#9ca3af';
      const st=(o.status||'').toUpperCase();
      const stColor=st==='COMPLETE'?'#22c55e':st==='REJECTED'?'#ef4444':'#eab308';
      return `<tr style="border-bottom:1px solid #1f2937">
        <td style="padding:8px;color:#6b7280">${t}</td>
        <td style="padding:8px;font-weight:700;color:#f9fafb">${o.tradingsymbol}</td>
        <td style="padding:8px;text-align:center;color:${cls}">${side}</td>
        <td style="padding:8px;text-align:center">${o.quantity}</td>
        <td style="padding:8px;text-align:center;color:${stColor};font-size:11px">${st}</td>
      </tr>`;
    }).join('');
    const ohEl=document.getElementById('a-order-history');
    const orders=(d.order_history||[]).filter(o=>o.status&&o.status.toUpperCase()==='COMPLETE');
    if(ohEl) ohEl.innerHTML=orders.length?orderRows(orders):'<tr><td colspan="5" style="text-align:center;color:#4b5563;padding:20px">No completed orders</td></tr>';
    const ro=(d.rejected_orders||[]);
    document.getElementById('a-rejected-count').textContent=ro.length;
    const rejEl=document.getElementById('a-rejected-orders');
    if(rejEl) rejEl.innerHTML=ro.length?orderRows(ro):'<tr><td colspan="5" style="text-align:center;color:#4b5563;padding:20px">No rejected orders</td></tr>';

    // ── TAB: BOT STATUS ───────────────────────────────────────────────────────
    document.getElementById('bs-kite').innerHTML=d.kite_ok?'<span class="green">✅ Connected</span>':'<span class="red">❌ Offline</span>';
    document.getElementById('bs-mode').textContent=(d.trading_mode||'swing').toUpperCase();
    document.getElementById('bs-token').textContent=fmtDateTime(d.token_expiry,true)||'—';
    document.getElementById('bs-token2').textContent=fmtDateTime(d.token_expiry,true)||'—';
    document.getElementById('bs-paper').innerHTML=d.paper_trading?'<span class="yellow">⚠️ Paper Mode</span>':'<span class="green">✅ Live Trading</span>';
    const bsrEl=document.getElementById('bs-regime');
    bsrEl.textContent=d.market_regime||'—';bsrEl.className='stat-value '+(d.market_regime==='BULL'?'green':d.market_regime==='BEAR'?'red':'yellow');
    document.getElementById('bs-budget').textContent=rupee(d.budget||0);
    const bsScanActive=d.scan_running?'Scanning now':(d.market_open?'Scanning soon':'Market closed');
    document.getElementById('bs-last-scan').textContent=(d.last_scan&&d.last_scan!=='—')?d.last_scan:bsScanActive;
    document.getElementById('bs-next-scan').textContent=(d.next_scan&&d.next_scan!=='—')?d.next_scan:(d.market_open?'Awaiting schedule':'Market closed');
    document.getElementById('bs-scanned').textContent=d.stocks_scanned?d.stocks_scanned.toLocaleString('en-IN'):(d.market_open?'No scan yet':'Market closed');
    document.getElementById('bs-ai-signals').textContent=(d.signals||[]).length;
    document.getElementById('bs-orders-exec').textContent=d.orders_executed||0;
    document.getElementById('bs-cfg-amount').textContent=rupee(d.cfg_trading_amount||d.budget||5000);
    document.getElementById('bs-cfg-maxpos').textContent=(d.cfg_max_positions||5);
    document.getElementById('bs-cfg-conf').textContent=((d.cfg_min_confidence||0.52)*100).toFixed(0)+'%';
    document.getElementById('bs-cfg-sl').textContent=((d.cfg_sl_pct||0.05)*100).toFixed(1)+'%';
    document.getElementById('bs-cfg-tgt').textContent=((d.cfg_tgt_pct||0.10)*100).toFixed(1)+'%';
    document.getElementById('bs-cfg-cap').textContent=((d.cfg_max_capital||0.95)*100).toFixed(0)+'%';
    document.getElementById('bs-cfg-loss').textContent=((d.cfg_daily_loss||0.05)*100).toFixed(1)+'%';
    document.getElementById('bs-cfg-risk').textContent=((d.cfg_risk_per_trade||0.02)*100).toFixed(1)+'% of capital';
    document.getElementById('bs-cfg-holddays').textContent=(d.cfg_swing_max_hold_days||15)+' days';
    document.getElementById('bs-cfg-reentry').textContent=(d.cfg_reentry_cooldown_hours||4)+'h cooldown';

    // Position capacity bar
    const posOpen=parseInt(d.open_positions||0);
    const posMax=parseInt(d.cfg_max_positions||7);
    const posBarEl=document.getElementById('bs-pos-bar');
    const posPct=Math.min(posOpen/posMax*100,100);
    if(posBarEl){
      posBarEl.style.width=posPct+'%';
      posBarEl.style.background=posPct>=100?'#ef4444':posPct>=80?'#eab308':'#3b82f6';
    }
    const posLbl=document.getElementById('bs-pos-label');
    if(posLbl) posLbl.textContent=posOpen+' / '+posMax;
    const posMaxLbl=document.getElementById('bs-pos-max');
    if(posMaxLbl) posMaxLbl.textContent=posMax+' max slots';

    // Daily P&L in health section
    const bsDpnl=parseFloat(d.daily_pnl||0);
    const bsDpnlEl=document.getElementById('bs-daily-pnl');
    if(bsDpnlEl){bsDpnlEl.textContent=pnlStr(bsDpnl);bsDpnlEl.className='stat-value '+(bsDpnl>=0?'green':'red');}

    // Drawdown from portfolio_health
    const bsPh=d.portfolio_health||{};
    const bsDdEl=document.getElementById('bs-drawdown');
    if(bsDdEl){
      const bsDd=parseFloat(bsPh.drawdown_pct||0);
      bsDdEl.textContent=bsDd.toFixed(2)+'%';
      bsDdEl.className='stat-value '+(bsDd<5?'green':bsDd<10?'yellow':'red');
    }

    // Win rate from journal (loaded separately in loadJournal)
    // — populated by the async journal loader below

    // Fetch health data for errors/circuit/latency/mem/cpu
    fetch('/api/health').then(r=>r.json()).then(h=>{
      const errEl=document.getElementById('bs-errors');
      if(errEl){const e=parseInt(h.errors_today||0);errEl.textContent=e;errEl.className='stat-value '+(e===0?'green':e<5?'yellow':'red');}
      const cbEl=document.getElementById('bs-circuit');
      if(cbEl){const open=h.circuit_open;cbEl.innerHTML=open?'<span class="red">⚡ OPEN</span>':'<span class="green">✅ Closed</span>';}
      const latEl=document.getElementById('bs-latency');
      if(latEl){const ms=parseFloat(h.api_latency_ms||0);latEl.textContent=ms.toFixed(0)+'ms';latEl.className='stat-value '+(ms<200?'green':ms<500?'yellow':'red');}
      const memEl=document.getElementById('bs-mem');
      if(memEl){const mp=parseFloat(h.mem_pct||0);memEl.textContent=mp.toFixed(0)+'%';memEl.className='stat-value '+(mp<70?'green':mp<85?'yellow':'red');}
      const cpuEl=document.getElementById('bs-cpu');
      if(cpuEl){const cp=parseFloat(h.cpu_pct||0);cpuEl.textContent=cp.toFixed(0)+'%';cpuEl.className='stat-value '+(cp<60?'green':cp<80?'yellow':'red');}
    }).catch(()=>{});

    // Reconciliation status (already embedded in api_data)
    const rs=d.reconciliation_status||{};
    const rsHealthyEl=document.getElementById('rs-healthy');
    if(rsHealthyEl){rsHealthyEl.innerHTML=rs.healthy?'<span class="green">✅ Synced</span>':'<span class="red">❌ Out of sync</span>';}
    const rsLastEl=document.getElementById('rs-last-sync');
    if(rsLastEl){rsLastEl.textContent=fmtDateTime(rs.last_sync)||'—';}
    const rsObjEl=document.getElementById('rs-objects');
    if(rsObjEl){
      const oc=rs.objects_checked||{};
      rsObjEl.textContent=(oc.positions||0)+' pos / '+(oc.trades||0)+' trades';
    }
    const rsRepEl=document.getElementById('rs-repairs');
    if(rsRepEl){rsRepEl.textContent=(rs.repairs||0);rsRepEl.className='stat-value '+((rs.repairs||0)===0?'green':'yellow');}
    const rsMisEl=document.getElementById('rs-mismatches');
    if(rsMisEl){rsMisEl.textContent=(rs.mismatches||0);rsMisEl.className='stat-value '+((rs.mismatches||0)===0?'green':'red');}
    const rsDurEl=document.getElementById('rs-duration');
    if(rsDurEl){rsDurEl.textContent=(rs.duration_ms||0)+' ms';}
    const rsSqlEl=document.getElementById('rs-sqlite');
    if(rsSqlEl){rsSqlEl.textContent=(rs.objects_checked?'Active':'Unknown');rsSqlEl.className='stat-value '+(rs.objects_checked?'green':'yellow');}
    const rsBrEl=document.getElementById('rs-broker');
    if(rsBrEl){rsBrEl.innerHTML=d.broker_live_ready?'<span class="green">✅ Ready</span>':'<span class="red">❌ Not ready</span>';}

    prevData=d;

  }catch(e){
    console.error('Dashboard error:',e);
    document.getElementById('last-updated').textContent='JS ERROR: '+e.message;
  }finally{
    _loading=false;
  }
}

// ─── Journal loader ──────────────────────────────────────────────────────────
async function loadJournal(){
  try{
    const j=await fetch('/api/journal').then(r=>r.json());

    // KPIs — show closed trade stats; fall back to '—' if no closed trades yet
    const np=j.total_net_pnl||0;
    const hasClosed=j.total_trades>0;
    document.getElementById('j-total').textContent=(j.total_trades||0)+' closed / '+(j.open_trades_count||0)+' open';
    const wrEl=document.getElementById('j-winrate');
    // API returns win_rate as 0-1 decimal (e.g. 1.0 = 100%)
    const wrVal=parseFloat(j.win_rate||0)*100;
    const bsWrEl=document.getElementById('bs-winrate');
    if(bsWrEl){bsWrEl.textContent=wrVal.toFixed(1)+'%';bsWrEl.className='stat-value '+(wrVal>=60?'green':wrVal>=40?'yellow':'red');}
    wrEl.textContent=wrVal.toFixed(1)+'%';
    wrEl.className='stat-value '+(wrVal>=60?'green':wrVal>=40?'yellow':'red');
    const npEl=document.getElementById('j-netpnl');
    npEl.textContent=(np>=0?'+':'-')+rupee(Math.abs(np));
    npEl.className='stat-value '+(np>=0?'green':'red');
    const pfVal=parseFloat(j.profit_factor||0);
    const noLosses=(j.avg_loss||0)===0&&(j.avg_win||0)>0&&(j.total_trades||0)>0;
    document.getElementById('j-pf').textContent=noLosses?'∞':pfVal.toFixed(2);
    document.getElementById('j-avgwin').textContent=rupee(j.avg_win||0);
    document.getElementById('j-avgloss').textContent=rupee(j.avg_loss||0);
    document.getElementById('j-avgscore').textContent=(j.avg_score||0).toFixed(1)+'/100';
    const hs=j.holding_stats||{};
    const fmtHold=(v,h)=>{v=parseFloat(v)||0;h=parseFloat(h)||0;if(v>=1)return v.toFixed(1)+'d';if(h>0)return h.toFixed(1)+'h';return v+'d';};
    document.getElementById('j-avghold').textContent=fmtHold(j.avg_hold_days, (j.avg_hold_days||0)*24);
    document.getElementById('j-longest').textContent=fmtHold(hs.longest_trade_days, hs.longest_trade_hours);
    document.getElementById('j-shortest').textContent=fmtHold(hs.shortest_trade_days, hs.shortest_trade_hours);
    document.getElementById('j-avgwinhold').textContent=fmtHold(hs.avg_winner_hold_days, hs.avg_winner_hold_days*24);
    document.getElementById('j-avglosehold').textContent=fmtHold(hs.avg_loser_hold_days, hs.avg_loser_hold_days*24);

    // Cumulative P&L chart
    if(typeof Chart!=='undefined'){
    const cumData=j.cumulative_pnl||[];
    const cumCtx=document.getElementById('j-chart-cumulative');
    if(cumCtx){
      const cfg={type:'line',data:{
        labels:cumData.map(d=>d.date),
        datasets:[{label:'Net P&L',data:cumData.map(d=>d.cumulative_pnl),
          borderColor:'#3b82f6',backgroundColor:'#3b82f611',fill:true,tension:0.4,pointRadius:2}]
      },options:{scales:{x:{ticks:{color:'#4b5563',font:{size:9},maxRotation:45}},y:{ticks:{color:'#4b5563',callback:v=>'₹'+v.toLocaleString('en-IN')}}},plugins:{legend:{display:false}},maintainAspectRatio:false}};
      if(jChartCumulative){jChartCumulative.data=cfg.data;jChartCumulative.update();}else{jChartCumulative=new Chart(cumCtx,cfg);}
    }

    const sb=j.by_score_bucket||{};
    const sbLabels=Object.keys(sb);
    const sbWR=sbLabels.map(k=>sb[k].win_rate||0);
    const sbPnl=sbLabels.map(k=>sb[k].net_pnl||0);
    const sbCtx=document.getElementById('j-chart-scorebucket');
    if(sbCtx&&sbLabels.length){
      const cfg={type:'bar',data:{labels:sbLabels,datasets:[{label:'Win Rate %',data:sbWR,backgroundColor:sbWR.map(v=>v>=60?'#16a34a88':'#dc262688'),borderRadius:4,yAxisID:'y'},{label:'Net P&L',data:sbPnl,type:'line',borderColor:'#60a5fa',pointRadius:3,yAxisID:'y2'}]},options:{scales:{x:{ticks:{color:'#4b5563'}},y:{ticks:{color:'#4b5563',callback:v=>v+'%'},max:100,min:0,title:{display:true,text:'Win Rate %',color:'#4b5563'}},y2:{position:'right',ticks:{color:'#60a5fa',callback:v=>'₹'+v},grid:{drawOnChartArea:false}}},plugins:{legend:{labels:{color:'#9ca3af',font:{size:11}}}},maintainAspectRatio:false}};
      if(jChartScoreBucket){jChartScoreBucket.data=cfg.data;jChartScoreBucket.update();}else{jChartScoreBucket=new Chart(sbCtx,cfg);}
    }

    const sec=j.by_sector||{};
    const secL=Object.keys(sec);
    const secPnl=secL.map(k=>sec[k].net_pnl||0);
    const secCtx2=document.getElementById('j-chart-sector');
    if(secCtx2&&secL.length){
      const cfg={type:'bar',data:{labels:secL,datasets:[{label:'Net P&L',data:secPnl,backgroundColor:secPnl.map(v=>v>=0?'#16a34a88':'#dc262688'),borderRadius:4}]},options:{scales:{x:{ticks:{color:'#4b5563'}},y:{ticks:{color:'#4b5563',callback:v=>'₹'+v}}},plugins:{legend:{display:false}},maintainAspectRatio:false,indexAxis:'y'}};
      if(jChartSector){jChartSector.data=cfg.data;jChartSector.update();}else{jChartSector=new Chart(secCtx2,cfg);}
    }

    const ex=j.by_exit_reason||{};
    const exL=Object.keys(ex);
    const exPnl=exL.map(k=>ex[k].net_pnl||0);
    const exCtx=document.getElementById('j-chart-exit');
    if(exCtx&&exL.length){
      const cfg={type:'bar',data:{labels:exL,datasets:[{label:'Net P&L',data:exPnl,backgroundColor:exPnl.map(v=>v>=0?'#16a34a88':'#dc262688'),borderRadius:4}]},options:{scales:{x:{ticks:{color:'#4b5563'}},y:{ticks:{color:'#4b5563',callback:v=>'₹'+v}}},plugins:{legend:{display:false}},maintainAspectRatio:false}};
      if(jChartExit){jChartExit.data=cfg.data;jChartExit.update();}else{jChartExit=new Chart(exCtx,cfg);}
    }

    const dow=j.by_day_of_week||{};
    const DOW_ORDER=['Monday','Tuesday','Wednesday','Thursday','Friday'];
    const dowL=DOW_ORDER.filter(d=>dow[d]);
    const dowPnl=dowL.map(d=>dow[d].net_pnl||0);
    const dowCtx=document.getElementById('j-chart-dow');
    if(dowCtx&&dowL.length){
      const cfg={type:'bar',data:{labels:dowL.map(d=>fmtDayShort(d)),datasets:[{label:'Net P&L',data:dowPnl,backgroundColor:dowPnl.map(v=>v>=0?'#16a34a88':'#dc262688'),borderRadius:4}]},options:{scales:{x:{ticks:{color:'#4b5563'}},y:{ticks:{color:'#4b5563',callback:v=>'₹'+v}}},plugins:{legend:{display:false}},maintainAspectRatio:false}};
      if(jChartDow){jChartDow.data=cfg.data;jChartDow.update();}else{jChartDow=new Chart(dowCtx,cfg);}
    }

    const reg=j.by_regime||{};
    const regL=Object.keys(reg);
    const regWR=regL.map(k=>reg[k].win_rate||0);
    const regCtx=document.getElementById('j-chart-regime');
    if(regCtx&&regL.length){
      const cfg={type:'bar',data:{labels:regL,datasets:[{label:'Win Rate %',data:regWR,backgroundColor:regWR.map(v=>v>=60?'#16a34a88':'#ca8a0488'),borderRadius:4}]},options:{scales:{x:{ticks:{color:'#4b5563'}},y:{ticks:{color:'#4b5563',callback:v=>v+'%'},max:100,min:0}},plugins:{legend:{display:false}},maintainAspectRatio:false}};
      if(jChartRegime){jChartRegime.data=cfg.data;jChartRegime.update();}else{jChartRegime=new Chart(regCtx,cfg);}
    }
    } // end Chart guard

    // Trade log table — merge open + closed BUY rows, newest first
    const closedTrades=j.recent_trades||[];
    const openTrades=j.open_trade_log||[];
    openTrades.forEach(t=>{t._is_open=true;});
    const allTrades=[...openTrades,...closedTrades];
    const jTbl=document.getElementById('j-trade-log');
    if(allTrades.length){
      jTbl.innerHTML=allTrades.map((t,i)=>{
        const isOpen=t._is_open||t.status==='OPEN';
        const pnl=parseFloat(t.net_pnl||0);
        const sc=parseFloat(t.trade_score||0);
        const sentiment=(t.sentiment||'').toUpperCase();
        const sentCol=sentiment==='POSITIVE'?'#22c55e':sentiment==='NEGATIVE'?'#ef4444':'#9ca3af';
        const regCol=t.market_regime==='BULL'?'#22c55e':t.market_regime==='BEAR'?'#ef4444':'#eab308';
        const reentryBadge=t.is_reentry?'<span style="background:#7c3aed;color:#fff;font-size:9px;padding:1px 5px;border-radius:6px;margin-left:4px">RE-ENTRY</span>':'';
        const statusCell=isOpen
          ? '<span style="background:#1d4ed8;color:#fff;font-size:10px;padding:2px 6px;border-radius:6px;font-weight:700">📂 OPEN</span>'
          : '<span style="background:#166534;color:#fff;font-size:10px;padding:2px 6px;border-radius:6px">✅ CLOSED</span>';
        const pnlCell=isOpen
          ? '<td style="color:#60a5fa;font-weight:700">holding</td>'
          : `<td class="${pnlClass(pnl)}" style="font-weight:700">${pnlStr(pnl)}</td>`;
        const hold=fmtHold(isOpen ? ((new Date() - new Date(t.timestamp||t.date||0))/86400000) : (t.holding_days||0), isOpen?0:(t.holding_hours||0));
        const openHold=isOpen ? hold + ' ongoing' : hold;
        const reasonMap={'kite_order':'Manual Sell','Broker SELL':'Broker SELL','stop_loss':'🛑 SL Hit','target':'🎯 Target Hit','max_hold':'⏰ Max Hold','rsi_overbought':'📈 RSI>80','signal_reversal':'🔄 Reversal','trailing_stop':'🔔 Trail SL'};
        const exitLabel=isOpen?'<span style="color:#4b5563">holding</span>':(reasonMap[t.exit_reason]||t.exit_reason||'—');
        const dateCell=isOpen?(t.date||'—'):(t.exit_date||t.date||'—');
        const detailId='j-detail-'+i;
        const buyScore=t.trade_score?t.trade_score.toFixed(1):'—';
        const exitScore=t.exit_score!=null?t.exit_score:'—';
        const gross=parseFloat(t.gross_pnl||0);
        const charges=parseFloat(t.charges||0);
        const net=parseFloat(t.net_pnl||0);
        const detail=`<div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;padding:12px;background:#111827;border-radius:8px;margin:8px 0">
          <div>
            <div style="font-weight:700;color:#22c55e;margin-bottom:8px;font-size:13px">🛒 Buy Snapshot</div>
            <div style="font-size:12px;color:#9ca3af;line-height:1.6">
              <div><b>Bought because:</b> ${t.buy_reason||'—'}</div>
              <div><b>Score:</b> ${buyScore}</div>
              <div><b>RSI:</b> ${t.rsi!=null?t.rsi.toFixed(1):'—'}</div>
              <div><b>Volume:</b> ${t.volume_ratio!=null?t.volume_ratio+'x':'—'}</div>
              <div><b>Market:</b> ${t.market_regime||'—'}</div>
              <div><b>Sector:</b> ${t.sector||'—'}</div>
              <div><b>AI Confidence:</b> ${t.confidence!=null?((t.confidence)*100).toFixed(0)+'%':'—'}</div>
            </div>
          </div>
          <div>
            <div style="font-weight:700;color:#ef4444;margin-bottom:8px;font-size:13px">💰 Sell Snapshot</div>
            <div style="font-size:12px;color:#9ca3af;line-height:1.6">
              ${isOpen?'<div style="color:#60a5fa">Position still open</div>':'<div><b>Sold because:</b> '+(t.exit_reason||'—')+'</div><div><b>Exit Score:</b> '+exitScore+'</div>'}
              <div><b>Holding:</b> ${openHold}</div>
              <div><b>Gross P&amp;L:</b> <span class="${pnlClass(gross)}">${pnlStr(gross)}</span></div>
              <div><b>Brokerage:</b> ${rupee(charges)}</div>
              <div><b>Net P&amp;L:</b> <span class="${pnlClass(net)}">${pnlStr(net)}</span></div>
            </div>
          </div>
        </div>`;
        return `<tr style="cursor:pointer" onclick="const d=document.getElementById('${detailId}'); const s=d.style; s.display=s.display==='none'?'table-row':'none'; this.style.background=s.display==='none'?'':'#1f2937'">
          <td style="color:#6b7280;white-space:nowrap;padding:8px">${dateCell}</td>
          <td style="font-weight:700;color:#f9fafb;padding:8px">${t.symbol}${reentryBadge}</td>
          <td style="padding:8px">${statusCell}</td>
          <td style="color:${scoreColor(sc)};font-weight:700;text-align:center;padding:8px">${buyScore}</td>
          <td style="color:${regCol};font-size:11px;text-align:center;padding:8px">${t.market_regime||'—'}</td>
          <td style="color:#9ca3af;font-size:12px;padding:8px">${t.sector||'—'}</td>
          <td style="padding:8px">${rupee(t.entry_price||0)}</td>
          <td style="padding:8px">${isOpen?'<span style="color:#4b5563">—</span>':(t.exit_price?rupee(t.exit_price):'—')}</td>
          <td style="text-align:center;padding:8px">${openHold}</td>
          <td style="color:${sentCol};font-size:11px;text-align:center;padding:8px">${sentiment||'—'}</td>
          <td style="text-align:center;font-size:12px;padding:8px">${t.rsi!=null?t.rsi.toFixed(0):'—'}</td>
          <td style="font-size:11px;padding:8px">${t.trend||'—'}</td>
          <td style="font-size:11px;color:#9ca3af;max-width:160px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;padding:8px" title="${t.exit_reason||t.buy_reason||''}">${exitLabel}</td>
          ${pnlCell.replace('<td', '<td style="padding:8px"')}
        </tr><tr id="${detailId}" style="display:none;background:#0f172a"><td colspan="14" style="padding:12px 20px;border-bottom:1px solid #1f2937">${detail}</td></tr>`;
      }).join('');
    }else{
      jTbl.innerHTML='<tr><td colspan="14" style="text-align:center;color:#4b5563;padding:20px">No trades yet — journal will populate after the first order executes</td></tr>';
    }
  }catch(e){console.error('Journal error:',e);}
}

// ─── Skipped Opportunities ──────────────────────────────────────────────────────
async function loadSkippedOpportunities(){
  try{
    const response = await fetch('/api/skipped-opportunities').then(r => r.json());
    const data = response.skipped_opportunities || [];
    const summary = response.summary || {};
    
    // Update KPIs
    document.getElementById('s-total-evaluated').textContent = summary.total_evaluated || 0;
    document.getElementById('s-skipped').textContent = summary.skipped || 0;
    document.getElementById('s-executed').textContent = summary.executed || 0;
    document.getElementById('s-skip-rate').textContent = summary.skip_rate ? (summary.skip_rate * 100).toFixed(1) + '%' : '0%';
    
    // Update rejection reasons summary
    const reasonsEl = document.getElementById('s-rejection-reasons');
    if (summary.rejection_reasons && Object.keys(summary.rejection_reasons).length > 0) {
      reasonsEl.innerHTML = Object.entries(summary.rejection_reasons)
        .map(([reason, count]) => `<div style="display:flex;justify-content:space-between;margin-bottom:4px"><span>${reason}</span><span style="color:#f9fafb;font-weight:600">${count}</span></div>`)
        .join('');
    } else {
      reasonsEl.innerHTML = '<div style="color:#4b5563">No rejection reasons recorded</div>';
    }
    
    // Update skipped opportunities table
    const tableEl = document.getElementById('s-skipped-table');
    if (data.length > 0) {
      tableEl.innerHTML = data.map(item => {
        const scoreColor = item.overall_score >= 80 ? '#16a34a' : item.overall_score >= 60 ? '#f59e0b' : '#dc2626';
        const confidenceColor = item.confidence >= 0.8 ? '#16a34a' : item.confidence >= 0.6 ? '#f59e0b' : '#dc2626';
        const rrColor = item.risk_reward_ratio >= 2 ? '#16a34a' : item.risk_reward_ratio >= 1.5 ? '#f59e0b' : '#dc2626';
        
        return `<tr style="border-bottom:1px solid #1f2937;cursor:pointer" onclick="showDetailedDecision('${item.symbol}')">
          <td style="padding:8px;font-weight:700;color:#f9fafb">${item.symbol}</td>
          <td style="padding:8px;color:${scoreColor}">${item.overall_score.toFixed(1)}</td>
          <td style="padding:8px;color:${confidenceColor}">${(item.confidence * 100).toFixed(0)}%</td>
          <td style="padding:8px;color:${rrColor}">${item.risk_reward_ratio.toFixed(2)}</td>
          <td style="padding:8px;color:#9ca3af;font-size:11px">${item.rejection_reason}</td>
          <td style="padding:8px;color:#60a5fa;font-family:monospace">₹${item.entry_price.toFixed(2)}</td>
          <td style="padding:8px;color:#9ca3af">${item.sector}</td>
          <td style="padding:8px;color:#4b5563;font-size:11px">${new Date(item.timestamp).toLocaleTimeString()}</td>
        </tr>`;
      }).join('');
    } else {
      tableEl.innerHTML = '<tr><td colspan="8" style="text-align:center;color:#4b5563;padding:20px">No skipped opportunities today</td></tr>';
    }
    
    // Store data for detailed view
    window.skippedData = data;
    
  }catch(e){
    console.error('Skipped opportunities error:',e);
    document.getElementById('s-skipped-table').innerHTML = '<tr><td colspan="8" style="text-align:center;color:#dc2626;padding:20px">Error loading data</td></tr>';
  }
}

function showDetailedDecision(symbol){
  const item = window.skippedData.find(d => d.symbol === symbol);
  if (!item) return;
  
  const detailsEl = document.getElementById('s-detailed-decisions');
  detailsEl.innerHTML = `
    <div style="background:#1f2937;padding:16px;border-radius:8px;margin-bottom:16px">
      <h4 style="color:#f9fafb;margin-bottom:12px">${symbol} - Detailed Analysis</h4>
      <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px">
        <div><strong style="color:#9ca3af">Overall Score:</strong> <span style="color:#60a5fa">${item.overall_score.toFixed(1)}</span></div>
        <div><strong style="color:#9ca3af">Confidence:</strong> <span style="color:#60a5fa">${(item.confidence * 100).toFixed(0)}%</span></div>
        <div><strong style="color:#9ca3af">Technical Score:</strong> <span style="color:#60a5fa">${item.technical_score.toFixed(1)}</span></div>
        <div><strong style="color:#9ca3af">News Sentiment:</strong> <span style="color:#60a5fa">${item.news_sentiment_score.toFixed(1)}</span></div>
        <div><strong style="color:#9ca3af">Sector Strength:</strong> <span style="color:#60a5fa">${item.sector_strength.toFixed(1)}</span></div>
        <div><strong style="color:#9ca3af">Market Regime:</strong> <span style="color:#60a5fa">${item.market_regime}</span></div>
        <div><strong style="color:#9ca3af">Risk/Reward:</strong> <span style="color:#60a5fa">${item.risk_reward_ratio.toFixed(2)}</span></div>
        <div><strong style="color:#9ca3af">Position Size:</strong> <span style="color:#60a5fa">${item.position_size_calculated}</span></div>
      </div>
    </div>
    
    <div style="background:#1f2937;padding:16px;border-radius:8px;margin-bottom:16px">
      <h4 style="color:#f9fafb;margin-bottom:12px">Risk Management</h4>
      <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px">
        <div><strong style="color:#9ca3af">Available Cash:</strong> <span style="color:#60a5fa">₹${item.available_cash.toFixed(0)}</span></div>
        <div><strong style="color:#9ca3af">Portfolio Exposure:</strong> <span style="color:#60a5fa">${(item.portfolio_exposure * 100).toFixed(1)}%</span></div>
        <div><strong style="color:#9ca3af">Open Positions:</strong> <span style="color:#60a5fa">${item.current_open_positions.length}</span></div>
        <div><strong style="color:#9ca3af">Holdings:</strong> <span style="color:#60a5fa">${item.existing_holdings.length}</span></div>
        <div><strong style="color:#9ca3af">Cooldown Status:</strong> <span style="color:#60a5fa">${item.cooldown_status ? 'Active' : 'Inactive'}</span></div>
        <div><strong style="color:#9ca3af">Max Position Size:</strong> <span style="color:#60a5fa">₹${item.max_position_size.toFixed(0)}</span></div>
      </div>
    </div>
    
    <div style="background:#1f2937;padding:16px;border-radius:8px">
      <h4 style="color:#f9fafb;margin-bottom:12px">Trade Parameters</h4>
      <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px">
        <div><strong style="color:#9ca3af">Entry Price:</strong> <span style="color:#60a5fa">₹${item.entry_price.toFixed(2)}</span></div>
        <div><strong style="color:#9ca3af">Stop Loss:</strong> <span style="color:#60a5fa">₹${item.stop_loss.toFixed(2)}</span></div>
        <div><strong style="color:#9ca3af">Target:</strong> <span style="color:#60a5fa">₹${item.target.toFixed(2)}</span></div>
        <div><strong style="color:#9ca3af">Rejection Reason:</strong> <span style="color:#dc2626">${item.rejection_reason}</span></div>
      </div>
    </div>
  `;
}

// ─── Market Intelligence Loader ──────────────────────────────────────────────
async function loadMarketIntelligence(){
  try{
    let d=window._lastData;
    const cacheAge=d&&d.lastUpdated?Date.now()-new Date(d.lastUpdated).getTime():Infinity;
    if(!d||cacheAge>30000) d=await fetch('/api/data',{cache:'no-store'}).then(r=>r.json());
    const miScore=d.market_intelligence_score;
    const scoreEl=document.getElementById('mi-score');
    if(scoreEl){
      if(miScore!=null){
        scoreEl.textContent=parseFloat(miScore).toFixed(1);
        scoreEl.style.color=(miScore>=70?'#4ade80':miScore>=45?'#facc15':'#f87171');
      }else{
        scoreEl.textContent='Awaiting data';
      }
    }

    const rows=[];
    const val=(v)=>v!=null&&v!==''&&v!=='—'?v:'Awaiting data';
    const add=(label, value, colorClass='')=>{
      rows.push(`<div class="card-sm"><div class="stat-label">${label}</div><div class="stat-value-sm ${colorClass}">${value}</div></div>`);
    };
    add('Technical','Market closed');
    add('Market Breadth',val(d.market_breadth&&d.market_breadth.breadth_score));
    add('Sector Momentum',val(d.sector_rotation&&d.sector_rotation.momentum_score));
    add('India VIX',val(d.vix_risk&&d.vix_risk.vix));
    add('FII/DII Net',val(d.fii_dii&&d.fii_dii.net_flow));
    add('Options PCR',val(d.options_intelligence&&d.options_intelligence.pcr));
    add('Global Sentiment',val(d.global_markets&&d.global_markets.sentiment_score));
    add('Event Risk',val(d.economic_event_risk&&d.economic_event_risk.reason));
    document.getElementById('mi-grid').innerHTML=rows.join('');
  }catch(e){console.error('Market Intelligence load error:',e);}
}

// ─── Portfolio Optimizer Loader ───────────────────────────────────────────────
async function loadPortfolioOptimizer(){
  try{
    const r=await fetch('/api/portfolio/optimizer');
    const d=await r.json();
    const po=d.portfolio_optimizer||{};
    const cm=d.correlation_matrix||{};
    const reb=d.rebalance_suggestions||[];

    const divEl=document.getElementById('po-div');
    divEl.textContent=po.diversification_score!=null?po.diversification_score.toFixed(0):'—';
    divEl.style.color=(po.diversification_score>=70?'#4ade80':po.diversification_score>=40?'#facc15':'#f87171');

    const rows=[];
    const add=(label, value, colorClass='')=>{
      rows.push(`<div class="card-sm"><div class="stat-label">${label}</div><div class="stat-value-sm ${colorClass}">${value}</div></div>`);
    };
    add('Diversification',po.diversification_score!=null?po.diversification_score.toFixed(1):'—');
    add('Capital Used',rupee(po.capital_used));
    add('Cash Remaining',rupee(po.cash_remaining));
    add('Max Deployable',rupee(po.max_deployable));
    add('Portfolio Beta',po.portfolio_beta!=null?po.portfolio_beta.toFixed(2):'—');
    add('Volatility',po.portfolio_volatility!=null?po.portfolio_volatility.toFixed(2)+'%':'—');
    add('Capital Limit',po.capital_limit_pct!=null?(po.capital_limit_pct*100).toFixed(0)+'%':'—');
    add('Open Positions',po.open_positions!=null?po.open_positions:'—');
    document.getElementById('po-grid').innerHTML=rows.join('');

    const se=po.sector_exposure||{};
    const sectorHtml=Object.entries(se).map(([s,p])=>`<div style="margin:2px 0"><span style="color:#9ca3af;width:100px;display:inline-block">${s}</span><span style="color:#f9fafb">${p.toFixed(1)}%</span></div>`).join('');
    document.getElementById('po-allocation').innerHTML=sectorHtml||'No data';

    const syms=cm.symbols||[];
    const matrix=cm.matrix||{};
    if(syms.length && Object.keys(matrix).length){
      let html='<table style="width:100%;border-collapse:collapse"><thead><tr style="background:#1f2937"><th style="padding:6px;text-align:left"></th>';
      syms.forEach(s=>{html+=`<th style="padding:6px;text-align:left;color:#f9fafb;font-size:11px">${s}</th>`;});
      html+='</tr></thead><tbody>';
      syms.forEach(s1=>{
        html+=`<tr><td style="padding:6px;color:#f9fafb;font-size:11px;border-bottom:1px solid #374151">${s1}</td>`;
        syms.forEach(s2=>{
          const v=parseFloat((matrix[s1]||{})[s2]||0);
          const c=Math.abs(v)>=0.8?(v>0?'#f87171':'#facc15'):'#9ca3af';
          html+=`<td style="padding:6px;color:${c};font-size:11px;border-bottom:1px solid #374151">${v.toFixed(2)}</td>`;
        });
        html+='</tr>';
      });
      html+='</tbody></table>';
      document.getElementById('po-corr').innerHTML=html;
    }else{
      document.getElementById('po-corr').innerHTML='No correlation data';
    }
  }catch(e){console.error('Portfolio optimizer load error:',e);}
}

// ─── AI Explainability ───────────────────────────────────────────────────────
async function loadExplainability(){
  try{
    const r=await fetch('/api/explain');
    const d=await r.json();
    const actions=d.actions||[];
    const tbody=document.getElementById('explain-table');
    if(actions.length===0){
      tbody.innerHTML='<tr><td colspan="11" style="text-align:center;color:#4b5563;padding:20px">No decisions recorded yet.</td></tr>';
      return;
    }
    const fmtTime=(ts)=>{try{return new Date(ts).toLocaleString('en-IN',{hour:'2-digit',minute:'2-digit'})}catch(e){return ts||'—';}};
    tbody.innerHTML=actions.map(a=>{
      const ts=fmtTime(a.timestamp);
      const sc=parseFloat(a.score||0).toFixed(1);
      const confPct=(parseFloat(a.confidence||0)*100).toFixed(1);
      const pnl=a.net_pnl!==undefined?parseFloat(a.net_pnl).toFixed(2):'—';
      const mis=(a.score_components&&a.score_components.market_intelligence_score!=null)?parseFloat(a.score_components.market_intelligence_score).toFixed(1):'—';
      const color=a.action==='BUY'?'green':(a.action||'').startsWith('SELL')?'red':'yellow';
      return `<tr>
        <td style="padding:8px;border-bottom:1px solid #374151">${ts}</td>
        <td style="padding:8px;border-bottom:1px solid #374151">${a.symbol||'—'}</td>
        <td style="padding:8px;border-bottom:1px solid #374151"><span class="stat-value-sm ${color}">${a.action||'—'}</span></td>
        <td style="padding:8px;border-bottom:1px solid #374151;max-width:250px;white-space:pre-wrap">${a.reason||'—'}</td>
        <td style="padding:8px;border-bottom:1px solid #374151">${sc}</td>
        <td style="padding:8px;border-bottom:1px solid #374151">${mis}</td>
        <td style="padding:8px;border-bottom:1px solid #374151">${confPct}%</td>
        <td style="padding:8px;border-bottom:1px solid #374151">${pnl}</td>
        <td style="padding:8px;border-bottom:1px solid #374151">${a.price!=null?a.price.toFixed(2):'—'}</td>
        <td style="padding:8px;border-bottom:1px solid #374151">${a.quantity!=null?a.quantity:'—'}</td>
        <td style="padding:8px;border-bottom:1px solid #374151">${a.sector||'—'}</td>
      </tr>`;
    }).join('');
  }catch(e){
    console.error('Explainability load error:',e);
    document.getElementById('explain-table').innerHTML='<tr><td colspan="11" style="text-align:center;color:#dc2626;padding:20px">Error loading explanations</td></tr>';
  }
}

// ─── IP Status ────────────────────────────────────────────────────────────────
function updateIpStatus(d){
  const cur       = d.current_ip   || 'unknown';
  const known     = d.known_ip     || 'unknown';
  const changed   = !!d.ip_changed;
  const ipv6      = d.current_ipv6 || 'Not available';
  const netType   = d.network_type || '—';
  const kiteRes   = d.kite_resolution || '—';
  const lastOrder = d.last_order_time || '—';
  const lastApi   = d.last_api_call  || '—';
  const latency   = d.api_latency_ms || 0;
  const status    = d.trading_status || '—';
  const statusCol = d.trading_status_color || '#94a3b8';

  // ── Tab button ────────────────────────────────────────────────────────────
  const tabBtn = document.getElementById('ip-tab-btn');
  if(tabBtn){
    tabBtn.style.background = changed ? '#f59e0b' : '';
    tabBtn.style.color      = changed ? '#000'    : '';
    tabBtn.textContent      = changed ? '⚠️ IP Changed!' : '🌐 IP Status';
  }

  // ── Banner ────────────────────────────────────────────────────────────────
  const banner     = document.getElementById('ip-banner');
  const bannerIcon = document.getElementById('ip-banner-icon');
  const bannerTitle= document.getElementById('ip-banner-title');
  const bannerSub  = document.getElementById('ip-banner-sub');
  if(banner){
    if(changed){
      banner.style.borderColor  = '#f59e0b';
      banner.style.background   = '#292119';
      bannerIcon.textContent    = '⚠️';
      bannerTitle.textContent   = 'IP Changed — Orders May Be Blocked!';
      bannerTitle.style.color   = '#f59e0b';
      bannerSub.textContent     = 'Your public IP changed. Update Kite whitelist immediately.';
    } else if(cur === 'unknown'){
      banner.style.borderColor  = '#ef4444';
      banner.style.background   = '#1a0f0f';
      bannerIcon.textContent    = '❌';
      bannerTitle.textContent   = 'Network Error — Cannot reach internet';
      bannerTitle.style.color   = '#ef4444';
      bannerSub.textContent     = 'Check your internet connection.';
    } else {
      banner.style.borderColor  = '#22c55e';
      banner.style.background   = '#0f2318';
      bannerIcon.textContent    = '✅';
      bannerTitle.textContent   = 'Network OK — Orders are working';
      bannerTitle.style.color   = '#22c55e';
      bannerSub.textContent     = 'Current IP is whitelisted in Kite. No action needed.';
    }
  }

  // ── Trading status badge ──────────────────────────────────────────────────
  const tsEl = document.getElementById('ip-trading-status');
  if(tsEl){ tsEl.textContent = status; tsEl.style.color = statusCol; }

  // ── IPv4 card ─────────────────────────────────────────────────────────────
  const elCur   = document.getElementById('ip-current');
  const elCurSt = document.getElementById('ip-current-status');
  if(elCur) elCur.textContent = cur;
  if(elCurSt){
    elCurSt.innerHTML = changed
      ? '<span style="color:#f59e0b;font-weight:600">⚠️ NOT in Kite whitelist</span>'
      : '<span style="color:#22c55e;font-weight:600">✅ Whitelisted (Verified)</span>';
  }
  const elVerified = document.getElementById('ip-verified-at');
  if(elVerified && lastApi && lastApi !== '—'){
    try{
      const verTs = new Date(lastApi.replace(' ','T'));
      const diffMin = Math.round((Date.now() - verTs.getTime()) / 60000);
      const relStr = diffMin < 1 ? 'just now'
        : diffMin === 1 ? '1 minute ago'
        : diffMin < 60 ? `${diffMin} minutes ago`
        : `${Math.round(diffMin/60)}h ago`;
      elVerified.textContent = `Verified ${relStr} (${fmtTime(lastApi)})`;
    }catch(_){ elVerified.textContent = `Verified: ${lastApi}`; }
  }

  // ── IPv6 card ─────────────────────────────────────────────────────────────
  const elV6 = document.getElementById('ip-v6');
  if(elV6) elV6.textContent = ipv6;

  // ── Kite resolution card ──────────────────────────────────────────────────
  const elKiteRes = document.getElementById('ip-kite-res');
  if(elKiteRes){
    elKiteRes.textContent  = kiteRes;
    elKiteRes.style.color  = kiteRes.includes('✅') ? '#22c55e' : '#f59e0b';
  }

  // ── Network card ──────────────────────────────────────────────────────────
  const elNet = document.getElementById('ip-network');
  const elLat = document.getElementById('ip-latency');
  if(elNet) elNet.textContent = netType;
  if(elLat) elLat.textContent = `Kite latency: ${latency ? latency.toFixed(0)+'ms' : '—'}`;

  // ── Last order card ───────────────────────────────────────────────────────
  const elOrder = document.getElementById('ip-last-order');
  if(elOrder) elOrder.textContent = lastOrder;

  // ── Last API call card ────────────────────────────────────────────────────
  const elApi = document.getElementById('ip-last-api');
  if(elApi) elApi.textContent = lastApi;

  // ── Kite Authentication Status card ───────────────────────────────────────
  const kiteOk     = d.kite_ok !== false;   // true = token accepted by Zerodha
  const tokenExpiry= d.token_expiry || '';  // e.g. "2026-07-15T14:12"
  const authCard   = document.getElementById('auth-status-card');
  const authIcon   = document.getElementById('auth-status-icon');
  const authText   = document.getElementById('auth-status-text');
  const authSub    = document.getElementById('auth-status-sub');
  const authExpiry = document.getElementById('auth-token-expiry');
  const authTtl    = document.getElementById('auth-token-ttl');
  const authBtn    = document.getElementById('auth-reauth-btn');

  if(authCard){
    // Compute time-to-expiry
    let ttlStr = '—', expiryDisp = '—';
    if(tokenExpiry){
      try{
        const exp  = new Date(tokenExpiry.replace(' ','T'));
        const diff = Math.round((exp.getTime() - Date.now()) / 60000);  // minutes
        expiryDisp = exp.toLocaleString('en-IN',{day:'2-digit',month:'short',hour:'2-digit',minute:'2-digit',hour12:true});
        if(diff <= 0) ttlStr = 'Expired';
        else if(diff < 60) ttlStr = `Expires in ${diff} min`;
        else ttlStr = `Expires in ${Math.round(diff/60)}h ${diff%60}m`;
      }catch(_){}
    }
    authExpiry.textContent = expiryDisp;
    authTtl.textContent    = ttlStr;

    if(kiteOk){
      // ── Authenticated ────────────────────────────────────────────────────
      authCard.style.borderColor   = '#22c55e';
      authCard.style.background    = '#0f2318';
      authIcon.textContent         = '🔓';
      authText.textContent         = 'Authentication Successful';
      authText.style.color         = '#22c55e';
      authSub.textContent          = 'Kite token is valid · Swing trading is active';
      authSub.style.color          = '#4ade80';
      authBtn.style.display        = 'none';
    } else {
      // ── Authentication Required ───────────────────────────────────────────
      authCard.style.borderColor   = '#ef4444';
      authCard.style.background    = '#1a0f0f';
      authIcon.textContent         = '🔒';
      authText.textContent         = 'Authentication Required';
      authText.style.color         = '#ef4444';
      authSub.textContent          = 'Token rejected by Zerodha · No trades can execute · Run python get_kite_token.py';
      authSub.style.color          = '#f87171';
      authBtn.style.display        = 'inline-block';
    }
  }

  // ── Action box ────────────────────────────────────────────────────────────
  const actionBox = document.getElementById('ip-action-box');
  const actionOld = document.getElementById('ip-action-old');
  const actionNew = document.getElementById('ip-action-new');
  if(actionBox){
    actionBox.style.display = changed ? 'block' : 'none';
    if(actionOld) actionOld.textContent = known;
    if(actionNew) actionNew.textContent = cur;
  }

  // ── History table ─────────────────────────────────────────────────────────
  const hist   = d.ip_history || [];
  const histEl = document.getElementById('ip-history');
  if(histEl){
    if(!hist.length){
      histEl.innerHTML = '<div style="color:#4b5563">No history yet</div>';
    } else {
      histEl.innerHTML =
        '<table style="width:100%;border-collapse:collapse">'
        +'<tr style="color:#64748b;font-size:11px;text-transform:uppercase">'
        +'<th style="text-align:left;padding:5px 8px">IP Address</th>'
        +'<th style="text-align:left;padding:5px 8px">Detected At</th>'
        +'<th style="text-align:left;padding:5px 8px">Network</th>'
        +'<th style="text-align:left;padding:5px 8px">Event</th>'
        +'</tr>'
        + hist.map(h=>`<tr style="border-top:1px solid #1e293b">
            <td style="padding:6px 8px;font-family:monospace;color:#f1f5f9">${h.ip}</td>
            <td style="padding:6px 8px;color:#94a3b8">${h.detected_at||'—'}</td>
            <td style="padding:6px 8px;color:#60a5fa">${h.network||'—'}</td>
            <td style="padding:6px 8px">${h.changed
              ? '<span style="color:#f59e0b">⚠️ Changed</span>'
              : '<span style="color:#22c55e">✅ Same</span>'}</td>
          </tr>`).join('')
        +'</table>';
    }
  }

  // ── Global top alert bar ──────────────────────────────────────────────────
  let alertBar = document.getElementById('ip-alert-bar');
  if(changed){
    if(!alertBar){
      alertBar = document.createElement('div');
      alertBar.id = 'ip-alert-bar';
      alertBar.style.cssText = 'position:fixed;top:0;left:0;right:0;z-index:9999;background:#f59e0b;color:#000;text-align:center;padding:10px 16px;font-size:14px;font-weight:700;cursor:pointer;';
      alertBar.onclick = ()=>{ switchTab('ipstatus', document.getElementById('ip-tab-btn')); alertBar.style.display='none'; };
      document.body.prepend(alertBar);
    }
    alertBar.style.display = 'block';
    alertBar.textContent = `⚠️ IP CHANGED: ${known} → ${cur} — Click here to update Kite whitelist or orders will FAIL`;
  } else {
    if(alertBar) alertBar.style.display = 'none';
  }
}

async function startKiteAuth(btn){
  const KITE_LOGIN_URL = 'https://kite.trade/connect/login?api_key=veq6w4lv31v27ogd&v=3';
  let authWindow = null;
  try{
    // Open Kite login synchronously so the popup is not blocked by the browser.
    authWindow = window.open(KITE_LOGIN_URL, '_blank');
    if(!authWindow){
      alert('Popup blocked. Please allow popups for this dashboard and try again.');
      return;
    }
    if(btn) { btn.style.opacity = '0.6'; btn.style.pointerEvents = 'none'; }
    // Start the local token receiver server in the background.
    // By the time the user finishes logging in, localhost:8080 will be ready.
    const res = await fetch('/api/start-token-server', {method: 'POST'}).then(r=>r.json());
    if(res.error && !res.already_running){
      console.error('Token server start warning:', res.error);
    } else if(res.started){
      console.log('Token server started on port 8080');
    } else if(res.already_running){
      console.log('Token server already running on port 8080');
    }
  }catch(e){
    console.error('Kite auth start error', e);
    alert('Failed to start Kite authentication. Please check the logs.');
  } finally {
    if(btn) { btn.style.opacity = ''; btn.style.pointerEvents = ''; }
  }
}

async function refreshIpStatus(){
  try{
    let d=window._lastData;
    const cacheAge=d&&d.lastUpdated?Date.now()-new Date(d.lastUpdated).getTime():Infinity;
    if(!d||cacheAge>30000) d=await fetch('/api/data',{cache:'no-store'}).then(r=>r.json());
    updateIpStatus(d);
  }catch(e){ console.error('IP refresh error',e); }
}

// ─── Backtest ──────────────────────────────────────────────────────────────────
let _btEquityChart = null;

function btQuick(syms){ document.getElementById('bt-symbols').value = syms; }

function _btBreakdownHtml(obj){
  if(!obj || !Object.keys(obj).length) return '<div style="color:#475569;font-size:12px">No data</div>';
  return Object.entries(obj).map(([k,v])=>`
    <div style="display:flex;justify-content:space-between;align-items:center;padding:5px 0;border-bottom:1px solid #1e293b;font-size:12px">
      <span style="color:#cbd5e1">${k}</span>
      <span>
        <span style="color:#94a3b8">${v.trades}t</span>
        <span style="margin:0 6px;color:${v.win_rate>=0.5?'#22c55e':'#ef4444'}">${(v.win_rate*100).toFixed(0)}%</span>
        <span style="color:${v.avg_pnl_pct>=0?'#22c55e':'#ef4444'};font-weight:600">${(v.avg_pnl_pct*100).toFixed(1)}%</span>
      </span>
    </div>`).join('');
}

async function runBacktest(){
  const rawSyms = document.getElementById('bt-symbols').value.trim();
  const years   = parseInt(document.getElementById('bt-years').value);
  const symbols = rawSyms ? rawSyms.split(',').map(s=>s.trim().toUpperCase()).filter(Boolean) : [];

  const btn  = document.getElementById('bt-run-btn');
  const prog = document.getElementById('bt-progress-wrap');
  const bar  = document.getElementById('bt-progress-bar');
  const msg  = document.getElementById('bt-progress-msg');
  const summ = document.getElementById('bt-summary');
  const errEl= document.getElementById('bt-error');

  btn.disabled = true; btn.textContent = '⏳ Running…';
  prog.style.display = 'block';
  summ.style.display = 'none';
  errEl.style.display = 'none';
  document.getElementById('bt-status-badge').textContent = 'Running…';
  bar.style.width = '5%';

  // Animate progress bar while waiting (server-side is synchronous)
  let fakePct = 5;
  const ticker = setInterval(()=>{
    fakePct = Math.min(fakePct + 1.5, 90);
    bar.style.width = fakePct + '%';
    if(fakePct < 40) msg.textContent = 'Fetching historical data from Kite…';
    else if(fakePct < 75) msg.textContent = 'Running signal simulation…';
    else msg.textContent = 'Computing metrics…';
  }, 800);

  try {
    const res = await fetch('/api/backtest', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({symbols, years})
    });
    const data = await res.json();
    clearInterval(ticker);
    bar.style.width = '100%';

    if(data.error){
      errEl.textContent = '❌ ' + data.error;
      errEl.style.display = 'block';
      document.getElementById('bt-status-badge').textContent = 'Error';
    } else {
      _btRenderResults(data);
      summ.style.display = 'block';
      document.getElementById('bt-status-badge').textContent = 'Complete ✅';
    }
  } catch(e){
    clearInterval(ticker);
    errEl.textContent = '❌ Network error: ' + e.message;
    errEl.style.display = 'block';
    document.getElementById('bt-status-badge').textContent = 'Error';
  } finally {
    btn.disabled = false; btn.textContent = '▶ Run Backtest';
    setTimeout(()=>{ prog.style.display='none'; bar.style.width='0%'; }, 1500);
  }
}

function _btRenderResults(data){
  const s = data.summary || {};
  const cagr    = (s.cagr||0)*100;
  const mdd     = (s.max_drawdown||0)*100;
  const wr      = (s.win_rate||0)*100;
  const pf      = s.profit_factor||0;
  const pnl     = s.total_pnl||0;

  // KPIs
  const cagrEl = document.getElementById('bt-cagr');
  cagrEl.textContent = cagr.toFixed(1)+'%';
  cagrEl.style.color = cagr>=0 ? '#22c55e' : '#ef4444';

  const wrEl = document.getElementById('bt-winrate');
  wrEl.textContent = wr.toFixed(1)+'%';
  wrEl.style.color = wr>=50 ? '#22c55e' : '#f59e0b';

  const mddEl = document.getElementById('bt-mdd');
  mddEl.textContent = mdd.toFixed(1)+'%';
  mddEl.style.color = mdd > -20 ? '#f59e0b' : '#ef4444';

  const pfEl = document.getElementById('bt-pf');
  pfEl.textContent = (pf===null||pf>=999) ? '∞' : pf.toFixed(2);
  pfEl.style.color = (pf===null||pf>=1.5) ? '#22c55e' : pf>=1 ? '#f59e0b' : '#ef4444';

  document.getElementById('bt-sharpe').textContent  = (s.sharpe_ratio||0).toFixed(2);
  document.getElementById('bt-sortino').textContent = (s.sortino_ratio||0).toFixed(2);
  document.getElementById('bt-hold').textContent    = (s.avg_hold_days||0).toFixed(1)+'d';
  document.getElementById('bt-trades').textContent  = `${s.win_trades||0}W / ${s.loss_trades||0}L`;

  document.getElementById('bt-initial').textContent = '₹'+(s.initial_capital||0).toLocaleString('en-IN');
  const finEl = document.getElementById('bt-final');
  finEl.textContent = '₹'+(s.final_capital||0).toLocaleString('en-IN');
  finEl.style.color = s.final_capital >= s.initial_capital ? '#22c55e' : '#ef4444';

  const pnlEl = document.getElementById('bt-pnl');
  pnlEl.textContent = (pnl>=0?'+':'')+' ₹'+Math.abs(pnl).toLocaleString('en-IN');
  pnlEl.style.color = pnl>=0 ? '#22c55e' : '#ef4444';

  document.getElementById('bt-period').textContent = `${s.start_date} → ${s.end_date} (${s.years}y)`;

  // Equity curve
  const eq = data.equity_curve || [];
  if(eq.length > 1 && typeof Chart!=='undefined'){
    const labels = eq.map(p=>p.date);
    const vals   = eq.map(p=>p.equity);
    const ctx    = document.getElementById('bt-equity-chart').getContext('2d');
    if(_btEquityChart) _btEquityChart.destroy();
    _btEquityChart = new Chart(ctx, {
      type:'line',
      data:{
        labels,
        datasets:[{
          label:'Portfolio Value (₹)',
          data: vals,
          borderColor: vals[vals.length-1] >= vals[0] ? '#22c55e' : '#ef4444',
          backgroundColor: vals[vals.length-1] >= vals[0]
            ? 'rgba(34,197,94,0.08)' : 'rgba(239,68,68,0.08)',
          fill:true, pointRadius:0, borderWidth:2, tension:0.3
        }]
      },
      options:{
        responsive:true,
        plugins:{legend:{display:false},tooltip:{callbacks:{label:c=>'₹'+c.parsed.y.toLocaleString('en-IN')}}},
        scales:{
          x:{ticks:{color:'#475569',maxTicksLimit:12},grid:{color:'#1e293b'}},
          y:{ticks:{color:'#475569',callback:v=>'₹'+v.toLocaleString('en-IN')},grid:{color:'#1e293b'}}
        }
      }
    });
  }

  // Breakdowns
  document.getElementById('bt-by-regime').innerHTML = _btBreakdownHtml(data.by_regime);
  document.getElementById('bt-by-conf').innerHTML   = _btBreakdownHtml(data.by_confidence);
  document.getElementById('bt-by-exit').innerHTML   = _btBreakdownHtml(data.by_exit_reason);

  // Trade log
  const tbody = document.getElementById('bt-trade-log');
  const trades = (data.trades||[]).slice(0,200);
  if(!trades.length){
    tbody.innerHTML = '<tr><td colspan="10" style="text-align:center;color:#475569;padding:16px">No completed trades</td></tr>';
  } else {
    tbody.innerHTML = trades.map(t=>{
      const p = (t.pnl_pct*100).toFixed(2);
      const col = t.pnl>=0 ? '#22c55e' : '#ef4444';
      const exitTag = {stop_loss:'🛑 SL', target:'🎯 Target', max_hold:'⏰ MaxHold', rsi_overbought:'📈 RSI>80', signal_reversal:'🔄 Reversal'}[t.exit_reason] || t.exit_reason;
      return `<tr style="border-top:1px solid #1e293b">
        <td style="padding:5px 8px;font-weight:600;color:#f1f5f9">${t.symbol}</td>
        <td style="padding:5px 8px;color:#94a3b8;font-size:11px">${fmtDateTime(t.entry_date,true)}</td>
        <td style="padding:5px 8px;color:#94a3b8;font-size:11px">${fmtDateTime(t.exit_date,true)}</td>
        <td style="padding:5px 8px;text-align:right;font-family:monospace">₹${t.entry_price.toLocaleString('en-IN')}</td>
        <td style="padding:5px 8px;text-align:right;font-family:monospace">₹${t.exit_price.toLocaleString('en-IN')}</td>
        <td style="padding:5px 8px;text-align:right;font-family:monospace;color:${col};font-weight:600">${t.pnl>=0?'+':''}₹${Math.abs(t.pnl).toFixed(0)}</td>
        <td style="padding:5px 8px;text-align:right;color:${col}">${t.pnl_pct>=0?'+':''}${p}%</td>
        <td style="padding:5px 8px;text-align:right;color:#64748b">${t.hold_days}</td>
        <td style="padding:5px 8px;color:#60a5fa;font-size:11px">${t.regime}</td>
        <td style="padding:5px 8px;font-size:11px;color:#94a3b8">${exitTag}</td>
      </tr>`;
    }).join('');
  }
}

load();
loadJournal();
loadHealthBadge();
switchTab('dashboard', document.querySelector('.tab-btn.active'));
setInterval(load,60000);
setInterval(loadJournal,120000);
setInterval(loadHealthBadge,60000);

// ─── Ask AI Chat ──────────────────────────────────────────────────────────────
function chipAsk(el){ document.getElementById('chat-input').value=el.textContent; sendChat(); }

async function sendChat(){
  const inp = document.getElementById('chat-input');
  const btn = document.getElementById('chat-send-btn');
  const msgs = document.getElementById('chat-msgs');
  const question = inp.value.trim();
  if(!question) return;

  // Show user bubble
  msgs.innerHTML += `<div class="msg-user">${escHtml(question)}</div>`;
  inp.value='';
  btn.disabled=true;
  document.getElementById('ai-status-dot').style.background='#eab308';

  // Show typing indicator
  const typingId='typing-'+Date.now();
  msgs.innerHTML += `<div class="msg-ai" id="${typingId}">
    <div class="ai-label">AI Assistant</div>
    <span class="typing-dot"></span><span class="typing-dot"></span><span class="typing-dot"></span>
  </div>`;
  msgs.scrollTop=msgs.scrollHeight;

  try{
    const res = await fetch('/api/ask', {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({question})
    });
    const data = await res.json();
    document.getElementById(typingId).remove();
    msgs.innerHTML += renderAIMessage(data);
  }catch(e){
    document.getElementById(typingId).remove();
    msgs.innerHTML += `<div class="msg-ai"><div class="ai-label">AI Assistant</div><span class="red">Error: could not reach server.</span></div>`;
  }
  btn.disabled=false;
  document.getElementById('ai-status-dot').style.background='#22c55e';
  msgs.scrollTop=msgs.scrollHeight;
}

function escHtml(s){ return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }

function renderAIMessage(d){
  if(d.error) return `<div class="msg-ai"><div class="ai-label">AI Assistant</div><span class="red">${escHtml(d.error)}</span></div>`;

  let body='<div class="ai-label">AI Assistant</div>';

  // Main answer text
  if(d.answer) body+=`<div style="margin-bottom:10px">${escHtml(d.answer)}</div>`;

  // Structured sections: action, score, regime, indicators, sentiment, exit, expected_return
  if(d.action){
    const cls = d.action==='BUY'?'ai-pill-green':d.action==='SELL'?'ai-pill-red':'ai-pill-yellow';
    body+=`<div class="ai-section"><span class="ai-pill ${cls}">${d.action}</span></div>`;
  }
  if(d.trade_score!=null){
    const col=d.trade_score>=80?'ai-pill-green':d.trade_score>=60?'ai-pill-yellow':'ai-pill-red';
    body+=`<div class="ai-section"><div class="ai-section-title">Trade Score</div><span class="ai-pill ${col}">${d.trade_score}/100</span></div>`;
  }
  if(d.regime){
    const col=d.regime==='BULL'?'ai-pill-green':d.regime==='BEAR'?'ai-pill-red':'ai-pill-yellow';
    body+=`<div class="ai-section"><div class="ai-section-title">Market Regime</div><span class="ai-pill ${col}">${d.regime}</span></div>`;
  }
  if(d.indicators && Object.keys(d.indicators).length){
    body+='<div class="ai-section"><div class="ai-section-title">Indicators</div>';
    for(const [k,v] of Object.entries(d.indicators)){
      const col=v.signal==='bullish'?'ai-pill-green':v.signal==='bearish'?'ai-pill-red':'ai-pill-gray';
      body+=`<span class="ai-pill ${col}" title="${escHtml(v.detail||'')}">${escHtml(k)}: ${escHtml(String(v.value))}</span>`;
    }
    body+='</div>';
  }
  if(d.sentiment){
    const col=d.sentiment==='POSITIVE'?'ai-pill-green':d.sentiment==='NEGATIVE'?'ai-pill-red':'ai-pill-gray';
    body+=`<div class="ai-section"><div class="ai-section-title">News Sentiment</div><span class="ai-pill ${col}">${d.sentiment}</span>`;
    if(d.sentiment_score!=null) body+=` <span class="ai-pill ai-pill-gray">Score: ${d.sentiment_score}</span>`;
    body+='</div>';
  }
  if(d.exit_reasons && d.exit_reasons.length){
    body+='<div class="ai-section"><div class="ai-section-title">Exit Triggers</div>';
    d.exit_reasons.forEach(r=>{ body+=`<span class="ai-pill ai-pill-red">${escHtml(r)}</span>`; });
    body+='</div>';
  }
  if(d.expected_return!=null){
    const col=d.expected_return>=0?'ai-pill-green':'ai-pill-red';
    body+=`<div class="ai-section"><div class="ai-section-title">Expected Return</div><span class="ai-pill ${col}">${d.expected_return>=0?'+':''}${d.expected_return}%</span></div>`;
  }
  if(d.bullets && d.bullets.length){
    body+='<ul style="margin-top:8px;padding-left:18px;list-style:disc">';
    d.bullets.forEach(b=>{ body+=`<li style="margin-bottom:4px;color:#d1d5db">${escHtml(b)}</li>`; });
    body+='</ul>';
  }
  return `<div class="msg-ai">${body}</div>`;
}

// ─── Backtesting Loader ───────────────────────────────────────────────────────
async function loadBacktesting(){
  try{
    const r=await fetch('/api/backtest/results');
    const d=await r.json();
    if(d.error){throw new Error(d.error);}
    const b=d.backtest||{};
    const wf=d.walk_forward||[];
    const mc=d.monte_carlo||{};

    const kpi=document.getElementById('bt-kpi');
    if(kpi){
      const add=(label, value, color='')=>`<div class="card-sm"><div class="stat-label">${label}</div><div class="stat-value-sm ${color}">${value}</div></div>`;
      kpi.innerHTML=[
        add('Total Return', pct(b.total_return_pct)),
        add('CAGR', pct(b.cagr_pct)),
        add('Max Drawdown', pct(b.max_drawdown_pct), 'red'),
        add('Win Rate', pct(b.win_rate_pct)),
        add('Sharpe', b.sharpe!=null?b.sharpe.toFixed(2):'—'),
        add('Sortino', b.sortino!=null?b.sortino.toFixed(2):'—'),
        add('Calmar', b.calmar!=null?b.calmar.toFixed(2):'—'),
        add('Profit Factor', b.profit_factor!=null?b.profit_factor.toFixed(2):'—'),
        add('Avg Win', rupee(b.avg_win)),
        add('Avg Loss', rupee(b.avg_loss), 'red'),
        add('Expectancy', rupee(b.expectancy)),
        add('Avg Holding', b.avg_holding_days!=null?b.avg_holding_days.toFixed(1)+'d':'—'),
      ].join('');
    }

    const fmtCurve=(data, key='equity')=>{
      if(!data||!data.length)return 'No data';
      let rows='<table style="width:100%;border-collapse:collapse"><thead><tr style="background:#1f2937"><th style="padding:4px;text-align:left;color:#f9fafb">Date</th><th style="padding:4px;text-align:left;color:#f9fafb">Value</th></tr></thead><tbody>';
      data.slice(-30).forEach(pt=>{
        const v=parseFloat(pt[key]||0).toFixed(2);
        rows+=`<tr><td style="padding:4px;color:#9ca3af;border-bottom:1px solid #374151;font-size:11px">${pt.date}</td><td style="padding:4px;color:#f9fafb;border-bottom:1px solid #374151;font-size:11px">${key==='equity'?rupee(v):pct(v)}</td></tr>`;
      });
      rows+='</tbody></table>';
      return rows;
    };
    const equity=document.getElementById('bt-equity-curve');
    if(equity)equity.innerHTML=fmtCurve(b.equity_curve,'equity');
    const dd=document.getElementById('bt-drawdown-curve');
    if(dd)dd.innerHTML=fmtCurve(b.drawdown_curve,'drawdown_pct');

    const mon=document.getElementById('bt-monthly');
    if(mon){
      const m=b.monthly_returns||{};
      const rows=Object.entries(m).map(([dt,v])=>`<div style="margin:2px 0"><span style="color:#9ca3af;width:100px;display:inline-block">${dt.split(' ')[0]}</span><span class="stat-value-sm ${v>=0?'green':'red'}">${pct(v)}</span></div>`).join('');
      mon.innerHTML=rows||'No data';
    }

    const wfd=document.getElementById('bt-walkforward');
    if(wfd){
      if(!wf.length){wfd.innerHTML='No data';}
      else{
        let rows='<table style="width:100%;border-collapse:collapse"><thead><tr style="background:#1f2937"><th style="padding:4px;text-align:left;color:#f9fafb">Fold</th><th style="padding:4px;text-align:right;color:#f9fafb">Return</th><th style="padding:4px;text-align:right;color:#f9fafb">Trades</th><th style="padding:4px;text-align:right;color:#f9fafb">Sharpe</th></tr></thead><tbody>';
        wf.forEach(f=>{
          const res=f.result||{};
          rows+=`<tr><td style="padding:4px;color:#9ca3af;border-bottom:1px solid #374151;font-size:11px">${f.name}</td><td style="padding:4px;color:#f9fafb;border-bottom:1px solid #374151;font-size:11px;text-align:right">${pct(res.total_return_pct)}</td><td style="padding:4px;color:#f9fafb;border-bottom:1px solid #374151;font-size:11px;text-align:right">${res.trades_count||0}</td><td style="padding:4px;color:#f9fafb;border-bottom:1px solid #374151;font-size:11px;text-align:right">${res.sharpe!=null?res.sharpe.toFixed(2):'—'}</td></tr>`;
        });
        rows+='</tbody></table>';
        wfd.innerHTML=rows;
      }
    }

    const mcEl=document.getElementById('bt-monte');
    if(mcEl){
      mcEl.innerHTML=[
        `<div style="margin:2px 0"><span style="color:#9ca3af;width:200px;display:inline-block">Simulations</span><span style="color:#f9fafb">${mc.n_simulations||0}</span></div>`,
        `<div style="margin:2px 0"><span style="color:#9ca3af;width:200px;display:inline-block">Probability of Profit</span><span class="stat-value-sm ${mc.probability_of_profit>=50?'green':'red'}">${pct(mc.probability_of_profit)}</span></div>`,
        `<div style="margin:2px 0"><span style="color:#9ca3af;width:200px;display:inline-block">Worst Drawdown (5th %ile)</span><span style="color:#f87171">${pct(mc.worst_drawdown_pct)}</span></div>`,
        `<div style="margin:2px 0"><span style="color:#9ca3af;width:200px;display:inline-block">Best Drawdown (95th %ile)</span><span style="color:#4ade80">${pct(mc.best_drawdown_pct)}</span></div>`,
        `<div style="margin:2px 0"><span style="color:#9ca3af;width:200px;display:inline-block">Mean Final P&L</span><span style="color:#f9fafb">${rupee(mc.mean_final_pnl)}</span></div>`,
        `<div style="margin:2px 0"><span style="color:#9ca3af;width:200px;display:inline-block">95% CI Low</span><span style="color:#f9fafb">${rupee(mc.ci_5_final_pnl)}</span></div>`,
        `<div style="margin:2px 0"><span style="color:#9ca3af;width:200px;display:inline-block">95% CI High</span><span style="color:#f9fafb">${rupee(mc.ci_95_final_pnl)}</span></div>`,
      ].join('');
    }

    const cmp=document.getElementById('bt-compare');
    if(cmp){
      const c=d.comparison||{};
      const strats=c.strategies||[];
      if(!strats.length){cmp.innerHTML='No data';}
      else{
        let rows='<table style="width:100%;border-collapse:collapse"><thead><tr style="background:#1f2937"><th style="padding:4px;text-align:left;color:#f9fafb">Strategy</th><th style="padding:4px;text-align:right;color:#f9fafb">CAGR %</th><th style="padding:4px;text-align:right;color:#f9fafb">CAGR Δ</th><th style="padding:4px;text-align:right;color:#f9fafb">Sharpe Δ</th><th style="padding:4px;text-align:right;color:#f9fafb">DD Δ</th></tr></thead><tbody>';
        strats.forEach(s=>{
          const imp=s.improvement_pct||{};
          const base=s.metrics||{};
          const arrow=(v)=>v>0?'↗':v<0?'↘':'—';
          rows+=`<tr><td style="padding:4px;color:#9ca3af;border-bottom:1px solid #374151;font-size:11px">${s.strategy}</td><td style="padding:4px;color:#f9fafb;border-bottom:1px solid #374151;font-size:11px;text-align:right">${pct(base.cagr_pct)}</td><td style="padding:4px;color:${imp.cagr_pct>=0?'#4ade80':'#f87171'};border-bottom:1px solid #374151;font-size:11px;text-align:right">${arrow(imp.cagr_pct)} ${(imp.cagr_pct||0).toFixed(1)}%</td><td style="padding:4px;color:${imp.sharpe>=0?'#4ade80':'#f87171'};border-bottom:1px solid #374151;font-size:11px;text-align:right">${arrow(imp.sharpe)} ${(imp.sharpe||0).toFixed(1)}%</td><td style="padding:4px;color:${imp.max_drawdown_pct>0?'#f87171':imp.max_drawdown_pct<0?'#4ade80':'#9ca3af'};border-bottom:1px solid #374151;font-size:11px;text-align:right">${arrow(imp.max_drawdown_pct)} ${(imp.max_drawdown_pct||0).toFixed(1)}%</td></tr>`;
        });
        rows+='</tbody></table>';
        cmp.innerHTML=rows;
      }
    }
  }catch(e){console.error('Backtesting load error:',e);}
}

async function refreshBacktesting(){
  await loadBacktesting();
}

// ─── Health Badge Loader ──────────────────────────────────────────────────────
async function loadHealthBadge(){
  try{
    const r=await fetch('/api/monitoring');
    const d=await r.json();
    if(d.error)throw new Error(d.error);
    const score=d.health_score||0;
    const dot=document.getElementById('health-dot');
    const txt=document.getElementById('health-text');
    if(!dot||!txt)return;
    let label='Excellent', color='#22c55e';
    if(score<70){label='Good'; color='#84cc16';}
    if(score<50){label='Warning'; color='#f97316';}
    if(score<30){label='Critical'; color='#ef4444';}
    dot.style.background=color;
    txt.textContent=label+' ('+score+')';
    txt.style.color=color;
  }catch(e){console.error('Health badge load error:',e);}
}

// ─── Monitoring Loader ────────────────────────────────────────────────────────
async function loadMonitoring(){
  try{
    const r=await fetch('/api/monitoring');
    const d=await r.json();
    if(d.error)throw new Error(d.error);
    const m=d.metrics||{};
    const sys=m.system||{};
    const sql=m.sqlite||{};
    const hb=m.scheduler_heartbeat||{};
    const rh=m.reconciliation||{};
    const k=m.kite||{};
    const inet=m.internet;

    const score=document.getElementById('mon-score');
    if(score){
      const s=d.health_score||0;
      let color='#22c55e';
      if(s<70) color='#84cc16';
      if(s<50) color='#f97316';
      if(s<30) color='#ef4444';
      score.innerHTML=`<span style="color:${color}">${s}</span>`;
    }

    const kpi=document.getElementById('mon-kpi');
    if(kpi){
      const add=(label, value, color='')=>`<div class="card-sm"><div class="stat-label">${label}</div><div class="stat-value-sm ${color}">${value}</div></div>`;
      kpi.innerHTML=[
        add('CPU', (sys.cpu_percent||0).toFixed(1)+'%'),
        add('Memory', (sys.memory_percent||0).toFixed(1)+'%'),
        add('Disk', (sys.disk_percent||0).toFixed(1)+'%'),
        add('API Latency', (m.api_latency_ms||0).toFixed(1)+'ms'),
        add('SQLite', sql.ok?'OK':'FAIL', sql.ok?'green':'red'),
        add('SQLite Latency', (sql.response_ms||0).toFixed(1)+'ms'),
        add('Internet', inet?'UP':'DOWN', inet?'green':'red'),
        add('Kite', k.ok?'UP':'DOWN', k.ok?'green':'red'),
        add('Scheduler', hb.ok?'OK':'MISSING', hb.ok?'green':'red'),
        add('Reconciliation', rh.ok?'OK':'FAIL', rh.ok?'green':'red')
      ].join('');
    }

    const status=document.getElementById('mon-status');
    if(status){
      const row=(label, ok)=>`<div style="margin:3px 0;display:flex;justify-content:space-between"><span style="color:#9ca3af">${label}</span><span style="color:${ok?'#4ade80':'#f87171'}">${ok?'●':'●'}</span></div>`;
      status.innerHTML=[
        row('SQLite', sql.ok),
        row('Internet', inet),
        row('Kite', k.ok||k),
        row('Scheduler Heartbeat', hb.ok),
        row('Reconciliation', rh.ok),
        row('AI Engine', m.ai_engine&&m.ai_engine.ok),
        row('Portfolio Optimizer', m.portfolio_optimizer&&m.portfolio_optimizer.ok),
        row('AI Learning', m.ai_learning&&m.ai_learning.ok)
      ].join('');
    }

    const hblog=document.getElementById('mon-heartbeat');
    if(hblog){
      const logs=d.heartbeat_logs||[];
      if(!logs.length){hblog.innerHTML='No data';}
      else{
        let rows='<table style="width:100%;border-collapse:collapse"><thead><tr style="background:#1f2937"><th style="padding:4px;text-align:left;color:#f9fafb">Time</th><th style="padding:4px;text-align:left;color:#f9fafb">Source</th><th style="padding:4px;text-align:left;color:#f9fafb">Status</th><th style="padding:4px;text-align:right;color:#f9fafb">ms</th></tr></thead><tbody>';
        logs.forEach(h=>{
          rows+=`<tr><td style="padding:4px;color:#9ca3af;border-bottom:1px solid #374151;font-size:11px">${fmtDateTime(h.timestamp,true)}</td><td style="padding:4px;color:#f9fafb;border-bottom:1px solid #374151;font-size:11px">${h.source}</td><td style="padding:4px;color:#f9fafb;border-bottom:1px solid #374151;font-size:11px">${h.status}</td><td style="padding:4px;color:#f9fafb;border-bottom:1px solid #374151;font-size:11px;text-align:right">${h.latency_ms}</td></tr>`;
        });
        rows+='</tbody></table>';
        hblog.innerHTML=rows;
      }
    }

    const alerts=document.getElementById('mon-alerts');
    if(alerts){
      const list=d.alerts||[];
      if(!list.length){alerts.innerHTML='No alerts';}
      else{
        let rows='<table style="width:100%;border-collapse:collapse"><thead><tr style="background:#1f2937"><th style="padding:4px;text-align:left;color:#f9fafb">Time</th><th style="padding:4px;text-align:left;color:#f9fafb">Level</th><th style="padding:4px;text-align:left;color:#f9fafb">Source</th><th style="padding:4px;text-align:left;color:#f9fafb">Message</th></tr></thead><tbody>';
        list.forEach(a=>{
          const color=a.level==='CRITICAL'?'#f87171':a.level==='WARNING'?'#f97316':'#9ca3af';
          rows+=`<tr><td style="padding:4px;color:#9ca3af;border-bottom:1px solid #374151;font-size:11px">${fmtDateTime(a.timestamp,true)}</td><td style="padding:4px;color:${color};border-bottom:1px solid #374151;font-size:11px;font-weight:600">${a.level}</td><td style="padding:4px;color:#f9fafb;border-bottom:1px solid #374151;font-size:11px">${a.source}</td><td style="padding:4px;color:#f9fafb;border-bottom:1px solid #374151;font-size:11px">${a.message}</td></tr>`;
        });
        rows+='</tbody></table>';
        alerts.innerHTML=rows;
      }
    }
  }catch(e){console.error('Monitoring load error:',e);}
}

// ─── Smart Execution Loader ───────────────────────────────────────────────────
async function loadSmartExecution(){
  try{
    const r=await fetch('/api/smart-execution');
    const d=await r.json();
    if(d.error)throw new Error(d.error);
    const q=document.getElementById('exec-quality');
    if(q){
      const s=d.execution_quality_score||0;
      let color='#22c55e';
      if(s<70) color='#84cc16';
      if(s<50) color='#f97316';
      if(s<30) color='#ef4444';
      q.innerHTML=`<span style="color:${color}">${s.toFixed(1)}</span>`;
    }
    const kpi=document.getElementById('exec-kpi');
    if(kpi){
      const add=(label,value,color='')=>`<div class="card-sm"><div class="stat-label">${label}</div><div class="stat-value-sm ${color}">${value}</div></div>`;
      kpi.innerHTML=[
        add('Today Orders', d.today_orders||0),
        add('Filled', d.filled||0, 'green'),
        add('Partial', d.partial||0, 'orange'),
        add('Rejected', d.rejected||0, 'red'),
        add('Avg Slippage', (d.avg_slippage_pct||0).toFixed(3)+'%'),
        add('Avg Fill Time', (d.avg_fill_time_ms||0)+'ms'),
        add('Broker Latency', (d.broker_latency_ms||0)+'ms'),
        add('Success %', (d.success_rate_pct||0).toFixed(1)+'%', d.success_rate_pct>=80?'green':''),
        add('Avg Retries', (d.avg_retry_count||0).toFixed(2))
      ].join('');
    }
    const orders=document.getElementById('exec-orders');
    if(orders){
      const list=d.orders||[];
      if(!list.length){orders.innerHTML='No orders';}
      else{
        let rows='<table style="width:100%;border-collapse:collapse"><thead><tr style="background:#1f2937"><th style="padding:4px;text-align:left;color:#f9fafb">Time</th><th style="padding:4px;text-align:left;color:#f9fafb">Symbol</th><th style="padding:4px;text-align:left;color:#f9fafb">Side</th><th style="padding:4px;text-align:right;color:#f9fafb">Qty</th><th style="padding:4px;text-align:right;color:#f9fafb">Filled</th><th style="padding:4px;text-align:right;color:#f9fafb">Avg</th><th style="padding:4px;text-align:left;color:#f9fafb">Type</th><th style="padding:4px;text-align:left;color:#f9fafb">Status</th></tr></thead><tbody>';
        list.forEach(o=>{
          const statusColor=o.status==='FILLED'?'#4ade80':o.status==='REJECTED'?'#f87171':o.status==='PARTIAL'?'#f97316':'#9ca3af';
          rows+=`<tr><td style="padding:4px;color:#9ca3af;border-bottom:1px solid #374151;font-size:11px">${fmtDateTime(o.timestamp,true)}</td><td style="padding:4px;color:#f9fafb;border-bottom:1px solid #374151;font-size:11px">${o.symbol}</td><td style="padding:4px;color:#f9fafb;border-bottom:1px solid #374151;font-size:11px">${o.side}</td><td style="padding:4px;color:#f9fafb;border-bottom:1px solid #374151;font-size:11px;text-align:right">${o.quantity}</td><td style="padding:4px;color:#f9fafb;border-bottom:1px solid #374151;font-size:11px;text-align:right">${o.filled_qty}</td><td style="padding:4px;color:#f9fafb;border-bottom:1px solid #374151;font-size:11px;text-align:right">₹${(o.avg_price||0).toFixed(2)}</td><td style="padding:4px;color:#f9fafb;border-bottom:1px solid #374151;font-size:11px">${o.order_type}</td><td style="padding:4px;color:${statusColor};border-bottom:1px solid #374151;font-size:11px;font-weight:600">${o.status}</td></tr>`;
        });
        rows+='</tbody></table>';
        orders.innerHTML=rows;
      }
    }
    const queue=document.getElementById('exec-queue');
    if(queue){
      const list=d.queue||[];
      if(!list.length){queue.innerHTML='No queued orders';}
      else{
        let rows='<table style="width:100%;border-collapse:collapse"><thead><tr style="background:#1f2937"><th style="padding:4px;text-align:left;color:#f9fafb">Time</th><th style="padding:4px;text-align:left;color:#f9fafb">Symbol</th><th style="padding:4px;text-align:left;color:#f9fafb">Side</th><th style="padding:4px;text-align:right;color:#f9fafb">Qty</th><th style="padding:4px;text-align:left;color:#f9fafb">Strategy</th><th style="padding:4px;text-align:left;color:#f9fafb">Status</th></tr></thead><tbody>';
        list.forEach(o=>{
          rows+=`<tr><td style="padding:4px;color:#9ca3af;border-bottom:1px solid #374151;font-size:11px">${fmtDateTime(o.timestamp,true)}</td><td style="padding:4px;color:#f9fafb;border-bottom:1px solid #374151;font-size:11px">${o.symbol}</td><td style="padding:4px;color:#f9fafb;border-bottom:1px solid #374151;font-size:11px">${o.side}</td><td style="padding:4px;color:#f9fafb;border-bottom:1px solid #374151;font-size:11px;text-align:right">${o.quantity}</td><td style="padding:4px;color:#f9fafb;border-bottom:1px solid #374151;font-size:11px">${o.strategy}</td><td style="padding:4px;color:#f9fafb;border-bottom:1px solid #374151;font-size:11px">${o.status}</td></tr>`;
        });
        rows+='</tbody></table>';
        queue.innerHTML=rows;
      }
    }
    const analytics=document.getElementById('exec-analytics');
    if(analytics){
      analytics.innerHTML=`<div class="grid grid-cols-2 md:grid-cols-3 gap-3"><div class="card-sm"><div class="stat-label">Fill Ratio</div><div class="stat-value-sm">${((d.fill_ratio||0)*100).toFixed(1)}%</div></div><div class="card-sm"><div class="stat-label">Avg Broker Latency</div><div class="stat-value-sm">${d.broker_latency_ms||0}ms</div></div><div class="card-sm"><div class="stat-label">Avg Retry Count</div><div class="stat-value-sm">${(d.avg_retry_count||0).toFixed(2)}</div></div></div>`;
    }
  }catch(e){console.error('Smart execution load error:',e);}
}

// ─── AI Learning Loader ───────────────────────────────────────────────────────
async function loadAiLearning(){
  try{
    const r=await fetch('/api/ai-learning');
    const d=await r.json();
    if(d.error)throw new Error(d.error);
    const m=d.metrics||{};
    const feats=d.feature_importance||[];
    const weights=d.model_weights||[];
    const curve=d.learning_curve||[];
    const top=d.top_indicators||[];
    const worst=d.worst_indicators||[];

    const retrain=document.getElementById('ai-last-retrain');
    if(retrain)retrain.textContent=m.timestamp?'Last retrain: '+fmtDateTime(m.timestamp,true):'No retrain yet';

    const met=document.getElementById('ai-metrics');
    if(met){
      const add=(label, value, color='')=>`<div class="card-sm"><div class="stat-label">${label}</div><div class="stat-value-sm ${color}">${value}</div></div>`;
      met.innerHTML=[
        add('Model Accuracy', m.accuracy!=null?pct(m.accuracy*100,1):'—'),
        add('Win Rate', m.win_rate!=null?pct(m.win_rate):'—'),
        add('Trades Used', m.trades_used!=null?m.trades_used:'—'),
        add('Learning Progress', m.trades_used!=null?Math.min(100,(m.trades_used/5000)*100).toFixed(1)+'%':'—')
      ].join('');
    }

    const fmtList=(list)=>list.map(x=>`<div style="margin:2px 0"><span style="color:#9ca3af;width:120px;display:inline-block">${x.feature}</span><span class="stat-value-sm ${x.importance>=0?'green':'red'}">${x.importance.toFixed(4)}</span></div>`).join('')||'No data';
    const tEl=document.getElementById('ai-top');
    if(tEl)tEl.innerHTML=fmtList(top);
    const wEl=document.getElementById('ai-worst');
    if(wEl)wEl.innerHTML=fmtList(worst);

    const imp=document.getElementById('ai-importance');
    if(imp){
      let rows='<table style="width:100%;border-collapse:collapse"><thead><tr style="background:#1f2937"><th style="padding:4px;text-align:left;color:#f9fafb">Feature</th><th style="padding:4px;text-align:right;color:#f9fafb">Correlation</th></tr></thead><tbody>';
      feats.slice(0,20).forEach(f=>{
        rows+=`<tr><td style="padding:4px;color:#9ca3af;border-bottom:1px solid #374151;font-size:11px">${f.feature}</td><td style="padding:4px;color:#f9fafb;border-bottom:1px solid #374151;font-size:11px;text-align:right">${(f.correlation||0).toFixed(4)}</td></tr>`;
      });
      rows+='</tbody></table>';
      imp.innerHTML=rows||'No data';
    }

    const wgt=document.getElementById('ai-weights');
    if(wgt){
      let rows='<table style="width:100%;border-collapse:collapse"><thead><tr style="background:#1f2937"><th style="padding:4px;text-align:left;color:#f9fafb">Category</th><th style="padding:4px;text-align:right;color:#f9fafb">Weight</th></tr></thead><tbody>';
      weights.forEach(w=>{
        rows+=`<tr><td style="padding:4px;color:#9ca3af;border-bottom:1px solid #374151;font-size:11px">${w.category}</td><td style="padding:4px;color:#f9fafb;border-bottom:1px solid #374151;font-size:11px;text-align:right">${(w.weight*100).toFixed(1)}%</td></tr>`;
      });
      rows+='</tbody></table>';
      wgt.innerHTML=rows||'No data';
    }

    const cur=document.getElementById('ai-curve');
    if(cur){
      if(!curve.length){cur.innerHTML='No data';}
      else{
        let rows='<table style="width:100%;border-collapse:collapse"><thead><tr style="background:#1f2937"><th style="padding:4px;text-align:left;color:#f9fafb">Date</th><th style="padding:4px;text-align:right;color:#f9fafb">Accuracy</th><th style="padding:4px;text-align:right;color:#f9fafb">Win Rate</th></tr></thead><tbody>';
        curve.forEach(c=>{
          rows+=`<tr><td style="padding:4px;color:#9ca3af;border-bottom:1px solid #374151;font-size:11px">${fmtDateTime(c.timestamp,true)}</td><td style="padding:4px;color:#f9fafb;border-bottom:1px solid #374151;font-size:11px;text-align:right">${c.accuracy!=null?pct(c.accuracy*100,1):'—'}</td><td style="padding:4px;color:#f9fafb;border-bottom:1px solid #374151;font-size:11px;text-align:right">${c.win_rate!=null?pct(c.win_rate):'—'}</td></tr>`;
        });
        rows+='</tbody></table>';
        cur.innerHTML=rows;
      }
    }
  }catch(e){console.error('AI Learning load error:',e);}
}
</script>
</body></html>"""

def get_kite():
    try:
        from token_manager import TokenManager
        return TokenManager().initialize_kite()
    except Exception:
        return None


@app.route('/')
def index():
    from flask import make_response, redirect, request
    # Force browsers that have an old cached HTML to load a fresh, cache-busted URL
    if request.args.get('v') != '4':
        return redirect('/?v=4', code=302)
    resp = make_response(render_template_string(HTML))
    resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    resp.headers['Pragma'] = 'no-cache'
    return resp


@app.route('/api/js-error', methods=['POST'])
def js_error():
    data = request.get_json(silent=True) or {}
    msg = data.get('message', 'unknown')
    stack = data.get('stack', '')
    app.logger.error('JS ERROR from browser: %s | stack: %s', msg, stack[:1000])
    return jsonify({'ok': True})


@app.route('/api/data')
def api_data():
    from config import config
    kite = get_kite()
    now_ist = datetime.now(IST)
    today_str = now_ist.strftime("%Y-%m-%d")

    data = {
        "timestamp": now_ist.isoformat(),
        "market_open": False,
        "kite_ok": kite is not None,
        "paper_trading": config.PAPER_TRADING,
        "trading_mode": config.TRADING_MODE,
        "token_expiry": "—",
        "cash": 0,
        "invested": 0,
        "daily_pnl": 0,
        "weekly_pnl": 0,
        "monthly_pnl": 0,
        "weekly_win_rate": 0,
        "monthly_win_rate": 0,
        "weekly_calendar": [0, 0, 0, 0, 0],
        "open_positions": 0,
        "win_rate": 0,
        "total_trades": 0,
        "budget": config.TRADING_AMOUNT,
        "cfg_trading_amount": config.TRADING_AMOUNT,
        "cfg_max_positions": config.MAX_POSITIONS,
        "cfg_min_confidence": config.MIN_CONFIDENCE,
        "cfg_sl_pct": config.SWING_STOP_LOSS_PERCENTAGE if config.TRADING_MODE == 'swing' else config.STOP_LOSS_PERCENTAGE,
        "cfg_tgt_pct": config.SWING_TARGET_PERCENTAGE if config.TRADING_MODE == 'swing' else config.TARGET_PERCENTAGE,
        "cfg_max_capital": config.MAX_CAPITAL_USAGE,
        "cfg_daily_loss": config.DAILY_MAX_LOSS_PCT,
        "cfg_sideways_buy_score_min": config.SIDEWAYS_BUY_SCORE_MIN,
        "cfg_risk_per_trade": config.RISK_PER_TRADE,
        "cfg_swing_max_hold_days": config.SWING_MAX_HOLD_DAYS,
        "cfg_reentry_cooldown_hours": config.REENTRY_COOLDOWN_HOURS,
        "positions": [],
        "signals": [],
        "orders": [],
        "holdings": [],
        "recommendations": [],
        "analytics": {},
        "market_regime": "UNKNOWN",
        "portfolio_health": {
            "account_value": 0,
            "peak_value": 0,
            "drawdown": 0
        },
        "strategy_stats": {
            "avg_win": 0,
            "avg_loss": 0,
            "profit_factor": 0,
            "expectancy": 0
        },
        "market_summary": {
            "nifty_change": 0,
            "banknifty_change": 0,
            "vix": 0,
            "market_regime": "UNKNOWN"
        },
        "market_data_metrics": {},
        "reconciliation_status": {
            "healthy": False,
            "last_sync": None,
            "mismatches": 0,
            "repairs": 0,
            "duration_ms": 0
        }
    }

    # Load broker mode status (written by broker_integration.py at startup)
    try:
        if get_store is not None:
            _bs = get_store().get_broker_state('status') or {}
        else:
            _bs = {}
        if _bs:
            data['broker_mode'] = _bs.get('mode', 'UNKNOWN')
            data['broker_live_ready'] = _bs.get('live_ready', False)
            data['broker_startup_timestamp'] = _bs.get('startup_timestamp', '—')
            data['broker_error'] = _bs.get('error', None)
        else:
            data['broker_mode'] = 'PAPER' if config.PAPER_TRADING else 'LIVE'
            data['broker_live_ready'] = not config.PAPER_TRADING and (kite is not None)
            data['broker_startup_timestamp'] = '—'
            data['broker_error'] = 'Broker status not yet recorded — restart trading orchestrator'
    except Exception:
        data['broker_mode'] = 'UNKNOWN'
        data['broker_live_ready'] = False
        data['broker_startup_timestamp'] = '—'
        data['broker_error'] = None

    # Reconciliation status
    try:
        if get_store is not None:
            _rs = get_store().get_broker_state('reconciliation') or {}
        else:
            _rs = {}
        data['reconciliation_status'] = _rs if _rs else data['reconciliation_status']
    except Exception:
        pass

    if not kite:
        resp = jsonify(data)
        resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
        return resp

    # Token expiry
    try:
        token_path = os.path.join(os.path.dirname(__file__), 'data', 'kite_token.json')
        if os.path.exists(token_path):
            with open(token_path) as f:
                tok = json.load(f)
            data['token_expiry'] = tok.get('expiry', '—')[:16]
    except Exception:
        pass

    # Market open and regime
    from market_data import MarketDataFetcher
    mdf = MarketDataFetcher(kite=kite)
    data['market_open'] = mdf.is_market_open()
    try:
        from market_regime import MarketRegimeDetector
        regime_det = MarketRegimeDetector(kite=kite)
        data['market_regime'] = regime_det.detect_regime()
        data['market_summary']['market_regime'] = data['market_regime']
    except Exception:
        pass

    # Market summary: NIFTY, BANKNIFTY, VIX
    try:
        def get_index_change(symbol):
            token = mdf._get_instrument_token(symbol)
            if token:
                q = kite.quote([token])
                info = q.get(str(token), {})
                last = info.get('last_price', 0)
                net_change = info.get('net_change', 0)
                prev_close = last - net_change
                if prev_close > 0:
                    return (net_change / prev_close) * 100
            return 0
        nifty_change = get_index_change('NIFTY 50')
        banknifty_change = get_index_change('NIFTY BANK') or get_index_change('BANKNIFTY')
        vix_token = mdf._get_instrument_token('INDIA VIX')
        vix = 0
        if vix_token:
            q = kite.quote([vix_token])
            vix = q.get(str(vix_token), {}).get('last_price', 0)
        data['market_summary'] = {
            "nifty_change": nifty_change,
            "banknifty_change": banknifty_change,
            "vix": vix,
            "market_regime": data['market_regime']
        }
    except Exception:
        pass

    # Margins / cash / account balance
    try:
        margins = kite.margins()
        eq = margins.get("equity", {})
        avail = eq.get("available", {})
        cash = avail.get("live_balance") or avail.get("cash") or eq.get("net", 0)
        used = eq.get("utilised", {})
        margin_blocked = used.get("debits", 0) or sum(used.values())
        account_balance = eq.get("net", cash + margin_blocked)
        data['cash'] = cash
        data['account_balance'] = account_balance
        data['margin_blocked'] = margin_blocked
        data['net_portfolio_value'] = account_balance
        data['budget'] = config.TRADING_AMOUNT
    except Exception:
        pass

    # Positions (intraday — separate try so holdings still load if positions() fails)
    net_pos = []
    try:
        pos_data = kite.positions()
        # Only include positive qty — negative = T+1 settlement offset for already-sold CNC stocks
        net_pos = [p for p in pos_data.get('net', []) if p.get('quantity', 0) > 0]
    except Exception:
        pass

    # Holdings (delivery CNC — merged into positions list for unified display)
    try:
        holdings_raw = kite.holdings()
        data['holdings'] = holdings_raw
        holdings_as_pos = []
        pos_symbols = {p.get('tradingsymbol') for p in net_pos}
        for h in holdings_raw:
            settled_qty = h.get('quantity', 0) or 0
            t1_qty      = h.get('t1_quantity', 0) or 0
            # Use settled qty only — T+1 stocks also appear in kite.positions() net
            # so using settled prevents double-counting. Show both in total for display.
            effective_qty = settled_qty
            if effective_qty == 0 and t1_qty == 0:
                continue
            display_qty = effective_qty if effective_qty > 0 else t1_qty
            if h.get('tradingsymbol') in pos_symbols:
                continue  # already captured in net positions (T+1 case)
            holdings_as_pos.append({
                'tradingsymbol':  h.get('tradingsymbol'),
                'exchange':       h.get('exchange', 'BSE'),
                'product':        h.get('product', 'CNC'),
                'quantity':       display_qty,
                'average_price':  h.get('average_price', 0),
                'last_price':     h.get('last_price', h.get('close_price', 0)),
                'close_price':    h.get('close_price', 0),
                'pnl':            h.get('pnl', 0),
                'day_change':     h.get('day_change', 0),
                'day_change_percentage': h.get('day_change_percentage', 0),
                'overnight_quantity': h.get('opening_quantity', 0),
                'value':          h.get('average_price', 0) * display_qty,
                '_source':        'holding',
            })
        all_positions = net_pos + holdings_as_pos
        data['positions'] = all_positions
        data['open_positions'] = len(all_positions)
        # Compute P&L: use kite's pnl field when available (intraday), else derive from prices
        def _pos_pnl(p):
            kite_pnl = p.get('pnl', 0) or 0
            if kite_pnl != 0:
                return kite_pnl
            ltp = p.get('last_price', 0) or p.get('close_price', 0) or 0
            avg = p.get('average_price', 0) or 0
            qty = p.get('quantity', 0) or 0
            return (ltp - avg) * qty if ltp and avg and qty else 0
        data['daily_pnl'] = sum(_pos_pnl(p) for p in all_positions)
        data['invested'] = sum(p.get('average_price', 0) * p.get('quantity', 0) for p in all_positions)

        # Enrich positions with re-entry metadata from trade journal
        # ── Enrich with SL / Target from risk_manager SQLite positions ─────
        try:
            _rm_map = {}
            if get_store is not None:
                for _rp in get_store().load_positions():
                    _rm_map[_rp['symbol']] = _rp
        except Exception:
            _rm_map = {}

        _sl_pct  = config.SWING_STOP_LOSS_PERCENTAGE  if config.TRADING_MODE == 'swing' else config.STOP_LOSS_PERCENTAGE
        _tgt_pct = config.SWING_TARGET_PERCENTAGE     if config.TRADING_MODE == 'swing' else config.TARGET_PERCENTAGE

        for pos in all_positions:
            sym = pos.get('tradingsymbol')
            avg = pos.get('average_price', 0) or 0
            _rm = _rm_map.get(sym, {})
            # SL / Target: prefer risk_manager store, fall back to config %
            sl  = _rm.get('stop_loss')   or (round(avg * (1 - _sl_pct),  2) if avg else None)
            tgt = _rm.get('target')      or (round(avg * (1 + _tgt_pct), 2) if avg else None)
            tsl = _rm.get('trailing_stop') or sl
            pos['stop_loss']    = sl
            pos['target']       = tgt
            pos['trailing_stop']= tsl
            # Add trade score from risk manager if available
            pos['trade_score']  = _rm.get('trade_score', 0)

        # ── Enrich with journal metadata (first entry, days held, re-entry) ──
        try:
            _buy_entries = []
            if get_store is not None:
                _buy_entries = get_store().get_trades(action='BUY')
            for pos in all_positions:
                sym = pos.get('tradingsymbol')
                sym_buys = [e for e in _buy_entries if e.get('symbol') == sym]
                avg = pos.get('average_price', 0) or 0
                if sym_buys:
                    sym_buys_sorted = sorted(sym_buys, key=lambda x: x.get('timestamp', ''))
                    first = sym_buys_sorted[0]
                    last  = sym_buys_sorted[-1]
                    reentry_count = sum(1 for e in sym_buys if e.get('is_reentry'))
                    pos['first_entry_price']  = first.get('entry_price', avg)
                    pos['entry_date']         = first.get('date', '')
                    pos['reentry_count']      = reentry_count
                    pos['is_reentry']         = last.get('is_reentry', False)
                    pos['prev_exit_reason']   = last.get('prev_exit_reason', '')
                    pos['reentry_score']      = last.get('reentry_score', 0)
                    pos['reentry_confidence'] = last.get('reentry_confidence', 0)
                    if pos['entry_date']:
                        try:
                            _entry_dt = datetime.strptime(pos['entry_date'], '%Y-%m-%d').date()
                            pos['days_held'] = (datetime.now().date() - _entry_dt).days
                        except Exception:
                            pos['days_held'] = 0
                    else:
                        pos['days_held'] = 0
                else:
                    # No journal entry — stock bought outside bot or before journal
                    pos.setdefault('first_entry_price', avg)
                    pos.setdefault('entry_date', today_str)
                    pos['days_held'] = 0  # treat as today
                    pos['reentry_count'] = 0
        except Exception:
            pass
    except Exception:
        pass

    # Orders and performance
    try:
        orders = kite.orders()
        pending_statuses = {'OPEN', 'TRIGGER PENDING', 'PENDING'}
        data['pending_orders'] = len([o for o in orders if o.get('status', '').upper() in pending_statuses])
        try:
            data['gtt_orders'] = len(kite.get_gtts())
        except Exception:
            data['gtt_orders'] = 0
        
        # FIFO P&L calculator: matches each sell with oldest available buys per symbol
        def calculate_pnl(all_orders):
            sorted_orders = sorted(
                [o for o in all_orders if o.get('status') == 'COMPLETE'],
                key=lambda x: str(x.get('order_timestamp', ''))
            )
            holdings = {}  # symbol -> list of (qty, price) remaining
            order_pnl = {}
            for o in sorted_orders:
                sym = o.get('tradingsymbol')
                qty = int(o.get('quantity', 0))
                price = float(o.get('average_price', 0) or o.get('price', 0))
                order_id = o.get('order_id', '')
                if o.get('transaction_type') == 'BUY':
                    holdings.setdefault(sym, []).append([qty, price])
                    order_pnl[order_id] = 0.0
                elif o.get('transaction_type') == 'SELL':
                    total_pnl = 0.0
                    remaining = qty
                    while remaining > 0 and holdings.get(sym):
                        lot = holdings[sym][0]
                        lot_qty, lot_price = lot[0], lot[1]
                        use = min(remaining, lot_qty)
                        total_pnl += (price - lot_price) * use
                        lot[0] -= use
                        remaining -= use
                        if lot[0] <= 0:
                            holdings[sym].pop(0)
                    order_pnl[order_id] = total_pnl
            return order_pnl
        
        # Load journal for buy-price lookup (needed for sells from past sessions)
        journal_entries = []
        try:
            if get_store is not None:
                journal_entries = get_store().all_trades()
        except Exception:
            pass

        # Build per-symbol buy price map from journal (for cross-session P&L)
        # journal BUY entries: entry_price=buy price, exit_price=sell price (set on SELL)
        _jnl_buy_map = {}  # symbol -> list of {qty, buy_price, sell_price, net_pnl}
        for je in journal_entries:
            if je.get('action') == 'BUY':
                sym = je.get('symbol')
                _jnl_buy_map.setdefault(sym, []).append({
                    'qty':       je.get('quantity', 1),
                    'buy_price': je.get('entry_price', 0),
                    'sell_price':je.get('exit_price'),   # set when position closed
                    'net_pnl':   je.get('net_pnl'),
                    'date':      je.get('date', ''),
                    'exit_reason': je.get('exit_reason', ''),  # Add exit reason to journal entry
                })

        # Match Kite orders to journal entries by order_id or symbol+price+qty
        jnl_by_order_id = {je.get('order_id'): je for je in journal_entries if je.get('order_id')}
        jnl_sell_map = {}
        for je in journal_entries:
            if je.get('action') == 'SELL' and not je.get('order_id'):
                key = (je.get('symbol'), round(float(je.get('exit_price') or 0), 2), int(je.get('quantity') or 0))
                jnl_sell_map[key] = je

        # Attach P&L to all orders via FIFO; fall back to journal for cross-session sells
        all_order_pnl = calculate_pnl(orders)
        for o in orders:
            oid = o.get('order_id', '')
            sym = o.get('tradingsymbol', '')
            sell_price = float(o.get('average_price', 0) or o.get('price', 0))
            fifo_pnl = all_order_pnl.get(oid, 0.0)
            if o.get('transaction_type') == 'SELL' and fifo_pnl == 0.0:
                # FIFO had no matching buy (cross-session) — look up journal
                jbuys = _jnl_buy_map.get(sym, [])
                if jbuys:
                    # Use journal net_pnl if available, else compute from buy price
                    jb = jbuys[-1]
                    if jb.get('net_pnl') is not None:
                        fifo_pnl = jb['net_pnl']
                        o['buy_price'] = jb['buy_price']
                    elif jb.get('buy_price'):
                        fifo_pnl = (sell_price - jb['buy_price']) * int(o.get('quantity', 1))
                        o['buy_price'] = jb['buy_price']
            elif o.get('transaction_type') == 'BUY':
                o['buy_price'] = sell_price  # for BUYs, show the buy price itself
            o['pnl'] = fifo_pnl
            if o.get('transaction_type') == 'SELL':
                o['sell_price'] = sell_price
                # If FIFO matched an in-session buy, derive blended buy price for display
                if not o.get('buy_price') and sell_price > 0 and int(o.get('quantity', 1)) > 0:
                    o['buy_price'] = round(sell_price - (fifo_pnl / int(o.get('quantity', 1))), 2)
                # Attach exit reason from journal
                reason = (jnl_by_order_id.get(oid) or {}).get('exit_reason')
                if not reason:
                    key = (sym, round(sell_price, 2), int(o.get('quantity', 1)))
                    reason = (jnl_sell_map.get(key) or {}).get('exit_reason')
                o['exit_reason'] = reason

        # All completed orders (for trade history tab) + today's orders
        all_completed = [o for o in orders if o.get('status') == 'COMPLETE']
        # Merge journal entries for orders not already in Kite's list (covers past sessions)
        try:
            kite_ids = {o.get('order_id') for o in all_completed}
            kite_syms_today = {o.get('tradingsymbol') for o in all_completed}
            for je in journal_entries:
                sym = je.get('symbol')
                je_dt = _safe_dt(
                    je.get('timestamp') or je.get('exit_date') or je.get('entry_date') or je.get('date')
                )
                ts = je_dt.strftime('%Y-%m-%d %H:%M:%S') if je_dt else '1970-01-01 09:00:00'
                if je.get('kite_order_id') not in kite_ids:
                    action = (je.get('action') or 'BUY').upper()
                    if action == 'SELL' and je.get('exit_price') is not None and je.get('net_pnl') is not None:
                        # Closed SELL from journal
                        all_completed.append({
                            'tradingsymbol':    sym,
                            'transaction_type': 'SELL',
                            'quantity':         je.get('quantity', 0),
                            'average_price':    je.get('exit_price', 0),
                            'buy_price':        je.get('entry_price', 0),
                            'sell_price':       je.get('exit_price', 0),
                            'order_timestamp':  ts,
                            'status':           'COMPLETE',
                            'pnl':              je.get('net_pnl', 0.0),
                            'order_id':         je.get('kite_order_id', ''),
                            '_source':          'journal',
                        })
                    else:
                        # Open or closed BUY (do not mislabel a closed BUY as a SELL)
                        all_completed.append({
                            'tradingsymbol':    sym,
                            'transaction_type': 'BUY',
                            'quantity':         je.get('quantity', 0),
                            'average_price':    je.get('entry_price', 0),
                            'buy_price':        je.get('entry_price', 0),
                            'order_timestamp':  ts,
                            'status':           'COMPLETE',
                            'pnl':              0.0,
                            'order_id':         je.get('kite_order_id', ''),
                            '_source':          'journal',
                        })
        except Exception:
            pass
        # Normalize, filter journal noise, and dedupe orders
        all_completed = [o for o in all_completed if int(o.get('quantity', 0) or 0) > 0]
        all_completed = [o for o in all_completed if not (
            o.get('_source') == 'journal' and
            (o.get('transaction_type') or 'BUY').upper() == 'SELL' and
            _safe_float(o.get('pnl'), 0.0) == 0.0 and
            _safe_float(o.get('buy_price'), 0.0) == _safe_float(o.get('average_price'), 0.0) and
            not o.get('order_id')
        )]
        for o in all_completed:
            _odt = _safe_dt(o.get('order_timestamp'))
            if _odt:
                o['order_timestamp'] = _odt.strftime('%Y-%m-%d %H:%M:%S')
        order_groups = {}
        for o in all_completed:
            k = (
                o.get('tradingsymbol') or o.get('symbol', ''),
                o.get('transaction_type', 'BUY').upper(),
                str(o.get('order_timestamp', '')),
                str(int(o.get('quantity', 0) or 0)),
            )
            order_groups.setdefault(k, []).append(o)
        deduped = []
        for k, items in order_groups.items():
            items.sort(
                key=lambda x: (
                    0 if x.get('_source') != 'journal' else 1,
                    0 if x.get('order_id') else 1,
                    -abs(_safe_float(x.get('pnl'), 0.0)),
                )
            )
            deduped.append(items[0])
        data['all_orders'] = sorted(deduped, key=lambda x: str(x.get('order_timestamp', '')), reverse=True)

        # Paired professional trade cards and BUY/SELL event history (no duplicate SELLs)
        try:
            now_naive = now_ist.replace(tzinfo=None)
            all_cards = _build_trade_cards(journal_entries, data.get('positions', []), now_naive)
            data['trade_cards'] = [c for c in all_cards if c.get('status') == 'Open']
            data['trade_events'] = _build_order_events(data.get('all_orders', []))
        except Exception as _tc_err:
            logger.error(f"Trade history card build failed: {_tc_err}")
            data['trade_cards'] = []
            data['trade_events'] = []

        # Pending SELL actions surfaced by the order executor
        try:
            if get_store is not None:
                _ps_items = get_store().load_daily_state().get('pending_sells', {})
                if isinstance(_ps_items, list):
                    data['pending_sells'] = _ps_items
                else:
                    data['pending_sells'] = sorted(list(_ps_items.values()), key=lambda x: x.get('last_attempt', ''), reverse=True)
            else:
                data['pending_sells'] = []
        except Exception as _ps_err:
            data['pending_sells'] = []

        # Today's orders
        data['orders'] = [o for o in orders if str(o.get('order_timestamp', '')).startswith(today_str)]
        completed = [o for o in data['orders'] if o.get('status') == 'COMPLETE']
        buys  = [o for o in completed if o.get('transaction_type') == 'BUY']
        sells = [o for o in completed if o.get('transaction_type') == 'SELL']
        data['total_trades'] = len(completed)
        win_sells = [o for o in sells if (o.get('pnl') or 0) > 0]
        data['win_rate'] = round(len(win_sells) / len(sells), 4) if sells else 0
        # daily_pnl = unrealized (from positions) + realized (from today's closed trades)
        realized_pnl = sum(o.get('pnl', 0) for o in sells)
        unrealized_pnl = data.get('daily_pnl', 0)  # set earlier from positions
        data['daily_pnl'] = unrealized_pnl + realized_pnl
        
        # Weekly / Monthly performance
        week_ago = (now_ist - timedelta(days=7)).strftime("%Y-%m-%d")
        month_ago = (now_ist - timedelta(days=30)).strftime("%Y-%m-%d")
        week_orders = [o for o in orders if str(o.get('order_timestamp', '')) >= week_ago and o.get('status') == 'COMPLETE']
        month_orders = [o for o in orders if str(o.get('order_timestamp', '')) >= month_ago and o.get('status') == 'COMPLETE']
        w_buys = [o for o in week_orders if o.get('transaction_type') == 'BUY']
        w_sells = [o for o in week_orders if o.get('transaction_type') == 'SELL']
        m_buys = [o for o in month_orders if o.get('transaction_type') == 'BUY']
        m_sells = [o for o in month_orders if o.get('transaction_type') == 'SELL']
        data['weekly_win_rate'] = len(w_sells) / len(w_buys) if w_buys else 0
        data['monthly_win_rate'] = len(m_sells) / len(m_buys) if m_buys else 0
        data['weekly_pnl'] = sum(o.get('pnl', 0) for o in w_sells)
        data['monthly_pnl'] = sum(o.get('pnl', 0) for o in m_sells)

        # Realized P&L per weekday for the trade calendar (Mon-Fri)
        try:
            weekday_pnl = [0.0, 0.0, 0.0, 0.0, 0.0]  # Mon..Fri
            week_start = now_ist.date() - timedelta(days=now_ist.weekday())  # Monday of this week
            for o in data.get('all_orders', []):
                if o.get('transaction_type') != 'SELL':
                    continue
                ts = str(o.get('order_timestamp', ''))
                try:
                    if 'T' in ts:
                        od = datetime.fromisoformat(ts.replace('Z', '+00:00')).date()
                    elif ' ' in ts:
                        od = datetime.strptime(ts[:10], '%Y-%m-%d').date()
                    else:
                        od = datetime.strptime(ts[:10], '%Y-%m-%d').date()
                except Exception:
                    continue
                if week_start <= od <= (week_start + timedelta(days=4)):
                    idx = od.weekday()  # Monday=0 .. Friday=4
                    weekday_pnl[idx] += float(o.get('pnl', 0) or 0)
            data['weekly_calendar'] = weekday_pnl
        except Exception:
            pass

        # Portfolio health — computed later after holdings are loaded (see below)
        
        # Order history (every order: BUY/SELL/REJECTED/CANCELLED)
        all_status = sorted(orders, key=lambda x: str(x.get('order_timestamp', '')), reverse=True)
        data['order_history'] = all_status
        data['rejected_orders'] = [
            o for o in all_status
            if o.get('status', '').upper() not in {'COMPLETE', 'OPEN', 'TRIGGER PENDING', 'PENDING'}
        ]
        data['orders_executed'] = len([o for o in all_status if o.get('status', '').upper() == 'COMPLETE'])

        # Today's order counts for the dashboard status cards
        today_orders = [
            o for o in all_status
            if str(o.get('order_timestamp', ''))[:10] == today_str
        ]
        data['today_orders'] = len(today_orders)
        data['filled'] = len([o for o in today_orders if o.get('status', '').upper() == 'COMPLETE'])
        data['partial'] = len([o for o in today_orders if o.get('status', '').upper() == 'PARTIAL'])
        data['rejected'] = len([
            o for o in today_orders
            if o.get('status', '').upper() in {'REJECTED', 'CANCELLED', 'CANCELLED BY USER'}
        ])

        # Journal-backed closed-trade analytics (BUY -> SELL pairs)
        if _TradeJournal is not None:
            j = _TradeJournal()
            j_analytics = j.analytics()
            closed = j.closed_trades()

            # Override win rate to use only completed BUY->SELL pairs
            data['win_rate'] = j_analytics.get('win_rate', 0)
            data['total_trades'] = j_analytics.get('total_trades', 0)
            data['closed_trade_count'] = data['total_trades']
            data['avg_hold_days'] = j_analytics.get('avg_hold_days', 0)
            data['ai_accuracy'] = round(data['win_rate'] * 100, 1) if data['total_trades'] else None
            data['ai_trades_used'] = data['total_trades']

            data['closed_trades'] = sorted([
                {
                    'symbol': t.get('symbol', ''),
                    'buy': round(t.get('entry_price', 0), 2),
                    'sell': round(t.get('exit_price', 0), 2),
                    'qty': t.get('quantity', 0),
                    'pnl': round(t.get('net_pnl', 0), 2),
                    'days': int(t.get('holding_days', 0) or 0),
                    'exit': t.get('exit_reason', '—') or '—',
                    'date': t.get('exit_date', '') or t.get('date', ''),
                    'score': round(t.get('trade_score', 0), 1),
                    'regime': t.get('market_regime', '—'),
                    'sector': t.get('sector', '—'),
                }
                for t in closed
            ], key=lambda x: x.get('date', ''), reverse=True)

            # Completed trades now reconciled from broker + journal orders (one row per SELL)
            data['completed_trades_full'] = _build_order_completed(data.get('all_orders', []))

            # Portfolio growth curve from realized P&L
            start_capital = float(data.get('budget', config.TRADING_AMOUNT) or 15000)
            closed_sorted = sorted(closed, key=lambda x: x.get('exit_date', '') or '')
            growth = []
            cum_pnl = 0.0
            for t in closed_sorted:
                cum_pnl += float(t.get('net_pnl', 0) or 0)
                growth.append({
                    'date': t.get('exit_date', ''),
                    'equity': round(start_capital + cum_pnl, 2)
                })
            # Final point = current portfolio value if known
            if data.get('net_portfolio_value'):
                if growth and growth[-1]['date'] != today_str:
                    growth.append({'date': today_str, 'equity': round(data['net_portfolio_value'], 2)})
                elif not growth:
                    growth.append({'date': today_str, 'equity': round(data['net_portfolio_value'], 2)})
            data['portfolio_growth'] = growth

            # Sharpe / Sortino from daily portfolio returns
            if len(growth) >= 2:
                rets = []
                for i in range(1, len(growth)):
                    prev = growth[i - 1]['equity']
                    cur = growth[i]['equity']
                    if prev > 0:
                        rets.append((cur - prev) / prev)
                if rets:
                    mean_r = sum(rets) / len(rets)
                    std = (sum((r - mean_r) ** 2 for r in rets) / len(rets)) ** 0.5
                    downside = [r for r in rets if r < 0]
                    dstd = (sum((r - mean_r) ** 2 for r in downside) / len(downside)) ** 0.5 if downside else 0
                    data['sharpe_ratio'] = round(mean_r / std * (252 ** 0.5), 2) if std > 1e-9 else 0.0
                    data['sortino_ratio'] = round(mean_r / dstd * (252 ** 0.5), 2) if dstd > 1e-9 else 0.0
                else:
                    data['sharpe_ratio'] = 0.0
                    data['sortino_ratio'] = 0.0
            else:
                data['sharpe_ratio'] = 0.0
                data['sortino_ratio'] = 0.0

            data['strategy_stats'] = {
                "avg_win": j_analytics.get('avg_win', 0),
                "avg_loss": j_analytics.get('avg_loss', 0),
                "profit_factor": j_analytics.get('profit_factor', 0),
                "expectancy": round(
                    (data['win_rate'] * j_analytics.get('avg_win', 0)) -
                    ((1 - data['win_rate']) * j_analytics.get('avg_loss', 0)), 2
                ),
                "avg_hold_days": j_analytics.get('avg_hold_days', 0),
                "by_sector": j_analytics.get('by_sector', {}),
                "by_exit_reason": j_analytics.get('by_exit_reason', {}),
                "by_regime": j_analytics.get('by_regime', {}),
                "by_score_bucket": j_analytics.get('by_score_bucket', {}),
                "total_net_pnl": j_analytics.get('total_net_pnl', 0),
                "total_trades": data['total_trades'],
            }
    except Exception:
        pass
    try:
        holdings_raw = kite.holdings()
        holdings = []
        for h in holdings_raw:
            settled = h.get('quantity', 0) or 0
            t1      = h.get('t1_quantity', 0) or 0
            total_qty = settled + t1
            if total_qty <= 0:
                continue   # skip sold/zero-qty entries (IFGLEXPOR, KALYANKJIL etc.)
            h['quantity'] = total_qty  # include T+1 so BPL appears
            holdings.append(h)
        data['holdings'] = holdings
        holdings_value = sum(h.get('quantity', 0) * h.get('last_price', 0) for h in holdings)
        # Also include T+1 positions (kite.positions net) — they settle tomorrow
        t1_value = sum(
            p.get('last_price', p.get('average_price', 0)) * p.get('quantity', 0)
            for p in data.get('positions', [])
            if p.get('_source') != 'holding'  # don't double-count settled holdings
        )
        total_stocks_value = holdings_value + t1_value

        # Same-day CNC sales are not yet settled in kite.margins(); include them in
        # the displayed account balance so the user sees where the money is.
        today_str = now_ist.strftime("%Y-%m-%d")
        unsettled = 0.0
        try:
            if get_store is not None:
                for t in get_store().get_trades(action='SELL', date_from=today_str):
                    unsettled += _safe_float(t.get('net_pnl', 0)) + _safe_float(t.get('invested', 0))
        except Exception:
            pass

        data['holdings_value'] = total_stocks_value
        data['unsettled_proceeds'] = round(unsettled, 2)
        # True portfolio = cash + current market value of all stocks (settled + T+1) + unsettled sales
        data['net_portfolio_value'] = data.get('cash', 0) + total_stocks_value + unsettled
        # account_balance shown in Portfolio tab header = total portfolio value
        data['account_balance'] = data.get('cash', 0) + total_stocks_value + unsettled
    except Exception:
        pass

    # Portfolio health — computed here after account_balance is fully set (cash + holdings)
    try:
        account_value = data.get('account_balance', 0) or data.get('cash', 0)
        peak_value = account_value
        try:
            if get_store is not None:
                _snap = get_store().get_latest_portfolio_snapshot() or {}
                _saved_peak = _snap.get('peak_value', account_value)
                if _snap.get('date', '') == today_str:
                    peak_value = max(_saved_peak, account_value)
                elif _saved_peak and _saved_peak > peak_value:
                    peak_value = _saved_peak
        except Exception:
            pass
        try:
            if get_store is not None:
                get_store().save_portfolio_snapshot({"peak_value": peak_value, "date": today_str})
        except Exception:
            pass
        drawdown = (peak_value - account_value) / peak_value if peak_value > 0 else 0
        data['portfolio_health'] = {
            "account_value": round(account_value, 2),
            "peak_value":    round(peak_value, 2),
            "drawdown_pct":  round(drawdown * 100, 2),
        }
    except Exception:
        pass

    # Portfolio analytics
    try:
        positions = data['positions']
        analytics = {
            "exposure": 0.0,
            "risk": 0.0,
            "potential_profit": 0.0,
            "potential_loss": 0.0,
            "risk_reward": 0.0,
            "portfolio_return": 0.0,
            "best_stock": "—",
            "worst_stock": "—",
            "sector_allocation": {}
        }
        if positions:
            total_exposure = 0.0
            total_risk = 0.0
            total_potential_profit = 0.0
            total_value = 0.0
            best_pct = -999
            worst_pct = 999
            best_stock = "—"
            worst_stock = "—"
            sector_map = {}
            
            for p in positions:
                qty = p.get('quantity', 0)
                avg = p.get('average_price', 0)
                ltp = p.get('last_price', avg)
                exposure = qty * avg
                unrealized_pct = ((ltp - avg) / avg * 100) if avg else 0
                
                total_exposure += exposure
                total_value += qty * ltp
                
                # Use SL/target from position if available, otherwise estimate
                sl = p.get('stop_loss', avg * 0.95)
                target = p.get('target', avg * 1.10)
                risk_per_share = abs(avg - sl)
                reward_per_share = abs(target - avg)
                total_risk += risk_per_share * qty
                total_potential_profit += reward_per_share * qty
                
                if unrealized_pct > best_pct:
                    best_pct = unrealized_pct
                    best_stock = f"{p.get('tradingsymbol', '')} ({unrealized_pct:+.2f}%)"
                if unrealized_pct < worst_pct:
                    worst_pct = unrealized_pct
                    worst_stock = f"{p.get('tradingsymbol', '')} ({unrealized_pct:+.2f}%)"
                
                # Simple sector mapping based on known symbols
                sector = SECTOR_MAP.get(p.get('tradingsymbol', ''), 'Other')
                sector_map[sector] = sector_map.get(sector, 0) + exposure
            
            analytics['exposure'] = total_exposure
            analytics['risk'] = total_risk
            analytics['potential_profit'] = total_potential_profit
            analytics['potential_loss'] = total_risk
            analytics['risk_reward'] = total_potential_profit / total_risk if total_risk else 0
            analytics['portfolio_return'] = ((total_value - total_exposure) / total_exposure * 100) if total_exposure else 0
            analytics['best_stock'] = best_stock
            analytics['worst_stock'] = worst_stock
            if sector_map:
                total = sum(sector_map.values())
                analytics['sector_allocation'] = {k: (v / total * 100) for k, v in sector_map.items()}
        
        data['analytics'] = analytics
    except Exception:
        data['analytics'] = {
            "exposure": 0.0, "risk": 0.0, "potential_profit": 0.0,
            "potential_loss": 0.0, "risk_reward": 0.0, "portfolio_return": 0.0,
            "best_stock": "—", "worst_stock": "—", "sector_allocation": {}
        }

    # Ensure every cross-tab metric is populated, even with single positions or no broker data
    try:
        npv = data.get('net_portfolio_value', 0) or 0
        cash = data.get('cash', 0) or 0
        budget = data.get('budget') or config.TRADING_AMOUNT
        invested = data.get('invested', 0) or 0
        an = data.get('analytics', {})
        exposure = an.get('exposure', 0) or 0
        positions = data.get('positions', [])
        holdings = data.get('holdings', [])
        unrealized = sum(
            float(p.get('unrealised', p.get('unrealized', 0)) or 0)
            for p in positions
        ) + sum(
            float(h.get('unrealised', h.get('unrealized', h.get('pnl', 0))) or 0)
            for h in holdings
        )
        data['buying_power'] = cash
        # Keep capital-used % consistent with the Portfolio tab (invested / budget)
        data['capital_used_pct'] = round((invested / budget * 100), 2) if budget else 0.0
        data['exposure_pct'] = round((exposure / npv * 100), 2) if npv else 0.0
        data['unrealized_pnl'] = round(unrealized, 2)
        # Portfolio return: align with Portfolio tab (return on invested capital)
        pf_ret = an.get('portfolio_return')
        if pf_ret is None and invested:
            pf_ret = (unrealized / invested) * 100
        data['portfolio_return'] = round(pf_ret or 0.0, 2)
        # Realized P&L from today's completed SELL orders
        if 'realized_pnl' not in data:
            try:
                data['realized_pnl'] = round(sum(
                    float(o.get('pnl', 0) or 0)
                    for o in data.get('all_orders', [])
                    if str(o.get('transaction_type', '')).upper() == 'SELL'
                    and str(o.get('status', '')).upper() == 'COMPLETE'
                    and str(o.get('order_timestamp', ''))[:10] == today_str
                ), 2)
            except Exception:
                data['realized_pnl'] = 0.0
    except Exception:
        pass

    # ── Morning report: trigger once per day from 9 AM ───────────────────────
    _maybe_trigger_morning_report()

    # ── Signals: read from background cache, trigger scan if stale ───────────
    _maybe_trigger_background_scan()
    with _SIGNAL_CACHE_LOCK:
        data['signals']         = _SIGNAL_CACHE["signals"]
        data['recommendations'] = _SIGNAL_CACHE["recommendations"]
        data['stocks_scanned']  = _SIGNAL_CACHE["stocks_scanned"]
        data['scan_running']    = _SIGNAL_CACHE["scanning"]
        _cache_ts = _SIGNAL_CACHE["timestamp"]
    data['last_scan'] = _cache_ts.strftime('%I:%M %p') if _cache_ts else '—'
    data['next_scan'] = (
        (_cache_ts + _SIGNAL_CACHE_TTL).strftime('%I:%M %p') if _cache_ts else '—'
    )

    # ── IP status — served from cache, refreshed every 5 min in background ──
    import socket as _sock
    _ip_file      = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'last_known_ip.txt')
    _ip_hist_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'ip_history.json')

    _maybe_refresh_ip()   # triggers background refresh if cache is stale (non-blocking)
    with _IP_CACHE_LOCK:
        current_ip   = _IP_CACHE["ipv4"]
        current_ipv6 = _IP_CACHE["ipv6"]

    # Fallback: synchronous IP fetch if background cache hasn't populated yet
    if current_ip == 'unknown':
        try:
            import urllib.request as _ur
            import ssl as _ssl
            _ctx = _ssl._create_unverified_context()
            for _url in ('https://api.ipify.org','https://ifconfig.me/ip','https://icanhazip.com'):
                try:
                    _raw = _ur.urlopen(_url, timeout=4, context=_ctx).read().decode().strip()
                    if _raw and '.' in _raw and len(_raw) < 20:
                        current_ip = _raw
                        break
                except Exception:
                    continue
        except Exception:
            pass

    # Network interface type (WiFi / Ethernet / VPN / unknown)
    try:
        if _HAS_PSUTIL:
            _ifaces = _psutil.net_if_stats()
            _addrs  = _psutil.net_if_addrs()
            _active = [i for i, s in _ifaces.items() if s.isup and i not in ('lo', 'lo0')]
            def _iface_type(name):
                n = name.lower()
                if any(x in n for x in ('en0', 'en1', 'wlan', 'wifi', 'wi-fi', 'wlp')):
                    return 'WiFi'
                if any(x in n for x in ('eth', 'en2', 'ens', 'enp', 'lan')):
                    return 'Ethernet'
                if any(x in n for x in ('tun', 'tap', 'utun', 'vpn', 'wg', 'ts')):
                    return 'VPN/Tailscale'
                return None
            _types = [_iface_type(i) for i in _active if _iface_type(i)]
            network_type = _types[0] if _types else 'Unknown'
        else:
            network_type = 'Unknown'
    except Exception:
        network_type = 'Unknown'

    # Kite API resolution — does api.kite.trade resolve to IPv4?
    try:
        _res = _sock.getaddrinfo('api.kite.trade', 443, _sock.AF_INET)
        kite_resolution = 'IPv4 ✅' if _res else 'Unresolved ⚠️'
    except Exception:
        kite_resolution = 'Unresolved ⚠️'

    # Last successful API call time (from health heartbeat)
    with _HEALTH_LOCK:
        _last_hb = _HEALTH.get('last_heartbeat', '')
        _kite_ok  = _HEALTH.get('kite_ok', False)
        _api_lat  = _HEALTH.get('api_latency_ms', 0)
    last_api_call = _last_hb[:19].replace('T', ' ') if _last_hb else '—'

    # Last successful order time (from trade journal — newest BUY or SELL)
    last_order_time = '—'
    try:
        if get_store is not None:
            _journal_file = get_store().all_trades()
            if _journal_file:
                _newest = max(_journal_file, key=lambda e: e.get('timestamp', ''))
                last_order_time = _newest.get('timestamp', '—')[:16].replace('T', ' ')
    except Exception:
        last_order_time = '—'

    # Known (whitelisted) IP
    try:
        known_ip = open(_ip_file).read().strip() if os.path.exists(_ip_file) else current_ip
    except Exception:
        known_ip = current_ip

    ip_changed = current_ip != known_ip and current_ip != 'unknown'

    # Overall trading status
    if current_ip == 'unknown':
        trading_status = 'NETWORK ERROR'
        trading_status_color = '#ef4444'
    elif ip_changed:
        trading_status = 'IP MISMATCH — ORDERS BLOCKED'
        trading_status_color = '#f59e0b'
    elif not _kite_ok:
        trading_status = 'KITE DISCONNECTED'
        trading_status_color = '#f59e0b'
    else:
        trading_status = 'SAFE ✅'
        trading_status_color = '#22c55e'

    # Update stored IP and history
    try:
        with open(_ip_file, 'w') as _f: _f.write(current_ip)
        _hist = []
        if os.path.exists(_ip_hist_file):
            _hist = json.load(open(_ip_hist_file))
        if not _hist or _hist[0].get('ip') != current_ip:
            _hist.insert(0, {
                'ip':          current_ip,
                'detected_at': datetime.now(IST).strftime('%Y-%m-%d %H:%M:%S'),
                'changed':     ip_changed,
                'network':     network_type,
            })
            _hist = _hist[:20]
            json.dump(_hist, open(_ip_hist_file, 'w'), indent=2)
    except Exception:
        _hist = []

    data['current_ip']          = current_ip
    data['current_ipv6']        = current_ipv6
    data['known_ip']            = known_ip
    data['ip_changed']          = ip_changed
    data['ip_history']          = _hist[:10]
    data['network_type']        = network_type
    data['kite_resolution']     = kite_resolution
    data['last_api_call']       = last_api_call
    data['last_order_time']     = last_order_time
    data['api_latency_ms']      = _api_lat
    data['trading_status']      = trading_status
    data['trading_status_color']= trading_status_color
    data['kite_whitelist_url']  = 'https://developers.kite.trade/profile'
    data['kite_login_url']      = _kite_login_url()

    try:
        from market_data import MarketDataFetcher
        data['market_data_metrics'] = MarketDataFetcher.load_cycle_metrics()
    except Exception:
        pass

    # Portfolio heat map (sector exposure, open positions, regime limits)
    try:
        from risk_manager import RiskManager
        data['portfolio_heat'] = RiskManager().get_portfolio_heat()
    except Exception:
        data['portfolio_heat'] = {
            'total_value': 0,
            'exposure_by_sector': {},
            'open_positions': 0,
            'regime_limits': {},
            'max_sector_exposure_pct': 0.3
        }

    # Trade Lifecycle summary
    try:
        from risk_manager import RiskManager
        from trade_lifecycle_manager import TradeLifecycleManager
        _prices = {p.get('tradingsymbol'): p.get('last_price', 0) for p in all_positions}
        _rm = RiskManager()
        _ltm = TradeLifecycleManager()
        data['lifecycle_positions'] = _ltm.get_lifecycle_summary(_rm.positions, _prices)
    except Exception:
        data['lifecycle_positions'] = []

    # Market breadth snapshot (from store; computed separately)
    try:
        if get_store is not None:
            data['market_breadth'] = get_store().get_latest_market_breadth() or {}
    except Exception:
        data['market_breadth'] = {}

    # Sector rotation snapshot (from store; computed separately)
    try:
        if get_store is not None:
            data['sector_rotation'] = get_store().get_latest_sector_rotation() or {}
    except Exception:
        data['sector_rotation'] = {}

    # Market-open guard for messaging (09:15-15:30 IST, Mon-Fri)
    _wd = now_ist.weekday()
    _hr, _min = now_ist.hour, now_ist.minute
    market_open = (
        _wd < 5
        and (_hr > 9 or (_hr == 9 and _min >= 15))
        and (_hr < 15 or (_hr == 15 and _min <= 30))
    )
    _status_unavailable = 'Market Closed' if not market_open else 'Awaiting Data'

    # India VIX risk snapshot (computed live from Kite)
    try:
        _vix = None
        for _vix_key in ("NSE:INDIA VIX", "NSE:INDIAVIX"):
            try:
                _q = kite.ltp([_vix_key])
                if _q and _vix_key in _q and _q[_vix_key].get('last_price'):
                    _p = float(_q[_vix_key]['last_price'])
                    if _p > 0:
                        _vix = _p
                        break
            except Exception:
                continue
        if _vix is not None:
            from vix_risk_engine import IndiaVIXRiskEngine
            data['vix_risk'] = {
                'timestamp': now_ist.isoformat(),
                'vix': round(_vix, 2),
                'volatility_score': round(min(100.0, _vix * 2.5), 2),
                'risk_factor': IndiaVIXRiskEngine.risk_factor_from_vix(_vix),
                'risk_level': IndiaVIXRiskEngine.risk_level(_vix),
            }
        else:
            data['vix_risk'] = {'status': _status_unavailable, 'reason': 'Could not fetch India VIX'}
    except Exception as _vix_err:
        data['vix_risk'] = {'status': _status_unavailable, 'reason': 'VIX fetch error'}

    # FII/DII institutional flow snapshot (computed live from NSE)
    try:
        from fii_dii import FII_DII_Engine
        _fd_engine = FII_DII_Engine(store=get_store() if get_store is not None else None)
        _fd = _fd_engine.compute(refresh=True)
        if _fd.get('reason'):
            data['fii_dii'] = {
                'status': _status_unavailable,
                'reason': _fd.get('reason'),
                'fii_net': None,
                'dii_net': None,
                'net_flow': None,
                'sentiment': None,
            }
        else:
            data['fii_dii'] = _fd
    except Exception as _fd_err:
        data['fii_dii'] = {'status': _status_unavailable, 'reason': 'FII/DII fetch error'}

    # Options chain intelligence snapshot (from store; computed separately)
    try:
        if get_store is not None:
            data['options_intelligence'] = get_store().get_latest_options_intelligence() or {}
    except Exception:
        data['options_intelligence'] = {}

    # Economic event risk (computed at runtime from the event calendar)
    try:
        from economic_events import EconomicEventRiskEngine
        data['economic_event_risk'] = EconomicEventRiskEngine().risk_status()
    except Exception:
        data['economic_event_risk'] = {}

    # Global market snapshot (from store; computed separately)
    try:
        if get_store is not None:
            data['global_markets'] = get_store().get_latest_global_markets() or {}
    except Exception:
        data['global_markets'] = {}

    resp = jsonify(data)
    resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    return resp


@app.route('/api/market/breadth')
def api_market_breadth():
    """Latest market breadth snapshot."""
    try:
        if get_store is not None:
            snap = get_store().get_latest_market_breadth()
            if snap:
                return jsonify({**snap, 'ok': True})
    except Exception as e:
        logger.error(f"Market breadth API error: {e}")
    return jsonify({'ok': False, 'advance': 0, 'decline': 0, 'ad_ratio': 0.0,
                    'above20': 0, 'above50': 0, 'above200': 0,
                    'breadth_score': 0, 'market_strength': 'NEUTRAL'})


@app.route('/api/start-token-server', methods=['POST'])
def api_start_token_server():
    """
    Start the Kite token receiver server on port 8080 (if not already running)
    and return the Kite login URL. The frontend can then open the URL in a new tab
    so the user is redirected back to localhost:8080 after login without any
    manual script startup.
    """
    result = _ensure_token_server()
    return jsonify(result)


@app.route('/api/ask', methods=['POST'])
def api_ask():
    """AI Chat endpoint — local-first engine, GPT optional."""
    try:
        from flask import request as freq
        from config import config
        from trade_journal import TradeJournal

        question = (freq.get_json(force=True) or {}).get('question', '').strip()
        if not question:
            return jsonify({'error': 'Empty question'})

        q = question.lower()

        # ── Load journal data ────────────────────────────────────────────────
        journal = TradeJournal()
        entries  = journal.all_entries()
        analytics = journal.analytics()

        closed     = [e for e in entries if e.get('status') == 'CLOSED']
        open_pos   = [e for e in entries if e.get('status') == 'OPEN']

        # ── Live market regime ───────────────────────────────────────────────
        regime = 'UNKNOWN'
        try:
            from market_regime import MarketRegimeDetector
            from market_data import MarketDataFetcher
            regime = MarketRegimeDetector(kite=MarketDataFetcher().kite).detect_regime()
        except Exception:
            pass

        # ── Helper: find most recent trade for a symbol ──────────────────────
        def find_trade(sym):
            sym = sym.upper()
            matches = [e for e in reversed(entries) if e.get('symbol','').upper() == sym]
            return matches[0] if matches else None

        # ── Helper: extract symbol from question ─────────────────────────────
        def extract_symbol():
            for e in entries:
                sym = e.get('symbol', '')
                if sym and sym.lower() in q:
                    return sym.upper()
            # also check common names
            name_map = {'reliance':'RELIANCE','bel':'BEL','tcs':'TCS','infy':'INFY',
                        'sbin':'SBIN','hdfc':'HDFCBANK','icici':'ICICIBANK',
                        'wipro':'WIPRO','itc':'ITC','ongc':'ONGC'}
            for k,v in name_map.items():
                if k in q:
                    return v
            return None

        # ════════════════════════════════════════════════════════════════════
        # LOCAL ANSWER ENGINE
        # ════════════════════════════════════════════════════════════════════

        # 1) WHY DID WE BUY <symbol>?
        if any(w in q for w in ['why did we buy','why buy','why bought','reason for buy','why we bought']):
            sym = extract_symbol()
            trade = find_trade(sym) if sym else (open_pos[-1] if open_pos else None)
            if not trade:
                return jsonify({'answer': f'No BUY trade found{"for " + sym if sym else ""}. No trades in journal yet.',
                                'bullets': ['Journal is empty or symbol not found']})
            rsi  = trade.get('rsi', 0) or 0
            macd = trade.get('macd_histogram', 0) or 0
            conf = (trade.get('confidence', 0) or 0) * 100
            score = trade.get('trade_score', 0) or 0
            reg  = trade.get('market_regime', 'UNKNOWN')
            senti = (trade.get('sentiment') or 'NEUTRAL').upper()
            reason = trade.get('buy_reason') or trade.get('reasoning') or 'Technical signal triggered'
            price = trade.get('entry_price', 0)
            return jsonify({
                'answer': f"We bought {trade['symbol']} at ₹{price:.2f} because the trade scored {score}/100 with {conf:.0f}% confidence in a {reg} market. {reason[:120]}",
                'action': 'BUY',
                'trade_score': score,
                'regime': reg,
                'indicators': {
                    'RSI':    {'value': round(rsi,1),  'signal': 'bullish' if rsi < 60 else 'bearish', 'detail': f'RSI={rsi:.1f}'},
                    'MACD':   {'value': round(macd,3), 'signal': 'bullish' if macd > 0 else 'bearish', 'detail': f'Histogram={macd:.3f}'},
                    'Volume': {'value': round(trade.get('volume_ratio',1),2), 'signal': 'bullish' if (trade.get('volume_ratio') or 1)>1 else 'neutral', 'detail': 'vs 20d avg'},
                },
                'sentiment': senti,
                'sentiment_score': trade.get('sentiment_score', 0),
                'bullets': [
                    f"Entry: ₹{price:.2f} | Score: {score}/100 | Confidence: {conf:.0f}%",
                    f"RSI: {rsi:.1f} | MACD histogram: {macd:.3f}",
                    f"Market regime: {reg} | Sentiment: {senti}",
                    f"Sector: {trade.get('sector','Unknown')} | MTF aligned: {trade.get('mtf_aligned', '?')}",
                ]
            })

        # 2) WHY DID WE SELL <symbol>?
        if any(w in q for w in ['why did we sell','why sell','why sold','exit reason','why we sold','why exit']):
            sym = extract_symbol()
            trade = find_trade(sym) if sym else (closed[-1] if closed else None)
            if not trade:
                return jsonify({'answer': 'No closed trade found. No exits in journal yet.',
                                'bullets': ['No closed trades in journal']})
            exit_r = trade.get('exit_reason') or 'Unknown exit trigger'
            entry  = trade.get('entry_price', 0)
            exit_p = trade.get('exit_price', 0) or 0
            net    = trade.get('net_pnl', 0) or 0
            pct    = ((exit_p - entry) / entry * 100) if entry else 0
            return jsonify({
                'answer': f"We exited {trade['symbol']} at ₹{exit_p:.2f} (entry ₹{entry:.2f}, {pct:+.1f}%). Exit trigger: {exit_r}. Net P&L: ₹{net:.2f}.",
                'action': 'SELL',
                'exit_reasons': [exit_r],
                'expected_return': round(pct, 2),
                'bullets': [
                    f"Entry: ₹{entry:.2f} → Exit: ₹{exit_p:.2f} ({pct:+.1f}%)",
                    f"Net P&L after charges: ₹{net:.2f}",
                    f"Exit trigger: {exit_r}",
                ]
            })

        # 3) MARKET REGIME
        if any(w in q for w in ['market regime','regime','bull','bear','sideways','market condition']):
            col = 'BULL' if regime == 'BULL' else ('BEAR' if regime == 'BEAR' else 'SIDEWAYS')
            desc = {'BULL': 'Nifty is above 50-DMA and trending up — bot is actively buying.',
                    'BEAR': 'Nifty is below 50-DMA — bot is NOT buying new positions, only exiting.',
                    'SIDEWAYS': 'Nifty is ranging — bot buys only highest-quality signals (score ≥70).',
                    'UNKNOWN': 'Regime could not be determined from Nifty data.'}.get(col, '')
            return jsonify({
                'answer': f"Current market regime is {regime}. {desc}",
                'regime': col,
                'bullets': [
                    f"Regime: {regime}",
                    desc,
                    'Bot pauses new BUYs only in BEAR regime.',
                    'Regime is re-checked every 15 minutes.',
                ]
            })

        # 4) WIN RATE / P&L SUMMARY
        if any(w in q for w in ['win rate','winrate','p&l','pnl','profit','summary','performance','how are we doing']):
            total  = analytics.get('total_trades', 0)
            wins   = analytics.get('winning_trades', 0)
            wr     = analytics.get('win_rate', 0)
            net    = analytics.get('total_net_pnl', 0)
            avg_w  = analytics.get('avg_win', 0)
            avg_l  = analytics.get('avg_loss', 0)
            pf     = analytics.get('profit_factor', 0)
            avg_sc = analytics.get('avg_score', 0)
            open_c = len(open_pos)
            if total == 0:
                return jsonify({'answer': 'No closed trades yet. The bot is still in early trading — check back after the first exits.',
                                'bullets': [f'Open positions: {open_c}', 'No closed trades to analyse yet']})
            return jsonify({
                'answer': f"Out of {total} closed trades, {wins} were winners — {wr:.1f}% win rate. Total net P&L: ₹{net:.2f}. Profit factor: {pf:.2f}x.",
                'bullets': [
                    f"Total closed trades: {total} | Win rate: {wr:.1f}%",
                    f"Avg win: ₹{avg_w:.2f} | Avg loss: ₹{avg_l:.2f}",
                    f"Profit factor: {pf:.2f}x | Avg score: {avg_sc:.0f}/100",
                    f"Open positions: {open_c} | Regime: {regime}",
                ]
            })

        # 5) OPEN POSITIONS
        if any(w in q for w in ['open position','current position','holding','what do we hold','what stocks']):
            if not open_pos:
                return jsonify({'answer': 'No open positions right now. The bot is waiting for high-quality BUY signals.',
                                'bullets': ['0 open positions', f'Market regime: {regime}', 'Bot scans 100 stocks every 15 min']})
            lines = []
            for p in open_pos:
                lines.append(f"{p['symbol']} — {p.get('quantity','?')} shares @ ₹{p.get('entry_price',0):.2f} | SL: ₹{p.get('stop_loss',0):.2f} | Target: ₹{p.get('target',0):.2f}")
            return jsonify({
                'answer': f"Currently holding {len(open_pos)} position(s): {', '.join(p['symbol'] for p in open_pos)}.",
                'bullets': lines
            })

        # 6) SECTOR PERFORMANCE
        if any(w in q for w in ['sector','industry','best sector','performing']):
            from collections import defaultdict
            sec_pnl = defaultdict(float)
            sec_cnt = defaultdict(int)
            for t in closed:
                sec = t.get('sector', 'Other')
                sec_pnl[sec] += t.get('net_pnl', 0) or 0
                sec_cnt[sec] += 1
            if not sec_pnl:
                return jsonify({'answer': 'No closed trades yet to rank sectors.',
                                'bullets': ['Trade more to see sector performance']})
            best = max(sec_pnl, key=sec_pnl.get)
            worst = min(sec_pnl, key=sec_pnl.get)
            bullets = [f"{s}: ₹{p:.2f} ({sec_cnt[s]} trades)" for s,p in sorted(sec_pnl.items(), key=lambda x:-x[1])]
            return jsonify({
                'answer': f"Best performing sector: {best} (₹{sec_pnl[best]:.2f}). Worst: {worst} (₹{sec_pnl[worst]:.2f}).",
                'bullets': bullets[:6]
            })

        # 7) SKIPPED TRADE
        if any(w in q for w in ['skipped','skip','not bought','why not','why was','rejected']):
            sym = extract_symbol()
            return jsonify({
                'answer': f"Trades are skipped if they fail any of the 7 filters: duplicate position, correlated sector, negative news, trade score <70/100, multi-timeframe not aligned, capital limit, or confidence <52%.",
                'bullets': [
                    '1. Already holding the stock',
                    '2. Same sector as existing position (correlation guard)',
                    '3. Negative news detected (fraud/SEBI/loss keywords)',
                    '4. Trade score < 70/100',
                    '5. Multi-timeframe not aligned (Daily+1H+15m)',
                    '6. Capital limit reached (95% deployed)',
                    '7. Confidence < 52% or R:R < 1:1',
                ]
            })

        # 8) INDICATORS
        if any(w in q for w in ['indicator','rsi','macd','volume','technical','which indicator']):
            if not closed:
                return jsonify({'answer': 'No closed trades yet to analyse indicators.',
                                'bullets': ['Trade more to see indicator stats']})
            bull_rsi = [t for t in closed if (t.get('rsi') or 0) < 60 and (t.get('net_pnl') or 0) > 0]
            bull_macd = [t for t in closed if (t.get('macd_histogram') or 0) > 0 and (t.get('net_pnl') or 0) > 0]
            return jsonify({
                'answer': f"RSI <60 at entry → {len(bull_rsi)}/{len(closed)} profitable. Positive MACD histogram at entry → {len(bull_macd)}/{len(closed)} profitable.",
                'bullets': [
                    f"RSI <60 at entry: {len(bull_rsi)} wins / {len(closed)} total",
                    f"Positive MACD histogram: {len(bull_macd)} wins / {len(closed)} total",
                    'Volume ratio >1 = above average volume (bullish confirmation)',
                    'MTF alignment = Daily UPTREND + 1H not DOWNTREND + 15m OK',
                ]
            })

        # ── FALLBACK: try GPT if quota available, else generic answer ────────
        try:
            import openai
            if not config.OPENAI_API_KEY:
                raise ValueError('No API key')
            client = openai.OpenAI(api_key=config.OPENAI_API_KEY)

            def fmt_trade(t):
                return (f"{t.get('action','?')} {t.get('symbol','?')} @ ₹{t.get('entry_price','?')} "
                        f"score={t.get('trade_score','?')} regime={t.get('market_regime','?')} "
                        f"rsi={t.get('rsi','?')} macd={t.get('macd_histogram','?')} "
                        f"conf={t.get('confidence','?')} senti={t.get('sentiment','?')} "
                        f"reason='{t.get('buy_reason','')}' exit='{t.get('exit_reason','')}' "
                        f"pnl=₹{t.get('net_pnl',0)}")

            ctx = (f"Regime: {regime}\n"
                   f"Open: {chr(10).join(fmt_trade(t) for t in open_pos) or 'none'}\n"
                   f"Closed (last 5): {chr(10).join(fmt_trade(t) for t in closed[-5:]) or 'none'}\n"
                   f"Stats: win_rate={analytics.get('win_rate',0)*100:.1f}% pnl=₹{analytics.get('total_net_pnl',0)} trades={analytics.get('total_trades',0)}")

            resp = client.chat.completions.create(
                model='gpt-4o-mini',
                messages=[
                    {'role': 'system', 'content': 'You are an AI assistant for an NSE swing trading bot. Answer in plain English. Return JSON with keys: answer, bullets (list). Do not use markdown fences.'},
                    {'role': 'user',   'content': f"Context:\n{ctx}\n\nQuestion: {question}"},
                ],
                temperature=0.3,
                max_tokens=400,
            )
            raw = resp.choices[0].message.content.strip()
            try:
                result = json.loads(raw)
            except Exception:
                result = {'answer': raw}
            return jsonify(result)

        except Exception as gpt_err:
            err_str = str(gpt_err)
            if 'quota' in err_str or 'insufficient' in err_str or '429' in err_str:
                hint = 'OpenAI quota exceeded — add credits at platform.openai.com/billing. Local answers above still work.'
            else:
                hint = f'Could not reach GPT: {err_str[:80]}'
            # Still give a useful generic local answer
            return jsonify({
                'answer': f"I couldn't find a specific answer for that question in my local data. {hint}",
                'bullets': [
                    f"Open positions: {len(open_pos)} ({', '.join(p['symbol'] for p in open_pos) or 'none'})",
                    f"Closed trades: {analytics.get('total_trades',0)} | Win rate: {analytics.get('win_rate',0)*100:.1f}%",
                    f"Net P&L: ₹{analytics.get('total_net_pnl',0):.2f} | Regime: {regime}",
                    hint,
                ]
            })

    except Exception as e:
        return jsonify({'error': f'Server error: {str(e)}'})


@app.route('/api/journal')
def api_journal():
    try:
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))
        from trade_journal import TradeJournal
        j = TradeJournal()

        # ── Sync Kite completed SELL orders → close matching open journal entries ──
        try:
            from broker_integration import BrokerIntegration
            _b = BrokerIntegration()
            kite_orders = _b.kite.orders() or [] if _b.kite else []
            entries = j.all_entries()
            changed = False
            for ko in kite_orders:
                if (ko.get('transaction_type') == 'SELL'
                        and ko.get('status') == 'COMPLETE'
                        and ko.get('average_price', 0) > 0):
                    ksym = ko.get('tradingsymbol', '')
                    kprice = float(ko.get('average_price', 0))
                    kqty   = int(ko.get('quantity', 1))
                    ktime  = str(ko.get('order_timestamp', ''))
                    # Find matching open BUY entry
                    for e in entries:
                        if (e.get('symbol') == ksym
                                and e.get('action') == 'BUY'
                                and e.get('status') == 'OPEN'):
                            buy_price = float(e.get('entry_price') or e.get('price') or 0)
                            gross_pnl = round((kprice - buy_price) * kqty, 2) if buy_price else 0
                            buy_ts = e.get('timestamp', e.get('date', ''))
                            try:
                                _bt = datetime.fromisoformat(str(buy_ts).replace('Z', '+00:00')[:23] if str(buy_ts).endswith('Z') else str(buy_ts))
                                _kt = datetime.fromisoformat(ktime.replace(' ', 'T')) if ' ' in ktime else datetime.fromisoformat(str(ktime).replace('Z', '+00:00'))
                                _delta = _kt - _bt
                                _hh = round(_delta.total_seconds() / 3600, 1)
                                _dd = round(_delta.total_seconds() / 86400, 2)
                            except Exception:
                                _hh, _dd = 0, 0
                            e['exit_price']    = kprice
                            e['exit_date']     = ktime[:10] if ktime else ''
                            e['exit_reason']   = 'Broker SELL'
                            e['gross_pnl']     = gross_pnl
                            e['net_pnl']       = gross_pnl
                            e['holding_hours'] = _hh
                            e['holding_days']  = _dd
                            e['status']        = 'CLOSED'
                            if e.get('id'):
                                j._store.update_trade(e['id'], e)
                            break

            # Back-fill holding time for pre-existing CLOSED BUY rows that were closed by SELL entries
            for e in entries:
                if e.get('action') == 'BUY' and e.get('status') == 'CLOSED':
                    _changed = False
                    needs_holding = not e.get('holding_hours') or (e.get('holding_days') is not None and e['holding_days'] < 0)
                    if needs_holding and e.get('timestamp') and e.get('exit_date'):
                        try:
                            buy_ts = e['timestamp']
                            bt = datetime.fromisoformat(str(buy_ts).replace('Z', '+00:00'))
                            sell_ts = None
                            for s in entries:
                                if (s.get('action') == 'SELL' and s.get('symbol') == e.get('symbol')
                                        and s.get('entry_date') == e.get('date')):
                                    sell_ts = s.get('timestamp')
                                    break
                            end = sell_ts or e['exit_date']
                            et = datetime.fromisoformat(str(end).replace('Z', '+00:00')) if 'T' in str(end) else datetime.strptime(str(end), '%Y-%m-%d')
                            secs = max(0, (et - bt).total_seconds())
                            e['holding_hours'] = round(secs / 3600, 1)
                            e['holding_days'] = round(secs / 86400, 2)
                            _changed = True
                        except Exception:
                            pass
                    if e.get('exit_reason') == 'kite_order':
                        e['exit_reason'] = 'Broker SELL'
                        _changed = True
                    if _changed and e.get('id'):
                        j._store.update_trade(e['id'], e)

            # Fix legacy market_regime values that were accidentally stored as numeric scores
            for e in entries:
                mr = e.get('market_regime')
                if not isinstance(mr, str) or not mr or mr.replace('.', '', 1).isdigit():
                    if e.get('id'):
                        j._store.update_trade(e['id'], {'market_regime': 'UNKNOWN'})

            # Merge duplicate open BUY rows for the same symbol into a single open row
            open_buys = [e for e in entries if e.get('action') == 'BUY' and e.get('status') == 'OPEN']
            by_sym = {}
            for e in open_buys:
                by_sym.setdefault(e.get('symbol', ''), []).append(e)
            for sym, sym_entries in by_sym.items():
                if len(sym_entries) <= 1:
                    continue
                sym_entries.sort(key=lambda x: x.get('timestamp', ''))
                base = sym_entries[0]
                total_qty = sum(int(e.get('quantity', 0) or 0) for e in sym_entries)
                total_cost = sum(float(e.get('entry_price', 0) or 0) * int(e.get('quantity', 0) or 0) for e in sym_entries)
                avg_px = round(total_cost / total_qty, 2) if total_qty else float(base.get('entry_price', 0) or 0)
                base_updates = {
                    'quantity': total_qty,
                    'entry_price': avg_px,
                    'invested': round(avg_px * total_qty, 2),
                }
                # Backfill missing/unknown snapshot fields from the latest entry
                latest = sym_entries[-1]
                _empty_vals = {None, '', 0, 'UNKNOWN', 'Unknown', 'Other', 'NEUTRAL', ' '}
                for field in ['market_regime', 'sector', 'trade_score', 'buy_reason', 'trend', 'rsi', 'volume_ratio', 'confidence']:
                    base_val = base.get(field)
                    latest_val = latest.get(field)
                    if latest_val and (base_val in _empty_vals or (field == 'trade_score' and base_val == 0)):
                        base_updates[field] = latest_val
                if base.get('id'):
                    j._store.update_trade(base['id'], base_updates)
                for extra in sym_entries[1:]:
                    if extra.get('id'):
                        j._store.update_trade(extra['id'], {
                            'status': 'MERGED',
                            'quantity': 0,
                            'net_pnl': 0,
                            'gross_pnl': 0,
                            'exit_reason': 'Merged into sibling open position',
                            'exit_date': datetime.now().strftime('%Y-%m-%d'),
                        })
        except Exception as _se:
            logger.warning(f"Journal Kite sync error: {_se}")

        analytics = j.analytics()
        all_entries = j.all_entries()
        open_trades  = [e for e in all_entries if e.get('action') == 'BUY' and e.get('status') == 'OPEN']
        closed_trades = [e for e in all_entries if e.get('status') == 'CLOSED' and e.get('action') == 'BUY']
        analytics['open_trades_count']  = len(open_trades)
        analytics['closed_trades_count'] = len(closed_trades)
        analytics['open_trade_log']     = sorted(open_trades,  key=lambda x: x.get('timestamp',''), reverse=True)[:20]
        analytics['closed_trade_log']   = sorted(closed_trades, key=lambda x: x.get('exit_date',''), reverse=True)[:20]
        # Trade log (Last 20) shows BUY rows only, newest first — SELL rows are internal entries, MERGED rows are hidden
        buy_entries = [e for e in all_entries if e.get('action') == 'BUY' and e.get('status') != 'MERGED']
        analytics['recent_trades']      = sorted(
            buy_entries,
            key=lambda x: x.get('timestamp', x.get('exit_date', '')),
            reverse=True
        )[:20]
        analytics['all_entries_count']  = len(all_entries)
        return jsonify(analytics)
    except Exception as e:
        import traceback
        logger.warning(f"api/journal error: {e}\n{traceback.format_exc()}")
        return jsonify({'error': str(e), 'total_trades': 0, 'open_trades_count': 0, 'open_trade_log': []})


@app.route('/api/skipped-opportunities')
def api_skipped_opportunities():
    """Get today's skipped opportunities with detailed rejection reasons"""
    try:
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))
        from decision_logger import DecisionLogger
        
        logger = DecisionLogger()
        # Always rebuild from the live trading log so the dashboard reflects the latest cycle
        logger.decisions_today = logger._parse_log_decisions()
        # Remove repeated (symbol, final_decision) records, keeping the most recent one
        seen_decisions = {}
        for d in reversed(logger.decisions_today):
            seen_decisions[(d.symbol, d.final_decision)] = d
        logger.decisions_today = list(seen_decisions.values())
        skipped = logger.get_skipped_opportunities()
        summary = logger.get_decision_summary()

        # Align "Executed" with the rest of the dashboard: count actual completed BUY orders today.
        # The decision log's BUY records are *intended* orders; use broker/journal truth when available.
        today_str = datetime.now().strftime('%Y-%m-%d')
        try:
            from broker_integration import BrokerIntegration
            broker = BrokerIntegration()
            kite_orders = broker.kite.orders() if broker.kite else []
            if kite_orders:
                summary['executed'] = len([
                    o for o in kite_orders
                    if o.get('transaction_type') == 'BUY'
                    and o.get('status', '').upper() == 'COMPLETE'
                    and str(o.get('order_timestamp', ''))[:10] == today_str
                ])
                summary['executed_source'] = 'broker'
            else:
                raise RuntimeError('No broker orders available')
        except Exception:
            # Fallback to journal BUY entries for today if broker is unreachable
            from trade_journal import TradeJournal
            j = TradeJournal()
            summary['executed'] = len([
                e for e in j.all_entries()
                if e.get('action') == 'BUY'
                and e.get('status') != 'MERGED'
                and e.get('date') == today_str
            ])
            summary['executed_source'] = 'journal'

        # Convert to JSON-serializable format
        skipped_data = []
        for decision in skipped:
            skipped_data.append({
                'symbol': decision.symbol,
                'timestamp': decision.timestamp,
                'overall_score': decision.overall_score,
                'confidence': decision.confidence,
                'technical_score': decision.technical_score,
                'news_sentiment_score': decision.news_sentiment_score,
                'sector_strength': decision.sector_strength,
                'market_regime': decision.market_regime,
                'risk_reward_ratio': decision.risk_reward_ratio,
                'position_size_calculated': decision.position_size_calculated,
                'available_cash': decision.available_cash,
                'current_open_positions': decision.current_open_positions,
                'existing_holdings': decision.existing_holdings,
                'cooldown_status': decision.cooldown_status,
                'portfolio_exposure': decision.portfolio_exposure,
                'max_position_size': decision.max_position_size,
                'final_decision': decision.final_decision,
                'rejection_reason': decision.rejection_reason,
                'detailed_factors': decision.detailed_factors,
                'entry_price': decision.entry_price,
                'stop_loss': decision.stop_loss,
                'target': decision.target,
                'sector': decision.sector
            })
        
        return jsonify({
            'skipped_opportunities': skipped_data,
            'summary': summary,
            'timestamp': datetime.now().isoformat()
        })
    except Exception as e:
        logger.error(f"Skipped opportunities API error: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/explain')
def api_explain():
    """AI explainability panel: why each BUY, SELL, HOLD and SKIP happened."""
    try:
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))
        from decision_logger import DecisionLogger

        actions = []

        # Executed trades from the journal
        journal = []
        try:
            if get_store is not None:
                journal = get_store().all_trades()
        except Exception:
            pass
        if journal:
            for t in journal:
                if t.get('status') == 'CLOSED':
                    actions.append({
                        'timestamp': t.get('timestamp'),
                        'symbol': t.get('symbol'),
                        'action': f"SELL ({t.get('exit_reason', 'closed')})",
                        'reason': t.get('exit_reason', 'Closed'),
                        'confidence': t.get('confidence'),
                        'score': t.get('trade_score'),
                        'pnl': t.get('net_pnl'),
                        'price': t.get('exit_price'),
                        'quantity': t.get('quantity'),
                        'sector': t.get('sector', 'Unknown')
                    })
                if t.get('action') == 'BUY':
                    actions.append({
                        'timestamp': t.get('timestamp'),
                        'symbol': t.get('symbol'),
                        'action': 'BUY',
                        'reason': t.get('buy_reason', 'AI signal'),
                        'confidence': t.get('confidence'),
                        'score': t.get('trade_score'),
                        'pnl': t.get('net_pnl'),
                        'price': t.get('entry_price'),
                        'quantity': t.get('quantity'),
                        'sector': t.get('sector', 'Unknown'),
                        'sub_scores': t.get('score_components', {})
                    })

        # Evaluated-but-skipped opportunities from the live trading log
        dl = DecisionLogger()
        dl.decisions_today = dl._parse_log_decisions()
        for d in dl.decisions_today:
            actions.append({
                'timestamp': d.timestamp,
                'symbol': d.symbol,
                'action': d.final_decision,
                'reason': d.rejection_reason or (
                    f"BUY recorded in trading log | Score {d.overall_score:.0f}/100 | Conf {d.confidence:.0%}"
                    if d.final_decision == 'BUY'
                    else f"Signal evaluated ({d.final_decision}, score {d.overall_score:.0f}, conf {d.confidence:.0%}, rr {d.risk_reward_ratio:.2f})"
                ),
                'confidence': d.confidence,
                'score': d.overall_score,
                'pnl': 0.0,
                'price': d.entry_price,
                'quantity': d.position_size_calculated,
                'sector': d.sector or 'Unknown',
                'sub_scores': d.detailed_factors or {}
            })

        # Deduplicate: keep the first authoritative record per (symbol, action) when iterating backwards;
        # trade-journal entries (added first) are preserved over log-derived duplicates.
        seen_actions = {}
        for a in reversed(actions):
            seen_actions[(a['symbol'], a['action'])] = a
        actions = list(seen_actions.values())

        actions.sort(key=lambda x: x['timestamp'] or '', reverse=True)
        return jsonify({'actions': actions[:100], 'count': len(actions), 'timestamp': datetime.now().isoformat()})
    except Exception as e:
        logger.error(f"Explain API error: {e}")
        return jsonify({'error': str(e), 'actions': []}), 500


@app.route('/api/morning-report')
def api_morning_report():
    """Return cached morning intelligence report; trigger generation if not done today."""
    _maybe_trigger_morning_report()
    with _MORNING_CACHE_LOCK:
        report     = _MORNING_CACHE["report"]
        date       = _MORNING_CACHE["date"]
        generating = _MORNING_CACHE["generating"]
    return jsonify({
        "report":     report or {},
        "date":       date,
        "generating": generating,
    })


@app.route('/api/morning-report/refresh', methods=['POST'])
def api_morning_report_refresh():
    """Force-regenerate morning report (manual trigger from UI)."""
    with _MORNING_CACHE_LOCK:
        already_running = _MORNING_CACHE["generating"]
    if not already_running:
        t = threading.Thread(target=_generate_morning_report, daemon=True)
        t.start()
    return jsonify({"status": "triggered"})


@app.route('/api/health')
def api_health():
    """System health snapshot: CPU, RAM, disk, Kite latency, scan time."""
    with _HEALTH_LOCK:
        h = dict(_HEALTH)
    with _SIGNAL_CACHE_LOCK:
        h["scan_running"]  = _SIGNAL_CACHE["scanning"]
        h["last_scan"]     = _SIGNAL_CACHE["timestamp"].isoformat() if _SIGNAL_CACHE["timestamp"] else None
        h["signals_count"] = len(_SIGNAL_CACHE["signals"])
    with _MORNING_CACHE_LOCK:
        h["morning_ready"]     = _MORNING_CACHE["date"] == datetime.now(IST).strftime("%Y-%m-%d")
        h["morning_generating"] = _MORNING_CACHE["generating"]
    h["circuit_open"] = False
    try:
        from market_data import MarketDataFetcher as _MDF
        h["circuit_open"]     = time.time() < _MDF._cb_open_until
        h["circuit_failures"] = _MDF._cb_failures
    except Exception:
        pass
    return jsonify(h)


@app.route('/api/portfolio/optimizer')
def api_portfolio_optimizer():
    """Latest portfolio optimizer snapshot and correlation matrix."""
    try:
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))
        from portfolio_optimizer import EnterprisePortfolioOptimizer
        opt = EnterprisePortfolioOptimizer()
        analysis = opt.analyze()
        opt.persist_analysis(analysis)
        opt.persist_correlation(opt.current_positions())
        cm = opt.get_latest_correlation_matrix() or {}
        return jsonify({
            'portfolio_optimizer': analysis,
            'correlation_matrix': cm,
            'rebalance_suggestions': opt.rebalance_suggestions(opt.current_positions(), analysis['cash']),
        })
    except Exception as e:
        return jsonify({'error': str(e), 'portfolio_optimizer': {}, 'correlation_matrix': {}}), 500


@app.route('/api/reconciliation/status')
def api_reconciliation_status():
    """Reconciliation engine health: last sync, mismatches, repairs, duration."""
    try:
        if get_store is not None:
            status = get_store().get_broker_state('reconciliation') or {
                'healthy': False,
                'last_sync': None,
                'mismatches': 0,
                'repairs': 0,
                'duration_ms': 0
            }
        else:
            status = {'healthy': False, 'last_sync': None, 'mismatches': 0, 'repairs': 0, 'duration_ms': 0}
    except Exception:
        status = {'healthy': False, 'last_sync': None, 'mismatches': 0, 'repairs': 0, 'duration_ms': 0}
    return jsonify(status)


_BT_CACHE: dict = {}
_BT_LOCK = threading.Lock()


@app.route('/api/backtest', methods=['POST'])
def api_backtest():
    """
    Run historical backtest.
    POST body: { "symbols": [...], "years": 2 }
    Returns full backtest result dict.
    """
    try:
        body    = request.get_json(force=True) or {}
        symbols = body.get('symbols') or []
        years   = int(body.get('years', 2))
        years   = max(1, min(years, 5))

        if not symbols:
            # Default: use config watchlist top-20
            from config import config as _cfg
            symbols = list(getattr(_cfg, 'WATCHLIST', []))[:20]
            if not symbols:
                return jsonify({'error': 'No symbols provided and WATCHLIST is empty'}), 400

        # Cache key — same symbols+years returns cached result for 30 min
        cache_key = f"{','.join(sorted(symbols))}_{years}"
        with _BT_LOCK:
            cached = _BT_CACHE.get(cache_key)
            if cached and (time.time() - cached['ts']) < 1800:
                return jsonify(cached['data'])

        sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))
        from backtester import Backtester
        bt     = Backtester()
        result = bt.run(symbols=symbols, years=years)

        with _BT_LOCK:
            _BT_CACHE[cache_key] = {'data': result, 'ts': time.time()}
            # Keep cache small
            if len(_BT_CACHE) > 10:
                oldest = min(_BT_CACHE, key=lambda k: _BT_CACHE[k]['ts'])
                del _BT_CACHE[oldest]

        return jsonify(result)
    except Exception as e:
        logger.exception("Backtest error")
        return jsonify({'error': str(e)}), 500


@app.route('/api/backtest/results')
def api_backtest_results():
    """Return latest EnterpriseBacktestEngine results (run, walk-forward, Monte Carlo, comparison)."""
    try:
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))
        from persistence import get_store
        store = get_store()
        raw_run = store.get_latest_backtest_results() or {}
        result_json = raw_run.get('result_json', '{}')
        backtest = json.loads(result_json) if isinstance(result_json, str) else {}
        wf = store.get_latest_walk_forward_results(limit=5)
        mc = store.get_latest_monte_carlo_results() or {}
        mc_json = mc.get('result_json', '{}')
        monte = json.loads(mc_json) if isinstance(mc_json, str) else {}
        return jsonify({
            'backtest': backtest,
            'walk_forward': [{'name': r.get('name'), 'result': json.loads(r.get('result_json', '{}'))} for r in wf],
            'monte_carlo': monte,
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/ai-learning')
def api_ai_learning():
    """Latest AI learning metrics, feature importance and weights."""
    try:
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))
        from ai_learning_engine import EnterpriseLearningEngine
        engine = EnterpriseLearningEngine()
        return jsonify(engine.get_dashboard_data())
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/monitoring')
def api_monitoring():
    """Latest system health snapshot and alert counts."""
    try:
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))
        from system_monitor import EnterpriseSystemMonitor
        from alert_engine import EnterpriseAlertEngine
        mon = EnterpriseSystemMonitor()
        alert_eng = EnterpriseAlertEngine(store=mon.store)
        metrics = mon.collect()
        mon.save_snapshot(metrics)
        counts = alert_eng.daily_summary().get('counts', {'CRITICAL': 0, 'WARNING': 0, 'INFO': 0})
        return jsonify({
            'metrics': metrics,
            'health_score': metrics.get('health_score', 0),
            'alerts_today': counts,
            'heartbeat_logs': mon.store.get_latest_heartbeat_logs(limit=10) if hasattr(mon.store, 'get_latest_heartbeat_logs') else [],
            'alerts': mon.store.get_system_alerts(limit=20) if hasattr(mon.store, 'get_system_alerts') else [],
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/alert/ack', methods=['POST'])
def api_ack_alert():
    try:
        body = request.get_json(force=True) or {}
        alert_id = int(body.get('alert_id', 0))
        if get_store is not None:
            get_store().acknowledge_alert(alert_id)
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/smart-execution')
def api_smart_execution():
    """Smart execution analytics and recent orders/queue."""
    try:
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))
        from smart_execution_engine import SmartExecutionEngine
        from persistence import get_store
        engine = SmartExecutionEngine(store=get_store())
        return jsonify(engine.get_dashboard_data())
    except Exception as e:
        return jsonify({'error': str(e)}), 500


if __name__ == '__main__':
    # Start background heartbeat
    _hb = threading.Thread(target=_heartbeat_loop, daemon=True, name="heartbeat")
    _hb.start()

    print("\n" + "="*55)
    print("  🤖 AI Trading Dashboard")
    print("  Open in browser: http://localhost:5001")
    print("  Auto-refreshes every 60 seconds")
    print("  Press Ctrl+C to stop")
    print("="*55 + "\n")
    app.run(host='0.0.0.0', port=5001, debug=False, threaded=True)
