"""
Unit tests for src/smart_execution_engine.py
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from smart_execution_engine import (
    SmartExecutionEngine,
    ORDER_TYPE_MARKET,
    ORDER_TYPE_LIMIT,
    ORDER_TYPE_SL_LIMIT,
    ORDER_TYPE_IOC,
    STATUS_FILLED,
    STATUS_PARTIAL,
    STATUS_REJECTED,
    STATUS_WAITING,
    SIDE_BUY,
    SIDE_SELL,
)


class FakeStore:
    def __init__(self):
        self.execution_orders = []
        self.execution_queue = []
        self.fill_history = []
        self.slippage_history = []
        self.broker_latency = []
        self.execution_metrics = []
        self.execution_retries = []
        self.execution_quality = []

    def save_execution_order(self, r):
        self.execution_orders.append(r)
        return len(self.execution_orders)

    def get_execution_orders(self, limit=20):
        return self.execution_orders[-limit:][::-1]

    def save_execution_queue(self, r):
        self.execution_queue.append(r)
        return len(self.execution_queue)

    def get_execution_queue(self, limit=20, status=None):
        out = self.execution_queue[-limit:][::-1]
        if status:
            out = [o for o in out if o.get('status') == status]
        return out

    def save_fill_history(self, r):
        self.fill_history.append(r)

    def save_slippage_history(self, r):
        self.slippage_history.append(r)

    def save_broker_latency(self, r):
        self.broker_latency.append(r)

    def save_execution_metrics(self, r):
        self.execution_metrics.append(r)

    def get_execution_metrics_latest(self):
        return self.execution_metrics[-1] if self.execution_metrics else None

    def save_execution_retry(self, r):
        self.execution_retries.append(r)

    def save_execution_quality(self, r):
        self.execution_quality.append(r)


def _good_market(qty=1000, spread=0.0005):
    return {
        "bid_qty": qty,
        "ask_qty": qty,
        "spread": spread,
        "volatility": 0.01,
    }


def _base_signal(market=None, qty=100, **kwargs):
    return {
        "symbol": "RELIANCE",
        "side": SIDE_BUY,
        "quantity": qty,
        "signal_price": 450.0,
        "current_price": 450.0,
        "ai_confidence": 0.9,
        "atr": 2.0,
        "execution_style": "VWAP",
        "market_data": market or _good_market(),
        **kwargs,
    }


class TestSmartExecutionEngine(unittest.TestCase):

    def _engine(self, store=None, broker=None, **kwargs):
        return SmartExecutionEngine(
            store=store,
            broker_fn=broker,
            retry_backoff_seconds=0,
            **kwargs,
        )

    def test_slippage_buy_positive(self):
        eng = self._engine()
        self.assertGreater(eng.slippage(450, 453, SIDE_BUY), 0)

    def test_slippage_sell_negative(self):
        eng = self._engine()
        self.assertLess(eng.slippage(450, 453, SIDE_SELL), 0)

    def test_liquidity_filter_pass(self):
        eng = self._engine()
        self.assertTrue(eng.liquidity_ok(500, 500, 0.0005))

    def test_liquidity_filter_reject_low_qty(self):
        eng = self._engine()
        self.assertFalse(eng.liquidity_ok(10, 10, 0.0005))

    def test_liquidity_filter_reject_wide_spread(self):
        eng = self._engine()
        self.assertFalse(eng.liquidity_ok(1000, 1000, 0.05))

    def test_route_market_high_confidence(self):
        eng = self._engine()
        s = _base_signal(qty=50, current_price=450)
        s["market_data"]["spread"] = 0.0005
        s["market_data"]["volatility"] = 0.005
        s["ai_confidence"] = 0.9
        self.assertEqual(eng.route_order(s), ORDER_TYPE_MARKET)

    def test_route_sl_limit_volatile(self):
        eng = self._engine()
        s = _base_signal(qty=100, current_price=450)
        s["market_data"]["volatility"] = 0.05
        s["market_data"]["spread"] = 0.003
        self.assertEqual(eng.route_order(s), ORDER_TYPE_SL_LIMIT)

    def test_route_ioc_large_qty(self):
        eng = self._engine()
        s = _base_signal(qty=200, current_price=450)
        s["market_data"]["volatility"] = 0.01
        s["market_data"]["spread"] = 0.0015
        s["ai_confidence"] = 0.6
        self.assertEqual(eng.route_order(s), ORDER_TYPE_IOC)

    def test_vwap_slices_sum_to_quantity(self):
        eng = self._engine()
        s = _base_signal(qty=100)
        slices = eng.build_vwap_slices(100, s, ORDER_TYPE_LIMIT)
        self.assertEqual(sum(p["quantity"] for p in slices), 100)
        self.assertGreater(len(slices), 1)

    def test_twap_slices_sum_to_quantity(self):
        eng = self._engine()
        s = _base_signal(qty=100)
        slices = eng.build_twap_slices(100, s, ORDER_TYPE_LIMIT)
        self.assertEqual(sum(p["quantity"] for p in slices), 100)

    def test_iceberg_slices_sum_to_quantity(self):
        eng = self._engine()
        s = _base_signal(qty=155)
        slices = eng.build_iceberg_slices(155, s, ORDER_TYPE_LIMIT, disclosed_qty=50)
        self.assertEqual(sum(p["quantity"] for p in slices), 155)
        self.assertTrue(all(p["quantity"] <= 50 for p in slices))

    def test_slippage_guard_waits(self):
        store = FakeStore()
        eng = self._engine(store=store)
        s = _base_signal(current_price=460, slippage_pct=0.01)
        res = eng.execute_signal(s)
        self.assertEqual(res["status"], STATUS_WAITING)

    def test_liquidity_filter_rejects(self):
        store = FakeStore()
        eng = self._engine(store=store)
        s = _base_signal(market={"bid_qty": 20, "ask_qty": 18, "spread": 0.018})
        res = eng.execute_signal(s)
        self.assertEqual(res["status"], STATUS_REJECTED)

    def test_execute_fills_market_order(self):
        store = FakeStore()

        def broker(order):
            return {
                "status": STATUS_FILLED,
                "filled_qty": order["quantity"],
                "filled_value": order["quantity"] * 450.0,
            }

        eng = self._engine(store=store, broker=broker)
        s = _base_signal(qty=50, current_price=450)
        res = eng.execute_signal(s)
        self.assertEqual(res["status"], STATUS_FILLED)
        self.assertEqual(res["filled_qty"], 50)
        self.assertEqual(len(store.execution_orders), 1)

    def test_execute_vwap_fills_all_slices(self):
        store = FakeStore()

        def broker(order):
            return {
                "status": STATUS_FILLED,
                "filled_qty": order["quantity"],
                "filled_value": order["quantity"] * 450.0,
            }

        eng = self._engine(store=store, broker=broker, slice_threshold=10)
        s = _base_signal(qty=100, current_price=450, execution_style="VWAP")
        res = eng.execute_signal(s)
        self.assertEqual(res["status"], STATUS_FILLED)
        self.assertEqual(res["filled_qty"], 100)

    def test_partial_fill_manager(self):
        store = FakeStore()

        def broker(order):
            return {
                "status": STATUS_PARTIAL,
                "filled_qty": order["quantity"] - 10,
                "filled_value": (order["quantity"] - 10) * 450.0,
            }

        eng = self._engine(store=store, broker=broker, slice_threshold=10)
        s = _base_signal(qty=100, current_price=450, execution_style="VWAP")
        res = eng.execute_signal(s)
        self.assertIn(res["status"], (STATUS_FILLED, STATUS_PARTIAL))

    def test_retry_on_broker_failure(self):
        store = FakeStore()
        attempts = {"n": 0}

        def broker(order):
            attempts["n"] += 1
            if attempts["n"] < 2:
                raise RuntimeError("network timeout")
            return {"status": STATUS_FILLED, "filled_qty": order["quantity"], "filled_value": order["quantity"] * 450.0}

        eng = self._engine(store=store, broker=broker, retry_max=3)
        s = _base_signal(qty=50, current_price=450)
        res = eng.execute_signal(s)
        self.assertEqual(res["status"], STATUS_FILLED)
        self.assertEqual(attempts["n"], 2)

    def test_retry_exhausted_becomes_rejected(self):
        store = FakeStore()

        def broker(order):
            raise RuntimeError("always fails")

        eng = self._engine(store=store, broker=broker, retry_max=2)
        s = _base_signal(qty=50, current_price=450)
        res = eng.execute_signal(s)
        self.assertEqual(res["status"], STATUS_REJECTED)

    def test_twap_style_fills(self):
        store = FakeStore()

        def broker(order):
            return {"status": STATUS_FILLED, "filled_qty": order["quantity"], "filled_value": order["quantity"] * 450.0}

        eng = self._engine(store=store, broker=broker, slice_threshold=10)
        s = _base_signal(qty=60, current_price=450, execution_style="TWAP")
        res = eng.execute_signal(s)
        self.assertEqual(res["status"], STATUS_FILLED)
        self.assertEqual(res["filled_qty"], 60)

    def test_iceberg_style_fills(self):
        store = FakeStore()

        def broker(order):
            return {"status": STATUS_FILLED, "filled_qty": order["quantity"], "filled_value": order["quantity"] * 450.0}

        eng = self._engine(store=store, broker=broker, slice_threshold=10)
        s = _base_signal(qty=150, current_price=450, execution_style="ICEBERG")
        res = eng.execute_signal(s)
        self.assertEqual(res["status"], STATUS_FILLED)
        self.assertEqual(res["filled_qty"], 150)

    def test_execution_quality_score(self):
        store = FakeStore()
        store.execution_metrics = [{
            "fill_ratio": 1.0,
            "success_rate": 1.0,
            "avg_slippage_pct": 0.001,
            "avg_retry_count": 0,
            "avg_fill_time_ms": 100,
            "avg_broker_latency_ms": 50,
            "total_orders": 1,
            "filled_orders": 1,
            "partial_orders": 0,
            "rejected_orders": 0,
        }]
        eng = self._engine(store=store)
        self.assertGreater(eng.execution_quality_score(), 90)

    def test_dashboard_data_shape(self):
        store = FakeStore()
        eng = self._engine(store=store)
        d = eng.get_dashboard_data()
        for k in ["today_orders", "filled", "partial", "rejected", "avg_slippage_pct",
                  "avg_fill_time_ms", "broker_latency_ms", "success_rate_pct",
                  "avg_retry_count", "execution_quality_score", "orders", "queue"]:
            self.assertIn(k, d)

    def test_persist_order_and_fill(self):
        store = FakeStore()

        def broker(order):
            return {"status": STATUS_FILLED, "filled_qty": order["quantity"], "filled_value": order["quantity"] * 450.0}

        eng = self._engine(store=store, broker=broker)
        s = _base_signal(qty=40, current_price=450)
        eng.execute_signal(s)
        self.assertEqual(len(store.execution_orders), 1)
        self.assertEqual(len(store.fill_history), 1)
        self.assertEqual(len(store.slippage_history), 1)
        self.assertEqual(len(store.broker_latency), 1)
        self.assertEqual(len(store.execution_metrics), 1)

    def test_metrics_rollup(self):
        store = FakeStore()

        def broker(order):
            return {"status": STATUS_FILLED, "filled_qty": order["quantity"], "filled_value": order["quantity"] * 450.0}

        eng = self._engine(store=store, broker=broker)
        for _ in range(3):
            s = _base_signal(qty=50, current_price=450)
            eng.execute_signal(s)
        m = store.execution_metrics[-1]
        self.assertEqual(m["total_orders"], 3)
        self.assertEqual(m["filled_orders"], 3)

    def test_queue_persist(self):
        store = FakeStore()
        store.save_execution_queue({"symbol": "INFY", "side": SIDE_BUY, "quantity": 100, "status": "PENDING"})
        eng = self._engine(store=store)
        d = eng.get_dashboard_data()
        self.assertEqual(len(d["queue"]), 1)


if __name__ == '__main__':
    unittest.main()
