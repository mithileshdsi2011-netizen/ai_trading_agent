"""
Enterprise Smart Execution Engine
Routes AI signals into sophisticated, market-aware order execution with
slippage control, liquidity filtering, VWAP/TWAP/Iceberg slicing,
retry logic, partial-fill management, fill verification and analytics.
"""
import json
import logging
import math
import time
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Default execution policies
DEFAULT_SLIPPAGE_PCT = 0.004
DEFAULT_MAX_SPREAD_PCT = 0.005
DEFAULT_MIN_LIQUIDITY = 100
DEFAULT_VWAP_SLICES = 5
DEFAULT_TWAP_WINDOW_SECONDS = 300
DEFAULT_RETRY_MAX = 3
DEFAULT_RETRY_BACKOFF_SECONDS = 2
DEFAULT_SLICE_THRESHOLD = 100
DEFAULT_MINUTES_PER_TWAP_SLICE = 5

ORDER_TYPE_MARKET = "MARKET"
ORDER_TYPE_LIMIT = "LIMIT"
ORDER_TYPE_SL_LIMIT = "SL-LIMIT"
ORDER_TYPE_IOC = "IOC"

SIDE_BUY = "BUY"
SIDE_SELL = "SELL"

STATUS_PENDING = "PENDING"
STATUS_FILLED = "FILLED"
STATUS_PARTIAL = "PARTIAL"
STATUS_REJECTED = "REJECTED"
STATUS_CANCELLED = "CANCELLED"
STATUS_WAITING = "WAITING"

# Permanent broker-side rejections: retrying these will not help
PERMANENT_ERROR_CATEGORIES = {
    'insufficient_funds',
    'insufficient_quantity',
    'rms_rejection',
    'invalid_token',
    'ip_whitelist',
}


class SmartExecutionEngine:
    """
    Brain between AI signals and the broker (Kite).  Accepts a signal and
    executes it with the best available strategy while recording quality
    metrics for post-trade analysis and dashboard display.
    """

    def __init__(
        self,
        store=None,
        broker_fn: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None,
        slippage_pct: float = DEFAULT_SLIPPAGE_PCT,
        max_spread_pct: float = DEFAULT_MAX_SPREAD_PCT,
        min_liquidity: int = DEFAULT_MIN_LIQUIDITY,
        vwap_slices: int = DEFAULT_VWAP_SLICES,
        twap_window_seconds: int = DEFAULT_TWAP_WINDOW_SECONDS,
        retry_max: int = DEFAULT_RETRY_MAX,
        retry_backoff_seconds: int = DEFAULT_RETRY_BACKOFF_SECONDS,
        slice_threshold: int = DEFAULT_SLICE_THRESHOLD,
    ):
        self.store = store
        self.broker_fn = broker_fn or self._default_broker
        self.slippage_pct = slippage_pct
        self.max_spread_pct = max_spread_pct
        self.min_liquidity = min_liquidity
        self.vwap_slices = vwap_slices
        self.twap_window_seconds = twap_window_seconds
        self.retry_max = retry_max
        self.retry_backoff_seconds = retry_backoff_seconds
        self.slice_threshold = slice_threshold
        # Symbols currently being executed; prevents duplicate BUY signals
        # while an order (including retries and rejection handling) is in process
        self._processing_symbols: set = set()

    # ── Public API ───────────────────────────────────────────────────────────

    def execute_signal(self, signal: Dict[str, Any]) -> Dict[str, Any]:
        """Main entry point: turn an AI signal into a fully tracked execution."""
        start_time = time.time()
        ts = datetime.now().isoformat()
        symbol = signal.get("symbol", "")
        side = signal.get("side", SIDE_BUY)
        quantity = int(signal.get("quantity", 0))
        signal_price = float(signal.get("signal_price", 0) or 0)
        current_price = float(signal.get("current_price", 0) or 0)

        # 1. Validate inputs
        if quantity <= 0 or current_price <= 0:
            return self._reject(ts, symbol, side, "invalid signal")

        # 1b. In-flight duplicate guard: one signal at a time per symbol
        if symbol in self._processing_symbols:
            return self._reject(ts, symbol, side, "duplicate signal: already processing")
        self._processing_symbols.add(symbol)

        # 2. Slippage guard
        slip = self.slippage(signal_price, current_price, side)
        if signal_price > 0 and abs(slip) > self.slippage_pct:
            return self._wait(ts, symbol, side, f"slippage {abs(slip):.4%} exceeds {self.slippage_pct:.4%}")

        # 3. Liquidity filter
        market = signal.get("market_data", {})
        bid_qty = int(market.get("bid_qty", 0))
        ask_qty = int(market.get("ask_qty", 0))
        spread = float(market.get("spread", 0) or 0)
        if not self.liquidity_ok(bid_qty, ask_qty, spread):
            return self._reject(ts, symbol, side, "liquidity filter failed")

        # 4. Smart order routing
        order_type = self.route_order(signal)

        # 5. Build execution plan (slices)
        slices = self.build_slices(order_type, quantity, signal)

        # 6. Execute slices
        order_record = self._new_order_record(
            ts, symbol, side, quantity, signal_price, current_price,
            order_type, len(slices)
        )
        filled_qty = 0
        filled_value = 0.0
        retry_count = 0
        rejected_slices = 0
        latency_records: List[Dict[str, Any]] = []

        for i, s in enumerate(slices):
            result, latency_ms, retries = self._submit_with_retry(s, signal)
            latency_records.append(
                {"slice": i, "broker_latency_ms": latency_ms, "retries": retries}
            )
            retry_count += retries
            status = result.get("status", STATUS_REJECTED)
            if status == STATUS_FILLED:
                filled_qty += int(result.get("filled_qty", 0))
                filled_value += float(result.get("filled_value", 0))
            elif status == STATUS_PARTIAL:
                filled_qty += int(result.get("filled_qty", 0))
                filled_value += float(result.get("filled_value", 0))
                remaining = int(s["quantity"] - result.get("filled_qty", 0))
                if remaining > 0 and self._should_continue_partial(signal, remaining):
                    cont = s.copy()
                    cont["quantity"] = remaining
                    cresult, clat, cret = self._submit_with_retry(cont, signal)
                    retry_count += cret
                    if cresult.get("status") in (STATUS_FILLED, STATUS_PARTIAL):
                        filled_qty += int(cresult.get("filled_qty", 0))
                        filled_value += float(cresult.get("filled_value", 0))
                else:
                    rejected_slices += 1
            elif status == STATUS_REJECTED:
                rejected_slices += 1

        # 7. Fill verification & record
        total_latency_ms = int((time.time() - start_time) * 1000)
        order_record.update({
            "filled_qty": filled_qty,
            "filled_value": round(filled_value, 2),
            "avg_price": round(filled_value / filled_qty, 2) if filled_qty > 0 else 0.0,
            "status": self._final_status(filled_qty, quantity, rejected_slices),
            "broker_latency_ms": total_latency_ms,
            "retry_count": retry_count,
            "rejected_slices": rejected_slices,
            "slices": len(slices),
            "slippage_pct": slip,
            "execution_time_ms": total_latency_ms,
            "order_type": order_type,
            "end_ts": datetime.now().isoformat(),
            "raw_broker_results": latency_records,
        })

        self._persist_order(order_record)
        self._persist_fill(order_record)
        self._record_latency(symbol, total_latency_ms, len(slices))
        self._record_slippage(symbol, current_price, order_record["avg_price"], filled_qty, side)
        self._record_metrics(order_record)

        self._processing_symbols.discard(symbol)
        return {
            "ok": filled_qty == quantity,
            "order": order_record,
            "filled_qty": filled_qty,
            "remaining_qty": quantity - filled_qty,
            "avg_price": order_record["avg_price"],
            "status": order_record["status"],
            "execution_quality": self.execution_quality_score(),
        }

    def slippage(self, signal_price: float, current_price: float, side: str) -> float:
        """Signed slippage vs the AI signal price."""
        if signal_price <= 0 or current_price <= 0:
            return 0.0
        if side == SIDE_BUY:
            return (current_price - signal_price) / signal_price
        return (signal_price - current_price) / signal_price

    def liquidity_ok(self, bid_qty: int, ask_qty: int, spread: float) -> bool:
        """Reject if spread too wide or book too thin."""
        if bid_qty < self.min_liquidity or ask_qty < self.min_liquidity:
            return False
        if spread > self.max_spread_pct:
            return False
        return True

    def route_order(self, signal: Dict[str, Any]) -> str:
        """Choose MARKET / LIMIT / SL-LIMIT / IOC based on market conditions."""
        market = signal.get("market_data", {})
        spread = float(market.get("spread", 0) or 0)
        volatility = float(market.get("volatility", 0) or 0)
        atr = float(signal.get("atr", 0) or 0)
        confidence = float(signal.get("ai_confidence", 0.5) or 0.5)
        quantity = int(signal.get("quantity", 0))
        current_price = float(signal.get("current_price", 0) or 0)

        if confidence >= 0.85 and spread <= 0.001 and quantity <= 50:
            return ORDER_TYPE_MARKET
        if spread > 0.002 or volatility > 0.03 or atr > current_price * 0.015:
            return ORDER_TYPE_SL_LIMIT
        if quantity >= self.slice_threshold or spread >= 0.0015:
            return ORDER_TYPE_IOC
        return ORDER_TYPE_LIMIT

    def build_slices(
        self,
        order_type: str,
        quantity: int,
        signal: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        """Create VWAP, TWAP or Iceberg slices for the order."""
        if quantity < self.slice_threshold:
            return [self._slice(signal, quantity, 1, order_type)]

        execution_style = signal.get("execution_style", "VWAP").upper()
        if execution_style == "TWAP":
            return self.build_twap_slices(quantity, signal, order_type)
        if execution_style == "ICEBERG":
            return self.build_iceberg_slices(quantity, signal, order_type)
        return self.build_vwap_slices(quantity, signal, order_type)

    def build_vwap_slices(
        self,
        quantity: int,
        signal: Dict[str, Any],
        order_type: str,
    ) -> List[Dict[str, Any]]:
        """Volume-weighted slices with larger slices at presumed higher volume."""
        n = self.vwap_slices
        weights = [1.0 + math.sin(math.pi * i / (n - 1 or 1)) for i in range(n)]
        total = sum(weights)
        slices = []
        allocated = 0
        for i, w in enumerate(weights[:-1]):
            q = int(quantity * w / total)
            slices.append(self._slice(signal, q, i + 1, order_type))
            allocated += q
        slices.append(self._slice(signal, quantity - allocated, n, order_type))
        return [s for s in slices if s["quantity"] > 0]

    def build_twap_slices(
        self,
        quantity: int,
        signal: Dict[str, Any],
        order_type: str,
    ) -> List[Dict[str, Any]]:
        """Even time/quantity slices across the configured window."""
        n = max(1, self.twap_window_seconds // (DEFAULT_MINUTES_PER_TWAP_SLICE * 60))
        base = quantity // n
        extra = quantity - base * n
        slices = []
        for i in range(n):
            q = base + (1 if i < extra else 0)
            slices.append(self._slice(signal, q, i + 1, order_type))
        return [s for s in slices if s["quantity"] > 0]

    def build_iceberg_slices(
        self,
        quantity: int,
        signal: Dict[str, Any],
        order_type: str,
        disclosed_qty: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Split a large order into equal disclosed-size chunks."""
        disclosed = disclosed_qty or max(1, min(50, quantity // self.vwap_slices))
        slices = []
        i = 1
        while quantity > 0:
            q = min(disclosed, quantity)
            slices.append(self._slice(signal, q, i, order_type))
            quantity -= q
            i += 1
        return slices

    def execution_quality_score(self) -> float:
        """Composite 0-100 score based on recent execution metrics."""
        m = self.recent_metrics()
        if not m:
            return 0.0
        fill_ratio = m.get("fill_ratio", 0)
        success_rate = m.get("success_rate", 0)
        avg_slip = m.get("avg_slippage_pct", 0)
        retry_rate = m.get("avg_retry_count", 0)
        score = (
            30 * fill_ratio
            + 35 * success_rate
            + 25 * max(0.0, 1 - avg_slip / self.slippage_pct)
            + 10 * max(0.0, 1 - min(retry_rate, 5) / 5)
        )
        return round(min(100, max(0, score)), 2)

    def recent_metrics(self) -> Dict[str, float]:
        """Return latest execution analytics if a store is configured."""
        if self.store is None:
            return self._in_memory_metrics()
        try:
            return self.store.get_execution_metrics_latest() or {}
        except Exception as e:
            logger.warning("Could not load execution metrics: %s", e)
            return {}

    def get_dashboard_data(self) -> Dict[str, Any]:
        """Data payload for the Smart Execution dashboard tab."""
        m = self.recent_metrics()
        orders = []
        if self.store is not None:
            try:
                orders = self.store.get_execution_orders(limit=20)
            except Exception:
                pass
        queue = []
        if self.store is not None:
            try:
                queue = self.store.get_execution_queue(limit=20)
            except Exception:
                pass
        return {
            "today_orders": m.get("total_orders", 0),
            "filled": m.get("filled_orders", 0),
            "partial": m.get("partial_orders", 0),
            "rejected": m.get("rejected_orders", 0),
            "avg_slippage_pct": round(m.get("avg_slippage_pct", 0) * 100, 4),
            "avg_fill_time_ms": int(m.get("avg_fill_time_ms", 0)),
            "broker_latency_ms": int(m.get("avg_broker_latency_ms", 0)),
            "success_rate_pct": round(m.get("success_rate", 0) * 100, 2),
            "avg_retry_count": round(m.get("avg_retry_count", 0), 2),
            "execution_quality_score": self.execution_quality_score(),
            "orders": orders,
            "queue": queue,
        }

    # ── Internal helpers ─────────────────────────────────────────────────────

    def _slice(self, signal: Dict[str, Any], quantity: int, seq: int, order_type: str) -> Dict[str, Any]:
        return {
            "symbol": signal.get("symbol", ""),
            "side": signal.get("side", SIDE_BUY),
            "quantity": quantity,
            "order_type": order_type,
            "limit_price": self._limit_price(signal, order_type),
            "trigger_price": self._trigger_price(signal, order_type),
            "sequence": seq,
        }

    def _limit_price(self, signal: Dict[str, Any], order_type: str) -> Optional[float]:
        p = float(signal.get("current_price", 0) or 0)
        side = signal.get("side", SIDE_BUY)
        if order_type in (ORDER_TYPE_LIMIT, ORDER_TYPE_IOC):
            return round(p * 1.005, 2) if side == SIDE_BUY else round(p * 0.995, 2)
        if order_type == ORDER_TYPE_SL_LIMIT:
            return round(p * 1.01, 2) if side == SIDE_BUY else round(p * 0.99, 2)
        return None

    def _trigger_price(self, signal: Dict[str, Any], order_type: str) -> Optional[float]:
        if order_type != ORDER_TYPE_SL_LIMIT:
            return None
        p = float(signal.get("current_price", 0) or 0)
        side = signal.get("side", SIDE_BUY)
        return round(p * 1.008, 2) if side == SIDE_BUY else round(p * 0.992, 2)

    def _submit_with_retry(
        self,
        order: Dict[str, Any],
        signal: Dict[str, Any],
    ) -> Tuple[Dict[str, Any], float, int]:
        """Submit a slice to the broker.  Only retry transient failures."""
        for attempt in range(1, self.retry_max + 1):
            t0 = time.time()
            try:
                result = self.broker_fn(order)
                latency_ms = (time.time() - t0) * 1000
                status = self._normalize_status(result, order.get("quantity", 1))

                if status in (STATUS_FILLED, STATUS_PARTIAL, STATUS_CANCELLED):
                    return result, latency_ms, attempt - 1

                # REJECTED / PENDING-like non-final state
                error_category = result.get("error_category", "other") or "other"
                broker_says_retry = result.get("retry", True)
                is_permanent = (
                    not broker_says_retry
                    or error_category in PERMANENT_ERROR_CATEGORIES
                )

                if is_permanent:
                    msg = (
                        f"Permanent rejection for {order.get('symbol')} "
                        f"(category={error_category}, attempt={attempt}): "
                        f"{result.get('error', 'no details')}"
                    )
                    logger.warning(msg)
                    self._record_rejection(order, result, attempt)
                    return {
                        "status": STATUS_REJECTED,
                        "filled_qty": 0,
                        "filled_value": 0.0,
                        "error": result.get("error"),
                        "error_category": error_category,
                    }, latency_ms, attempt - 1

                # Transient failure: record the retry, back off, and try again
                self._record_retry(order, result, attempt)
                logger.warning(
                    "Transient rejection for %s (attempt %d/%d): category=%s error=%s",
                    order.get("symbol"),
                    attempt,
                    self.retry_max,
                    error_category,
                    result.get("error"),
                )
                if attempt < self.retry_max:
                    time.sleep(self.retry_backoff_seconds * attempt)

            except Exception as e:
                logger.warning("Broker submit exception attempt %d: %s", attempt, e)
                self._record_retry(order, {"error": str(e)}, attempt)
                if attempt < self.retry_max:
                    time.sleep(self.retry_backoff_seconds * attempt)

        logger.warning(
            "Exhausted all %d retry attempts for %s slice", self.retry_max, order.get("symbol")
        )
        self._record_rejection(order, {"error": "retry exhausted"}, self.retry_max)
        return {"status": STATUS_REJECTED, "filled_qty": 0, "filled_value": 0.0}, 0.0, self.retry_max

    def _default_broker(self, order: Dict[str, Any]) -> Dict[str, Any]:
        """Dummy broker used when no real broker is injected."""
        logger.warning("No broker function configured; returning rejected placeholder")
        return {"status": STATUS_REJECTED, "filled_qty": 0, "filled_value": 0.0}

    def _should_continue_partial(self, signal: Dict[str, Any], remaining: int) -> bool:
        """Simple heuristic for partial-fill continuation."""
        market = signal.get("market_data", {})
        spread = float(market.get("spread", 0) or 0)
        return spread <= self.max_spread_pct * 1.5 and remaining >= 1

    def _final_status(self, filled_qty: int, quantity: int, rejected_slices: int) -> str:
        if filled_qty == quantity:
            return STATUS_FILLED
        if filled_qty > 0:
            return STATUS_PARTIAL
        if rejected_slices > 0:
            return STATUS_REJECTED
        return STATUS_PENDING

    def _normalize_status(self, result: Dict[str, Any], expected_qty: int) -> str:
        """Map a broker result dict into one of the engine status constants."""
        explicit = result.get("status")
        if explicit in (STATUS_FILLED, STATUS_PARTIAL, STATUS_CANCELLED, STATUS_REJECTED):
            return explicit
        if not result.get("success"):
            return STATUS_REJECTED
        filled_qty = int(result.get("filled_qty", expected_qty) or expected_qty)
        if filled_qty <= 0:
            return STATUS_REJECTED
        if filled_qty < expected_qty:
            return STATUS_PARTIAL
        return STATUS_FILLED

    def _record_rejection(self, order: Dict[str, Any], result: Dict[str, Any], attempt: int) -> None:
        """Persist a permanent rejection to the execution queue and retry log."""
        if self.store is None:
            return
        try:
            now = datetime.now().isoformat()
            self.store.save_execution_queue({
                "timestamp": now,
                "symbol": order.get("symbol", ""),
                "side": order.get("side", ""),
                "quantity": int(order.get("quantity", 0)),
                "filled_qty": 0,
                "status": STATUS_REJECTED,
                "strategy": order.get("order_type", "MARKET"),
                "data_json": json.dumps({
                    "reason": "permanent broker rejection",
                    "attempt": attempt,
                    "error": result.get("error"),
                    "error_category": result.get("error_category", "other"),
                    "broker_retry": result.get("retry"),
                }, default=str),
            })
            self.store.save_execution_retry({
                "timestamp": now,
                "order_id": result.get("order_id") or 0,
                "attempt": attempt,
                "status": "permanent_rejected",
                "message": f"{result.get('error_category', 'other')}: {result.get('error')}",
            })
        except Exception as e:
            logger.warning("Failed to record execution rejection: %s", e)

    def _record_retry(self, order: Dict[str, Any], result: Dict[str, Any], attempt: int) -> None:
        """Persist a transient retry attempt to the retry log."""
        if self.store is None:
            return
        try:
            self.store.save_execution_retry({
                "timestamp": datetime.now().isoformat(),
                "order_id": result.get("order_id") or 0,
                "attempt": attempt,
                "status": "transient_retry",
                "message": f"{result.get('error_category', 'other')}: {result.get('error')}",
            })
        except Exception as e:
            logger.warning("Failed to record execution retry: %s", e)

    def _new_order_record(
        self,
        ts: str,
        symbol: str,
        side: str,
        quantity: int,
        signal_price: float,
        current_price: float,
        order_type: str,
        slices: int,
    ) -> Dict[str, Any]:
        return {
            "ts": ts,
            "symbol": symbol,
            "side": side,
            "quantity": quantity,
            "signal_price": signal_price,
            "current_price": current_price,
            "order_type": order_type,
            "slices": slices,
        }

    def _reject(self, ts: str, symbol: str, side: str, reason: str) -> Dict[str, Any]:
        rec = {
            "ts": ts,
            "symbol": symbol,
            "side": side,
            "status": STATUS_REJECTED,
            "reason": reason,
            "filled_qty": 0,
            "remaining_qty": 0,
            "avg_price": 0.0,
        }
        self._processing_symbols.discard(symbol)
        self._persist_order(rec)
        return {"ok": False, "order": rec, "status": STATUS_REJECTED, "reason": reason}

    def _wait(self, ts: str, symbol: str, side: str, reason: str) -> Dict[str, Any]:
        rec = {
            "ts": ts,
            "symbol": symbol,
            "side": side,
            "status": STATUS_WAITING,
            "reason": reason,
            "filled_qty": 0,
            "remaining_qty": 0,
            "avg_price": 0.0,
        }
        self._processing_symbols.discard(symbol)
        self._persist_order(rec)
        return {"ok": False, "order": rec, "status": STATUS_WAITING, "reason": reason}

    # ── Persistence helpers ──────────────────────────────────────────────────

    def _in_memory_metrics(self) -> Dict[str, float]:
        return {}

    def _persist_order(self, rec: Dict[str, Any]) -> None:
        if self.store is None:
            return
        try:
            self.store.save_execution_order(rec)
        except Exception as e:
            logger.warning("Failed to persist order: %s", e)

    def _persist_fill(self, rec: Dict[str, Any]) -> None:
        if self.store is None:
            return
        try:
            self.store.save_fill_history({
                "ts": rec.get("end_ts") or rec.get("ts"),
                "symbol": rec.get("symbol"),
                "side": rec.get("side"),
                "quantity": rec.get("quantity"),
                "filled_qty": rec.get("filled_qty"),
                "avg_price": rec.get("avg_price"),
                "order_type": rec.get("order_type"),
                "status": rec.get("status"),
                "slippage_pct": rec.get("slippage_pct", 0),
                "execution_time_ms": rec.get("execution_time_ms", 0),
            })
        except Exception as e:
            logger.warning("Failed to persist fill: %s", e)

    def _record_latency(self, symbol: str, latency_ms: int, slices: int) -> None:
        if self.store is None:
            return
        try:
            self.store.save_broker_latency({
                "ts": datetime.now().isoformat(),
                "symbol": symbol,
                "latency_ms": latency_ms,
                "slices": slices,
            })
        except Exception as e:
            logger.warning("Failed to persist latency: %s", e)

    def _record_slippage(
        self,
        symbol: str,
        signal_price: float,
        avg_price: float,
        filled_qty: int,
        side: str,
    ) -> None:
        if self.store is None or filled_qty <= 0 or avg_price <= 0 or signal_price <= 0:
            return
        try:
            self.store.save_slippage_history({
                "ts": datetime.now().isoformat(),
                "symbol": symbol,
                "signal_price": signal_price,
                "avg_price": avg_price,
                "filled_qty": filled_qty,
                "side": side,
                "slippage_pct": (avg_price - signal_price) / signal_price if side == SIDE_BUY else (signal_price - avg_price) / signal_price,
            })
        except Exception as e:
            logger.warning("Failed to persist slippage: %s", e)

    def _record_metrics(self, rec: Dict[str, Any]) -> None:
        if self.store is None:
            return
        try:
            prev = self.store.get_execution_metrics_latest() or {}
            total = prev.get("total_orders", 0) + 1
            filled = prev.get("filled_orders", 0) + (1 if rec.get("status") == STATUS_FILLED else 0)
            partial = prev.get("partial_orders", 0) + (1 if rec.get("status") == STATUS_PARTIAL else 0)
            rejected = prev.get("rejected_orders", 0) + (1 if rec.get("status") == STATUS_REJECTED else 0)
            old_fill = prev.get("fill_ratio", 0)
            old_time = prev.get("avg_fill_time_ms", 0)
            old_latency = prev.get("avg_broker_latency_ms", 0)
            old_slip = prev.get("avg_slippage_pct", 0)
            old_retry = prev.get("avg_retry_count", 0)

            qty = rec.get("quantity") or 1
            new_fill = (rec.get("filled_qty", 0) / qty)
            new_time = rec.get("execution_time_ms", 0)
            new_latency = rec.get("broker_latency_ms", 0)
            new_slip = abs(rec.get("slippage_pct", 0))
            new_retry = rec.get("retry_count", 0)

            metrics = {
                "ts": datetime.now().isoformat(),
                "total_orders": total,
                "filled_orders": filled,
                "partial_orders": partial,
                "rejected_orders": rejected,
                "fill_ratio": round((old_fill * (total - 1) + new_fill) / total, 4),
                "avg_fill_time_ms": round((old_time * (total - 1) + new_time) / total, 2),
                "avg_broker_latency_ms": round((old_latency * (total - 1) + new_latency) / total, 2),
                "avg_slippage_pct": round((old_slip * (total - 1) + new_slip) / total, 6),
                "avg_retry_count": round((old_retry * (total - 1) + new_retry) / total, 4),
                "success_rate": round(filled / total, 4) if total else 0,
            }
            self.store.save_execution_metrics(metrics)
        except Exception as e:
            logger.warning("Failed to persist metrics: %s", e)
