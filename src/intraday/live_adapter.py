"""
Intraday LIVE Execution Adapter

Real-money order lifecycle for Angel SmartAPI — intent persistence,
durable client order IDs, broker acknowledgement, status polling,
partial fills, rejections, unknown outcomes and restart reconciliation.

HARD SAFETY PROPERTIES
- DISARMED by default. `armed` requires BOTH the config flag and the
  environment arm token; nothing here can enable itself.
- An order intent is persisted BEFORE submission — a crash mid-submit
  is recoverable, never silently duplicated.
- An API acknowledgement is NEVER treated as a fill. Only a broker-
  reported terminal state (FILLED) books a position or an exit.
- UNKNOWN outcomes are never blindly retried — they persist and are
  reconciled against the broker order book first.
- A live position is never marked closed without a broker-confirmed
  exit fill. EXIT_REQUESTED / ACCEPTED / PARTIAL / FILLED / REJECTED /
  UNKNOWN are explicit persisted states.
- All state lives under <state_dir>/live/ — fully separate from the
  PAPER store; PAPER journals and counters are untouched.
"""
import hashlib
import logging
import os
import threading
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

from .angel.orders import classify_broker_status, DISARMED_RESULT
from .state import IntradayStateStore

logger = logging.getLogger(__name__)

# Order lifecycle — INTENT persisted before submission, terminal states
# only ever set from broker-reported data.
ORDER_STATES = (
    'INTENT', 'SUBMIT_FAILED', 'ACKED', 'OPEN', 'PARTIAL',
    'FILLED', 'REJECTED', 'CANCELLED', 'UNKNOWN',
)
TERMINAL_ORDER_STATES = {'FILLED', 'REJECTED', 'CANCELLED'}
# Live-position exit lifecycle — never 'closed' without broker fill.
EXIT_REQUESTED = 'EXIT_REQUESTED'
EXIT_ACCEPTED = 'EXIT_ACCEPTED'
EXIT_PARTIAL = 'EXIT_PARTIAL'
EXIT_FILLED = 'EXIT_FILLED'
EXIT_REJECTED = 'EXIT_REJECTED'
EXIT_UNKNOWN = 'EXIT_UNKNOWN'


def _now_iso() -> str:
    return datetime.now().isoformat()


def _dt(value) -> Optional[datetime]:
    if value is None or isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(value)
    except Exception:
        return None


class LiveOrderStore:
    """Atomic JSON persistence for live orders and positions."""

    def __init__(self, state_dir: Optional[str]):
        self._dir = os.path.join(state_dir or "", "live")
        self._io = IntradayStateStore()
        self._lock = threading.Lock()

    def _path(self, name: str) -> str:
        return os.path.join(self._dir, name)

    def load_orders(self) -> List[Dict[str, Any]]:
        data = self._io._read_json(self._path("orders.json"))
        return data if isinstance(data, list) else []

    def save_orders(self, orders: List[Dict[str, Any]]):
        with self._lock:
            os.makedirs(self._dir, exist_ok=True)
            self._io._write_json(self._path("orders.json"), orders)

    def load_positions(self) -> List[Dict[str, Any]]:
        data = self._io._read_json(self._path("positions.json"))
        return data if isinstance(data, list) else []

    def save_positions(self, positions: List[Dict[str, Any]]):
        with self._lock:
            os.makedirs(self._dir, exist_ok=True)
            self._io._write_json(self._path("positions.json"), positions)


class LiveExecutionAdapter:
    """
    Live order executor. All broker mutation flows through `gateway`,
    which independently re-checks `armed` at call time.
    """

    def __init__(
        self,
        gateway,
        risk,
        instruments=None,
        state_dir: Optional[str] = None,
        alert_hook: Optional[Callable[[str, str, str], None]] = None,
        armed: bool = False,
    ):
        self.gateway = gateway
        self.risk = risk
        self.instruments = instruments
        self._armed = bool(armed)
        self._alert_hook = alert_hook
        self.store = LiveOrderStore(state_dir)
        self._lock = threading.RLock()
        self._orders: List[Dict[str, Any]] = self.store.load_orders()
        self._positions: List[Dict[str, Any]] = self.store.load_positions()
        self._reconciled_at: Optional[datetime] = None
        self._reconcile_mismatches: List[str] = []
        if self._armed:
            logger.warning("[LIVE ADAPTER] LIVE ARMED — real orders possible")
        else:
            logger.info("[LIVE ADAPTER] DISARMED — order submission impossible")

    # ── arm state ───────────────────────────────────────────────────
    def armed(self) -> bool:
        return bool(self._armed)

    def _incident(self, action: str, key: str, detail: str):
        if self._alert_hook is None:
            return
        try:
            self._alert_hook(action, key, detail)
        except Exception as e:
            logger.warning(f"[LIVE ADAPTER] incident hook failed: {e}")

    # ── durable client order id ─────────────────────────────────────
    @staticmethod
    def client_order_id(intent_key: str, when: Optional[datetime] = None) -> str:
        """Deterministic per intent — a crashed submission retried after
        restart resumes the SAME intent record, never a duplicate."""
        when = when or datetime.now()
        digest = hashlib.sha1(intent_key.encode()).hexdigest()[:12].upper()
        return f"IA-{when.strftime('%Y%m%d')}-{digest}"

    # ── lookups ─────────────────────────────────────────────────────
    def _find_order(self, client_order_id: str) -> Optional[Dict[str, Any]]:
        for o in self._orders:
            if o.get('client_order_id') == client_order_id:
                return o
        return None

    def _persist(self):
        self.store.save_orders(self._orders)
        self.store.save_positions(self._positions)

    def _get_position(self, symbol: str) -> Optional[Dict[str, Any]]:
        for p in self._positions:
            if p.get('symbol') == symbol and p.get('state') == 'OPEN':
                return p
        return None

    def open_positions(self) -> List[Dict[str, Any]]:
        return [p for p in self._positions if p.get('state') == 'OPEN']

    # ── order param construction ────────────────────────────────────
    def _order_params(self, symbol: str, direction: str, quantity: int,
                      client_order_id: str) -> Optional[Dict[str, Any]]:
        token = None
        tradingsymbol = f"{symbol}-EQ"
        if self.instruments is not None:
            inst = self.instruments.get_instrument(symbol)
            if inst is None:
                return None
            token = inst.symbol_token
            tradingsymbol = self.instruments.get_tradingsymbol(symbol) or tradingsymbol
        return {
            'variety': 'NORMAL',
            'tradingsymbol': tradingsymbol,
            'symboltoken': token,
            'transactiontype': 'BUY' if direction == 'LONG' else 'SELL',
            'exchange': 'NSE',
            'ordertype': 'MARKET',
            'producttype': 'INTRADAY',
            'duration': 'DAY',
            'quantity': int(quantity),
            'ordertag': client_order_id[:20],  # broker-side dedup tag
        }

    def _exit_order_params(self, position: Dict[str, Any], quantity: int,
                           client_order_id: str) -> Optional[Dict[str, Any]]:
        params = self._order_params(
            position['symbol'], position['direction'], quantity, client_order_id)
        if params is None:
            return None
        # Exit flips the transaction side.
        params['transactiontype'] = (
            'SELL' if position['direction'] == 'LONG' else 'BUY')
        return params

    # ── entry ───────────────────────────────────────────────────────
    def submit_entry(
        self,
        symbol: str,
        direction: str,
        quantity: int,
        expected_price: float,
        stop_loss: float,
        target: float,
        quote_ltp: Optional[float],
        intent_key: str,
        signal_meta: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Submit a live entry order. Returns a result dict; the order is
        tracked in `self._orders` until a broker-terminal state.
        """
        if not self.armed():
            return {'success': False, 'error': 'LIVE DISARMED', 'disarmed': True}
        if self.blocked_reason():
            return {'success': False,
                    'error': f"entries blocked: {self.blocked_reason()}"}

        cid = self.client_order_id(intent_key)
        with self._lock:
            existing = self._find_order(cid)
            if existing is not None:
                # Same intent resubmitted — resume tracking, never double-send.
                if existing['state'] in TERMINAL_ORDER_STATES:
                    return {'success': False,
                            'error': f"intent already terminal ({existing['state']})"}
                return {'success': True, 'resumed': True,
                        'client_order_id': cid, 'state': existing['state']}

            # In-flight entry orders reserve capacity: an order that is
            # INTENT/ACKED/OPEN/PARTIAL/UNKNOWN may become a position at
            # the broker at any moment — the concurrent cap and daily
            # entry limit must count it before a second signal squeezes
            # through the same window.
            in_flight = [o for o in self._orders
                         if o.get('purpose') == 'ENTRY'
                         and o['state'] in ('INTENT', 'ACKED', 'OPEN',
                                            'PARTIAL', 'UNKNOWN')]
            open_pos = self.open_positions() + [
                {'symbol': o['symbol'], 'quantity': o['quantity'],
                 'entry_price': o.get('expected_price', 0),
                 'unrealized_pnl': 0.0}
                for o in in_flight]
            funds = self.gateway.funds()
            available = funds.get('available_cash') if funds.get('success') else None
            if not funds.get('success'):
                self.risk.record_broker_error('funds check failed')
                return {'success': False,
                        'error': f"funds check failed: {funds.get('error')}"}
            ok, reason = self.risk.preflight_entry(
                symbol=symbol, direction=direction, quantity=quantity,
                expected_price=expected_price, quote_ltp=quote_ltp,
                stop_loss=stop_loss, open_positions=open_pos,
                available_funds=available,
                open_unrealized=sum(
                    float(p.get('unrealized_pnl', 0.0)) for p in open_pos),
                reserved_entries=len(in_flight))
            if not ok:
                return {'success': False, 'error': reason, 'risk_rejected': True}

            params = self._order_params(symbol, direction, quantity, cid)
            if params is None:
                return {'success': False, 'error': f"no instrument for {symbol}"}

            # Intent persisted BEFORE submission — crash here leaves a
            # resumable INTENT record, not an invisible broker order.
            order = {
                'client_order_id': cid,
                'purpose': 'ENTRY',
                'symbol': symbol,
                'direction': direction,
                'quantity': int(quantity),
                'expected_price': float(expected_price),
                'stop_loss': float(stop_loss),
                'target': float(target),
                'intent_key': intent_key,
                'state': 'INTENT',
                'broker_order_id': None,
                'broker_status': None,
                'filled_qty': 0,
                'avg_price': None,
                'created_at': _now_iso(),
                'submitted_at': None,
                'updated_at': _now_iso(),
                'error': None,
                'signal_meta': signal_meta or {},
            }
            self._orders.append(order)
            self._persist()

        return self._submit_and_track(order, params)

    def _submit_and_track(self, order: Dict[str, Any],
                          params: Dict[str, Any]) -> Dict[str, Any]:
        """Submit a persisted INTENT order and record the ack. An ack is
        never a fill — the order stays non-terminal until broker status."""
        order['submitted_at'] = _now_iso()
        result = self.gateway.place(params)
        with self._lock:
            order['updated_at'] = _now_iso()
            if result.get('success'):
                order['state'] = 'ACKED'
                order['broker_order_id'] = result.get('broker_order_id')
                self.risk.record_broker_success()
            elif result.get('unknown'):
                # Submission outcome unknown — may already be at the
                # broker. UNKNOWN is persisted and reconciled; no retry.
                order['state'] = 'UNKNOWN'
                order['error'] = result.get('error')
                self.risk.record_broker_error('submit outcome unknown')
                self._incident(
                    'notify', 'intraday:live:order_unknown',
                    f"LIVE order {order['client_order_id']} submission outcome "
                    f"UNKNOWN ({order['symbol']}) — reconciling against "
                    "order book before any further action.")
            elif result.get('disarmed'):
                order['state'] = 'SUBMIT_FAILED'
                order['error'] = result.get('error')
            else:
                order['state'] = 'SUBMIT_FAILED'
                order['error'] = result.get('error')
                self.risk.record_broker_error(f"submit failed: {result.get('error')}")
            self._persist()
        # First status poll right away — does not block on completion.
        self.poll_orders()
        return {'success': bool(result.get('success')),
                'client_order_id': order['client_order_id'],
                'state': order['state'], 'error': order.get('error')}

    # ── exit ────────────────────────────────────────────────────────
    def submit_exit(self, symbol: str, reason: str,
                    quantity: Optional[int] = None) -> Dict[str, Any]:
        """Submit a live exit for an open live position. The position is
        marked EXIT_REQUESTED immediately and is only closed on a
        broker-confirmed FILLED exit order."""
        if not self.armed():
            return {'success': False, 'error': 'LIVE DISARMED', 'disarmed': True}
        with self._lock:
            pos = self._get_position(symbol)
            if pos is None:
                return {'success': False, 'error': f"no live position for {symbol}"}
            if pos.get('exit_state') in (EXIT_REQUESTED, EXIT_ACCEPTED,
                                         EXIT_PARTIAL, EXIT_UNKNOWN):
                return {'success': False,
                        'error': f"exit already in flight ({pos.get('exit_state')})"}
            qty = quantity or int(pos['quantity'])
            cid = self.client_order_id(
                f"EXIT:{pos['symbol']}:{pos['entry_time']}:{reason}")
            params = self._exit_order_params(pos, qty, cid)
            if params is None:
                return {'success': False, 'error': f"no instrument for {symbol}"}
            pos['exit_state'] = EXIT_REQUESTED
            pos['exit_reason'] = reason
            order = {
                'client_order_id': cid,
                'purpose': 'EXIT',
                'symbol': symbol,
                'direction': pos['direction'],
                'quantity': qty,
                'expected_price': float(pos.get('current_price') or 0),
                'intent_key': f"EXIT:{symbol}:{pos['entry_time']}",
                'state': 'INTENT',
                'broker_order_id': None,
                'broker_status': None,
                'filled_qty': 0,
                'avg_price': None,
                'created_at': _now_iso(),
                'submitted_at': None,
                'updated_at': _now_iso(),
                'error': None,
            }
            self._orders.append(order)
            self._persist()

        result = self._submit_and_track(order, params)
        with self._lock:
            if order['state'] == 'ACKED':
                pos['exit_state'] = EXIT_ACCEPTED
            elif order['state'] == 'UNKNOWN':
                pos['exit_state'] = EXIT_UNKNOWN
                self._incident(
                    'notify', 'intraday:live:exit_unknown',
                    f"LIVE exit for {symbol} outcome UNKNOWN — position "
                    "remains open; manual broker check advised.")
            elif order['state'] == 'SUBMIT_FAILED':
                pos['exit_state'] = EXIT_REJECTED
            self._persist()
        return result

    # ── polling / finalization ──────────────────────────────────────
    def poll_orders(self, now: Optional[datetime] = None) -> int:
        """Refresh all non-terminal orders against the broker order book.
        Returns the number of orders updated."""
        now = now or datetime.now()
        pending = [o for o in self._orders
                   if o['state'] not in TERMINAL_ORDER_STATES | {'INTENT'}]
        unsubmitted = [o for o in self._orders if o['state'] == 'INTENT']
        if not pending and not unsubmitted:
            return 0
        book = self.gateway.order_book()
        if not book.get('success'):
            self.risk.record_broker_error('order book poll failed')
            return 0
        self.risk.record_broker_success()
        updated = 0
        with self._lock:
            for order in pending:
                row = self._match_broker_order(book['orders'], order)
                if row is None:
                    # Not visible yet — ACKed orders need a grace period;
                    # beyond the timeout they are UNKNOWN (not retryable).
                    if order['state'] in ('ACKED', 'OPEN', 'PARTIAL'):
                        submitted = _dt(order.get('submitted_at'))
                        if (submitted and
                                (now - submitted).total_seconds()
                                > self.risk.config.order_timeout_s):
                            order['state'] = 'UNKNOWN'
                            self._mark_exit_unknown(order)
                            self._incident(
                                'notify', 'intraday:live:order_unknown',
                                f"Order {order['client_order_id']} "
                                f"({order['symbol']}) not in broker book "
                                f"after {self.risk.config.order_timeout_s}s "
                                "— UNKNOWN; no automatic retry.")
                            updated += 1
                    continue
                if self._apply_broker_row(order, row):
                    updated += 1
            for order in unsubmitted:
                # INTENT with no submission — process died before submit.
                # Safe to re-drive: the intent is durable and idempotent.
                order['state'] = 'INTENT'  # stays until engine re-drives
            self._persist()
        return updated

    @staticmethod
    def _match_broker_order(rows: List[Dict[str, Any]],
                            order: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        for row in rows or []:
            if (order.get('broker_order_id')
                    and str(row.get('orderid')) == str(order['broker_order_id'])):
                return row
        for row in rows or []:
            if str(row.get('ordertag') or '') == str(order['client_order_id'])[:20]:
                return row
        return None

    def _mark_exit_unknown(self, order: Dict[str, Any]):
        if order.get('purpose') != 'EXIT':
            return
        pos = self._get_position(order['symbol'])
        if pos is not None:
            pos['exit_state'] = EXIT_UNKNOWN

    def _apply_broker_row(self, order: Dict[str, Any],
                          row: Dict[str, Any]) -> bool:
        """Update an order from a broker order-book row. Returns True if
        anything changed."""
        try:
            filled = int(float(row.get('filledshares') or row.get('filledShares') or 0))
        except (TypeError, ValueError):
            filled = 0
        try:
            avg = float(row.get('averageprice') or row.get('averagePrice') or 0) or None
        except (TypeError, ValueError):
            avg = None
        cls = classify_broker_status(
            row.get('status'), filled, int(order['quantity']))
        order['broker_order_id'] = order['broker_order_id'] or row.get('orderid')
        order['broker_status'] = row.get('status')
        order['filled_qty'] = filled
        order['avg_price'] = avg or order.get('avg_price')
        order['updated_at'] = _now_iso()

        if cls == 'FILLED':
            if order['state'] != 'FILLED':
                order['state'] = 'FILLED'
                self._finalize_fill(order)
            return True
        if cls == 'PARTIAL':
            if order['state'] != 'PARTIAL' or order.get('filled_qty') != filled:
                order['state'] = 'PARTIAL'
                if order.get('purpose') == 'EXIT':
                    pos = self._get_position(order['symbol'])
                    if pos is not None:
                        pos['exit_state'] = EXIT_PARTIAL
            return True
        if cls == 'REJECTED':
            order['state'] = 'REJECTED'
            if order.get('purpose') == 'EXIT':
                pos = self._get_position(order['symbol'])
                if pos is not None:
                    pos['exit_state'] = EXIT_REJECTED
                self._incident(
                    'notify', 'intraday:live:exit_rejected',
                    f"LIVE exit for {order['symbol']} REJECTED by broker — "
                    "position remains open; immediate attention required.")
            return True
        if cls == 'CANCELLED':
            order['state'] = 'CANCELLED'
            if order.get('purpose') == 'EXIT':
                pos = self._get_position(order['symbol'])
                if pos is not None:
                    pos['exit_state'] = EXIT_REJECTED
            return True
        if cls == 'OPEN':
            if order['state'] != 'OPEN':
                order['state'] = 'OPEN'
                return True
        return False

    def _finalize_fill(self, order: Dict[str, Any]):
        """Book a broker-confirmed fill into the live position ledger."""
        avg = order.get('avg_price') or order.get('expected_price')
        if order.get('purpose') == 'ENTRY':
            if self._get_position(order['symbol']) is None:
                self._positions.append({
                    'symbol': order['symbol'],
                    'direction': order['direction'],
                    'quantity': order.get('filled_qty') or order['quantity'],
                    'entry_price': avg,
                    'current_price': avg,
                    'stop_loss': order.get('stop_loss'),
                    'target': order.get('target'),
                    'entry_time': _now_iso(),
                    'entry_order_id': order['client_order_id'],
                    'state': 'OPEN',
                    'exit_state': None,
                    'unrealized_pnl': 0.0,
                })
                self.risk.record_entry()
        else:  # EXIT
            pos = self._get_position(order['symbol'])
            if pos is not None:
                filled = order.get('filled_qty') or order['quantity']
                pos['exit_state'] = EXIT_FILLED
                pos['exit_price'] = avg
                pos['exit_time'] = _now_iso()
                pos['state'] = 'CLOSED'
                direction = pos['direction']
                gross = ((avg - pos['entry_price']) * filled
                         if direction == 'LONG'
                         else (pos['entry_price'] - avg) * filled)
                pos['realized_pnl'] = round(gross, 4)
                self.risk.record_realized(gross)

    # ── reconciliation ──────────────────────────────────────────────
    def reconcile(self) -> Dict[str, Any]:
        """
        Startup/periodic reconciliation against the broker:

        1. Non-terminal orders are re-matched against the order book.
        2. Broker positions are compared to the local live ledger —
           any mismatch blocks new entries until resolved (fail closed).
        """
        report = {'reconciled': False, 'mismatches': [], 'orders_updated': 0}
        self.poll_orders()  # refreshes non-terminal orders first
        book = self.gateway.order_book()
        positions = self.gateway.positions()
        if not book.get('success') or not positions.get('success'):
            report['error'] = 'broker data unavailable — reconciliation incomplete'
            self._reconcile_mismatches = ['BROKER UNREACHABLE']
            return report

        broker_open = {}
        for row in positions.get('positions') or []:
            try:
                qty = int(float(row.get('netqty') or row.get('netQty') or 0))
            except (TypeError, ValueError):
                qty = 0
            if qty != 0:
                sym = str(row.get('tradingsymbol') or row.get('symbol') or '')
                broker_open[sym.replace('-EQ', '')] = qty

        local_open = {p['symbol']: int(p['quantity']) for p in self.open_positions()}
        mismatches = []
        for sym, qty in broker_open.items():
            if local_open.get(sym) != qty:
                mismatches.append(f"{sym}: broker {qty} vs local {local_open.get(sym, 0)}")
        for sym in local_open:
            if sym not in broker_open:
                mismatches.append(f"{sym}: local {local_open[sym]} vs broker 0")

        self._reconcile_mismatches = mismatches
        self._reconciled_at = datetime.now()
        report['reconciled'] = not mismatches
        report['mismatches'] = mismatches
        if mismatches:
            self._incident(
                'notify', 'intraday:live:position_mismatch',
                f"LIVE position reconciliation mismatch: {mismatches} — "
                "new entries blocked until resolved.")
        return report

    def blocked_reason(self) -> Optional[str]:
        """Why new LIVE entries are blocked (None = not blocked)."""
        if self._reconcile_mismatches:
            return f"unreconciled positions: {self._reconcile_mismatches}"
        unknown = [o['client_order_id'] for o in self._orders
                   if o['state'] == 'UNKNOWN']
        if unknown:
            return f"unknown-order outcomes pending reconciliation: {unknown}"
        return None

    # ── status ──────────────────────────────────────────────────────
    def get_status(self) -> Dict[str, Any]:
        counts: Dict[str, int] = {}
        for o in self._orders:
            counts[o['state']] = counts.get(o['state'], 0) + 1
        return {
            'armed': self.armed(),
            'order_counts': counts,
            'open_positions': len(self.open_positions()),
            'blocked_reason': self.blocked_reason(),
            'reconciled_at': (self._reconciled_at.isoformat()
                              if self._reconciled_at else None),
            'reconcile_mismatches': list(self._reconcile_mismatches),
            'risk': self.risk.get_status(self.open_positions()),
        }
