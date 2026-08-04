"""
Shared pytest fixtures / environment for the AI Trading Agent test suite.

- Forces paper-trading mode so no real Kite orders are placed.
- Routes all persistent state (SQLite + journal JSON) into a temp directory per test.
- Resets module-level singletons (persistence store) between tests.
"""
import os
import sys
import shutil
import tempfile
from pathlib import Path
import pytest

# Ensure project root and src/ are importable
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from config import config  # noqa: E402

# Force test-safe configuration before any src modules are imported during collection.
config.PAPER_TRADING = True
config.TRADING_MODE = "swing"
config.TRADING_AMOUNT = 15000.0
config.MAX_POSITIONS = 7
config.MIN_CONFIDENCE = 0.60
config.MIN_RISK_REWARD = 1.5
config.TRADING_START = "00:00"
config.INTRADAY_CUTOFF = "23:59"

# Enterprise AI decision engine thresholds expected by the test suite
config.SCORE_BULL_THRESHOLD = 65
config.SCORE_SIDEWAYS_THRESHOLD = 75
config.SCORE_BEAR_THRESHOLD = 90

# Reconciliation: create synthetic journal entries on the first missing-order cycle in tests
from reconciliation_engine import ReconciliationEngine
ReconciliationEngine.MISSING_JOURNAL_RETRY_THRESHOLD = 1


@pytest.fixture(autouse=True, scope="function")
def isolated_test_env(tmp_path, monkeypatch):
    """Each test gets a fresh temp directory and a clean persistence layer."""
    # Persistent state paths
    db_path = tmp_path / "trading.db"
    journal_path = tmp_path / "trade_journal.json"
    positions_path = tmp_path / "positions.json"
    ip_path = tmp_path / "last_known_ip.txt"

    # Patch persistence singleton
    from persistence import get_store, DEFAULT_DB_PATH, the_store
    monkeypatch.setattr("persistence.DEFAULT_DB_PATH", str(db_path))
    # Reset the cached store so the next call creates a fresh one
    import persistence
    persistence.the_store = None

    # Patch trade journal file
    import trade_journal
    monkeypatch.setattr(trade_journal, "JOURNAL_FILE", str(journal_path))

    # Prevent real network/token side effects from tests
    monkeypatch.setenv("PAPER_TRADING", "True")
    monkeypatch.setenv("TRADING_MODE", "swing")

    # Some modules use the current working directory for logs/data; keep original.
    # Reset global singletons that tests may cache on class instances.
    yield

    # Clean up temp directory automatically by tmp_path fixture, but also
    # ensure the global store is not reused by a later test.
    persistence.the_store = None
