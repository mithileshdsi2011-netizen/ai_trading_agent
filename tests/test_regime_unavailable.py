"""Regression: regime-detection failure must report UNAVAILABLE + last-known,
never fabricate a regime value (was silently shipping 'UNKNOWN', causing
BEAR↔UNKNOWN alert flapping)."""
import os
import sys
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


@pytest.fixture
def reset_regime_cache():
    import dashboard
    dashboard._LAST_KNOWN_REGIME['value'] = None
    dashboard._LAST_KNOWN_REGIME['at'] = None
    yield dashboard


def test_detection_failure_reports_unavailable(reset_regime_cache):
    dashboard = reset_regime_cache
    with patch('market_regime.MarketRegimeDetector') as MD:
        MD.return_value.detect_regime.side_effect = RuntimeError("kite down")
        regime, last_known = dashboard._detect_regime_or_unavailable(MagicMock())
    assert regime == 'UNAVAILABLE'
    assert last_known is None  # nothing genuine to report yet


def test_failure_exposes_last_known_regime(reset_regime_cache):
    dashboard = reset_regime_cache
    with patch('market_regime.MarketRegimeDetector') as MD:
        MD.return_value.detect_regime.return_value = 'BEAR'
        regime, _ = dashboard._detect_regime_or_unavailable(MagicMock())
        assert regime == 'BEAR'

        # next call fails → UNAVAILABLE but last-known BEAR preserved
        MD.return_value.detect_regime.side_effect = RuntimeError("kite down")
        regime, last_known = dashboard._detect_regime_or_unavailable(MagicMock())
    assert regime == 'UNAVAILABLE'
    assert last_known['value'] == 'BEAR'
    assert last_known['at'] is not None


def test_bogus_regime_value_rejected(reset_regime_cache):
    dashboard = reset_regime_cache
    with patch('market_regime.MarketRegimeDetector') as MD:
        MD.return_value.detect_regime.return_value = 'GARBAGE'
        regime, _ = dashboard._detect_regime_or_unavailable(MagicMock())
    assert regime == 'UNAVAILABLE'
