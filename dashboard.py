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
    _SCORE_SKIP_THRESHOLD = 60

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
    """Parse a journal date/timestamp into a naive datetime."""
    if not value:
        return None
    try:
        if isinstance(value, datetime):
            return value.replace(tzinfo=None)
        s = str(value)
        if 'T' in s:
            return datetime.fromisoformat(s.replace('Z', '+00:00')).replace(tzinfo=None)
        return datetime.strptime(s[:10], '%Y-%m-%d')
    except Exception:
        return None


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
            'source': c.get('source') or 'Bot',
            'trade_id': c.get('id'),
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
            events.append({
                'datetime': sell_dt_iso,
                'symbol': c['symbol'],
                'type': 'SELL',
                'quantity': c.get('quantity', 0),
                'price': c.get('exit_price'),
                'buy_price': c.get('entry_price'),
                'sell_price': c.get('exit_price'),
                'total_value': total_sell,
                'pnl': c.get('net_pnl'),
                'source': 'Bot',
                'trade_id': c.get('id'),
            })
    return sorted(events, key=lambda x: x['datetime'] or '', reverse=True)


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

        for sig in raw_signals:
            sym   = sig.get('symbol', '')
            act   = sig.get('action', '')
            conf  = sig.get('confidence', 0)
            raw_score = float(sig.get('overall_score') or sig.get('trade_score') or 0)
            # Normalize to 0-100: ai_research_agent returns 0-1 float; TradeScorer returns 0-100
            score = round(raw_score * 100, 1) if raw_score <= 1.0 else round(raw_score, 1)
            rr    = float(sig.get('risk_reward_ratio') or 0)
            price = float(sig.get('current_price') or sig.get('price') or 0)

            if act != 'BUY':
                bot_decision = 'SELL signal — not buying'
            elif sym in _held_syms:
                bot_decision = 'Already held'
            elif _open_count >= config.MAX_POSITIONS:
                bot_decision = 'Max positions reached'
            elif score < _SCORE_SKIP_THRESHOLD:
                bot_decision = f'Score {score:.0f}/100 below threshold ({_SCORE_SKIP_THRESHOLD})'
            elif rr < config.MIN_RISK_REWARD:
                bot_decision = f'R:R {rr:.2f} below {config.MIN_RISK_REWARD} min'
            elif conf < config.MIN_CONFIDENCE:
                bot_decision = f'Confidence {conf:.0%} below min'
            else:
                bot_decision = 'Will buy*'

            sig_data = {
                'symbol':            sym,
                'action':            act,
                'price':             price,
                'current_price':     price,
                'target':            sig.get('target') or round(price * (1 + tgt_pct), 2),
                'stop_loss':         sig.get('stop_loss') or round(price * (1 - sl_pct), 2),
                'confidence':        conf,
                'overall_score':     score,
                'risk_reward_ratio': round(rr, 2),
                'trend':             sig.get('trend', ''),
                'reasoning':         sig.get('reasoning', ''),
                'bot_decision':      bot_decision,
                'sector':            _SMAP.get(sym, 'Other'),
                'market_regime':     sig.get('market_regime', ''),
                'atr':               sig.get('atr', 0),
                'mtf_aligned':       sig.get('mtf_aligned', False),
            }
            all_signals.append(sig_data)
            if sym in display_set:
                display_sigs.append(sig_data)
            if act == 'BUY' and bot_decision == 'Will buy*':
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
.tab-btn{padding:8px 18px;border-radius:8px;font-size:13px;font-weight:600;cursor:pointer;border:none;transition:all .2s;color:#6b7280;background:transparent}
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
<div style="background:#111827;border-bottom:1px solid #1f2937;padding:6px 16px" class="flex gap-2">
  <button class="tab-btn active" onclick="switchTab('dashboard',this)">🏠 Dashboard</button>
  <button class="tab-btn" onclick="switchTab('morning',this)" id="morning-tab-btn">🌅 Morning Intel</button>
  <button class="tab-btn" onclick="switchTab('portfolio',this)">📈 Portfolio</button>
  <button class="tab-btn" onclick="switchTab('positions',this)">📋 Positions</button>
  <button class="tab-btn" onclick="switchTab('history',this)">🕒 History</button>
  <button class="tab-btn" onclick="switchTab('signals',this)">🤖 AI Signals</button>
  <button class="tab-btn" onclick="switchTab('analytics',this)">📊 Analytics</button>
  <button class="tab-btn" onclick="switchTab('journal',this)">📓 Trade Journal</button>
  <button class="tab-btn" onclick="switchTab('skipped',this)">⚠️ Skipped Opportunities</button>
  <button class="tab-btn" onclick="switchTab('explain',this)">🔍 AI Explain</button>
  <button class="tab-btn" onclick="switchTab('askai',this)">💬 Ask AI</button>
  <button class="tab-btn" onclick="switchTab('botstatus',this)">⚙️ Bot Status</button>
  <button class="tab-btn" id="ip-tab-btn" onclick="switchTab('ipstatus',this)">🌐 IP Status</button>
  <button class="tab-btn" onclick="switchTab('backtest',this)">📈 Backtest</button>
</div>

<div style="padding:16px 20px;max-width:1800px;margin:0 auto">

<!-- ===== TAB: MORNING INTELLIGENCE ===== -->
<div id="tab-morning" class="tab-content">

  <!-- Header -->
  <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:16px">
    <div>
      <div style="font-size:20px;font-weight:800;color:#f9fafb">🌅 Morning Market Intelligence</div>
      <div style="font-size:12px;color:#4b5563" id="mr-generated-at">Generated: —</div>
    </div>
    <div style="display:flex;gap:8px;align-items:center">
      <div id="mr-generating-badge" style="display:none;font-size:11px;color:#93c5fd;background:#1d4ed822;border:1px solid #3b82f644;padding:4px 10px;border-radius:6px">⏳ Generating…</div>
      <button onclick="refreshMorningReport()" style="background:#1d4ed8;color:#fff;border:none;border-radius:6px;padding:6px 14px;font-size:12px;cursor:pointer">↻ Regenerate</button>
    </div>
  </div>

  <!-- Section 1: Market Overview -->
  <div class="grid grid-cols-2 md:grid-cols-5 gap-3 mb-4">
    <div class="card" style="text-align:center">
      <div class="stat-label">Sentiment</div>
      <div style="font-size:18px;font-weight:800" id="mr-sentiment">—</div>
    </div>
    <div class="card" style="text-align:center">
      <div class="stat-label">NIFTY</div>
      <div style="font-size:16px;font-weight:700" id="mr-nifty">—</div>
      <div style="font-size:11px" id="mr-nifty-chg">—</div>
    </div>
    <div class="card" style="text-align:center">
      <div class="stat-label">BANKNIFTY</div>
      <div style="font-size:16px;font-weight:700" id="mr-bnf">—</div>
    </div>
    <div class="card" style="text-align:center">
      <div class="stat-label">India VIX</div>
      <div style="font-size:16px;font-weight:700" id="mr-vix">—</div>
      <div style="font-size:11px" id="mr-vix-label">—</div>
    </div>
    <div class="card" style="text-align:center">
      <div class="stat-label">Market Regime</div>
      <div style="font-size:16px;font-weight:700" id="mr-regime">—</div>
    </div>
  </div>

  <div class="grid grid-cols-1 md:grid-cols-2 gap-4 mb-4">

    <!-- Section 2: Sector Strength -->
    <div class="card">
      <div style="font-size:13px;font-weight:700;color:#f9fafb;margin-bottom:10px">📊 Sector Strength</div>
      <div id="mr-sectors" style="font-size:13px">—</div>
    </div>

    <!-- Section 9: Trading Plan -->
    <div class="card" style="border:1px solid #1d4ed855">
      <div style="font-size:13px;font-weight:700;color:#f9fafb;margin-bottom:10px">📋 Today's Trading Plan</div>
      <div id="mr-plan">—</div>
    </div>

  </div>

  <!-- Section 3+4: Gainers + Gap-Up -->
  <div class="grid grid-cols-1 md:grid-cols-2 gap-4 mb-4">
    <div class="card">
      <div style="font-size:13px;font-weight:700;color:#f9fafb;margin-bottom:10px">🚀 Top Gainers (Pre-Market)</div>
      <table style="width:100%;border-collapse:collapse" id="mr-gainers-tbl">
        <thead><tr>
          <th style="text-align:left;font-size:11px">Symbol</th>
          <th style="font-size:11px">Chg%</th>
          <th style="font-size:11px;text-align:left">Sector</th>
          <th style="text-align:right;font-size:11px">Price</th>
        </tr></thead>
        <tbody id="mr-gainers"><tr><td colspan="4" style="color:#4b5563;padding:10px;text-align:center">—</td></tr></tbody>
      </table>
    </div>
    <div class="card">
      <div style="font-size:13px;font-weight:700;color:#f9fafb;margin-bottom:10px">⬆️ Gap-Up Stocks <span style="font-size:10px;font-weight:400;color:#4b5563">(gap ≥ 1.5%, vol filter)</span></div>
      <table style="width:100%;border-collapse:collapse">
        <thead><tr>
          <th style="text-align:left;font-size:11px">Symbol</th>
          <th style="font-size:11px">Gap%</th>
          <th style="font-size:11px;text-align:left">Sector</th>
          <th style="text-align:right;font-size:11px">Price</th>
        </tr></thead>
        <tbody id="mr-gapup"><tr><td colspan="4" style="color:#4b5563;padding:10px;text-align:center">—</td></tr></tbody>
      </table>
    </div>
  </div>

  <!-- Section 5+6: Delivery Volume + Strong News -->
  <div class="grid grid-cols-1 md:grid-cols-2 gap-4 mb-4">
    <div class="card">
      <div style="font-size:13px;font-weight:700;color:#f9fafb;margin-bottom:10px">📦 High Volume / Institutional Interest</div>
      <div id="mr-delivery">—</div>
    </div>
    <div class="card">
      <div style="font-size:13px;font-weight:700;color:#f9fafb;margin-bottom:10px">📰 Strong News Catalysts</div>
      <div id="mr-news">—</div>
    </div>
  </div>

  <!-- Section 7: AI Top Picks (tradeable) -->
  <div class="card mb-4" style="border:1px solid #22c55e44">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">
      <div style="font-size:14px;font-weight:800;color:#22c55e">🎯 AI Top Picks — Tradeable Today</div>
      <div style="font-size:11px;color:#4b5563">Pass live filters · 0–100 AI score · max 3 per sector</div>
    </div>
    <div style="overflow-x:auto">
    <table style="width:100%;border-collapse:collapse">
      <thead><tr>
        <th style="font-size:11px;text-align:center;width:30px">#</th>
        <th style="font-size:11px;text-align:left">Symbol</th>
        <th style="font-size:11px;text-align:center">Score</th>
        <th style="font-size:11px;text-align:center">Conf.</th>
        <th style="font-size:11px;text-align:left">Sector</th>
        <th style="font-size:11px;text-align:right">Entry</th>
        <th style="font-size:11px;text-align:right">Target</th>
        <th style="font-size:11px;text-align:right">SL</th>
        <th style="font-size:11px;text-align:center">R:R</th>
        <th style="font-size:11px;text-align:center">Exp.Ret</th>
        <th style="font-size:11px;text-align:left">Reason</th>
      </tr></thead>
      <tbody id="mr-picks"><tr><td colspan="11" style="color:#4b5563;padding:20px;text-align:center">—</td></tr></tbody>
    </table>
    </div>
  </div>

  <!-- Section 7b: AI Watchlist (being monitored) -->
  <div class="card mb-4" style="border:1px solid #f59e0b33">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">
      <div style="font-size:14px;font-weight:800;color:#f59e0b">👁️ Stocks Being Monitored (Watchlist)</div>
      <div style="font-size:11px;color:#4b5563">Near entry but not yet tradeable</div>
    </div>
    <div style="overflow-x:auto">
    <table style="width:100%;border-collapse:collapse">
      <thead><tr>
        <th style="font-size:11px;text-align:left">Symbol</th>
        <th style="font-size:11px;text-align:center">Score</th>
        <th style="font-size:11px;text-align:center">Conf.</th>
        <th style="font-size:11px;text-align:left">Sector</th>
        <th style="font-size:11px;text-align:right">Entry</th>
        <th style="font-size:11px;text-align:right">Target</th>
        <th style="font-size:11px;text-align:center">R:R</th>
        <th style="font-size:11px;text-align:center">Exp.Ret</th>
        <th style="font-size:11px;text-align:left">Why not tradeable</th>
      </tr></thead>
      <tbody id="mr-watchlist"><tr><td colspan="9" style="color:#4b5563;padding:20px;text-align:center">—</td></tr></tbody>
    </table>
    </div>
  </div>

  <!-- Section 8: Stocks to Avoid -->
  <div class="card" style="border:1px solid #ef444444">
    <div style="font-size:13px;font-weight:700;color:#ef4444;margin-bottom:10px">🚫 Stocks to Avoid Today</div>
    <div id="mr-avoid">—</div>
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

  <!-- Row 2: Risk Monitor -->
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

  <!-- Row 3: Position Heatmap -->
  <div class="card mb-4">
    <div style="font-size:13px;font-weight:600;color:#9ca3af;margin-bottom:12px;text-transform:uppercase;letter-spacing:.06em">🌡️ Position Heatmap</div>
    <div id="d-heatmap" class="flex flex-wrap gap-3">
      <div style="color:#4b5563;font-size:13px">No open positions</div>
    </div>
  </div>

  <!-- Row 4: Portfolio Summary -->
  <div class="card mb-4" style="background: linear-gradient(135deg, #1e293b 0%, #334155 100%); border: 1px solid #475569;">
    <div style="font-size:13px;font-weight:600;color:#e2e8f0;margin-bottom:12px;text-transform:uppercase;letter-spacing:.06em">📊 Portfolio Summary</div>
    <div class="grid grid-cols-2 md:grid-cols-4 gap-4">
      <div>
        <div style="font-size:11px;color:#94a3b8;margin-bottom:2px">Total Investment</div>
        <div style="font-size:16px;font-weight:700;color:#f1f5f9" id="d-summary-investment">₹—</div>
      </div>
      <div>
        <div style="font-size:11px;color:#94a3b8;margin-bottom:2px">Current Value</div>
        <div style="font-size:16px;font-weight:700;color:#f1f5f9" id="d-summary-current">₹—</div>
      </div>
      <div>
        <div style="font-size:11px;color:#94a3b8;margin-bottom:2px">Today's P&L</div>
        <div style="font-size:16px;font-weight:700" id="d-summary-day-pnl">₹—</div>
      </div>
      <div>
        <div style="font-size:11px;color:#94a3b8;margin-bottom:2px">Total P&L</div>
        <div style="font-size:16px;font-weight:700" id="d-summary-total-pnl">₹—</div>
      </div>
    </div>
  </div>

  <!-- Row 5: Open Positions Table -->
  <div class="card mb-4">
    <div style="font-size:13px;font-weight:600;color:#9ca3af;margin-bottom:12px;text-transform:uppercase;letter-spacing:.06em">
      📈 Open Positions / Holdings <span id="d-holdings-count" style="color:#3b82f6">(0)</span> <span class="pulse green" style="font-size:11px">● LIVE</span>
    </div>
    <div style="overflow-x:auto">
    <table style="width:100%;border-collapse:collapse;font-size:13px">
      <thead><tr style="background:#1f2937">
        <th style="text-align:left;padding:10px 8px">Instrument</th>
        <th style="text-align:right;padding:10px 8px">Qty</th>
        <th style="text-align:right;padding:10px 8px">Avg Cost</th>
        <th style="text-align:right;padding:10px 8px">LTP</th>
        <th style="text-align:right;padding:10px 8px">Invested</th>
        <th style="text-align:right;padding:10px 8px">Current Value</th>
        <th style="text-align:right;padding:10px 8px">Total P&L</th>
        <th style="text-align:right;padding:10px 8px">Net Change %</th>
        <th style="text-align:right;padding:10px 8px">Day Change %</th>
        <th style="text-align:center;padding:10px 8px">Days Held</th>
        <th style="text-align:right;padding:10px 8px">Trail SL</th>
        <th style="text-align:right;padding:10px 8px">Target</th>
        <th style="text-align:right;padding:10px 8px">AI Score</th>
      </tr></thead>
      <tbody id="d-positions"><tr><td colspan="13" style="text-align:center;color:#4b5563;padding:20px">No open positions</td></tr></tbody>
      <tfoot id="d-positions-total" style="display:none;background:#1f2937;font-weight:600">
        <tr>
          <td style="padding:10px 8px;text-align:left">Total</td>
          <td style="padding:10px 8px;text-align:right" id="d-total-qty">—</td>
          <td style="padding:10px 8px;text-align:right" id="d-total-avg">—</td>
          <td style="padding:10px 8px;text-align:right" id="d-total-ltp">—</td>
          <td style="padding:10px 8px;text-align:right" id="d-total-invested">—</td>
          <td style="padding:10px 8px;text-align:right" id="d-total-current">—</td>
          <td style="padding:10px 8px;text-align:right" id="d-total-pnl">—</td>
          <td style="padding:10px 8px;text-align:right" id="d-total-change-pct">—</td>
          <td style="padding:10px 8px;text-align:right" id="d-total-day-pct">—</td>
          <td style="padding:10px 8px;text-align:center">—</td>
          <td style="padding:10px 8px;text-align:right">—</td>
          <td style="padding:10px 8px;text-align:right">—</td>
          <td style="padding:10px 8px;text-align:right">—</td>
        </tr>
      </tfoot>
    </table>
    </div>
  </div>

  <!-- Row 5+6: AI Opportunities + Market -->
  <div class="grid grid-cols-1 lg:grid-cols-2 gap-4 mb-4">

    <!-- AI Opportunities -->
    <div class="card">
      <div style="font-size:13px;font-weight:600;color:#9ca3af;margin-bottom:12px;text-transform:uppercase;letter-spacing:.06em">🤖 Today's Best Opportunities</div>
      <div style="overflow-x:auto">
      <table style="width:100%;border-collapse:collapse">
        <thead><tr>
          <th style="text-align:left">Stock</th><th>Score</th><th>Trend</th>
          <th>Entry</th><th>Target</th><th>Risk</th>
        </tr></thead>
        <tbody id="d-opportunities"><tr><td colspan="6" style="text-align:center;color:#4b5563;padding:20px">Scanning...</td></tr></tbody>
      </table>
      </div>
    </div>

    <!-- Market Overview -->
    <div class="card">
      <div style="font-size:13px;font-weight:600;color:#9ca3af;margin-bottom:12px;text-transform:uppercase;letter-spacing:.06em">🌍 Market Overview</div>
      <div class="grid grid-cols-2 gap-3 mb-4">
        <div class="card-sm"><div class="stat-label">NIFTY 50</div><div class="stat-value-sm" id="d-nifty">—</div></div>
        <div class="card-sm"><div class="stat-label">BANKNIFTY</div><div class="stat-value-sm" id="d-banknifty">—</div></div>
        <div class="card-sm"><div class="stat-label">VIX</div><div class="stat-value-sm" id="d-vix">—</div>
          <div style="font-size:11px;margin-top:2px" id="d-vix-label">—</div>
        </div>
        <div class="card-sm"><div class="stat-label">Market Regime</div><div class="stat-value-sm" id="d-regime">—</div></div>
      </div>
      <div>
        <div class="stat-label mb-2">Sector Strength</div>
        <div id="d-sectors" class="flex flex-wrap gap-2"></div>
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
    <div class="card">
      <div style="font-size:13px;font-weight:600;color:#9ca3af;margin-bottom:12px;text-transform:uppercase;letter-spacing:.06em">🔔 Recent Alerts</div>
      <div id="d-notifications">
        <div style="color:#4b5563;font-size:13px;padding:20px;text-align:center">No alerts yet</div>
      </div>
    </div>
  </div>

  <!-- Row 8: Daily Goal Progress -->
  <div class="card mb-4">
    <div style="font-size:13px;font-weight:600;color:#9ca3af;margin-bottom:12px;text-transform:uppercase;letter-spacing:.06em">🎯 Daily Goals</div>
    <div class="grid grid-cols-1 md:grid-cols-3 gap-4">
      <div>
        <div class="flex justify-between mb-1"><span style="font-size:12px;color:#9ca3af">Profit Target</span><span style="font-size:12px" id="d-goal-profit-val">₹0 / ₹100</span></div>
        <div class="progress-bar"><div class="progress-fill" id="d-goal-profit-bar" style="width:0%;background:#22c55e"></div></div>
      </div>
      <div>
        <div class="flex justify-between mb-1"><span style="font-size:12px;color:#9ca3af">Capital Utilisation</span><span style="font-size:12px" id="d-goal-capital-val">0%</span></div>
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

  <div class="grid grid-cols-2 md:grid-cols-4 gap-4 mb-4">
    <div class="card"><div class="stat-label">Portfolio Value</div><div class="stat-value" id="p-account-balance">₹—</div><div style="font-size:11px;color:#4b5563;margin-top:4px">Cash + Holdings</div></div>
    <div class="card"><div class="stat-label">Available Cash</div><div class="stat-value green" id="p-cash">₹—</div></div>
    <div class="card"><div class="stat-label">Margin Used</div><div class="stat-value" id="p-margin">₹—</div></div>
    <div class="card"><div class="stat-label">Holdings Value</div><div class="stat-value" id="p-holdings-val">₹—</div><div style="font-size:11px;color:#4b5563;margin-top:4px">At market price</div></div>
  </div>

  <div class="grid grid-cols-1 lg:grid-cols-2 gap-4 mb-4">
    <!-- Capital Allocation Chart -->
    <div class="card">
      <div style="font-size:13px;font-weight:600;color:#9ca3af;margin-bottom:16px;text-transform:uppercase;letter-spacing:.06em">💰 Capital Allocation</div>
      <div style="max-height:260px;display:flex;justify-content:center">
        <canvas id="chart-allocation" style="max-height:250px;max-width:250px"></canvas>
      </div>
    </div>
    <!-- Sector Allocation Chart -->
    <div class="card">
      <div style="font-size:13px;font-weight:600;color:#9ca3af;margin-bottom:16px;text-transform:uppercase;letter-spacing:.06em">🏭 Sector Allocation</div>
      <div style="max-height:260px;display:flex;justify-content:center">
        <canvas id="chart-sector" style="max-height:250px;max-width:250px"></canvas>
      </div>
    </div>
  </div>

  <!-- Portfolio Summary -->
  <div class="card mb-4" style="background: linear-gradient(135deg, #1e293b 0%, #334155 100%); border: 1px solid #475569;">
    <div style="font-size:13px;font-weight:600;color:#e2e8f0;margin-bottom:12px;text-transform:uppercase;letter-spacing:.06em">📊 Portfolio Summary</div>
    <div class="grid grid-cols-2 md:grid-cols-4 gap-4">
      <div>
        <div style="font-size:11px;color:#94a3b8;margin-bottom:2px">Total Investment</div>
        <div style="font-size:16px;font-weight:700;color:#f1f5f9" id="p-summary-investment">₹—</div>
      </div>
      <div>
        <div style="font-size:11px;color:#94a3b8;margin-bottom:2px">Current Value</div>
        <div style="font-size:16px;font-weight:700;color:#f1f5f9" id="p-summary-current">₹—</div>
      </div>
      <div>
        <div style="font-size:11px;color:#94a3b8;margin-bottom:2px">Today's P&L</div>
        <div style="font-size:16px;font-weight:700" id="p-summary-day-pnl">₹—</div>
      </div>
      <div>
        <div style="font-size:11px;color:#94a3b8;margin-bottom:2px">Total P&L</div>
        <div style="font-size:16px;font-weight:700" id="p-summary-total-pnl">₹—</div>
      </div>
    </div>
  </div>

  <!-- Holdings Table -->
  <div class="card mb-4">
    <div style="font-size:13px;font-weight:600;color:#9ca3af;margin-bottom:12px;text-transform:uppercase;letter-spacing:.06em">
      📈 Open Positions / Holdings <span id="holdings-count" style="color:#3b82f6">(0)</span>
    </div>
    <div style="overflow-x:auto">
    <table style="width:100%;border-collapse:collapse;font-size:13px">
      <thead><tr style="background:#1f2937">
        <th style="text-align:left;padding:10px 8px">Instrument</th>
        <th style="text-align:right;padding:10px 8px">Qty</th>
        <th style="text-align:right;padding:10px 8px">Avg Cost</th>
        <th style="text-align:right;padding:10px 8px">LTP</th>
        <th style="text-align:right;padding:10px 8px">Invested</th>
        <th style="text-align:right;padding:10px 8px">Current Value</th>
        <th style="text-align:right;padding:10px 8px">Total P&L</th>
        <th style="text-align:right;padding:10px 8px">Net Change %</th>
        <th style="text-align:right;padding:10px 8px">Day Change %</th>
        <th style="text-align:center;padding:10px 8px">Days Held</th>
        <th style="text-align:right;padding:10px 8px">Trail SL</th>
        <th style="text-align:right;padding:10px 8px">Target</th>
        <th style="text-align:right;padding:10px 8px">AI Score</th>
      </tr></thead>
      <tbody id="p-holdings"><tr><td colspan="13" style="text-align:center;color:#4b5563;padding:20px">No delivery holdings</td></tr></tbody>
      <tfoot id="p-holdings-total" style="display:none;background:#1f2937;font-weight:600">
        <tr>
          <td style="padding:10px 8px;text-align:left">Total</td>
          <td style="padding:10px 8px;text-align:right" id="total-qty">—</td>
          <td style="padding:10px 8px;text-align:right" id="total-avg">—</td>
          <td style="padding:10px 8px;text-align:right" id="total-ltp">—</td>
          <td style="padding:10px 8px;text-align:right" id="p-total-invested">—</td>
          <td style="padding:10px 8px;text-align:right" id="p-total-current">—</td>
          <td style="padding:10px 8px;text-align:right" id="p-total-pnl">—</td>
          <td style="padding:10px 8px;text-align:right" id="p-total-change-pct">—</td>
          <td style="padding:10px 8px;text-align:right" id="p-total-day-pct">—</td>
          <td style="padding:10px 8px;text-align:center">—</td>
          <td style="padding:10px 8px;text-align:right">—</td>
          <td style="padding:10px 8px;text-align:right">—</td>
          <td style="padding:10px 8px;text-align:right">—</td>
        </tr>
      </tfoot>
    </table>
    </div>
  </div>

  <!-- Recent Activity -->
  <div class="card mb-4">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px">
      <div style="font-size:13px;font-weight:600;color:#9ca3af;text-transform:uppercase;letter-spacing:.06em">🕒 Recent Activity (Last 5 Trades)</div>
      <a href="#" onclick="switchTab('history', this)" style="font-size:11px;color:#60a5fa">View full history →</a>
    </div>
    <div style="overflow-x:auto">
    <table style="width:100%;border-collapse:collapse">
      <thead><tr>
        <th style="text-align:left">Date</th><th style="text-align:left">Symbol</th>
        <th>Type</th><th>Qty</th><th>Buy Price</th><th>Sell Price</th><th>P&amp;L</th><th>P&amp;L %</th>
      </tr></thead>
      <tbody id="p-trade-history"><tr><td colspan="8" style="text-align:center;color:#4b5563;padding:20px">No recent trades</td></tr></tbody>
    </table>
    </div>
  </div>

  <!-- Portfolio Value Chart -->
  <div class="card mb-4">
    <div style="font-size:13px;font-weight:600;color:#9ca3af;margin-bottom:16px;text-transform:uppercase;letter-spacing:.06em">📈 Portfolio Value (Today)</div>
    <canvas id="chart-portfolio" style="max-height:200px"></canvas>
  </div>

  <!-- P&L Bar Chart -->
  <div class="card mb-4">
    <div style="font-size:13px;font-weight:600;color:#9ca3af;margin-bottom:16px;text-transform:uppercase;letter-spacing:.06em">📊 Daily P&amp;L (This Week)</div>
    <canvas id="chart-pnl" style="max-height:160px"></canvas>
  </div>

</div><!-- /tab-portfolio -->


<!-- ===== TAB: POSITIONS ===== -->
<div id="tab-positions" class="tab-content">

  <!-- Summary row -->
  <div class="grid grid-cols-2 md:grid-cols-4 gap-4 mb-4">
    <div class="card"><div class="stat-label">Open Positions</div><div class="stat-value blue" id="pos-count">—</div></div>
    <div class="card"><div class="stat-label">Total Invested</div><div class="stat-value" id="pos-invested">₹—</div></div>
    <div class="card"><div class="stat-label">Unrealised P&amp;L</div><div class="stat-value" id="pos-pnl">₹—</div></div>
    <div class="card"><div class="stat-label">Re-entries Active</div><div class="stat-value yellow" id="pos-reentries">—</div></div>
  </div>

  <!-- Position cards grid -->
  <div id="pos-cards" class="grid grid-cols-1 md:grid-cols-2 gap-4 mb-4">
    <div class="card" style="color:#4b5563;text-align:center;padding:40px">No open positions</div>
  </div>

  <!-- Full detail table -->
  <div class="card mb-4">
    <div style="font-size:13px;font-weight:600;color:#9ca3af;margin-bottom:12px;text-transform:uppercase;letter-spacing:.06em">
      📋 All Positions — Full Detail
    </div>
    <div style="overflow-x:auto">
    <table style="width:100%;border-collapse:collapse">
      <thead><tr>
        <th style="text-align:left;min-width:130px">Symbol</th>
        <th>Qty</th>
        <th>First Entry</th>
        <th>Avg Price</th>
        <th>CMP</th>
        <th>P&amp;L</th>
        <th>P&amp;L %</th>
        <th>Days Held</th>
        <th>Trail SL</th>
        <th>▼ to SL</th>
        <th>Target</th>
        <th>▲ to Tgt</th>
        <th>AI Score</th>
        <th>Re-entry</th>
      </tr></thead>
      <tbody id="pos-table"><tr><td colspan="14" style="text-align:center;color:#4b5563;padding:20px">No open positions</td></tr></tbody>
    </table>
    </div>
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
        <th style="text-align:left">Stock</th><th>Qty</th><th>Avg Buy</th><th>Current Price</th>
        <th>Invested</th><th>Current Value</th><th>P&amp;L</th><th>Return</th>
      </tr></thead>
      <tbody id="h-stock-breakdown"><tr><td colspan="8" style="text-align:center;color:#4b5563;padding:20px">Loading...</td></tr></tbody>
    </table>
    </div>
  </div>

  <!-- Open Positions -->
  <div class="card mb-4">
    <div style="font-size:13px;font-weight:600;color:#9ca3af;margin-bottom:4px;text-transform:uppercase;letter-spacing:.06em">Open Positions</div>
    <div style="font-size:11px;color:#6b7280;margin-bottom:12px">Live open trades from broker + journal. Completed trades are shown in the history table below.</div>
    <div id="h-trade-cards" style="display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:12px">
      <div style="color:#4b5563;padding:20px;text-align:center">Loading...</div>
    </div>
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
        <th style="text-align:left">Date &amp; Time</th>
        <th style="text-align:left">Stock</th>
        <th>Type</th><th>Qty</th><th>Buy Price</th><th>Sell Price</th><th>Total Value</th><th>P&amp;L</th><th>Source</th>
      </tr></thead>
      <tbody id="h-history-table"><tr><td colspan="9" style="text-align:center;color:#4b5563;padding:20px">No history yet</td></tr></tbody>
    </table>
    </div>
  </div>

</div><!-- /tab-history -->


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
    <div class="card"><div class="stat-label">Today's Return</div><div class="stat-value" id="a-today-ret">—</div></div>
    <div class="card"><div class="stat-label">Weekly Return</div><div class="stat-value" id="a-weekly-ret">—</div></div>
    <div class="card"><div class="stat-label">Monthly Return</div><div class="stat-value" id="a-monthly-ret">—</div></div>
    <div class="card"><div class="stat-label">Win Rate</div><div class="stat-value" id="a-win-rate">—</div></div>
  </div>

  <div class="grid grid-cols-2 md:grid-cols-4 gap-4 mb-4">
    <div class="card"><div class="stat-label" title="Profit Factor = Gross Profit / Gross Loss. It can be &#x221e; (infinity) when there are no losing trades; high values mean wins are much larger than losses.">Profit Factor <span style="cursor:help;color:#94a3b8">&#9432;</span></div><div class="stat-value green" id="a-profit-factor">—</div></div>
    <div class="card"><div class="stat-label">Average Win</div><div class="stat-value green" id="a-avg-win">—</div></div>
    <div class="card"><div class="stat-label">Average Loss</div><div class="stat-value red" id="a-avg-loss">—</div></div>
    <div class="card"><div class="stat-label">Expectancy</div><div class="stat-value" id="a-expectancy">—</div></div>
  </div>

  <div class="grid grid-cols-2 md:grid-cols-4 gap-4 mb-4">
    <div class="card"><div class="stat-label">Weekly P&amp;L</div><div class="stat-value" id="a-weekly-pnl">—</div></div>
    <div class="card"><div class="stat-label">Monthly P&amp;L</div><div class="stat-value" id="a-monthly-pnl">—</div></div>
    <div class="card"><div class="stat-label">Max Drawdown</div><div class="stat-value red" id="a-max-drawdown">—</div></div>
    <div class="card"><div class="stat-label">Total Trades</div><div class="stat-value" id="a-total-trades">—</div></div>
  </div>

  <!-- Win Rate Gauge + Trade Calendar -->
  <div class="grid grid-cols-1 lg:grid-cols-2 gap-4 mb-4">
    <div class="card">
      <div style="font-size:13px;font-weight:600;color:#9ca3af;margin-bottom:16px;text-transform:uppercase;letter-spacing:.06em">📊 Win Rate Gauge</div>
      <canvas id="chart-winrate" style="max-height:200px"></canvas>
    </div>
    <div class="card">
      <div style="font-size:13px;font-weight:600;color:#9ca3af;margin-bottom:12px;text-transform:uppercase;letter-spacing:.06em">📅 Trade Calendar (This Week)</div>
      <div id="a-calendar" class="flex gap-2 justify-around"></div>
    </div>
  </div>

  <!-- Full Trade History -->
  <div class="card mb-4">
    <div style="font-size:13px;font-weight:600;color:#9ca3af;margin-bottom:12px;text-transform:uppercase;letter-spacing:.06em">📋 Full Trade History (Today)</div>
    <div style="overflow-x:auto">
    <table style="width:100%;border-collapse:collapse">
      <thead><tr>
        <th style="text-align:left">Time</th><th style="text-align:left">Symbol</th><th>Action</th>
        <th>Qty</th><th>Price</th><th>Amount</th><th>Status</th><th>P&amp;L</th>
      </tr></thead>
      <tbody id="a-history"><tr><td colspan="8" style="text-align:center;color:#4b5563;padding:20px">No trades today</td></tr></tbody>
    </table>
    </div>
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
        <th>Days</th>
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
    <div class="card"><div class="stat-label">Executed</div><div class="stat-value green" id="s-executed">—</div></div>
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
            <th style="padding:8px;text-align:left;color:#f9fafb">Conf.</th>
            <th style="padding:8px;text-align:left;color:#f9fafb">P&L</th>
            <th style="padding:8px;text-align:left;color:#f9fafb">Price</th>
            <th style="padding:8px;text-align:left;color:#f9fafb">Qty</th>
            <th style="padding:8px;text-align:left;color:#f9fafb">Sector</th>
          </tr>
        </thead>
        <tbody id="explain-table">
          <tr><td colspan="10" style="text-align:center;color:#4b5563;padding:20px">Loading AI explanations...</td></tr>
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

  <div style="text-align:right;font-size:11px;color:#374151;padding:8px 0">
    <a href="/api/data" style="color:#374151;text-decoration:underline">Raw API JSON</a> &nbsp;|
    <a href="/api/health" style="color:#374151;text-decoration:underline">Health JSON</a>
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
  btn.classList.add('active');
  if(id==='ipstatus') refreshIpStatus();
  if(id==='morning') loadMorningReport();
  if(id==='skipped') loadSkippedOpportunities();
  if(id==='explain') loadExplainability();
}

// ── Morning Intelligence Report ───────────────────────────────────────────────
async function loadMorningReport(){
  try{
    const r=await fetch('/api/morning-report');
    const d=await r.json();
    renderMorningReport(d);
  }catch(e){console.error('Morning report load error:',e);}
}

async function refreshMorningReport(){
  document.getElementById('mr-generating-badge').style.display='inline-block';
  try{
    await fetch('/api/morning-report/refresh',{method:'POST'});
    // Poll every 10s until done
    const poll=setInterval(async()=>{
      const r=await fetch('/api/morning-report');
      const d=await r.json();
      if(!d.generating){
        clearInterval(poll);
        renderMorningReport(d);
        document.getElementById('mr-generating-badge').style.display='none';
      }
    },10000);
  }catch(e){document.getElementById('mr-generating-badge').style.display='none';}
}

function renderMorningReport(d){
  const rpt=d.report||{};
  const generating=d.generating;

  const badge=document.getElementById('mr-generating-badge');
  if(badge) badge.style.display=generating?'inline-block':'none';

  const genAt=document.getElementById('mr-generated-at');
  if(genAt){
    if(generating) genAt.textContent='Generating… this takes 3–5 min';
    else if(rpt.generated_at) genAt.textContent='Generated: '+new Date(rpt.generated_at).toLocaleTimeString('en-IN',{hour:'2-digit',minute:'2-digit'});
  }

  // 1. Market Overview
  const mo=rpt.market_overview||{};
  const setT=(id,txt,cls)=>{const el=document.getElementById(id);if(el){el.textContent=txt||'—';if(cls)el.className=cls;}};
  const sent=mo.sentiment||'Neutral';
  const sentEl=document.getElementById('mr-sentiment');
  if(sentEl){sentEl.textContent=sent;sentEl.style.color=sent==='Bullish'?'#22c55e':sent==='Bearish'?'#ef4444':'#eab308';}
  setT('mr-nifty', mo.nifty?mo.nifty.toLocaleString('en-IN'):'—');
  const nchgEl=document.getElementById('mr-nifty-chg');
  if(nchgEl&&mo.nifty_chg!==undefined){const c=parseFloat(mo.nifty_chg||0);nchgEl.textContent=(c>=0?'+':'')+c.toFixed(2)+'%';nchgEl.style.color=c>=0?'#22c55e':'#ef4444';}
  const bnfEl=document.getElementById('mr-bnf');
  if(bnfEl&&mo.banknifty_chg!==undefined){const c=parseFloat(mo.banknifty_chg||0);bnfEl.textContent=(c>=0?'+':'')+c.toFixed(2)+'%';bnfEl.style.color=c>=0?'#22c55e':'#ef4444';}
  const vixEl=document.getElementById('mr-vix');
  if(vixEl){vixEl.textContent=mo.vix||'—';vixEl.style.color=mo.vix_label==='LOW'?'#22c55e':mo.vix_label==='HIGH'?'#ef4444':'#eab308';}
  setT('mr-vix-label', mo.vix_label||'—');
  const regEl=document.getElementById('mr-regime');
  if(regEl){regEl.textContent=mo.regime||'—';regEl.style.color=mo.regime==='BULL'?'#22c55e':mo.regime==='BEAR'?'#ef4444':'#eab308';}

  // 2. Sectors
  const secEl=document.getElementById('mr-sectors');
  if(secEl){
    const secs=rpt.sector_strength||[];
    if(secs.length){
      secEl.innerHTML=secs.map(s=>{
        const clr=s.change_pct>0?'#22c55e':'#ef4444';
        const bg=s.change_pct>0?'#16a34a18':'#dc262618';
        return `<div style="display:flex;justify-content:space-between;align-items:center;padding:5px 8px;margin-bottom:4px;background:${bg};border-radius:5px">
          <span style="font-weight:600;color:#f9fafb;font-size:13px">${s.sector}</span>
          <span style="color:${clr};font-weight:700;font-size:13px">${s.direction} ${s.change_pct>0?'+':''}${s.change_pct}%</span>
        </div>`;
      }).join('');
    } else {secEl.innerHTML='<span style="color:#4b5563">Calculating…</span>';}
  }

  // 9. Trading Plan
  const planEl=document.getElementById('mr-plan');
  if(planEl){
    const p=rpt.trading_plan||{};
    if(p.regime){
      const modeClr=p.risk_mode==='Aggressive'?'#22c55e':p.risk_mode==='Defensive'?'#ef4444':'#eab308';
      planEl.innerHTML=`
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:10px">
          <div class="card-sm"><div class="stat-label">Market Regime</div><b style="color:${p.regime==='BULL'?'#22c55e':p.regime==='BEAR'?'#ef4444':'#eab308'}">${p.regime}</b></div>
          <div class="card-sm"><div class="stat-label">Risk Mode</div><b style="color:${modeClr}">${p.risk_mode}</b></div>
          <div class="card-sm"><div class="stat-label">Expected Trades</div><b>${p.expected_trades}</b></div>
          <div class="card-sm"><div class="stat-label">India VIX</div><b>${p.vix||'—'}</b></div>
        </div>
        ${p.preferred_sectors&&p.preferred_sectors.length?`<div style="margin-bottom:6px"><span style="color:#4b5563;font-size:11px">✅ Preferred Sectors: </span><b style="color:#22c55e;font-size:12px">${p.preferred_sectors.join(' · ')}</b></div>`:''}
        ${p.avoid_sectors&&p.avoid_sectors.length?`<div style="margin-bottom:6px"><span style="color:#4b5563;font-size:11px">❌ Avoid: </span><b style="color:#ef4444;font-size:12px">${p.avoid_sectors.join(' · ')}</b></div>`:''}
        <div style="font-size:12px;color:#6b7280;border-top:1px solid #1f2937;padding-top:8px;margin-top:4px">${p.note||''}</div>`;
    } else {planEl.innerHTML='<span style="color:#4b5563">Generating…</span>';}
  }

  // 3. Gainers
  const gainEl=document.getElementById('mr-gainers');
  if(gainEl){
    const g=rpt.top_gainers||[];
    gainEl.innerHTML=g.length?g.map(s=>`<tr style="border-bottom:1px solid #1f293744">
      <td style="padding:5px 4px;font-weight:600;color:#f9fafb">${s.symbol}</td>
      <td style="text-align:center;color:${s.change_pct>=0?'#22c55e':'#ef4444'};font-weight:700">${s.change_pct>=0?'+':''}${s.change_pct}%</td>
      <td style="color:#6b7280;font-size:11px">${s.sector||'—'}</td>
      <td style="text-align:right;font-family:monospace;font-size:12px">${rupee(s.price)}</td>
    </tr>`).join(''):'<tr><td colspan="4" style="color:#4b5563;padding:12px;text-align:center">No data yet</td></tr>';
  }

  // 4. Gap-Up
  const gapEl=document.getElementById('mr-gapup');
  if(gapEl){
    const g=rpt.gap_up_stocks||[];
    gapEl.innerHTML=g.length?g.map(s=>`<tr style="border-bottom:1px solid #1f293744">
      <td style="padding:5px 4px;font-weight:600;color:#f9fafb">${s.symbol}</td>
      <td style="text-align:center;color:#22c55e;font-weight:700">+${s.gap_pct}%</td>
      <td style="color:#6b7280;font-size:11px">${s.sector||'—'}</td>
      <td style="text-align:right;font-family:monospace;font-size:12px">${rupee(s.price)}</td>
    </tr>`).join(''):'<tr><td colspan="4" style="color:#4b5563;padding:12px;text-align:center">No gap-ups today (< 1.5%)</td></tr>';
  }

  // 5. Delivery Volume
  const delEl=document.getElementById('mr-delivery');
  if(delEl){
    const items=rpt.delivery_volume||[];
    delEl.innerHTML=items.length?items.map(s=>`
      <div style="display:flex;justify-content:space-between;align-items:center;padding:5px 0;border-bottom:1px solid #1f293744">
        <div><b style="color:#f9fafb">${s.symbol}</b> <span style="color:#4b5563;font-size:11px">${s.sector||''}</span></div>
        <div style="text-align:right"><div style="font-family:monospace;font-size:12px">${rupee(s.price)}</div><div style="font-size:10px;color:#6b7280">${(s.volume||0).toLocaleString('en-IN')} vol</div></div>
      </div>`).join(''):'<span style="color:#4b5563;font-size:12px">No high-volume stocks detected</span>';
  }

  // 6. News
  const newsEl=document.getElementById('mr-news');
  if(newsEl){
    const items=rpt.strong_news||[];
    newsEl.innerHTML=items.length?items.map(s=>`
      <div style="padding:7px 0;border-bottom:1px solid #1f293744">
        <div style="display:flex;justify-content:space-between">
          <b style="color:#f9fafb;font-size:13px">${s.symbol}</b>
          <span style="font-size:11px;background:#16a34a22;color:#22c55e;padding:1px 6px;border-radius:3px">${s.sentiment} ${s.confidence}%</span>
        </div>
        <div style="color:#6b7280;font-size:11px;margin-top:2px">${s.headline||''}</div>
      </div>`).join(''):'<span style="color:#4b5563;font-size:12px">No strong news catalysts today</span>';
  }

  // 7. AI Top Picks
  const picksEl=document.getElementById('mr-picks');
  if(picksEl){
    const picks=rpt.ai_top_picks||[];
    picksEl.innerHTML=picks.length?picks.map(p=>`<tr style="border-bottom:1px solid #1f2937">
      <td style="text-align:center;color:#4b5563;font-size:11px;padding:8px 4px">${p.rank}</td>
      <td style="padding:8px;font-weight:700;color:#f9fafb">${p.symbol}</td>
      <td style="text-align:center;padding:8px 4px"><span style="background:${p.score>=80?'#16a34a33':'#ca8a0433'};color:${p.score>=80?'#22c55e':'#eab308'};padding:2px 7px;border-radius:4px;font-weight:700">${p.score}</span></td>
      <td style="text-align:center;padding:8px 4px;color:${scoreColor(p.confidence)}">${p.confidence}%</td>
      <td style="padding:8px 4px;color:#6b7280;font-size:11px">${p.sector||'—'}</td>
      <td style="text-align:right;padding:8px 4px;font-family:monospace;font-size:12px">${rupee(p.entry)}</td>
      <td style="text-align:right;padding:8px 4px;font-family:monospace;font-size:12px;color:#22c55e">${rupee(p.target)}</td>
      <td style="text-align:right;padding:8px 4px;font-family:monospace;font-size:12px;color:#ef4444">${rupee(p.stop_loss)}</td>
      <td style="text-align:center;padding:8px 4px;font-weight:600;color:${p.rr>=2?'#22c55e':p.rr>=1.5?'#eab308':'#9ca3af'}">${p.rr}x</td>
      <td style="text-align:center;padding:8px 4px;color:${p.exp_return>=5?'#22c55e':p.exp_return>=2?'#eab308':'#9ca3af'};font-weight:600">${p.exp_return>0?'+':''}${p.exp_return}%</td>
      <td style="padding:8px 4px;color:#9ca3af;font-size:11px;max-width:160px">${p.reason||'—'}</td>
    </tr>`).join(''):'<tr><td colspan="11" style="color:#4b5563;padding:20px;text-align:center">AI analysis running…</td></tr>';
  }

  // 7b. AI Watchlist (stocks monitored but not passing live filters)
  const watchEl=document.getElementById('mr-watchlist');
  if(watchEl){
    const wl=rpt.ai_watchlist||[];
    watchEl.innerHTML=wl.length?wl.map(w=>`<tr style="border-bottom:1px solid #1f2937">
      <td style="padding:8px;font-weight:700;color:#f9fafb">${w.symbol}</td>
      <td style="text-align:center;padding:8px 4px"><span style="background:${w.score>=60?'#16a34a33':'#dc262633'};color:${w.score>=60?'#22c55e':'#ef4444'};padding:2px 7px;border-radius:4px;font-weight:700">${w.score}</span></td>
      <td style="text-align:center;padding:8px 4px;color:${scoreColor(w.confidence)}">${w.confidence}%</td>
      <td style="padding:8px 4px;color:#6b7280;font-size:11px">${w.sector||'—'}</td>
      <td style="text-align:right;padding:8px 4px;font-family:monospace;font-size:12px">${rupee(w.entry)}</td>
      <td style="text-align:right;padding:8px 4px;font-family:monospace;font-size:12px;color:#22c55e">${rupee(w.target)}</td>
      <td style="text-align:center;padding:8px 4px;font-weight:600;color:${w.rr>=2?'#22c55e':w.rr>=1.5?'#eab308':'#9ca3af'}">${w.rr}x</td>
      <td style="text-align:center;padding:8px 4px;color:${w.exp_return>=5?'#22c55e':w.exp_return>=0?'#eab308':'#ef4444'};font-weight:600">${w.exp_return>0?'+':''}${w.exp_return}%</td>
      <td style="padding:8px 4px;color:#9ca3af;font-size:11px;max-width:180px" title="${w.reason||''}">${w.filter_note||'—'}</td>
    </tr>`).join(''):'<tr><td colspan="9" style="color:#4b5563;padding:20px;text-align:center">No stocks currently being monitored</td></tr>';
  }

  // 8. Avoid
  const avoidEl=document.getElementById('mr-avoid');
  if(avoidEl){
    const items=rpt.stocks_to_avoid||[];
    avoidEl.innerHTML=items.length?`<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:8px">`+
      items.map(s=>`<div style="background:#dc262611;border:1px solid #ef444433;border-radius:6px;padding:8px 10px">
        <div style="font-weight:700;color:#ef4444">${s.symbol} <span style="font-size:11px;font-weight:400;color:#6b7280">${rupee(s.price)}</span></div>
        <div style="font-size:10px;color:#f87171;margin-top:2px">${s.reason||'Negative signal'}</div>
        <div style="font-size:10px;color:#4b5563">${s.change_pct>=0?'+':''}${s.change_pct}% today · ${s.sector||''}</div>
      </div>`).join('')+'</div>':
      '<span style="color:#4b5563;font-size:12px">No stocks flagged for avoidance</span>';
  }
}

function renderPositionsTab(d){
  const positions = d.positions || [];
  const totalInvested = positions.reduce((s,p)=>s+(parseFloat(p.average_price||0)*parseInt(p.quantity||0)),0);
  const totalPnl = positions.reduce((s,p)=>s+parseFloat(p.pnl||0),0);
  const reentryCount = positions.filter(p=>p.is_reentry).length;
  document.getElementById('pos-count').textContent = positions.length;
  document.getElementById('pos-invested').textContent = rupee(totalInvested);
  const pnlEl = document.getElementById('pos-pnl');
  pnlEl.textContent = pnlStr(totalPnl);
  pnlEl.className = 'stat-value ' + (totalPnl >= 0 ? 'green' : 'red');
  document.getElementById('pos-reentries').textContent = reentryCount;

  const cardsEl = document.getElementById('pos-cards');
  const tableEl = document.getElementById('pos-table');
  if (!positions.length) {
    cardsEl.innerHTML = '<div class="card" style="color:#4b5563;text-align:center;padding:40px;grid-column:1/-1">No open positions</div>';
    tableEl.innerHTML = '<tr><td colspan="14" style="text-align:center;color:#4b5563;padding:20px">No open positions</td></tr>';
    return;
  }

  const now = new Date();
  cardsEl.innerHTML = positions.map(p => {
    const sym = p.tradingsymbol || '—';
    const qty = parseInt(p.quantity || 0);
    const avg = parseFloat(p.average_price || 0);
    const ltp = parseFloat(p.last_price || avg);
    const pnl = parseFloat(p.pnl || (ltp - avg) * qty);
    const pnlPct = avg > 0 ? ((ltp - avg) / avg * 100) : 0;
    const firstEntry = parseFloat(p.first_entry_price || avg);
    const trailSL = parseFloat(p.trailing_stop != null ? p.trailing_stop : (p.stop_loss || 0));
    const target = parseFloat(p.target || 0);
    const days = parseInt(p.days_held != null ? p.days_held : 0);
    const distToSL = trailSL > 0 ? ((ltp - trailSL) / ltp * 100) : null;
    const distToTgt = target > 0 ? ((target - ltp) / ltp * 100) : null;
    const reentryN = parseInt(p.reentry_count || 0);
    const aiScore = p.trade_score != null ? Number(p.trade_score).toFixed(0) : '—';
    const isReentry = p.is_reentry;
    const reentryLabel = isReentry
      ? `<span style="background:#7c3aed;color:#fff;font-size:10px;padding:2px 7px;border-radius:10px;font-weight:700">🔄 Re-entry #${reentryN}</span>`
      : (reentryN > 0 ? `<span style="background:#1f2937;color:#a78bfa;font-size:10px;padding:2px 7px;border-radius:10px">${reentryN}× traded</span>` : '');
    const pnlColor = pnl >= 0 ? '#22c55e' : '#ef4444';
    const slPct = distToSL !== null ? `<span style="color:#ef4444">▼ ${distToSL.toFixed(1)}%</span>` : '—';
    const tgtPct = distToTgt !== null ? `<span style="color:#22c55e">▲ ${distToTgt.toFixed(1)}%</span>` : '—';
    return `<div class="card">
      <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:10px">
        <div>
          <div style="font-size:17px;font-weight:800;color:#f9fafb">${sym}</div>
          <div style="margin-top:3px">${reentryLabel}</div>
        </div>
        <div style="text-align:right">
          <div style="font-size:18px;font-weight:700;color:${pnlColor}">${pnlStr(pnl)}</div>
          <div style="font-size:12px;color:${pnlColor}">${pnlPct >= 0 ? '+' : ''}${pnlPct.toFixed(2)}%</div>
        </div>
      </div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;font-size:12px">
        <div><div style="color:#6b7280">Qty</div><div style="font-weight:600;color:#e2e8f0">${qty}</div></div>
        <div><div style="color:#6b7280">CMP</div><div style="font-weight:600;color:#e2e8f0">${rupee(ltp)}</div></div>
        <div><div style="color:#6b7280">First Entry</div><div style="font-weight:600;color:#60a5fa">${rupee(firstEntry)}</div></div>
        <div><div style="color:#6b7280">Avg Price</div><div style="font-weight:600;color:#e2e8f0">${rupee(avg)}</div></div>
        <div><div style="color:#6b7280">Days Held</div><div style="font-weight:600;color:#e2e8f0">${days}d</div></div>
        <div><div style="color:#6b7280">Trail SL</div><div style="font-weight:600;color:#ef4444">${trailSL > 0 ? rupee(trailSL) : '—'}</div></div>
        <div><div style="color:#6b7280">▼ to SL</div><div style="font-weight:600">${slPct}</div></div>
        <div><div style="color:#6b7280">▲ to Target</div><div style="font-weight:600">${tgtPct}</div></div>
        <div><div style="color:#6b7280">AI Score</div><div style="font-weight:600;color:#e2e8f0">${aiScore}</div></div>
      </div>
      ${p.prev_exit_reason ? `<div style="margin-top:8px;font-size:11px;color:#a78bfa">Prev exit: ${p.prev_exit_reason}</div>` : ''}
    </div>`;
  }).join('');

  tableEl.innerHTML = positions.map(p => {
    const sym = p.tradingsymbol || '—';
    const qty = parseInt(p.quantity || 0);
    const avg = parseFloat(p.average_price || 0);
    const ltp = parseFloat(p.last_price || avg);
    const pnl = parseFloat(p.pnl || (ltp - avg) * qty);
    const pnlPct = avg > 0 ? ((ltp - avg) / avg * 100) : 0;
    const firstEntry = parseFloat(p.first_entry_price || avg);
    const trailSL = parseFloat(p.trailing_stop != null ? p.trailing_stop : (p.stop_loss || 0));
    const target = parseFloat(p.target || 0);
    const days = parseInt(p.days_held != null ? p.days_held : 0);
    const distToSL = trailSL > 0 ? ((ltp - trailSL) / ltp * 100).toFixed(1) + '%' : '—';
    const distToTgt = target > 0 ? ((target - ltp) / ltp * 100).toFixed(1) + '%' : '—';
    const reentryN = parseInt(p.reentry_count || 0);
    const aiScore = p.trade_score != null ? Number(p.trade_score).toFixed(0) : '—';
    const reentryCell = p.is_reentry
      ? `<span style="background:#7c3aed;color:#fff;font-size:10px;padding:2px 6px;border-radius:8px">🔄 #${reentryN}</span>`
      : (reentryN > 0 ? `<span style="color:#a78bfa;font-size:11px">${reentryN}×</span>` : '<span style="color:#374151">—</span>');
    return `<tr>
      <td style="font-weight:700;color:#f9fafb">${sym}</td>
      <td style="text-align:center">${qty}</td>
      <td style="color:#60a5fa">${rupee(firstEntry)}</td>
      <td>${rupee(avg)}</td>
      <td style="font-weight:600">${rupee(ltp)}</td>
      <td class="${pnlClass(pnl)}">${pnlStr(pnl)}</td>
      <td class="${pnlClass(pnlPct)}">${pnlPct >= 0 ? '+' : ''}${pnlPct.toFixed(2)}%</td>
      <td style="text-align:center">${days}d</td>
      <td style="color:#ef4444">${trailSL > 0 ? rupee(trailSL) : '—'}</td>
      <td style="color:#ef4444">${distToSL}</td>
      <td style="color:#22c55e">${target > 0 ? rupee(target) : '—'}</td>
      <td style="color:#22c55e">${distToTgt}</td>
      <td style="text-align:center">${aiScore}</td>
      <td>${reentryCell}</td>
    </tr>`;
  }).join('');
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
    el.innerHTML='<tr><td colspan="9" style="text-align:center;color:#4b5563;padding:20px">No history yet</td></tr>';
    return;
  }
  el.innerHTML=filtered.map(o=>{
    const isBuy=(o.type||'').toUpperCase()==='BUY';
    const buyP=parseFloat(o.buy_price||0);
    const sellP=o.sell_price===null||o.sell_price===undefined?null:parseFloat(o.sell_price);
    const qty=parseInt(o.quantity||0);
    const val=parseFloat(o.total_value||0);
    const ts=fmtDateTime(o.datetime);
    const pnlVal=o.pnl===null||o.pnl===undefined?null:parseFloat(o.pnl);
    const pnlText=pnlVal===null?'—':pnlStr(pnlVal);
    const pnlClassName=pnlVal===null?'':pnlClass(pnlVal);
    const src='<span style="font-size:10px;color:#a78bfa;background:#1f2937;padding:2px 6px;border-radius:4px">'+String(o.source||'Bot')+'</span>';
    return `<tr>
      <td style="font-size:12px;color:#9ca3af;white-space:nowrap">${ts}</td>
      <td style="font-weight:700;color:#f9fafb">${o.symbol||'—'}</td>
      <td><span class="badge ${isBuy?'badge-buy':'badge-sell'}">${isBuy?'BUY':'SELL'}</span></td>
      <td style="text-align:center">${qty}</td>
      <td style="color:#60a5fa;font-family:monospace">${rupee(buyP)}</td>
      <td style="font-family:monospace">${sellP===null?'<span style="color:#4b5563">—</span>':rupee(sellP)}</td>
      <td style="font-weight:600">${rupee(val)}</td>
      <td class="${pnlClassName}">${pnlText}</td>
      <td>${src}</td>
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

function renderRetryQueue(items){
  const el=document.getElementById('h-retry-queue');
  if(!el) return;
  if(!items || !items.length){ el.innerHTML='<tr><td colspan="4" style="text-align:center;color:#4b5563;padding:20px">No queued retries</td></tr>'; return; }
  el.innerHTML=items.map(p=>{
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
let chartAlloc=null, chartSector=null, chartPortfolio=null, chartPnl=null, chartWinrate=null;
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
  if(!el)return;
  if(!notifs.length){el.innerHTML='<div style="color:#4b5563;font-size:13px;padding:20px;text-align:center">No alerts yet</div>';return;}
  el.innerHTML=notifs.slice(0,8).map(n=>`
    <div class="notif-item">
      <span style="font-size:16px">${n.icon}</span>
      <div style="flex:1"><div style="font-weight:600;font-size:13px ${n.cls?';color:'+n.cls:''}">${n.msg}</div></div>
      <div style="font-size:11px;color:#4b5563">${n.time}</div>
    </div>`).join('');
}

// ─── Data Store ───────────────────────────────────────────────────────────────
let prevData=null;

async function load(){
  try{
    const d=await fetch('/api/data').then(r=>r.json());
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
    document.getElementById('hdr-last-scan').textContent=nowStr;
    document.getElementById('hdr-next-scan').textContent=nextStr;
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

    // Heatmap
    const hm=document.getElementById('d-heatmap');
    if(d.positions&&d.positions.length){
      hm.innerHTML=d.positions.map(p=>{
        const pnl=parseFloat(p.pnl||0);
        const col=pnl>0?'#16a34a33':pnl<0?'#dc262633':'#1f2937';
        const brd=pnl>0?'#22c55e':pnl<0?'#ef4444':'#374151';
        const icon=pnl>0?'🟢':pnl<0?'🔴':'🟡';
        return `<div class="heatmap-item" style="background:${col};border:1px solid ${brd};min-width:120px">
          ${icon} ${p.tradingsymbol||p.symbol}<br>
          <span style="font-size:12px;font-weight:400" class="${pnlClass(pnl)}">${pnlStr(pnl)}</span>
        </div>`;
      }).join('');
    } else {
      hm.innerHTML='<div style="color:#4b5563;font-size:13px">No open positions</div>';
    }

    // Positions table with enhanced data
    const pb=document.getElementById('d-positions');
    const totalEl=document.getElementById('d-positions-total');
    const positions=d.positions||[];
    
    // Update holdings count
    document.getElementById('d-holdings-count').textContent = `(${positions.length})`;
    
    if(positions.length){
      // Calculate totals
      let totalInvested = 0;
      let totalCurrent = 0;
      let totalPnl = 0;
      let totalDayPnl = 0;
      let totalQty = 0;
      
      const positionsRows = positions.map(p=>{
        const qty=parseInt(p.quantity||0);
        const avg=parseFloat(p.average_price||p.entry_price||0);
        const ltp=parseFloat(p.last_price||avg);
        const closePrice=parseFloat(p.close_price||avg); // Previous close for day change
        const invested=avg*qty;
        const current=ltp*qty;
        const pnl=current-invested;
        const retPct=avg>0?((ltp-avg)/avg*100).toFixed(2):'0.00';
        const dayPct=closePrice>0?((ltp-closePrice)/closePrice*100).toFixed(2):'0.00';
        const dayPnl=(ltp-closePrice)*qty;
        
        // Get SL/Target/Score from position data (same source as Positions tab)
        const trailSL = (p.trailing_stop != null ? p.trailing_stop : (p.stop_loss || null)) > 0 ? rupee(p.trailing_stop != null ? p.trailing_stop : p.stop_loss) : '—';
        const target = p.target ? rupee(p.target) : '—';
        const aiScore = p.trade_score != null ? Number(p.trade_score).toFixed(0) : '—';
        const daysHeld = p.days_held != null ? p.days_held : '—';
        
        // Accumulate totals
        totalInvested += invested;
        totalCurrent += current;
        totalPnl += pnl;
        totalDayPnl += dayPnl;
        totalQty += qty;
        
        // Row background based on P&L
        const rowBg = pnl > 0 ? 'rgba(34, 197, 94, 0.05)' : pnl < 0 ? 'rgba(239, 68, 68, 0.05)' : '';
        
        return `<tr style="background:${rowBg}">
          <td style="font-weight:700;color:#f9fafb;padding:10px 8px">${p.tradingsymbol||p.symbol}</td>
          <td style="text-align:right;padding:10px 8px">${qty}</td>
          <td style="text-align:right;padding:10px 8px">${rupee(avg)}</td>
          <td style="text-align:right;padding:10px 8px">${rupee(ltp)}</td>
          <td style="text-align:right;padding:10px 8px">${rupee(invested)}</td>
          <td style="text-align:right;padding:10px 8px">${rupee(current)}</td>
          <td style="text-align:right;padding:10px 8px" class="${pnlClass(pnl)}">${pnlStr(pnl)}</td>
          <td style="text-align:right;padding:10px 8px" class="${pnlClass(retPct)}">${pct(retPct)}</td>
          <td style="text-align:right;padding:10px 8px" class="${pnlClass(dayPct)}">${pct(dayPct)}</td>
          <td style="text-align:center;padding:10px 8px">${daysHeld}</td>
          <td style="text-align:right;padding:10px 8px">${trailSL}</td>
          <td style="text-align:right;padding:10px 8px">${target}</td>
          <td style="text-align:right;padding:10px 8px">${aiScore}</td>
        </tr>`;
      }).join('');
      
      pb.innerHTML = positionsRows;
      
      // Update totals
      const totalChangePct = totalInvested > 0 ? ((totalCurrent - totalInvested) / totalInvested * 100).toFixed(2) : '0.00';
      const totalDayChangePct = totalCurrent > 0 ? (totalDayPnl / (totalCurrent - totalDayPnl) * 100).toFixed(2) : '0.00';
      
      document.getElementById('d-total-qty').textContent = totalQty;
      document.getElementById('d-total-invested').textContent = rupee(totalInvested);
      document.getElementById('d-total-current').textContent = rupee(totalCurrent);
      document.getElementById('d-total-pnl').textContent = pnlStr(totalPnl);
      document.getElementById('d-total-pnl').className = pnlClass(totalPnl);
      document.getElementById('d-total-change-pct').textContent = pct(totalChangePct);
      document.getElementById('d-total-change-pct').className = pnlClass(totalChangePct);
      document.getElementById('d-total-day-pct').textContent = pct(totalDayChangePct);
      document.getElementById('d-total-day-pct').className = pnlClass(totalDayChangePct);
      
      // Show totals row
      totalEl.style.display = 'table-footer-group';
      
      // Update portfolio summary
      document.getElementById('d-summary-investment').textContent = rupee(totalInvested);
      document.getElementById('d-summary-current').textContent = rupee(totalCurrent);
      document.getElementById('d-summary-day-pnl').textContent = pnlStr(totalDayPnl);
      document.getElementById('d-summary-day-pnl').className = pnlClass(totalDayPnl);
      document.getElementById('d-summary-total-pnl').textContent = pnlStr(totalPnl);
      document.getElementById('d-summary-total-pnl').className = pnlClass(totalPnl);
      
    } else {
      pb.innerHTML='<tr><td colspan="13" style="text-align:center;color:#4b5563;padding:20px">No open positions</td></tr>';
      totalEl.style.display = 'none';
      
      // Reset summary
      document.getElementById('d-summary-investment').textContent = '₹—';
      document.getElementById('d-summary-current').textContent = '₹—';
      document.getElementById('d-summary-day-pnl').textContent = '₹—';
      document.getElementById('d-summary-total-pnl').textContent = '₹—';
    }

    // AI Opportunities — tradeable BUY recommendations only
    const opp=(d.recommendations||[]).filter(s=>s.action==='BUY');
    const oppEl=document.getElementById('d-opportunities');
    if(opp.length){
      oppEl.innerHTML=opp.slice(0,7).map(s=>{
        const score=Math.round(s.overall_score||0);
        const rr2=s.stop_loss&&s.target&&s.price?Math.abs(s.target-s.price)/Math.abs(s.price-s.stop_loss):0;
        const trend=s.trend||(score>=70?'Bullish':score>=50?'Neutral':'Bearish');
        const trendStyle=trend==='Bullish'?'color:#22c55e':trend==='Bearish'?'color:#ef4444':'color:#eab308';
        return `<tr>
          <td style="font-weight:700;color:#f9fafb">${s.symbol}</td>
          <td><span style="font-size:16px;font-weight:800;color:${scoreColor(score)}">${score}</span><span class="score-bar" style="background:${scoreColor(score)};width:${score*0.4}px"></span></td>
          <td style="${trendStyle}">${trend}</td>
          <td>${rupee(s.price)}</td>
          <td class="green">${rupee(s.target)}</td>
          <td>${riskLabel(rr2)}</td>
        </tr>`;
      }).join('');
    } else {
      oppEl.innerHTML='<tr><td colspan="6" style="text-align:center;color:#4b5563;padding:20px">No tradeable opportunities right now</td></tr>';
    }

    // Market overview
    const ms2=d.market_summary||{};
    const nChg=parseFloat(ms2.nifty_change||0);
    const bChg=parseFloat(ms2.banknifty_change||0);
    const nEl=document.getElementById('d-nifty');
    nEl.textContent=pct(nChg);nEl.className='stat-value-sm '+(nChg>=0?'green':'red');
    const bEl=document.getElementById('d-banknifty');
    bEl.textContent=pct(bChg);bEl.className='stat-value-sm '+(bChg>=0?'green':'red');
    const vix=parseFloat(ms2.vix||0);
    const vixEl=document.getElementById('d-vix');
    vixEl.textContent=vix.toFixed(1);
    vixEl.className='stat-value-sm '+(vix<15?'green':vix<20?'yellow':'red');
    document.getElementById('d-vix-label').textContent=vix<15?'🟢 LOW FEAR':vix<20?'🟡 MODERATE':'🔴 HIGH FEAR';
    const regEl=document.getElementById('d-regime');
    regEl.textContent=ms2.market_regime||d.market_regime||'—';
    regEl.className='stat-value-sm '+(d.market_regime==='BULL'?'green':d.market_regime==='BEAR'?'red':'yellow');

    // Sectors
    const secEl=document.getElementById('d-sectors');
    const sec=an.sector_allocation||{};
    const TREND_ICONS=['↑↑','↑','→','↓'];
    if(Object.keys(sec).length){
      secEl.innerHTML=Object.entries(sec).sort((a,b)=>b[1]-a[1]).map(([k,v])=>{
        const icon=v>30?'↑↑':v>20?'↑':v>10?'→':'↓';
        const col=v>20?'#22c55e':v>10?'#eab308':'#9ca3af';
        return `<span style="font-size:12px;font-weight:600;color:${col};background:#1f2937;padding:3px 10px;border-radius:6px">${k} ${icon}</span>`;
      }).join('');
    } else {
      secEl.innerHTML='<span style="color:#4b5563;font-size:12px">No positions</span>';
    }

    // Recent orders
    const ordEl=document.getElementById('d-recent-orders');
    const allOrders=(d.orders||[]).slice().reverse().slice(0,10);
    if(allOrders.length){
      ordEl.innerHTML=allOrders.map(o=>{
        const isBuy=o.transaction_type==='BUY';
        const t=fmtTime(o.order_timestamp)||'—';
        const amt=parseFloat(o.average_price||o.price||0)*parseInt(o.quantity||0);
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
      ordEl.innerHTML='<div style="color:#4b5563;font-size:13px;padding:20px;text-align:center">No orders today</div>';
    }

    // Daily goals
    const profitGoal=100;
    const lossLimit=250;
    const capitalGoalPct=70;
    const pnlPct=Math.min(100,Math.max(0,dpnl/profitGoal*100));
    const lossPct=Math.min(100,Math.max(0,Math.abs(Math.min(0,dpnl))/lossLimit*100));
    const capUsed=Math.min(100,Math.round((parseFloat(d.invested||0)/budget)*100));
    document.getElementById('d-goal-profit-val').textContent=rupee(Math.max(0,dpnl))+' / '+rupee(profitGoal);
    document.getElementById('d-goal-profit-bar').style.width=pnlPct+'%';
    document.getElementById('d-goal-capital-val').textContent=capUsed+'%';
    document.getElementById('d-goal-capital-bar').style.width=Math.min(100,capUsed/capitalGoalPct*100)+'%';
    document.getElementById('d-goal-loss-val').textContent=rupee(Math.abs(Math.min(0,dpnl)))+' / '+rupee(lossLimit);
    document.getElementById('d-goal-loss-bar').style.width=lossPct+'%';

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
    document.getElementById('p-account-balance').textContent=rupee(d.account_balance||0);
    document.getElementById('p-cash').textContent=rupee(d.cash||0);
    const pmEl=document.getElementById('p-margin');
    const marginUsed=Math.abs(parseFloat(d.margin_blocked||0));
    pmEl.textContent=rupee(marginUsed);
    document.getElementById('p-holdings-val').textContent=rupee(d.holdings_value||0);

    // Holdings table with enhanced data
    const hldEl=document.getElementById('p-holdings');
    const pTotalEl=document.getElementById('p-holdings-total');
    const activeHoldings=(d.holdings||[]).filter(h=>parseInt(h.quantity||0)>0);
    
    // Update holdings count
    document.getElementById('holdings-count').textContent = `(${activeHoldings.length})`;
    
    if(activeHoldings.length){
      // Calculate totals
      let totalInvested = 0;
      let totalCurrent = 0;
      let totalPnl = 0;
      let totalDayPnl = 0;
      let totalQty = 0;
      
      const holdingsRows = activeHoldings.map(h=>{
        const qty=parseInt(h.quantity||0);
        const avg=parseFloat(h.average_price||0);
        const ltp=parseFloat(h.last_price||avg);
        const closePrice=parseFloat(h.close_price||avg); // Previous close for day change
        const invested=avg*qty;
        const current=ltp*qty;
        const pnl=current-invested;
        const retPct=avg>0?((ltp-avg)/avg*100).toFixed(2):'0.00';
        const dayPct=closePrice>0?((ltp-closePrice)/closePrice*100).toFixed(2):'0.00';
        const dayPnl=(ltp-closePrice)*qty;
        
        // Get position data for SL/Target/AI Score (same source as Positions tab)
        const position = (d.positions || []).find(p => p.tradingsymbol === h.tradingsymbol);
        const rawSL = position ? (position.trailing_stop != null ? position.trailing_stop : position.stop_loss) : 0;
        const trailSL = rawSL > 0 ? rupee(rawSL) : '—';
        const target = position && position.target > 0 ? rupee(position.target) : '—';
        const aiScore = position && position.trade_score != null ? Number(position.trade_score).toFixed(0) : '—';
        const daysHeld = position && position.days_held != null ? position.days_held : '—';
        
        // Progress bar for price relative to SL and Target
        let progressBar = '';
        if (position && position.stop_loss && position.target && ltp > 0) {
          const sl = position.stop_loss;
          const tgt = position.target;
          const range = tgt - sl;
          const position_pct = ((ltp - sl) / range * 100).toFixed(0);
          const clamped_pct = Math.max(0, Math.min(100, position_pct));
          progressBar = `
            <div style="width:60px;height:6px;background:#1f2937;border-radius:3px;overflow:hidden">
              <div style="width:${clamped_pct}%;height:100%;background:${clamped_pct < 50 ? '#ef4444' : clamped_pct < 80 ? '#f59e0b' : '#10b981'};transition:width 0.3s"></div>
            </div>
          `;
        }
        
        // Accumulate totals
        totalInvested += invested;
        totalCurrent += current;
        totalPnl += pnl;
        totalDayPnl += dayPnl;
        totalQty += qty;
        
        // Row background based on P&L
        const rowBg = pnl > 0 ? 'rgba(34, 197, 94, 0.05)' : pnl < 0 ? 'rgba(239, 68, 68, 0.05)' : '';
        
        return `<tr style="background:${rowBg}">
          <td style="font-weight:700;color:#f9fafb;padding:10px 8px">${h.tradingsymbol}</td>
          <td style="text-align:right;padding:10px 8px">${qty}</td>
          <td style="text-align:right;padding:10px 8px">${rupee(avg)}</td>
          <td style="text-align:right;padding:10px 8px">${rupee(ltp)}</td>
          <td style="text-align:right;padding:10px 8px">${rupee(invested)}</td>
          <td style="text-align:right;padding:10px 8px">${rupee(current)}</td>
          <td style="text-align:right;padding:10px 8px" class="${pnlClass(pnl)}">${pnlStr(pnl)}</td>
          <td style="text-align:right;padding:10px 8px" class="${pnlClass(retPct)}">${pct(retPct)}</td>
          <td style="text-align:right;padding:10px 8px" class="${pnlClass(dayPct)}">${pct(dayPct)}</td>
          <td style="text-align:center;padding:10px 8px">${daysHeld}</td>
          <td style="text-align:right;padding:10px 8px">${trailSL}</td>
          <td style="text-align:right;padding:10px 8px">${target}</td>
          <td style="text-align:right;padding:10px 8px">${aiScore}</td>
        </tr>`;
      }).join('');
      
      hldEl.innerHTML = holdingsRows;
      
      // Update totals
      const totalChangePct = totalInvested > 0 ? ((totalCurrent - totalInvested) / totalInvested * 100).toFixed(2) : '0.00';
      const totalDayChangePct = totalCurrent > 0 ? (totalDayPnl / (totalCurrent - totalDayPnl) * 100).toFixed(2) : '0.00';
      
      document.getElementById('total-qty').textContent = totalQty;
      document.getElementById('p-total-invested').textContent = rupee(totalInvested);
      document.getElementById('p-total-current').textContent = rupee(totalCurrent);
      document.getElementById('p-total-pnl').textContent = pnlStr(totalPnl);
      document.getElementById('p-total-pnl').className = pnlClass(totalPnl);
      document.getElementById('p-total-change-pct').textContent = pct(totalChangePct);
      document.getElementById('p-total-change-pct').className = pnlClass(totalChangePct);
      document.getElementById('p-total-day-pct').textContent = pct(totalDayChangePct);
      document.getElementById('p-total-day-pct').className = pnlClass(totalDayChangePct);
      
      // Show totals row
      totalEl.style.display = 'table-footer-group';
      
      // Update portfolio summary
      document.getElementById('p-summary-investment').textContent = rupee(totalInvested);
      document.getElementById('p-summary-current').textContent = rupee(totalCurrent);
      document.getElementById('p-summary-day-pnl').textContent = pnlStr(totalDayPnl);
      document.getElementById('p-summary-day-pnl').className = pnlClass(totalDayPnl);
      document.getElementById('p-summary-total-pnl').textContent = pnlStr(totalPnl);
      document.getElementById('p-summary-total-pnl').className = pnlClass(totalPnl);
      
    } else {
      hldEl.innerHTML='<tr><td colspan="13" style="text-align:center;color:#4b5563;padding:20px">No delivery holdings</td></tr>';
      pTotalEl.style.display = 'none';
      
      // Reset summary
      document.getElementById('p-summary-investment').textContent = '₹—';
      document.getElementById('p-summary-current').textContent = '₹—';
      document.getElementById('p-summary-day-pnl').textContent = '₹—';
      document.getElementById('p-summary-total-pnl').textContent = '₹—';
    }

    // Recent Activity table
    const thEl=document.getElementById('p-trade-history');
    const allOrd=(d.all_orders||[]);
    if(allOrd.length){
      thEl.innerHTML=allOrd.slice(0,5).map(o=>{
        const isBuy=o.transaction_type==='BUY';
        const isSell=o.transaction_type==='SELL';
        const qty=parseInt(o.quantity||0);
        const buyP=parseFloat(o.buy_price||o.average_price||0);
        const sellP=parseFloat(o.sell_price||o.average_price||0);
        const pnl=parseFloat(o.pnl||0);
        const pnlPct=(isSell&&buyP>0)?((sellP-buyP)/buyP*100):0;
        const ts=fmtDateTime(o.order_timestamp);
        // Buy Price column: show for both BUY and SELL rows
        const buyCell=isSell
          ?`<td style="color:#60a5fa;font-family:monospace">${rupee(buyP)}</td>`
          :`<td style="color:#60a5fa;font-family:monospace">${rupee(buyP)}</td>`;
        // Sell Price column: only meaningful for SELL rows
        const sellCell=isSell
          ?`<td style="color:#f9fafb;font-family:monospace">${rupee(sellP)}</td>`
          :`<td style="color:#4b5563">—</td>`;
        // P&L cell: only for SELLs
        const pnlCell=isSell
          ?`<td class="${pnlClass(pnl)}" style="font-weight:700">${pnlStr(pnl)}</td>`
          :`<td style="color:#4b5563">—</td>`;
        const pnlPctCell=isSell
          ?`<td class="${pnlClass(pnlPct)}" style="font-size:12px">${pct(pnlPct,1)}</td>`
          :`<td style="color:#4b5563">—</td>`;
        return `<tr style="border-bottom:1px solid #1f2937">
          <td style="font-size:12px;color:#9ca3af;padding:8px 6px">${ts}</td>
          <td style="font-weight:700;color:#f9fafb;padding:8px 6px">${o.tradingsymbol}</td>
          <td style="padding:8px 4px"><span class="badge ${isBuy?'badge-buy':'badge-sell'}" style="font-size:11px">${o.transaction_type}</span></td>
          <td style="text-align:center;padding:8px 4px">${qty}</td>
          ${buyCell}${sellCell}${pnlCell}${pnlPctCell}
        </tr>`;
      }).join('');
    } else {
      thEl.innerHTML='<tr><td colspan="8" style="text-align:center;color:#4b5563;padding:20px">No recent trades</td></tr>';
    }

    // ── TAB: POSITIONS ───────────────────────────────────────────
    renderPositionsTab(d);

    // ── TAB: HISTORY ─────────────────────────────────────────────
    const hCash=parseFloat(d.cash||0);
    const hHeld=parseFloat(d.holdings_value||0);
    const hPositions=d.positions||[];
    const hInvested=hPositions.reduce((s,p)=>s+parseFloat(p.average_price||0)*parseInt(p.quantity||0),0);
    const hCurrentVal=hPositions.reduce((s,p)=>s+parseFloat(p.last_price||p.average_price||0)*parseInt(p.quantity||0),0);
    const hTotal=hCash+hCurrentVal;
    document.getElementById('h-cash').textContent=rupee(hCash);
    document.getElementById('h-invested').textContent=rupee(hInvested);
    document.getElementById('h-holdings-val').textContent=rupee(hCurrentVal);
    document.getElementById('h-total').textContent=rupee(hTotal);

    // Stock breakdown
    const sbEl=document.getElementById('h-stock-breakdown');
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
          <td>${rupee(ltp)}</td>
          <td>${rupee(invested)}</td>
          <td>${rupee(curVal)}</td>
          <td class="${pnlClass(pnl)}">${pnlStr(pnl)}</td>
          <td class="${pnlClass(retPct)}">${retPct.toFixed(2)}%</td>
        </tr>`;
      }).join('');
    } else {
      sbEl.innerHTML='<tr><td colspan="8" style="text-align:center;color:#4b5563;padding:20px">No open positions</td></tr>';
    }

    // Trade cards + event history + retry queue
    renderTradeCards(d.trade_cards || []);
    renderRetryQueue(d.pending_sells || []);
    window._historyData=d.trade_events||[];
    renderHistory(window._historyFilter||'ALL');

    // Allocation Chart
    const cash2=parseFloat(d.cash||0);
    const invested2=parseFloat(d.invested||0);
    const held=parseFloat(d.holdings_value||0);
    const margB=parseFloat(d.margin_blocked||0);
    if(typeof Chart!=='undefined'){
    const allocCtx=document.getElementById('chart-allocation');
    if(allocCtx){
      const allocCfg={type:'doughnut',data:{labels:['Cash','Invested','Holdings','Margin'],datasets:[{data:[cash2,invested2,held,margB],backgroundColor:['#22c55e','#3b82f6','#a78bfa','#ef4444'],borderWidth:0}]},options:{plugins:{legend:{labels:{color:'#9ca3af',font:{size:11}}}},cutout:'65%',maintainAspectRatio:false}};
      if(chartAlloc){chartAlloc.data=allocCfg.data;chartAlloc.update();}else{chartAlloc=new Chart(allocCtx,allocCfg);}
    }
    const secData=Object.entries(an.sector_allocation||{Cash:100});
    const secCtx=document.getElementById('chart-sector');
    if(secCtx){
      const secCfg={type:'doughnut',data:{labels:secData.map(s=>s[0]),datasets:[{data:secData.map(s=>s[1]),backgroundColor:CHART_COLORS,borderWidth:0}]},options:{plugins:{legend:{labels:{color:'#9ca3af',font:{size:11}}}},cutout:'55%',maintainAspectRatio:false}};
      if(chartSector){chartSector.data=secCfg.data;chartSector.update();}else{chartSector=new Chart(secCtx,secCfg);}
    }
    const portCtx=document.getElementById('chart-portfolio');
    if(portCtx&&!chartPortfolio){
      chartPortfolio=new Chart(portCtx,{type:'line',data:{labels:['9:30','10:00','10:30','11:00','11:30','12:00','12:30','1:00','1:30','Now'],datasets:[{label:'Portfolio',data:[portVal-50,portVal-30,portVal-40,portVal-20,portVal-10,portVal+5,portVal+20,portVal+dpnl*0.3,portVal+dpnl*0.7,portVal],borderColor:'#3b82f6',backgroundColor:'#3b82f611',fill:true,tension:0.4,pointRadius:2}]},options:{scales:{x:{ticks:{color:'#4b5563',font:{size:10}}},y:{ticks:{color:'#4b5563',font:{size:10},callback:v=>'₹'+v.toLocaleString('en-IN')}}},plugins:{legend:{display:false}},maintainAspectRatio:false}});
    }
    const pnlCtx=document.getElementById('chart-pnl');
    if(pnlCtx&&!chartPnl){
      const days=['Mon','Tue','Wed','Thu','Fri'];
      const vals=[52,-10,84,12,dpnl];
      chartPnl=new Chart(pnlCtx,{type:'bar',data:{labels:days,datasets:[{data:vals,backgroundColor:vals.map(v=>v>=0?'#16a34a88':'#dc262688'),borderRadius:4}]},options:{scales:{x:{ticks:{color:'#4b5563'}},y:{ticks:{color:'#4b5563',callback:v=>'₹'+v}}},plugins:{legend:{display:false}},maintainAspectRatio:false}});
    }
    } // end Chart guard

    // ── TAB: AI SIGNALS ───────────────────────────────────────────────────────
    if (typeof renderAiSignals === 'function') renderAiSignals(d);

    // ── TAB: ANALYTICS ────────────────────────────────────────────────────────
    const strat=d.strategy_stats||{};
    const portValAn=parseFloat(ph.account_value||0);
    const retToday=portValAn>0?(dpnl/portValAn*100).toFixed(2):0;
    const wPnl=parseFloat(d.weekly_pnl||0);
    const mPnl=parseFloat(d.monthly_pnl||0);
    const retW=portValAn>0?(wPnl/portValAn*100).toFixed(2):0;
    const retM=portValAn>0?(mPnl/portValAn*100).toFixed(2):0;

    const aTodEl=document.getElementById('a-today-ret');
    aTodEl.textContent=pct(retToday);aTodEl.className='stat-value '+(dpnl>=0?'green':'red');
    const aWEl=document.getElementById('a-weekly-ret');
    aWEl.textContent=pct(retW);aWEl.className='stat-value '+(wPnl>=0?'green':'red');
    const aMEl=document.getElementById('a-monthly-ret');
    aMEl.textContent=pct(retM);aMEl.className='stat-value '+(mPnl>=0?'green':'red');
    const wrEl=document.getElementById('a-win-rate');
    const wr=parseFloat(d.win_rate||0)*100;
    wrEl.textContent=wr.toFixed(0)+'%';wrEl.className='stat-value '+(wr>=60?'green':wr>=40?'yellow':'red');
    // Profit factor: ∞ when no losses exist
    const apfVal=parseFloat(strat.profit_factor||0);
    const aNoLosses=(strat.avg_loss||0)===0&&(strat.avg_win||0)>0;
    document.getElementById('a-profit-factor').textContent=aNoLosses?'∞':apfVal.toFixed(2);
    document.getElementById('a-avg-win').textContent=rupee(strat.avg_win||0);
    document.getElementById('a-avg-loss').textContent=rupee(strat.avg_loss||0);
    document.getElementById('a-expectancy').textContent=rupee(strat.expectancy||0);
    const awPnlEl=document.getElementById('a-weekly-pnl');
    awPnlEl.textContent=pnlStr(wPnl);awPnlEl.className='stat-value '+(wPnl>=0?'green':'red');
    const amPnlEl=document.getElementById('a-monthly-pnl');
    amPnlEl.textContent=pnlStr(mPnl);amPnlEl.className='stat-value '+(mPnl>=0?'green':'red');
    const ddEl=document.getElementById('a-max-drawdown');
    ddEl.textContent=parseFloat(ph.drawdown_pct||0).toFixed(2)+'%';
    document.getElementById('a-total-trades').textContent=d.total_trades||0;

    // Win rate gauge (doughnut)
    if(typeof Chart!=='undefined'){
    const wrCtx=document.getElementById('chart-winrate');
    if(wrCtx){
      const wrVal=Math.round(wr);
      const wrCfg={type:'doughnut',data:{labels:['Win','Loss'],datasets:[{data:[wrVal,100-wrVal],backgroundColor:[wrVal>=60?'#22c55e':wrVal>=40?'#eab308':'#ef4444','#1f2937'],borderWidth:0}]},options:{plugins:{legend:{display:false},tooltip:{enabled:false},beforeDraw(chart){const {ctx,chartArea:{top,left,width,height}}=chart;ctx.save();ctx.font='bold 28px Inter';ctx.fillStyle='#f9fafb';ctx.textAlign='center';ctx.textBaseline='middle';ctx.fillText(wrVal+'%',left+width/2,top+height/2);ctx.restore();}},cutout:'70%',maintainAspectRatio:false}};
      if(chartWinrate){chartWinrate.data=wrCfg.data;chartWinrate.update();}else{chartWinrate=new Chart(wrCtx,wrCfg);}
    }
    } // end Chart guard

    // Trade Calendar (realized P&L per weekday, from backend)
    const calEl=document.getElementById('a-calendar');
    const wc=(d.weekly_calendar||[0,0,0,0,0]).map(v=>parseFloat(v||0));
    const weekDays=[{d:'Mon',v:wc[0]},{d:'Tue',v:wc[1]},{d:'Wed',v:wc[2]},{d:'Thu',v:wc[3]},{d:'Fri',v:wc[4]}];
    calEl.innerHTML=weekDays.map(({d:day,v})=>`
      <div style="text-align:center;flex:1">
        <div style="font-size:11px;color:#4b5563;margin-bottom:4px;white-space:nowrap">${day}</div>
        <div style="padding:8px 4px;border-radius:8px;font-size:13px;font-weight:700;background:${v>=0?'#16a34a22':'#dc262622'};color:${v>=0?'#22c55e':'#ef4444'}">${v>=0?'+':''}${v.toFixed(0)}</div>
      </div>`).join('');

    // Trade history
    const ahEl=document.getElementById('a-history');
    if(d.orders&&d.orders.length){
      ahEl.innerHTML=d.orders.map(o=>{
        const isBuy=o.transaction_type==='BUY';
        const t=fmtTime(o.order_timestamp)||'—';
        const amt=parseFloat(o.average_price||o.price||0)*parseInt(o.quantity||0);
        const opnl=parseFloat(o.pnl||0);
        return `<tr>
          <td style="color:#6b7280">${t}</td>
          <td style="font-weight:700;color:#f9fafb">${o.tradingsymbol}</td>
          <td><span class="badge ${isBuy?'badge-buy':'badge-sell'}">${o.transaction_type}</span></td>
          <td style="text-align:center">${o.quantity}</td>
          <td>${rupee(o.average_price||o.price||0)}</td>
          <td>${rupee(amt)}</td>
          <td style="color:${o.status==='COMPLETE'?'#22c55e':'#eab308'};font-size:11px">${o.status}</td>
          <td class="${pnlClass(opnl)}">${isBuy?'—':pnlStr(opnl)}</td>
        </tr>`;
      }).join('');
    } else {
      ahEl.innerHTML='<tr><td colspan="8" style="text-align:center;color:#4b5563;padding:20px">No trades today</td></tr>';
    }

    // ── TAB: BOT STATUS ───────────────────────────────────────────────────────
    document.getElementById('bs-kite').innerHTML=d.kite_ok?'<span class="green">✅ Connected</span>':'<span class="red">❌ Offline</span>';
    document.getElementById('bs-mode').textContent=(d.trading_mode||'swing').toUpperCase();
    document.getElementById('bs-token').textContent=fmtDateTime(d.token_expiry,true)||'—';
    document.getElementById('bs-token2').textContent=fmtDateTime(d.token_expiry,true)||'—';
    document.getElementById('bs-paper').innerHTML=d.paper_trading?'<span class="yellow">⚠️ Paper Mode</span>':'<span class="green">✅ Live Trading</span>';
    const bsrEl=document.getElementById('bs-regime');
    bsrEl.textContent=d.market_regime||'—';bsrEl.className='stat-value '+(d.market_regime==='BULL'?'green':d.market_regime==='BEAR'?'red':'yellow');
    document.getElementById('bs-budget').textContent=rupee(d.budget||0);
    document.getElementById('bs-last-scan').textContent=nowStr;
    document.getElementById('bs-next-scan').textContent=nextStr;
    document.getElementById('bs-scanned').textContent=d.stocks_scanned||'—';
    document.getElementById('bs-ai-signals').textContent=(d.signals||[]).length;
    document.getElementById('bs-orders-exec').textContent=d.total_trades||0;
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

    prevData=d;

  }catch(e){
    console.error('Dashboard error:',e);
    document.getElementById('last-updated').textContent='JS ERROR: '+e.message;
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
    // Also populate Bot Status tab win rate
    const bsWrEl=document.getElementById('bs-winrate');
    if(bsWrEl){bsWrEl.textContent=wrVal.toFixed(1)+'%';bsWrEl.className='stat-value '+(wrVal>=60?'green':wrVal>=40?'yellow':'red');}
    wrEl.textContent=wrVal.toFixed(1)+'%';
    wrEl.className='stat-value '+(wrVal>=60?'green':wrVal>=40?'yellow':'red');
    const npEl=document.getElementById('j-netpnl');
    npEl.textContent=(np>=0?'+':'-')+rupee(Math.abs(np));
    npEl.className='stat-value '+(np>=0?'green':'red');
    // Profit factor: ∞ when no losses exist
    const pfVal=parseFloat(j.profit_factor||0);
    const noLosses=(j.avg_loss||0)===0&&(j.avg_win||0)>0&&(j.total_trades||0)>0;
    document.getElementById('j-pf').textContent=noLosses?'∞':pfVal.toFixed(2);
    document.getElementById('j-avgwin').textContent=rupee(j.avg_win||0);
    document.getElementById('j-avgloss').textContent=rupee(j.avg_loss||0);
    document.getElementById('j-avgscore').textContent=(j.avg_score||0).toFixed(1)+'/100';
    document.getElementById('j-avghold').textContent=(j.avg_hold_days||0).toFixed(1)+' days';

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

    // Score bucket bar chart
    const sb=j.by_score_bucket||{};
    const sbLabels=Object.keys(sb);
    const sbWR=sbLabels.map(k=>sb[k].win_rate||0);
    const sbPnl=sbLabels.map(k=>sb[k].net_pnl||0);
    const sbCtx=document.getElementById('j-chart-scorebucket');
    if(sbCtx&&sbLabels.length){
      const cfg={type:'bar',data:{
        labels:sbLabels,
        datasets:[
          {label:'Win Rate %',data:sbWR,backgroundColor:sbWR.map(v=>v>=60?'#16a34a88':'#dc262688'),borderRadius:4,yAxisID:'y'},
          {label:'Net P&L',data:sbPnl,type:'line',borderColor:'#60a5fa',pointRadius:3,yAxisID:'y2'}
        ]
      },options:{scales:{
        x:{ticks:{color:'#4b5563'}},
        y:{ticks:{color:'#4b5563',callback:v=>v+'%'},max:100,min:0,title:{display:true,text:'Win Rate %',color:'#4b5563'}},
        y2:{position:'right',ticks:{color:'#60a5fa',callback:v=>'₹'+v},grid:{drawOnChartArea:false}}
      },plugins:{legend:{labels:{color:'#9ca3af',font:{size:11}}}},maintainAspectRatio:false}};
      if(jChartScoreBucket){jChartScoreBucket.data=cfg.data;jChartScoreBucket.update();}else{jChartScoreBucket=new Chart(sbCtx,cfg);}
    }

    // Sector bar chart
    const sec=j.by_sector||{};
    const secL=Object.keys(sec);
    const secPnl=secL.map(k=>sec[k].net_pnl||0);
    const secCtx2=document.getElementById('j-chart-sector');
    if(secCtx2&&secL.length){
      const cfg={type:'bar',data:{
        labels:secL,
        datasets:[{label:'Net P&L',data:secPnl,backgroundColor:secPnl.map(v=>v>=0?'#16a34a88':'#dc262688'),borderRadius:4}]
      },options:{scales:{x:{ticks:{color:'#4b5563'}},y:{ticks:{color:'#4b5563',callback:v=>'₹'+v}}},plugins:{legend:{display:false}},maintainAspectRatio:false,indexAxis:'y'}};
      if(jChartSector){jChartSector.data=cfg.data;jChartSector.update();}else{jChartSector=new Chart(secCtx2,cfg);}
    }

    // Exit reason chart
    const ex=j.by_exit_reason||{};
    const exL=Object.keys(ex);
    const exPnl=exL.map(k=>ex[k].net_pnl||0);
    const exCtx=document.getElementById('j-chart-exit');
    if(exCtx&&exL.length){
      const cfg={type:'bar',data:{
        labels:exL,
        datasets:[{label:'Net P&L',data:exPnl,backgroundColor:exPnl.map(v=>v>=0?'#16a34a88':'#dc262688'),borderRadius:4}]
      },options:{scales:{x:{ticks:{color:'#4b5563'}},y:{ticks:{color:'#4b5563',callback:v=>'₹'+v}}},plugins:{legend:{display:false}},maintainAspectRatio:false}};
      if(jChartExit){jChartExit.data=cfg.data;jChartExit.update();}else{jChartExit=new Chart(exCtx,cfg);}
    }

    // Day of week chart
    const dow=j.by_day_of_week||{};
    const DOW_ORDER=['Monday','Tuesday','Wednesday','Thursday','Friday'];
    const dowL=DOW_ORDER.filter(d=>dow[d]);
    const dowPnl=dowL.map(d=>dow[d].net_pnl||0);
    const dowCtx=document.getElementById('j-chart-dow');
    if(dowCtx&&dowL.length){
      const cfg={type:'bar',data:{
        labels:dowL.map(d=>fmtDayShort(d)),
        datasets:[{label:'Net P&L',data:dowPnl,backgroundColor:dowPnl.map(v=>v>=0?'#16a34a88':'#dc262688'),borderRadius:4}]
      },options:{scales:{x:{ticks:{color:'#4b5563'}},y:{ticks:{color:'#4b5563',callback:v=>'₹'+v}}},plugins:{legend:{display:false}},maintainAspectRatio:false}};
      if(jChartDow){jChartDow.data=cfg.data;jChartDow.update();}else{jChartDow=new Chart(dowCtx,cfg);}
    }

    // Regime chart
    const reg=j.by_regime||{};
    const regL=Object.keys(reg);
    const regWR=regL.map(k=>reg[k].win_rate||0);
    const regCtx=document.getElementById('j-chart-regime');
    if(regCtx&&regL.length){
      const cfg={type:'bar',data:{
        labels:regL,
        datasets:[{label:'Win Rate %',data:regWR,backgroundColor:regWR.map(v=>v>=60?'#16a34a88':'#ca8a0488'),borderRadius:4}]
      },options:{scales:{x:{ticks:{color:'#4b5563'}},y:{ticks:{color:'#4b5563',callback:v=>v+'%'},max:100,min:0}},plugins:{legend:{display:false}},maintainAspectRatio:false}};
      if(jChartRegime){jChartRegime.data=cfg.data;jChartRegime.update();}else{jChartRegime=new Chart(regCtx,cfg);}
    }
    } // end Chart guard

    // Trade log table — merge open + closed trades, newest first
    const closedTrades=j.recent_trades||[];
    const openTrades=j.open_trade_log||[];
    // Mark open trades so we can badge them
    openTrades.forEach(t=>{t._is_open=true;});
    const allTrades=[...openTrades,...closedTrades];
    const jTbl=document.getElementById('j-trade-log');
    if(allTrades.length){
      jTbl.innerHTML=allTrades.map(t=>{
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
        return `<tr>
          <td style="color:#6b7280;white-space:nowrap">${isOpen?(t.date||'—'):(t.exit_date||t.date||'—')}</td>
          <td style="font-weight:700;color:#f9fafb">${t.symbol}${reentryBadge}</td>
          <td>${statusCell}</td>
          <td style="color:${scoreColor(sc)};font-weight:700;text-align:center">${sc||'—'}</td>
          <td style="color:${regCol};font-size:11px;text-align:center">${t.market_regime||'—'}</td>
          <td style="color:#9ca3af;font-size:12px">${t.sector||'—'}</td>
          <td>${rupee(t.entry_price||0)}</td>
          <td>${isOpen?'<span style="color:#4b5563">—</span>':(t.exit_price?rupee(t.exit_price):'—')}</td>
          <td style="text-align:center">${(()=>{if(isOpen){const d0=new Date(t.date||'');const now=new Date();const diff=d0&&!isNaN(d0)?Math.floor((now-d0)/86400000):0;return diff+'d ongoing';}else{const d0=new Date(t.date||'');const d1=new Date(t.exit_date||t.date||'');const diff=d0&&d1&&!isNaN(d0)&&!isNaN(d1)?Math.floor((d1-d0)/86400000):0;return diff+'d';}})()}</td>
          <td style="color:${sentCol};font-size:11px;text-align:center">${sentiment||'—'}</td>
          <td style="text-align:center;font-size:12px">${t.rsi?t.rsi.toFixed(0):'—'}</td>
          <td style="font-size:11px">${t.trend||'—'}</td>
          <td style="font-size:11px;color:#9ca3af;max-width:160px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis" title="${t.exit_reason||t.buy_reason||''}">${(()=>{if(isOpen)return '<span style="color:#4b5563">holding</span>';const r=t.exit_reason||'';const lbl={'kite_order':'Manual Sell','stop_loss':'🛑 SL Hit','target':'🎯 Target Hit','max_hold':'⏰ Max Hold','rsi_overbought':'📈 RSI>80','signal_reversal':'🔄 Reversal','trailing_stop':'🔔 Trail SL'};return lbl[r]||r||'—';})()}</td>
          ${pnlCell}
        </tr>`;
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

// ─── AI Explainability ───────────────────────────────────────────────────────
async function loadExplainability(){
  try{
    const r=await fetch('/api/explain');
    const d=await r.json();
    const actions=d.actions||[];
    const tbody=document.getElementById('explain-table');
    if(actions.length===0){
      tbody.innerHTML='<tr><td colspan="10" style="text-align:center;color:#4b5563;padding:20px">No decisions recorded yet.</td></tr>';
      return;
    }
    const rupee=(n)=>{n=parseFloat(n)||0; return '₹'+n.toFixed(2);};
    const fmtTime=(ts)=>{try{return new Date(ts).toLocaleString('en-IN',{hour:'2-digit',minute:'2-digit',second:'2-digit'})}catch(e){return ts||'—';}};
    tbody.innerHTML=actions.map(a=>{
      const isBuy=a.action==='BUY';
      const isSell=(typeof a.action==='string') && a.action.startsWith('SELL');
      const actionColor=isBuy?'#16a34a':isSell?'#dc2626':'#9ca3af';
      const pnl=parseFloat(a.pnl||0);
      const pnlColor=pnl>0?'#16a34a':pnl<0?'#dc2626':'#9ca3af';
      return `<tr style="border-bottom:1px solid #1f2937">
        <td style="padding:8px;color:#9ca3af;font-family:monospace;font-size:11px">${fmtTime(a.timestamp)}</td>
        <td style="padding:8px;font-weight:700;color:#f9fafb">${a.symbol||'—'}</td>
        <td style="padding:8px;color:${actionColor};font-weight:600">${a.action||'—'}</td>
        <td style="padding:8px;color:#d1d5db;font-size:12px;max-width:300px;white-space:normal">${a.reason||'—'}</td>
        <td style="padding:8px;color:#60a5fa">${a.score!=null?a.score.toFixed(1):'—'}</td>
        <td style="padding:8px;color:#f59e0b">${a.confidence!=null?(a.confidence*100).toFixed(0)+'%':'—'}</td>
        <td style="padding:8px;color:${pnlColor}">${pnl!==0?rupee(pnl):'—'}</td>
        <td style="padding:8px;color:#9ca3af">${a.price?rupee(a.price):'—'}</td>
        <td style="padding:8px;color:#9ca3af">${a.quantity||'—'}</td>
        <td style="padding:8px;color:#9ca3af">${a.sector||'Unknown'}</td>
      </tr>`;
    }).join('');
  }catch(e){
    console.error('Explainability load error:',e);
    document.getElementById('explain-table').innerHTML='<tr><td colspan="10" style="text-align:center;color:#dc2626;padding:20px">Error loading explanations</td></tr>';
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
    const d=await fetch('/api/data').then(r=>r.json());
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
setInterval(load,60000);
setInterval(loadJournal,120000);

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
        "market_data_metrics": {}
    }

    # Load broker mode status (written by broker_integration.py at startup)
    try:
        _bs_path = os.path.join(os.path.dirname(__file__), 'data', 'broker_status.json')
        if os.path.exists(_bs_path):
            with open(_bs_path) as _bf:
                _bs = json.load(_bf)
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

    if not kite:
        return jsonify(data)

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
        # ── Enrich with SL / Target from risk_manager positions.json ─────
        try:
            _pos_file = os.path.join(os.path.dirname(__file__), 'data', 'positions.json')
            _rm_map = {}
            if os.path.exists(_pos_file):
                with open(_pos_file) as _pf:
                    _pd = json.load(_pf)
                for _rp in _pd.get('positions', []):
                    _rm_map[_rp['symbol']] = _rp
        except Exception:
            _rm_map = {}

        _sl_pct  = config.SWING_STOP_LOSS_PERCENTAGE  if config.TRADING_MODE == 'swing' else config.STOP_LOSS_PERCENTAGE
        _tgt_pct = config.SWING_TARGET_PERCENTAGE     if config.TRADING_MODE == 'swing' else config.TARGET_PERCENTAGE

        for pos in all_positions:
            sym = pos.get('tradingsymbol')
            avg = pos.get('average_price', 0) or 0
            _rm = _rm_map.get(sym, {})
            # SL / Target: prefer risk_manager file, fall back to config %
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
            _jpath = os.path.join(os.path.dirname(__file__), 'data', 'trade_journal.json')
            with open(_jpath) as _jf:
                _jentries = json.load(_jf)
            _buy_entries = [e for e in _jentries if e.get('action') == 'BUY']
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
        journal_path = os.path.join(os.path.dirname(__file__), 'data', 'trade_journal.json')
        journal_entries = []
        try:
            with open(journal_path) as _jf:
                journal_entries = json.load(_jf)
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
                })

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

        # All completed orders (for trade history tab) + today's orders
        all_completed = [o for o in orders if o.get('status') == 'COMPLETE']
        # Merge journal entries for orders not already in Kite's list (covers past sessions)
        try:
            kite_ids = {o.get('order_id') for o in all_completed}
            kite_syms_today = {o.get('tradingsymbol') for o in all_completed}
            for je in journal_entries:
                sym = je.get('symbol')
                ts = str(je.get('date', '')) + ' 09:00:00'
                if je.get('kite_order_id') not in kite_ids:
                    # Show as SELL row if the journal entry has an exit price (closed trade)
                    if je.get('exit_price') and je.get('net_pnl') is not None:
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
                        # Still-open position — show as BUY row
                        all_completed.append({
                            'tradingsymbol':    sym,
                            'transaction_type': je.get('action', 'BUY'),
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
        data['all_orders'] = sorted(all_completed, key=lambda x: str(x.get('order_timestamp', '')), reverse=True)

        # Paired professional trade cards and BUY/SELL event history (no duplicate SELLs)
        try:
            now_naive = now_ist.replace(tzinfo=None)
            all_cards = _build_trade_cards(journal_entries, data.get('positions', []), now_naive)
            data['trade_cards'] = [c for c in all_cards if c.get('status') == 'Open']
            data['trade_events'] = _build_trade_events([c for c in all_cards if c.get('status') == 'Completed'])
        except Exception as _tc_err:
            logger.error(f"Trade history card build failed: {_tc_err}")
            data['trade_cards'] = []
            data['trade_events'] = []

        # Pending SELL actions surfaced by the order executor
        try:
            _ps_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'pending_sells.json')
            if os.path.exists(_ps_path):
                with open(_ps_path) as _psf:
                    _ps_items = json.load(_psf)
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
        
        # Strategy statistics (all-time complete sells)
        all_sells = [o for o in orders if o.get('status') == 'COMPLETE' and o.get('transaction_type') == 'SELL']
        sell_pnls = [o.get('pnl', 0) for o in all_sells]
        wins = [p for p in sell_pnls if p > 0]
        losses = [p for p in sell_pnls if p < 0]
        avg_win = sum(wins) / len(wins) if wins else 0
        avg_loss = sum(abs(p) for p in losses) / len(losses) if losses else 0
        total_wins = sum(wins)
        total_losses = sum(abs(p) for p in losses)
        profit_factor = total_wins / total_losses if total_losses > 0 else 0
        total_sells = len(all_sells)
        win_rate = len(wins) / total_sells if total_sells > 0 else 0
        expectancy = (win_rate * avg_win) - ((1 - win_rate) * avg_loss) if total_sells > 0 else 0
        data['strategy_stats'] = {
            "avg_win": round(avg_win, 2),
            "avg_loss": round(avg_loss, 2),
            "profit_factor": round(profit_factor, 2),
            "expectancy": round(expectancy, 2)
        }
    except Exception:
        pass

    # Delivery holdings
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
        data['holdings_value'] = total_stocks_value
        # True portfolio = cash + current market value of all stocks (settled + T+1)
        data['net_portfolio_value'] = data.get('cash', 0) + total_stocks_value
        # account_balance shown in Portfolio tab header = total portfolio value
        data['account_balance'] = data.get('cash', 0) + total_stocks_value
    except Exception:
        pass

    # Portfolio health — computed here after account_balance is fully set (cash + holdings)
    try:
        account_value = data.get('account_balance', 0) or data.get('cash', 0)
        _peak_file = os.path.join(os.path.dirname(__file__), 'data', 'peak_value.json')
        peak_value = account_value
        try:
            if os.path.exists(_peak_file):
                with open(_peak_file) as _pf:
                    _saved = json.load(_pf)
                    _saved_peak = _saved.get('peak_value', account_value)
                    if _saved.get('date', '') == today_str:
                        peak_value = max(_saved_peak, account_value)
        except Exception:
            pass
        try:
            os.makedirs(os.path.dirname(_peak_file), exist_ok=True)
            with open(_peak_file, 'w') as _pf:
                json.dump({"peak_value": peak_value, "date": today_str}, _pf)
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
    _journal_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'trade_journal.json')
    last_order_time = '—'
    try:
        if os.path.exists(_journal_file):
            _jdata = json.load(open(_journal_file))
            if _jdata:
                _newest = max(_jdata, key=lambda e: e.get('timestamp', ''))
                last_order_time = _newest.get('timestamp', '—')[:16].replace('T', ' ')
    except Exception:
        pass

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

    return jsonify(data)


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
                    ktime  = str(ko.get('order_timestamp', ''))[:10]
                    # Find matching open BUY entry
                    for e in entries:
                        if (e.get('symbol') == ksym
                                and e.get('action') == 'BUY'
                                and e.get('status') == 'OPEN'):
                            buy_price = float(e.get('entry_price') or e.get('price') or 0)
                            gross_pnl = round((kprice - buy_price) * kqty, 2) if buy_price else 0
                            e['exit_price']   = kprice
                            e['exit_date']    = ktime
                            e['exit_reason']  = 'kite_order'
                            e['gross_pnl']    = gross_pnl
                            e['net_pnl']      = gross_pnl
                            e['holding_days'] = 0
                            e['status']       = 'CLOSED'
                            changed = True
                            break
            if changed:
                j._save(entries)
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
        # Trade Log (Last 20) must show both BUY and SELL, newest first
        analytics['recent_trades']      = sorted(
            all_entries,
            key=lambda x: x.get('timestamp', x.get('exit_date', '')),
            reverse=True
        )[:20]
        analytics['all_entries_count']  = len(all_entries)
        return jsonify(analytics)
    except Exception as e:
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
        journal_path = os.path.join(os.path.dirname(__file__), 'data', 'trade_journal.json')
        if os.path.exists(journal_path):
            with open(journal_path, 'r') as f:
                journal = json.load(f)
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
                        'sector': t.get('sector', 'Unknown')
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
                'sector': d.sector or 'Unknown'
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
    app.run(host='0.0.0.0', port=5001, debug=False)
