"""SWINGPULSE consolidated EOD email — one email per trading day covering
Swing LIVE + Intraday PAPER, bounded wait, INCOMPLETE fallback, persistent
dedup/retry via the shared incident store, and the engine EOD hook."""
import os
import sys
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from email_reports import EmailReporter
from intraday.engine import IntradayEngine
from intraday.models import IntradayTrade, SignalDirection, SignalClassification
from persistence import TradingStore


def _capture(monkeypatch):
    """Capture send_report calls without SMTP.  Dedups on incident_key
    exactly like the real persistent path — a second attempt with the
    same key records nothing."""
    calls = []
    seen = set()

    def _send(self, subject, body, incident_key=None, text=None):
        if incident_key is not None:
            if incident_key in seen:
                return False
            seen.add(incident_key)
        calls.append({'subject': subject, 'body': body,
                      'text': text, 'incident_key': incident_key})
        return True

    monkeypatch.setattr(EmailReporter, 'send_report', _send)
    return calls


def _isolate(monkeypatch, tmp_path, trading_ok=True, hour=15, minute=35):
    """Isolate the snapshot dir, trading calendar and clock (15:35 IST —
    inside the bounded wait window, before the 16:00 deadline)."""
    monkeypatch.setattr(EmailReporter, '_eod_dir', lambda self: str(tmp_path))
    import trading_calendar
    monkeypatch.setattr(trading_calendar, 'is_trading_day',
                        lambda now=None: trading_ok)
    fixed = datetime.now().replace(hour=hour, minute=minute,
                                 second=0, microsecond=0)

    class _FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed

    monkeypatch.setattr('email_reports.datetime', _FrozenDateTime)


def _summary(regime='BEAR'):
    return {
        'cash': 50000.0, 'invested': 0.0, 'total_value': 50000.0,
        'daily_pnl': 320.0, 'open_positions': 0, 'market_regime': regime,
        'execution_summary': {'position_summary': {'total_charges': 0.0}},
    }


def _ctx(trades):
    return {
        'trading_date': datetime.now().strftime('%Y-%m-%d'),
        'date': datetime.now().strftime('%d %b %Y'), 'generated_at': '03:15 PM IST',
        'capital': '₹10,000', 'max_trades': 5,
        'daily_loss_limit': '₹200', 'loss_limit_status': 'not hit',
        'entries_today': len(trades),
        'wins': sum(1 for t in trades if t['net_pnl'] > 0),
        'losses': sum(1 for t in trades if t['net_pnl'] <= 0),
        'win_rate': 40.0 if trades else None,
        'gross_pnl': sum(t['gross_pnl'] for t in trades),
        'charges': sum(t['charges'] for t in trades),
        'net_pnl': sum(t['net_pnl'] for t in trades),
        'trades': trades, 'open_positions': [], 'eod_pending_positions': 0,
        'eod_completed_at': '03:15 PM IST', 'entry_permission': 'ENTRY WINDOW CLOSED',
        'auth_status': 'CONNECTED', 'market_data_status': 'LIVE',
        'strategy_breakdown': {'ORB_VWAP': len(trades)},
        'notes': ['Test note'],
    }


def _trade_row(**kw):
    base = {'symbol': 'ADANIPORTS', 'direction': 'LONG', 'quantity': 6,
            'entry_price': 1743.0, 'exit_price': 1734.4, 'gross_pnl': -32.0,
            'charges': 14.07, 'net_pnl': -46.07, 'exit_reason': 'STOP_LOSS',
            'strategy': 'ORB_VWAP'}
    base.update(kw)
    return base


def _post_deadline():
    return datetime.now().replace(hour=16, minute=5, second=0, microsecond=0)


class TestConsolidatedSend:
    def test_both_ready_sends_exactly_one(self, monkeypatch, tmp_path):
        calls = _capture(monkeypatch)
        _isolate(monkeypatch, tmp_path)
        r = EmailReporter()
        r.daily_report(_summary(), [], [])
        r.intraday_daily_report(_ctx([_trade_row()]))
        assert len(calls) == 1
        assert calls[0]['subject'] == (
            f"SWINGPULSE TERMINAL | Daily Trading Report | "
            f"{datetime.now().strftime('%d %b %Y')}")
        assert calls[0]['incident_key'] == (
            f"swingpulse:consolidated_eod:{datetime.now().strftime('%Y-%m-%d')}")

    def test_consolidated_sections_and_labels(self, monkeypatch, tmp_path):
        calls = _capture(monkeypatch)
        _isolate(monkeypatch, tmp_path)
        r = EmailReporter()
        r.daily_report(_summary(), [], [])
        r.intraday_daily_report(_ctx([_trade_row()]))
        b = calls[0]['body']
        assert 'Executive Summary' in b
        assert 'Swing LIVE — Zerodha Kite' in b and '● LIVE' in b
        assert 'Intraday PAPER — Angel One' in b and 'PAPER SIMULATION' in b
        assert 'PAPER TRADING — SIMULATED RESULTS' in b
        assert 'EOD Status' in b and 'READY' in b
        assert 'ADANIPORTS' in b and 'STOP_LOSS' in b

    def test_pnl_reported_separately_never_combined(self, monkeypatch, tmp_path):
        calls = _capture(monkeypatch)
        _isolate(monkeypatch, tmp_path)
        r = EmailReporter()
        r.daily_report(_summary(), [], [])          # net ₹+320.00
        r.intraday_daily_report(_ctx([_trade_row()]))  # net ₹-46.07
        b = calls[0]['body']
        assert '₹320.00' in b and '₹-46.07' in b
        assert '₹273.93' not in b  # P&L must never be summed
        assert 'SIMULATED' in b

    def test_one_workspace_waits_bounded(self, monkeypatch, tmp_path):
        calls = _capture(monkeypatch)
        _isolate(monkeypatch, tmp_path)
        EmailReporter().intraday_daily_report(_ctx([]))
        assert calls == []  # swing missing, before deadline → wait

    def test_deadline_sends_incomplete_section(self, monkeypatch, tmp_path):
        calls = _capture(monkeypatch)
        _isolate(monkeypatch, tmp_path)
        EmailReporter().intraday_daily_report(_ctx([_trade_row()]))
        assert calls == []
        EmailReporter().attempt_consolidated(now=_post_deadline())
        assert len(calls) == 1
        b = calls[0]['body']
        assert 'INCOMPLETE' in b and 'Swing LIVE' in b
        assert 'Intraday PAPER — Angel One' in b  # ready section still full

    def test_swing_missing_labels_intraday_incomplete(self, monkeypatch, tmp_path):
        calls = _capture(monkeypatch)
        _isolate(monkeypatch, tmp_path)
        EmailReporter().daily_report(_summary(), [], [])
        assert calls == []
        EmailReporter().attempt_consolidated(now=_post_deadline())
        assert 'INCOMPLETE' in calls[0]['body']
        assert 'Intraday PAPER' in calls[0]['body']

    def test_no_trade_day(self, monkeypatch, tmp_path):
        calls = _capture(monkeypatch)
        _isolate(monkeypatch, tmp_path)
        r = EmailReporter()
        r.daily_report(_summary(), [], [])
        r.intraday_daily_report(_ctx([]))
        assert len(calls) == 1
        assert 'No trades today' in calls[0]['body']
        assert 'BUY permission blocked' in calls[0]['body']  # BEAR explanation

    def test_holiday_never_sends(self, monkeypatch, tmp_path):
        calls = _capture(monkeypatch)
        _isolate(monkeypatch, tmp_path, trading_ok=False)
        EmailReporter().attempt_consolidated(now=_post_deadline())
        assert calls == []

    def test_no_snapshots_never_sends(self, monkeypatch, tmp_path):
        """Post-deadline with zero published snapshots → no email.
        Guards against an all-INCOMPLETE send (e.g. a deploy-day retry
        running before the feature ever produced a snapshot)."""
        calls = _capture(monkeypatch)
        _isolate(monkeypatch, tmp_path)
        EmailReporter().attempt_consolidated(now=_post_deadline())
        assert calls == []

    def test_stale_and_malformed_snapshots_not_ready(self, monkeypatch, tmp_path):
        """Only today's well-formed dict snapshots count as READY."""
        calls = _capture(monkeypatch)
        _isolate(monkeypatch, tmp_path)
        r = EmailReporter()
        old = (datetime.now() - timedelta(days=2)).strftime('%Y-%m-%d')
        (tmp_path / f"{old}.swing.json").write_text(
            '{"workspace": "swing", "net": 1.0, "html": "<div>old</div>", "text": "old"}')
        today = datetime.now().strftime('%Y-%m-%d')
        (tmp_path / f"{today}.intraday.json").write_text('{not valid json')
        (tmp_path / f"{today}.swing.json").write_text('[1,2,3]')  # wrong type
        r.attempt_consolidated(now=_post_deadline())
        assert calls == []  # neither counts as READY → nothing sends

    def test_plain_text_fallback(self, monkeypatch, tmp_path):
        calls = _capture(monkeypatch)
        _isolate(monkeypatch, tmp_path)
        r = EmailReporter()
        r.daily_report(_summary('NEUTRAL'), [], [])
        r.intraday_daily_report(_ctx([_trade_row()]))
        t = calls[0]['text']
        assert 'SWINGPULSE TERMINAL' in t
        assert 'Swing LIVE (Kite)' in t and 'Intraday PAPER (Angel)' in t
        assert 'SIMULATED' in t and 'BUY permission' in t

    def test_html_escaping(self, monkeypatch, tmp_path):
        calls = _capture(monkeypatch)
        _isolate(monkeypatch, tmp_path)
        orders = [{'tradingsymbol': '<b>X</b>', 'transaction_type': 'BUY',
                   'quantity': 1, 'average_price': 1.0, 'status': 'COMPLETE',
                   'order_timestamp': datetime.now().isoformat()}]
        r = EmailReporter()
        r.daily_report(_summary('NEUTRAL'), [], orders)
        r.intraday_daily_report(_ctx([]))
        assert '<b>X</b>' not in calls[0]['body']
        assert '&lt;b&gt;X&lt;/b&gt;' in calls[0]['body']


@pytest.fixture
def live_reporter(tmp_path, monkeypatch):
    """Reporter on the real dedup path: tmp snapshot dir, tmp incident
    store, trading day forced, _deliver stubbed per-test."""
    monkeypatch.setattr(EmailReporter, '_eod_dir', lambda self: str(tmp_path))
    import trading_calendar
    monkeypatch.setattr(trading_calendar, 'is_trading_day', lambda now=None: True)
    store = TradingStore(str(tmp_path / 'incidents.db'))
    monkeypatch.setattr(EmailReporter, '_incident_store', lambda self: store)
    r = EmailReporter()
    r.enabled, r.username, r.password, r.to_email = True, 'u', 'p', 't@x'
    return r


def _publish_both(r, monkeypatch):
    monkeypatch.setattr(r, '_load_ready', lambda ws, td: {
        'workspace': ws, 'date': '28 Oct 2026', 'generated_at': '03:30 PM IST',
        'html': f'<div>{ws} section</div>', 'text': f'{ws} text',
        'net': 1.0, 'regime': 'NEUTRAL'})


class TestDedupAndRetry:
    def test_cross_restart_dedup(self, live_reporter, monkeypatch, tmp_path):
        _publish_both(live_reporter, monkeypatch)
        sends = []
        monkeypatch.setattr(EmailReporter, '_deliver',
                            lambda self, s, b, t: sends.append(s) or True)
        assert live_reporter.attempt_consolidated() is True
        assert len(sends) == 1
        # "Restart": brand-new reporter + a fresh attempt on the same day.
        r2 = EmailReporter()
        r2.enabled, r2.username, r2.password, r2.to_email = True, 'u', 'p', 't@x'
        monkeypatch.setattr(r2, '_load_ready', live_reporter._load_ready)
        assert r2.attempt_consolidated() is False
        assert len(sends) == 1

    def test_smtp_failure_then_retry(self, live_reporter, monkeypatch, tmp_path):
        _publish_both(live_reporter, monkeypatch)
        attempts = []
        monkeypatch.setattr(EmailReporter, '_deliver',
                            lambda self, s, b, t: attempts.append(1) or False)
        assert live_reporter.attempt_consolidated() is False
        monkeypatch.setattr(EmailReporter, '_deliver',
                            lambda self, s, b, t: attempts.append(1) or True)
        assert live_reporter.attempt_consolidated() is True
        assert len(attempts) == 2

    def test_concurrent_attempts_single_delivery(self, live_reporter,
                                                 monkeypatch, tmp_path):
        _publish_both(live_reporter, monkeypatch)
        r2 = EmailReporter()
        r2.enabled, r2.username, r2.password, r2.to_email = True, 'u', 'p', 't@x'
        monkeypatch.setattr(r2, '_load_ready', live_reporter._load_ready)
        sends = []
        monkeypatch.setattr(EmailReporter, '_deliver',
                            lambda self, s, b, t: sends.append(s) or True)
        live_reporter.attempt_consolidated()
        r2.attempt_consolidated()          # peer process firing same moment
        live_reporter.attempt_consolidated()
        assert len(sends) == 1


def _trade(symbol='RELIANCE', net=-46.0):
    today = datetime.now().replace(hour=9, minute=45, second=0, microsecond=0)
    return IntradayTrade(
        id='t1', symbol=symbol, direction=SignalDirection.LONG,
        entry_price=1743.0, exit_price=1734.4, quantity=6,
        entry_time=today, exit_time=today.replace(minute=55),
        exit_reason='STOP_LOSS', gross_pnl=-32.0, charges=14.0, net_pnl=net,
        pnl_pct=-0.5, stop_loss=1737.9, target=1748.0, risk_reward=3.0,
        signal_score=77.2,
        signal_classification=SignalClassification.GOOD_SETUP,
        strategy='ORB_VWAP')


def _engine(tmp_path):
    eng = IntradayEngine(state_dir=str(tmp_path),
                         alert_hook=lambda a, k, d: None)
    eng.validator.is_square_off_time = MagicMock(return_value=False)
    return eng


class TestEngineEodHook:
    def test_mark_eod_complete_fires_report_once(self, tmp_path):
        eng = _engine(tmp_path)
        eng._send_eod_report = MagicMock()
        eng._mark_eod_complete()
        eng._mark_eod_complete()
        assert eng._send_eod_report.call_count == 1
        assert eng._eod_completed_at is not None

    def test_repeat_completion_retries_consolidated(self, tmp_path):
        eng = _engine(tmp_path)
        eng._send_eod_report = MagicMock()
        calls = []
        with patch('email_reports.EmailReporter') as cls:
            inst = cls.return_value
            inst.consolidated_pending.return_value = True
            inst.attempt_consolidated.side_effect = lambda: calls.append(1) or True
            eng._mark_eod_complete()          # first: snapshot + attempt
            eng._mark_eod_complete()          # repeat: coordinator retry
            eng._mark_eod_complete()
        assert eng._send_eod_report.call_count == 1
        assert len(calls) == 2               # one retry per later tick

    def test_send_eod_report_builds_context(self, tmp_path, monkeypatch):
        eng = _engine(tmp_path)
        eng.trade_manager._trades.append(_trade())
        eng.risk_manager.get_daily_state = MagicMock(return_value={
            'trades_today': 1, 'blocked': False, 'block_reason': None})
        eng._entry_permission = MagicMock(return_value='ENTRY WINDOW CLOSED')
        eng._effective_auth_state = MagicMock(return_value='CONNECTED')
        eng.market_data_status = MagicMock(return_value='LIVE')
        captured = {}
        with patch('email_reports.EmailReporter') as cls:
            cls.return_value.intraday_daily_report = (
                lambda ctx: captured.update(ctx) or True)
            eng._eod_completed_at = datetime.now()
            eng._send_eod_report()
        assert captured['net_pnl'] == -46.0
        assert captured['trading_date'] == datetime.now().strftime('%Y-%m-%d')
        assert captured['trades'][0]['symbol'] == 'RELIANCE'
        assert captured['trades'][0]['direction'] == 'LONG'
        assert captured['capital'] == '₹10,000'
        assert captured['eod_pending_positions'] == 0

    def test_email_failure_never_raises(self, tmp_path):
        eng = _engine(tmp_path)
        with patch('email_reports.EmailReporter') as cls:
            cls.return_value.intraday_daily_report.side_effect = RuntimeError('smtp down')
            eng._eod_completed_at = datetime.now()
            eng._send_eod_report()  # must not raise
