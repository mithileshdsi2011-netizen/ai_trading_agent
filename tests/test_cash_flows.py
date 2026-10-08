"""Cash-flow ledger + circuit-breaker withdrawal handling.

Verifies that confirmed deposits/withdrawals adjust the equity comparison
while real trading losses still trip the breaker, and that missing or
malformed records fail safe (raw equity behavior preserved).
"""
import json
import os
from datetime import datetime
from unittest.mock import Mock, patch

import pytest
import pytz

from cash_flows import record_flow, flows_on, net_outflow_on, _path
from config import data_path

_TODAY = datetime.now(pytz.timezone('Asia/Kolkata')).strftime('%Y-%m-%d')


# ── Ledger basics ─────────────────────────────────────────────────────

def test_withdrawal_counts_as_outflow(tmp_path):
    record_flow('withdrawal', 5000)
    assert net_outflow_on(_TODAY) == 5000.0


def test_deposit_reduces_outflow(tmp_path):
    record_flow('withdrawal', 5000)
    record_flow('deposit', 2000)
    assert net_outflow_on(_TODAY) == 3000.0


def test_unconfirmed_flows_ignored(tmp_path):
    record_flow('withdrawal', 5000, confirmed=False)
    assert net_outflow_on(_TODAY) == 0.0
    assert flows_on(_TODAY) == []


def test_other_dates_ignored(tmp_path):
    record_flow('withdrawal', 9000, date_str='2001-01-01')
    assert net_outflow_on(_TODAY) == 0.0


def test_missing_file_is_zero(tmp_path):
    assert net_outflow_on(_TODAY) == 0.0


def test_malformed_file_is_zero(tmp_path):
    p = _path()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, 'w') as f:
        f.write("{not json")
    assert net_outflow_on(_TODAY) == 0.0


def test_ledger_writes_to_temp_dir(tmp_path):
    record_flow('withdrawal', 100)
    assert str(tmp_path) in _path()
    prod = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        'data', 'cash_flows.json')
    assert os.path.abspath(_path()) != os.path.abspath(prod)


def test_duplicate_flow_id_not_counted_twice(tmp_path):
    record_flow('withdrawal', 5000, flow_id='zerodha-wd-001')
    record_flow('withdrawal', 5000, flow_id='zerodha-wd-001')  # dup — ignored
    record_flow('withdrawal', 5000, flow_id='zerodha-wd-002')  # distinct — counts
    assert net_outflow_on(_TODAY) == 10000.0


def test_deposit_cannot_hide_loss(tmp_path):
    """A deposit produces negative net outflow → it lowers adjusted equity,
    increasing measured drawdown — never masking a real loss."""
    record_flow('deposit', 8000)
    assert net_outflow_on(_TODAY) == -8000.0  # subtracted from equity


def test_invalid_flow_rejected(tmp_path):
    with pytest.raises(ValueError):
        record_flow('transfer', 100)
    with pytest.raises(ValueError):
        record_flow('withdrawal', -5)


# ── Circuit-breaker equity adjustment (end-to-end via run_once) ───────

def _stubbed_orchestrator(total_value):
    import sys
    tests_dir = os.path.dirname(os.path.abspath(__file__))
    if tests_dir not in sys.path:
        sys.path.insert(0, tests_dir)
    from test_integration import _stub_run_once_deps
    from trading_orchestrator import TradingOrchestrator
    with patch('trading_orchestrator.MarketDataFetcher'), \
         patch('trading_orchestrator.SignalGenerator'), \
         patch('trading_orchestrator.OrderExecutor'), \
         patch('trading_orchestrator.ReconciliationEngine') as mock_recon:
        mock_recon.return_value.reconcile_all.return_value = {'healthy': True}
        orch = TradingOrchestrator()
    _stub_run_once_deps(orch, Mock(), Mock(), Mock())
    orch.order_executor.broker.get_holdings.return_value = {
        'cash': total_value, 'positions': [], 'total_value': total_value,
    }
    return orch


def _seed_peak(value):
    p = data_path('peak_value.json')
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, 'w') as f:
        json.dump({'peak_value': value, 'date': _TODAY}, f)


def test_breaker_fires_on_real_drawdown(tmp_path):
    _seed_peak(20000.0)
    orch = _stubbed_orchestrator(1186.5)          # 94% drawdown, no flows
    result = orch.run_once()
    assert any('Circuit breaker' in e for e in result['errors'])


def test_breaker_suppressed_by_confirmed_withdrawal(tmp_path):
    _seed_peak(20000.0)
    record_flow('withdrawal', 19000.0)            # adjusted equity = 20186.5
    orch = _stubbed_orchestrator(1186.5)
    result = orch.run_once()
    assert not any('Circuit breaker' in e for e in result['errors'])


def test_breaker_still_fires_on_partial_withdrawal(tmp_path):
    """Withdrawal 5000 covers only part of the drop — remaining 25%
    drawdown is real loss and must still fire."""
    _seed_peak(20000.0)
    record_flow('withdrawal', 5000.0)             # adjusted equity = 6186.5 -> 69% dd
    orch = _stubbed_orchestrator(1186.5)
    result = orch.run_once()
    assert any('Circuit breaker' in e for e in result['errors'])


def test_unconfirmed_withdrawal_does_not_suppress(tmp_path):
    _seed_peak(20000.0)
    record_flow('withdrawal', 19000.0, confirmed=False)
    orch = _stubbed_orchestrator(1186.5)
    result = orch.run_once()
    assert any('Circuit breaker' in e for e in result['errors'])
