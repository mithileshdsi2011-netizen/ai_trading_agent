"""Cross-workspace status-bar isolation and engine-state semantics.

Regression tests for the 2026-10-28 incident where switching
Intraday -> Swing left Angel values in the shared top status bar
(mode ANGEL PAPER, STOPPED, "No completed scan", "Paused — market
closed") because only cash/positions were repainted.

These tests pin the embedded-JS contract in dashboard.py so the
specific wiring cannot silently regress.
"""
import os
import re

DASH = os.path.join(os.path.dirname(__file__), '..', 'dashboard.py')
SRC = open(DASH, encoding='utf-8').read()


def _fn(name):
    """Extract a JS function body (balanced-brace scan)."""
    m = re.search(r'function ' + re.escape(name) + r'\s*\([^)]*\)\s*\{', SRC)
    assert m, f'{name} not found in dashboard.py'
    depth, i = 1, m.end()
    while i < len(SRC) and depth:
        depth += {'{': 1, '}': -1}.get(SRC[i], 0)
        i += 1
    return SRC[m.start():i]


class TestWorkspaceHeaderRepaint:
    """Every status-bar field must repaint on workspace switch."""

    def test_switch_to_swing_repaints_full_header(self):
        body = _fn('_updateHdrWorkspace')
        assert '_swingHdr(' in body
        assert '_swingHdrCashPos(' not in body, (
            'partial repaint leaves intraday mode/scan/status values behind')

    def test_switch_to_intraday_repaints_header(self):
        body = _fn('_updateHdrWorkspace')
        assert '_intradayHdr(' in body

    def test_swing_header_paints_all_shared_fields(self):
        body = _fn('_swingHdr') + _fn('_swingHdrCashPos')
        for el in ('hdr-market', 'hdr-mode', 'hdr-last-scan',
                   'hdr-next-scan', 'hdr-status', 'hdr-token',
                   'hdr-cash', 'hdr-positions'):
            assert el in body, f'_swingHdr does not paint {el}'
        assert '_swingHdrCashPos(d)' in _fn('_swingHdr')

    def test_intraday_header_paints_all_shared_fields(self):
        body = _fn('_intradayHdr')
        for el in ('hdr-market', 'hdr-mode', 'hdr-last-scan',
                   'hdr-next-scan', 'hdr-status', 'hdr-token',
                   'hdr-cash', 'hdr-positions'):
            assert el in body, f'_intradayHdr does not paint {el}'

    def test_load_paints_swing_header_only_in_swing_workspace(self):
        assert re.search(
            r"NAV_STATE\.ws==='swing'\)\s*_swingHdr\(d\)", SRC)

    def test_intraday_load_paints_only_in_intraday_workspace(self):
        assert re.search(
            r"NAV_STATE\.ws==='intraday'\)\s*_intradayHdr\(d\)", SRC)


class TestEngineStateSemantics:
    """Bot status must reflect real engine health, not market/auth alone."""

    def test_swing_status_uses_reconciliation_heartbeat(self):
        body = _fn('_swingHdr')
        assert 'reconciliation_status' in body
        assert 'last_sync' in body
        # Heartbeat-derived states present
        assert 'Heartbeat' in body and 'Engine offline' in body

    def test_swing_status_not_inferred_from_kite_alone(self):
        body = _fn('_swingHdr')
        # kite_ok may annotate but must not be the sole status source
        assert 'd.kite_ok?\'<span' not in body

    def test_intraday_status_distinguishes_idle_from_failure(self):
        body = _fn('_intradayHdr')
        assert 'kill switch' in body
        assert 'Engine down' in body
        assert 'Idle — market closed' in body
        assert 'thread_alive' in body or 'running===false' in body

    def test_intraday_trading_status_idle_not_stopped(self):
        # Pane mirror: STOPPED at market close with live scheduler is wrong.
        m = re.search(r"getElementById\('intraday-trading-status'\)", SRC)
        assert m
        seg = SRC[m.start():m.start() + 2000]
        assert 'IDLE — MARKET CLOSED' in seg
        assert 'ENGINE DOWN' in seg


class TestScanAndTokenSemantics:
    def test_last_scan_never_shows_next_scan_value(self):
        body = _fn('_swingHdr')
        # last-scan falls back to an honest empty state, not a schedule
        assert 'No completed scan' in body
        assert 'Awaiting first scan' in body

    def test_expired_token_flagged(self):
        body = _fn('_swingHdr')
        assert 'EXPIRED' in body

    def test_intraday_next_scan_uses_scheduler_tick(self):
        body = _fn('_intradayHdr')
        assert 'expected_interval_seconds' in body
        assert 'last_tick' in body

    def test_reconciliation_stale_flag(self):
        m = re.search(r"getElementById\('rs-last-sync'\)", SRC)
        assert m
        seg = SRC[m.start():m.start() + 1200]
        assert 'stale' in seg


class TestWorkspaceChip:
    def test_workspace_chip_distinct_modes(self):
        body = _fn('_updateHdrWorkspace')
        assert 'INTRADAY' in body and 'PAPER' in body
        assert 'SWING' in body and 'LIVE' in body
