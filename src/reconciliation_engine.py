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
        data = broker.kite.holdings()
    except Exception as e:
        logger.error(f"Could not fetch Kite holdings: {e}")
        return []

    # CNC T1 delivery: 'quantity' = settled, 't1_quantity' = not yet settled.
    # The bot owns the total, so combine them.
    for p in data:
        settled = _safe_float(p.get('quantity', 0))
        t1 = _safe_float(p.get('t1_quantity', 0))
        p['quantity'] = settled + t1

    return data


@dataclass
class Mismatch:
    type_: str
    old_value: Any
    new_value: Any
    repair: str


class ReconciliationEngine:
    """Compare Kite API with SQLite and repair SQLite to match Kite."""

    # How many reconciliation cycles a completed Kite order may be missing
    # from the journal before we raise a CRITICAL alert.
    MISSING_JOURNAL_RETRY_THRESHOLD = 3

    def __init__(self, broker=None, market_data_fetcher=None, store=None, alert_engine=None):
        self.broker = broker
        self.market_data_fetcher = market_data_fetcher
        self._store = store or (get_store() if get_store else None)
        self._alert_engine = alert_engine
        self._scheduler_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._last_status: Optional[Dict[str, Any]] = None
        self._last_healthy: bool = True
        self._lock = threading.Lock()
        # Track completed Kite orders that are not yet reflected in the journal
        self._missing_journal_retries: Dict[str, Dict[str, Any]] = {}

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

            if self._alert_engine:
                try:
                    if not healthy and self._last_healthy:
                        first_detected = next((m for m in mismatches if m.repair == 'detected'), None)
                        detail = first_detected.type_ if first_detected else (status.get('error') or 'unknown')
                        self._alert_engine.critical(
                            'Reconciliation',
                            f'Reconciliation unhealthy: {detail}',
                            status
                        )
                    elif healthy and not self._last_healthy:
                        self._alert_engine.info('Reconciliation', 'Reconciliation recovered', status)
                except Exception:
                    pass
            self._last_healthy = healthy

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
            if self._alert_engine:
                try:
                    self._alert_engine.critical('Reconciliation', f'Full reconciliation failed: {e}', status)
                except Exception:
                    pass
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
                if sq <= 0:
                    continue
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
            elif needs_save or (existing.get('status') != 'OPEN' and sq > 0):
                # Update live fields; only force OPEN when broker reports a positive quantity.
                # A CLOSED position with 0 broker quantity must stay closed to avoid churn.
                target_status = 'OPEN' if sq > 0 else existing.get('status', 'OPEN')
                updates = {
                    'quantity': sq,
                    'average_price': avg,
                    'last_price': ltp,
                    'product': product,
                    'exchange': exchange,
                    'status': target_status,
                    'updated_at': _now()
                }
                if target_status == 'CLOSED':
                    exit_reason = existing.get('exit_reason')
                    if not exit_reason or exit_reason == 'RECONCILIATION':
                        for trade in reversed(self._store.get_trades(symbol=sym, action='SELL')):
                            if trade.get('exit_reason') and trade.get('exit_reason') != 'RECONCILIATION':
                                exit_reason = trade.get('exit_reason')
                                break
                    updates['exit_reason'] = exit_reason or existing.get('exit_reason') or 'RECONCILIATION'
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
                    ('status', 'OPEN'),
                ]:
                    if existing.get(field) != kite_val:
                        changes[field] = kite_val
                if changes:
                    changes['updated_at'] = _now()
                    self._store.update_position(sym, 'OPEN', changes)
                    self._add_mismatch(mismatches, f'position_mismatch:{sym}', existing, changes, True)

        # Do not close an open position just because it is absent from Kite
        # positions (settlement delays can make a CNC position briefly disappear).
        # Close only if we actually see a completed SELL order for the symbol.
        kite_orders = _kite_orders(self.broker) if self.broker else []
        completed_sell_symbols = {
            ko.get('tradingsymbol', ko.get('symbol'))
            for ko in kite_orders
            if ko.get('transaction_type') == 'SELL' and ko.get('status') == 'COMPLETE'
        }

        for sym, sp in sqlite_positions.items():
            if sp.get('status') != 'OPEN':
                continue
            if sym not in kite_by_sym:
                if sp.get('_seen_in_holdings'):
                    logger.info(
                        f"Position {sym}: not in Kite positions but present in holdings; "
                        "keeping OPEN (T+1/settlement timing)"
                    )
                    continue
                if sym in completed_sell_symbols:
                    sell_order = next(
                        (ko for ko in kite_orders
                         if ko.get('tradingsymbol', ko.get('symbol')) == sym
                         and ko.get('transaction_type') == 'SELL'
                         and ko.get('status') == 'COMPLETE'),
                        None
                    )
                    oid = sell_order.get('order_id') if sell_order else 'unknown'
                    exit_reason = sp.get('exit_reason')
                    if not exit_reason or exit_reason == 'RECONCILIATION':
                        for trade in reversed(self._store.get_trades(symbol=sym, action='SELL')):
                            if trade.get('exit_reason') and trade.get('exit_reason') != 'RECONCILIATION':
                                exit_reason = trade.get('exit_reason')
                                break
                    if not exit_reason:
                        exit_reason = 'RECONCILIATION'
                    logger.warning(
                        f"Position {sym}: broker reports completed SELL order_id={oid}; "
                        f"closing SQLite position with exit_reason={exit_reason}"
                    )
                    self._store.update_position(
                        sym, 'OPEN',
                        {'status': 'CLOSED', 'exit_reason': exit_reason, 'updated_at': _now()}
                    )
                    self._add_mismatch(
                        mismatches, f'position_closed:{sym}', sp,
                        {'status': 'CLOSED', 'exit_reason': exit_reason}, True
                    )
                else:
                    logger.info(
                        f"Position {sym}: not found in broker positions/holdings and "
                        "no completed SELL order on record; keeping OPEN"
                    )

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

        # Deduplicate by order_id + key trade fields (always run, even without broker)
        seen: set = set()
        duplicates: List[int] = []
        for t in sqlite_trades:
            key = (t.get('order_id'), t.get('symbol'), t.get('timestamp'), t.get('entry_price'), t.get('exit_price'), t.get('quantity'), t.get('action'))
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

        # Track completed Kite orders that are not yet reflected in the journal.
        # We used to create synthetic SELL trades here, but that hides real
        # order-execution / journaling bugs. Instead, we retry for several
        # reconciliation cycles and raise a CRITICAL alert so the issue is
        # surfaced and fixed at the source.
        kite_orders = _kite_orders(self.broker)
        current_missing: set = set()
        for ko in kite_orders:
            if ko.get('status') != 'COMPLETE':
                continue
            oid = ko.get('order_id')
            sym = ko.get('tradingsymbol', ko.get('symbol'))
            if not oid or not sym:
                continue
            # Match by (order_id, symbol). A journal row should have order_id
            # written by order_executor after the broker confirms the fill.
            if any(t.get('order_id') == oid and t.get('symbol') == sym for t in sqlite_trades):
                continue

            action = ko.get('transaction_type', 'BUY')
            current_missing.add(oid)
            self._missing_journal_retries.setdefault(oid, {
                'symbol': sym,
                'action': action,
                'first_seen': _now(),
                'count': 0,
                'order': ko,
            })
            self._missing_journal_retries[oid]['count'] += 1
            count = self._missing_journal_retries[oid]['count']
            logger.warning(
                f"{sym}: journal still missing for {action} order_id={oid} "
                f"(reconciliation cycle {count}/{self.MISSING_JOURNAL_RETRY_THRESHOLD})"
            )
            if count >= self.MISSING_JOURNAL_RETRY_THRESHOLD:
                logger.warning(
                    f"{sym}: {action} order_id={oid} missing from journal for "
                    f"{count} cycles — creating synthetic trade"
                )
                synthetic_trade = {
                    'order_id': oid,
                    'symbol': sym,
                    'action': action,
                    'quantity': float(ko.get('quantity', ko.get('filled_quantity', 0)) or 0),
                    'price': float(ko.get('average_price', ko.get('price', 0)) or 0),
                    'entry_price': float(ko.get('average_price', ko.get('price', 0)) or 0),
                    'timestamp': ko.get('order_timestamp') or ko.get('exchange_timestamp') or _now(),
                    'status': 'OPEN' if action == 'BUY' else 'CLOSED',
                    'product': ko.get('product', 'CNC'),
                    'exchange': ko.get('exchange', 'NSE'),
                    'source': 'reconciliation',
                }
                self._store.add_trade(synthetic_trade)
                self._add_mismatch(
                    mismatches, f'journal_synthetic:{oid}',
                    None,
                    {
                        'symbol': sym,
                        'action': action,
                        'order_id': oid,
                        'missing_for_cycles': count,
                        'first_seen': self._missing_journal_retries[oid]['first_seen'],
                    },
                    True
                )

        # Clear any resolved missing-order entries
        resolved = [oid for oid in self._missing_journal_retries if oid not in current_missing]
        for oid in resolved:
            sym = self._missing_journal_retries[oid]['symbol']
            logger.info(f"{sym}: journal entry for order_id={oid} now present")
            del self._missing_journal_retries[oid]

        return mismatches

    # ── 6. portfolio value ────────────────────────────────────────────────

    def _reconcile_portfolio(self) -> List[Mismatch]:
        mismatches: List[Mismatch] = []
        if not self.broker:
            return mismatches

        summary = self.broker.get_holdings()
        cash = _safe_float(summary.get('cash', 0))
        holdings_value = _safe_float(summary.get('total_value', 0)) - cash

        # Same-day CNC sale proceeds are not yet settled in kite.margins().
        # Show them explicitly so the user sees where the money is.
        today_str = date.today().isoformat()
        unsettled = 0.0
        for t in self._store.get_trades(action='SELL', date_from=today_str):
            # Sell credit = net_pnl + invested (cost already removed from net)
            unsettled += _safe_float(t.get('net_pnl', 0)) + _safe_float(t.get('invested', 0))

        snapshot = {
            'cash': cash,
            'holdings_value': holdings_value,
            'unsettled_proceeds': round(unsettled, 2),
            'total_value': round(cash + holdings_value + unsettled, 2),
            'date': today_str,
            'timestamp': _now()
        }

        if unsettled > 0:
            logger.info(
                f"Portfolio: cash ₹{cash:,.2f} + holdings ₹{holdings_value:,.2f} "
                f"+ unsettled sale proceeds ₹{unsettled:,.2f} = total ₹{snapshot['total_value']:,.2f}"
            )

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

        old = self._store.get_broker_state('status') or {}
        market_open = old.get('market_open', False)
        if self.market_data_fetcher:
            try:
                market_open = self.market_data_fetcher.is_market_open()
            except Exception as e:
                logger.warning(f"market_data_fetcher.is_market_open() failed: {e} — keeping previous={market_open}")

        token_expiry = '—'
        try:
            token_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'kite_token.json')
            if os.path.exists(token_path):
                with open(token_path) as f:
                    token_expiry = json.load(f).get('expiry', '—')[:16]
        except Exception:
            pass

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
