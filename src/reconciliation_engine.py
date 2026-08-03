"""
Enterprise Reconciliation Engine
Keeps Kite API, SQLite and the dashboard 100% synchronized.
"""
import os
import json
import logging
import threading
import time
from datetime import datetime, date
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field, asdict
from functools import wraps

from config import config
try:
    from persistence import get_store
except ImportError:
    get_store = None  # type: ignore

LOG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'logs', 'reconciliation.log')
os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)

logger = logging.getLogger(__name__)
if not logger.handlers:
    _fh = logging.FileHandler(LOG_PATH)
    _fh.setFormatter(logging.Formatter('%(asctime)s %(levelname)s: %(message)s'))
    logger.addHandler(_fh)
    logger.setLevel(logging.INFO)


def _now() -> str:
    return datetime.now().isoformat(timespec='seconds')


def _safe_float(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _kite_orders(broker) -> List[Dict[str, Any]]:
    """Return a normalized list of Kite/broker orders."""
    if broker.paper_trading:
        return list(broker.paper_portfolio.get('orders', []))
    if not broker.kite:
        return []
    try:
        return broker.kite.orders()
    except Exception as e:
        logger.error(f"Could not fetch Kite orders: {e}")
        return []


def _kite_positions(broker) -> List[Dict[str, Any]]:
    """Return a normalized list of Kite/broker positions."""
    if broker.paper_trading:
        return broker._get_paper_positions()
    if not broker.kite:
        return []
    try:
        # Include day and net positions
        data = broker.kite.positions()
        positions = []
        for key in ('day', 'net'):
            for p in data.get(key, []):
                if p.get('quantity', 0) > 0:
                    positions.append(p)
        return positions
    except Exception as e:
        logger.error(f"Could not fetch Kite positions: {e}")
        return []


def _kite_holdings(broker) -> List[Dict[str, Any]]:
    """Return a normalized list of Kite holdings."""
    if broker.paper_trading:
        return broker._get_paper_positions()
    if not broker.kite:
        return []
    try:
        return broker.kite.holdings()
    except Exception as e:
        logger.error(f"Could not fetch Kite holdings: {e}")
        return []


@dataclass
class Mismatch:
    type_: str
    old_value: Any
    new_value: Any
    repair: str


class ReconciliationEngine:
    """Compare Kite API with SQLite and repair SQLite to match Kite."""

    def __init__(self, broker=None, market_data_fetcher=None, store=None):
        self.broker = broker
        self.market_data_fetcher = market_data_fetcher
        self._store = store or (get_store() if get_store else None)
        self._scheduler_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._last_status: Optional[Dict[str, Any]] = None
        self._lock = threading.Lock()

    # ── public status / health ─────────────────────────────────────────────

    def get_status(self) -> Dict[str, Any]:
        if self._last_status:
            return dict(self._last_status)
        # Read persisted status if any
        if self._store:
            return self._store.get_broker_state('reconciliation') or {
                'healthy': False,
                'last_sync': None,
                'mismatches': 0,
                'repairs': 0,
                'duration_ms': 0
            }
        return {'healthy': False, 'last_sync': None, 'mismatches': 0, 'repairs': 0, 'duration_ms': 0}

    # ── scheduling ─────────────────────────────────────────────────────────

    def start_scheduler(self, interval: int = 60):
        """Run full reconciliation every `interval` seconds in a background thread."""
        if self._scheduler_thread and self._scheduler_thread.is_alive():
            return
        self._stop_event.clear()

        def _loop():
            while not self._stop_event.wait(interval):
                try:
                    self.reconcile_all()
                except Exception:
                    logger.exception("Scheduled reconciliation failed")

        self._scheduler_thread = threading.Thread(target=_loop, name='reconciliation-scheduler', daemon=True)
        self._scheduler_thread.start()

    def stop_scheduler(self):
        self._stop_event.set()
        if self._scheduler_thread:
            self._scheduler_thread.join(timeout=2)

    def trigger(self, event: str):
        """Event-driven reconciliation. Runs in a background thread to avoid blocking."""
        logger.info(f"Reconciliation triggered by: {event}")
        t = threading.Thread(target=self.reconcile_all, name=f'reconciliation-{event}', daemon=True)
        t.start()
        return {'triggered': event}

    # ── main entrypoint ────────────────────────────────────────────────────

    def reconcile_all(self) -> Dict[str, Any]:
        start = time.time()
        mismatches: List[Mismatch] = []

        if not self._store:
            raise RuntimeError("No SQLite store available for reconciliation")

        try:
            if self.broker:
                mismatches.extend(self._reconcile_holdings())
                mismatches.extend(self._reconcile_open_positions())
                mismatches.extend(self._reconcile_orders())
                mismatches.extend(self._reconcile_pending_sells())
                mismatches.extend(self._reconcile_portfolio())
                mismatches.extend(self._reconcile_broker_state())
            mismatches.extend(self._reconcile_journal())
            mismatches.extend(self._reconcile_peak_value())
            mismatches.extend(self._reconcile_daily_state())

            duration_ms = int((time.time() - start) * 1000)
            has_unrepaired = any(m.repair == 'detected' for m in mismatches)
            has_source = (self.broker is not None or self.market_data_fetcher is not None)
            healthy = not has_unrepaired and has_source

            status = {
                'healthy': healthy,
                'last_sync': _now(),
                'mismatches': len([m for m in mismatches if m.repair != 'no_repair']),
                'repairs': len([m for m in mismatches if m.repair == 'repaired']),
                'duration_ms': duration_ms,
                'objects_checked': self._count_objects(),
                'last_repair': mismatches[-1].type_ if mismatches and mismatches[-1].repair == 'repaired' else None,
                'details': [asdict(m) for m in mismatches]
            }

            self._store.save_broker_state('reconciliation', status)
            with self._lock:
                self._last_status = status

            self._log_mismatches(mismatches, duration_ms)
            return status

        except Exception as e:
            logger.exception("Full reconciliation failed")
            duration_ms = int((time.time() - start) * 1000)
            status = {
                'healthy': False,
                'last_sync': _now(),
                'mismatches': 0,
                'repairs': 0,
                'duration_ms': duration_ms,
                'error': str(e)
            }
            if self._store:
                self._store.save_broker_state('reconciliation', status)
            with self._lock:
                self._last_status = status
            return status

    # ── helpers: logging and counting ─────────────────────────────────────

    def _log_mismatches(self, mismatches: List[Mismatch], duration_ms: int):
        for m in mismatches:
            msg = (
                f"RECONCILE type={m.type_} old={m.old_value} new={m.new_value} "
                f"repair={m.repair} duration_ms={duration_ms}"
            )
            if m.repair == 'repaired':
                logger.warning(msg)
            else:
                logger.info(msg)

    def _count_objects(self) -> Dict[str, int]:
        return {
            'positions': len(self._store.load_positions()) if self._store else 0,
            'trades': len(self._store.all_trades()) if self._store else 0,
            'orders': len(self._store.get_orders()) if self._store else 0,
            'pending_sells': len(self._store.load_daily_state().get('pending_sells', {})) if self._store else 0,
        }

    def _add_mismatch(self, mismatches: List[Mismatch], type_: str, old_value: Any, new_value: Any, repaired: bool):
        mismatches.append(Mismatch(
            type_=type_,
            old_value=old_value,
            new_value=new_value,
            repair='repaired' if repaired else ('no_repair' if old_value == new_value else 'detected')
        ))

    # ── 1. holdings ───────────────────────────────────────────────────────

    def _reconcile_holdings(self) -> List[Mismatch]:
        mismatches: List[Mismatch] = []
        if not self.broker:
            return mismatches

        kite_holdings = _kite_holdings(self.broker)
        sqlite_positions = {p.get('symbol'): p for p in self._store.load_positions()}

        for h in kite_holdings:
            sym = h.get('tradingsymbol')
            if not sym:
                continue
            sq = _safe_float(h.get('quantity', 0))
            avg = _safe_float(h.get('average_price', h.get('entry_price', 0)))
            ltp = _safe_float(h.get('last_price', h.get('close_price', 0)))
            product = h.get('product', 'CNC')
            exchange = h.get('exchange', 'NSE')

            existing = sqlite_positions.get(sym, {})
            needs_save = False
            for field, kite_val in [
                ('quantity', sq),
                ('average_price', avg),
                ('last_price', ltp),
                ('product', product),
                ('exchange', exchange),
            ]:
                if existing.get(field) != kite_val:
                    needs_save = True
                    break

            if not existing:
                # Kite has a holding we don't have in SQLite
                position = {
                    'symbol': sym,
                    'status': 'OPEN',
                    'quantity': sq,
                    'average_price': avg,
                    'last_price': ltp,
                    'product': product,
                    'exchange': exchange,
                    'source': 'reconciliation',
                    'updated_at': _now()
                }
                self._store.save_position(position)
                self._add_mismatch(mismatches, f'holdings_missing:{sym}', None, position, True)
            elif needs_save:
                # Update existing live fields
                updates = {
                    'quantity': sq,
                    'average_price': avg,
                    'last_price': ltp,
                    'product': product,
                    'exchange': exchange,
                    'updated_at': _now()
                }
                self._store.update_position(sym, existing.get('status', 'OPEN'), updates)
                self._add_mismatch(mismatches, f'holdings_mismatch:{sym}', existing, updates, True)

        return mismatches

    # ── 2. open positions ─────────────────────────────────────────────────

    def _reconcile_open_positions(self) -> List[Mismatch]:
        mismatches: List[Mismatch] = []
        if not self.broker:
            return mismatches

        kite_positions = _kite_positions(self.broker)
        sqlite_positions = {p.get('symbol'): p for p in self._store.load_positions(status='OPEN')}

        kite_by_sym = {p.get('tradingsymbol', p.get('symbol')): p for p in kite_positions if p.get('quantity', 0) > 0}

        # Update/create positions from Kite
        for sym, kp in kite_by_sym.items():
            sq = _safe_float(kp.get('quantity', 0))
            avg = _safe_float(kp.get('average_price', kp.get('entry_price', 0)))
            ltp = _safe_float(kp.get('last_price', kp.get('close_price', 0)))
            product = kp.get('product', 'MIS')
            exchange = kp.get('exchange', 'NSE')

            existing = sqlite_positions.get(sym, {})
            if not existing:
                position = {
                    'symbol': sym,
                    'status': 'OPEN',
                    'quantity': sq,
                    'average_price': avg,
                    'last_price': ltp,
                    'product': product,
                    'exchange': exchange,
                    'source': 'reconciliation',
                    'updated_at': _now()
                }
                self._store.save_position(position)
                self._add_mismatch(mismatches, f'position_missing:{sym}', None, position, True)
            else:
                changes = {}
                for field, kite_val in [
                    ('quantity', sq),
                    ('average_price', avg),
                    ('last_price', ltp),
                    ('product', product),
                    ('exchange', exchange),
                ]:
                    if existing.get(field) != kite_val:
                        changes[field] = kite_val
                if changes:
                    changes['updated_at'] = _now()
                    self._store.update_position(sym, 'OPEN', changes)
                    self._add_mismatch(mismatches, f'position_mismatch:{sym}', existing, changes, True)

        # Close positions SQLite thinks are open but Kite doesn't have
        for sym, sp in sqlite_positions.items():
            if sym not in kite_by_sym:
                # Verify not just in holdings (already handled in _reconcile_holdings)
                if not sp.get('_seen_in_holdings'):
                    self._store.update_position(sym, 'OPEN', {'status': 'CLOSED', 'exit_reason': 'reconciliation: missing in broker', 'updated_at': _now()})
                    self._add_mismatch(mismatches, f'position_closed:{sym}', sp, {'status': 'CLOSED'}, True)

        return mismatches

    # ── 3. orders ─────────────────────────────────────────────────────────

    def _reconcile_orders(self) -> List[Mismatch]:
        mismatches: List[Mismatch] = []
        if not self.broker:
            return mismatches

        kite_orders = _kite_orders(self.broker)
        sqlite_orders = {o.get('order_id'): o for o in self._store.get_orders()}

        for ko in kite_orders:
            oid = ko.get('order_id')
            if not oid:
                continue
            status = ko.get('status', 'PENDING')
            so = sqlite_orders.get(oid)
            if not so:
                order = {
                    'order_id': oid,
                    'symbol': ko.get('tradingsymbol', ko.get('symbol')),
                    'action': ko.get('transaction_type'),
                    'quantity': _safe_float(ko.get('quantity', 0)),
                    'price': _safe_float(ko.get('average_price', ko.get('price', 0))),
                    'status': status,
                    'timestamp': ko.get('order_timestamp') or _now(),
                    'product': ko.get('product', ''),
                    'exchange': ko.get('exchange', ''),
                    'source': 'reconciliation'
                }
                self._store.add_order(order)
                self._add_mismatch(mismatches, f'order_missing:{oid}', None, order, True)
            elif so.get('status') != status:
                self._store.update_order_status(so.get('symbol'), so.get('action'), status)
                self._add_mismatch(mismatches, f'order_status:{oid}', so.get('status'), status, True)

        return mismatches

    # ── 4. pending sell queue ─────────────────────────────────────────────

    def _reconcile_pending_sells(self) -> List[Mismatch]:
        mismatches: List[Mismatch] = []
        if not self.broker:
            return mismatches

        state = self._store.load_daily_state()
        pending = state.get('pending_sells', {})
        kite_orders = _kite_orders(self.broker)
        open_sell_ids = {o.get('order_id') for o in kite_orders if o.get('transaction_type') == 'SELL' and o.get('status') in ('OPEN', 'PENDING', 'AMO', 'UPDATE_PENDING')}

        # Remove pending sells where the actual order is completed/rejected/cancelled
        keys_to_remove = []
        for key, ps in pending.items():
            oid = ps.get('order_id')
            if oid and oid not in open_sell_ids:
                # Check current status in Kite
                for ko in kite_orders:
                    if ko.get('order_id') == oid:
                        if ko.get('status') in ('COMPLETE', 'REJECTED', 'CANCELLED', 'AMO CANCELLED'):
                            keys_to_remove.append(key)
                        break
                else:
                    # Order not found at all
                    keys_to_remove.append(key)

        def _save_pending():
            self._store.save_daily_state(
                state_date=date.today().isoformat(),
                daily_pnl=_safe_float(state.get('daily_pnl', 0)),
                daily_blacklist=list(state.get('daily_blacklist', [])),
                last_exit_by_symbol=dict(state.get('last_exit_by_symbol', {})),
                daily_trades=int(state.get('daily_trades', 0)),
                pending_sells=dict(pending)
            )

        if keys_to_remove:
            for k in keys_to_remove:
                del pending[k]
            _save_pending()
            self._add_mismatch(mismatches, 'pending_sell_removed', keys_to_remove, pending, True)

        # Add missing pending sells for open SELL orders
        for ko in kite_orders:
            if ko.get('transaction_type') == 'SELL' and ko.get('status') in ('OPEN', 'PENDING'):
                oid = ko.get('order_id')
                sym = ko.get('tradingsymbol', ko.get('symbol'))
                exists = any(ps.get('order_id') == oid for ps in pending.values())
                if not exists:
                    pending[oid] = {
                        'order_id': oid,
                        'symbol': sym,
                        'quantity': _safe_float(ko.get('quantity', 0)),
                        'last_attempt': _now(),
                        'order_timestamp': ko.get('order_timestamp') or _now()
                    }
                    _save_pending()
                    self._add_mismatch(mismatches, f'pending_sell_added:{oid}', None, pending[oid], True)

        return mismatches

    # ── 5. trade journal ──────────────────────────────────────────────────

    def _reconcile_journal(self) -> List[Mismatch]:
        mismatches: List[Mismatch] = []

        sqlite_trades = self._store.all_trades()
        sqlite_by_id = {t.get('order_id') or t.get('id'): t for t in sqlite_trades if t.get('order_id') or t.get('id')}

        # Deduplicate by order_id + symbol (always run, even without broker)
        seen: set = set()
        duplicates: List[int] = []
        for t in sqlite_trades:
            key = (t.get('order_id'), t.get('symbol'), t.get('timestamp'))
            if key in seen:
                duplicates.append(t.get('id'))
            else:
                seen.add(key)

        # We don't delete historical trades, but we log duplicates
        if duplicates:
            logger.warning(f"Duplicate trades detected: {duplicates}")
            self._add_mismatch(mismatches, 'duplicate_trades', duplicates, None, False)

        if not self.broker:
            return mismatches

        # Ensure every completed Kite order is in the journal
        kite_orders = _kite_orders(self.broker)
        for ko in kite_orders:
            if ko.get('status') == 'COMPLETE':
                oid = ko.get('order_id')
                sym = ko.get('tradingsymbol', ko.get('symbol'))
                action = ko.get('transaction_type', 'BUY')
                if (oid, sym) not in [(t.get('order_id'), t.get('symbol')) for t in sqlite_trades]:
                    trade = {
                        'order_id': oid,
                        'symbol': sym,
                        'action': action,
                        'quantity': _safe_float(ko.get('filled_quantity', ko.get('quantity', 0))),
                        'entry_price': _safe_float(ko.get('average_price', ko.get('price', 0))),
                        'timestamp': ko.get('order_timestamp') or _now(),
                        'status': 'OPEN' if action == 'BUY' else 'CLOSED',
                        'source': 'reconciliation'
                    }
                    self._store.add_trade(trade)
                    self._add_mismatch(mismatches, f'trade_missing:{oid}', None, trade, True)

        return mismatches

    # ── 6. portfolio value ────────────────────────────────────────────────

    def _reconcile_portfolio(self) -> List[Mismatch]:
        mismatches: List[Mismatch] = []
        if not self.broker:
            return mismatches

        summary = self.broker.get_holdings()
        cash = _safe_float(summary.get('cash', 0))
        holdings_value = _safe_float(summary.get('total_value', 0)) - cash

        snapshot = {
            'cash': cash,
            'holdings_value': holdings_value,
            'total_value': _safe_float(summary.get('total_value', 0)),
            'date': date.today().isoformat(),
            'timestamp': _now()
        }

        latest = self._store.get_latest_portfolio_snapshot() or {}
        # Only update if materially changed or newer
        if (latest.get('cash') != cash or
                latest.get('holdings_value') != holdings_value or
                latest.get('date') != snapshot['date']):
            self._store.save_portfolio_snapshot(snapshot)
            self._add_mismatch(mismatches, 'portfolio_snapshot', latest, snapshot, True)

        return mismatches

    # ── 7. broker state ───────────────────────────────────────────────────

    def _reconcile_broker_state(self) -> List[Mismatch]:
        mismatches: List[Mismatch] = []
        if not self.broker:
            return mismatches

        start = time.time()
        api_ok = False
        try:
            _ = self.broker.get_holdings()
            api_ok = True
        except Exception:
            pass
        latency_ms = int((time.time() - start) * 1000)

        market_open = False
        if self.market_data_fetcher:
            try:
                market_open = self.market_data_fetcher.is_market_open()
            except Exception:
                pass

        token_expiry = '—'
        try:
            token_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'kite_token.json')
            if os.path.exists(token_path):
                with open(token_path) as f:
                    token_expiry = json.load(f).get('expiry', '—')[:16]
        except Exception:
            pass

        old = self._store.get_broker_state('status') or {}
        new = {
            'mode': 'PAPER' if self.broker.paper_trading else 'LIVE',
            'live_ready': self.broker.live_ready if not self.broker.paper_trading else True,
            'market_open': market_open,
            'api_status': 'ok' if api_ok else 'down',
            'latency_ms': latency_ms,
            'last_api_call': _now(),
            'token_expiry': token_expiry,
            'updated_at': _now(),
        }

        # Merge old fields like startup_timestamp so we don't lose them
        for k, v in old.items():
            new.setdefault(k, v)

        self._store.save_broker_state('status', new)
        self._add_mismatch(mismatches, 'broker_state', old, new, not (old == new))
        return mismatches

    # ── 8. peak portfolio value ───────────────────────────────────────────

    def _reconcile_peak_value(self) -> List[Mismatch]:
        mismatches: List[Mismatch] = []

        snap = self._store.get_latest_portfolio_snapshot() or {}
        total_value = _safe_float(snap.get('total_value', 0))
        today_str = date.today().isoformat()
        last_date = snap.get('date', '')

        # New day: reset peak to current value
        if last_date != today_str:
            peak_value = total_value
            self._store.save_portfolio_snapshot({
                'total_value': total_value,
                'peak_value': peak_value,
                'date': today_str,
                'timestamp': _now()
            })
            self._add_mismatch(mismatches, 'peak_value_new_day', last_date, today_str, True)
        else:
            peak_value = _safe_float(snap.get('peak_value', total_value))
            if total_value > peak_value:
                peak_value = total_value
                snap['total_value'] = total_value
                snap['peak_value'] = peak_value
                snap['date'] = today_str
                snap['timestamp'] = _now()
                self._store.save_portfolio_snapshot(snap)
                self._add_mismatch(mismatches, 'peak_value_up', snap.get('peak_value'), total_value, True)

        return mismatches

    # ── 9. daily state ────────────────────────────────────────────────────

    def _reconcile_daily_state(self) -> List[Mismatch]:
        mismatches: List[Mismatch] = []
        today_str = date.today().isoformat()
        state = self._store.load_daily_state(today_str)

        # Ensure all daily_state keys exist; keep only schema-supported keys
        expected = {
            'daily_pnl': 0.0,
            'daily_blacklist': [],
            'last_exit_by_symbol': {},
            'daily_trades': 0,
            'pending_sells': {},
        }

        changed = False
        for k, v in expected.items():
            if k not in state:
                state[k] = v
                changed = True

        if changed:
            self._store.save_daily_state(
                state_date=today_str,
                daily_pnl=_safe_float(state.get('daily_pnl', 0)),
                daily_blacklist=list(state.get('daily_blacklist', [])),
                last_exit_by_symbol=dict(state.get('last_exit_by_symbol', {})),
                daily_trades=int(state.get('daily_trades', 0)),
                pending_sells=dict(state.get('pending_sells', {}))
            )
            self._add_mismatch(mismatches, 'daily_state_init', None, {k: state[k] for k in expected}, True)

        return mismatches
