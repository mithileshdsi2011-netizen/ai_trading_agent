"""Market-status resilience tests for TradingOrchestrator.

Validates that market-open status resolution is fail-safe:
- reuses fresh reconciled broker_state to avoid duplicate API calls
- falls back to market_data.is_market_open() only when stale/missing
- on API exception: market_open=False, logs MARKET_STATUS_UNAVAILABLE,
  preserves previous state for dashboard, skips BUY/SELL, and continues
  monitoring as safely as possible.
"""
import logging
import threading
from unittest.mock import MagicMock, patch

import pytest

from trading_orchestrator import TradingOrchestrator


@pytest.fixture
def orchestrator(monkeypatch):
    """Build a TradingOrchestrator instance with __init__ bypassed."""
    monkeypatch.setattr(TradingOrchestrator, "__init__", lambda self: None)
    to = TradingOrchestrator()

    # Minimum attributes needed by _resolve_market_open and run_once
    to._store = MagicMock()
    to.market_data = MagicMock()
    to.order_executor = MagicMock()
    to.reconciliation_engine = MagicMock()
    to.reconciliation_engine.get_status.return_value = {"healthy": True}
    to._kite_fail_count = 0
    to._kite_alert_sent = False
    to._morning_shortlist = []
    to._morning_shortlist_date = ""
    to._circuit_breaker_fired_today = False
    to.telegram = MagicMock()
    to.email = MagicMock()
    to._alert = MagicMock()
    to._is_trading_day = MagicMock(return_value=True)
    to._recently_sold = {}
    to._last_rebalance_week = None
    to.trade_log = []
    to._TRADE_LOG_MAX = 500
    to._run_once_lock = threading.Lock()

    to.order_executor.should_stop_trading.return_value = False
    to.order_executor.broker.get_holdings.return_value = {
        "cash": 0.0,
        "positions": [],
        "total_value": 0.0,
    }
    to.order_executor.broker.paper_trading = True
    to.order_executor.broker.live_ready = False
    to.order_executor.risk_manager = MagicMock()
    to.order_executor.risk_manager.max_daily_loss = 1000.0
    to.order_executor.risk_manager.daily_pnl = 0.0
    to.order_executor.monitor_positions.return_value = []

    to.market_data.get_cycle_metrics.return_value = None
    to.market_data.is_market_holiday.return_value = False
    to.market_data.kite = None
    to.market_data.new_cycle.return_value = None
    to.market_data._get_instruments.return_value = None
    return to


# ─────────────────────────────────────────────────────────────────────────────
# _resolve_market_open tests
# ─────────────────────────────────────────────────────────────────────────────

def test_resolve_market_open_uses_fresh_broker_state(orchestrator, caplog):
    """Normal market hours: trading allowed because broker_state is fresh."""
    orchestrator._store.get_broker_state.return_value = {
        "market_open": True,
        "updated_at": "2099-01-01T10:00:00",
    }
    orchestrator.market_data.is_market_open.return_value = True

    market_open, error, previous = orchestrator._resolve_market_open()

    assert market_open is True
    assert error is None
    assert previous is True
    orchestrator.market_data.is_market_open.assert_not_called()


def test_resolve_market_open_falls_back_when_stale(orchestrator):
    """If broker_state is stale, call market_data.is_market_open()."""
    orchestrator._store.get_broker_state.return_value = {
        "market_open": False,
        "updated_at": "2000-01-01T00:00:00",
    }
    orchestrator.market_data.is_market_open.return_value = True

    market_open, error, previous = orchestrator._resolve_market_open()

    assert market_open is True
    assert error is None
    assert previous is False
    orchestrator.market_data.is_market_open.assert_called_once()


def test_resolve_market_open_exception_fails_safe_preserves_previous(orchestrator, caplog):
    """API exception: market_open=False, logs MARKET_STATUS_UNAVAILABLE, keeps previous."""
    caplog.set_level(logging.WARNING)
    orchestrator._store.get_broker_state.return_value = {
        "market_open": True,
        "updated_at": "2000-01-01T00:00:00",
    }
    orchestrator.market_data.is_market_open.side_effect = RuntimeError("kite timeout")

    market_open, error, previous = orchestrator._resolve_market_open()

    assert market_open is False
    assert "MARKET_STATUS_UNAVAILABLE" in error
    assert previous is True
    assert "MARKET_STATUS_UNAVAILABLE" in caplog.text
    assert "kite timeout" in caplog.text


def test_resolve_market_open_outside_hours(orchestrator):
    """Outside market hours: market_open=False."""
    orchestrator._store.get_broker_state.return_value = None
    orchestrator.market_data.is_market_open.return_value = False

    market_open, error, previous = orchestrator._resolve_market_open()

    assert market_open is False
    assert error is None


def test_resolve_market_open_recovery_after_exception(orchestrator, caplog):
    """Next successful call resumes normal market-open status."""
    caplog.set_level(logging.WARNING)
    orchestrator._store.get_broker_state.return_value = {
        "market_open": True,
        "updated_at": "2000-01-01T00:00:00",
    }
    orchestrator.market_data.is_market_open.side_effect = [
        RuntimeError("kite timeout"),
        True,
    ]

    # First call — exception
    market_open_1, error_1, _ = orchestrator._resolve_market_open()
    assert market_open_1 is False
    assert "MARKET_STATUS_UNAVAILABLE" in error_1

    # Second call — recovery
    market_open_2, error_2, _ = orchestrator._resolve_market_open()
    assert market_open_2 is True
    assert error_2 is None


# ─────────────────────────────────────────────────────────────────────────────
# run_once market-status failure path
# ─────────────────────────────────────────────────────────────────────────────

def test_run_once_does_not_trade_when_market_status_unavailable(orchestrator, caplog):
    """When is_market_open() raises, run_once returns early without BUY/SELL."""
    caplog.set_level(logging.WARNING)
    orchestrator._store.get_broker_state.return_value = {
        "market_open": True,
        "updated_at": "2000-01-01T00:00:00",
    }
    orchestrator.market_data.is_market_open.side_effect = RuntimeError("kite timeout")

    result = orchestrator.run_once()

    assert result["market_open"] is False
    assert "MARKET_STATUS_UNAVAILABLE" in result["market_status_error"]
    assert result["previous_market_open"] is True
    assert result["signals_generated"] == []
    assert result["orders_executed"] == []
    # monitoring was attempted because hard-safety exits should still be checked
    orchestrator.order_executor.monitor_positions.assert_called_once()
