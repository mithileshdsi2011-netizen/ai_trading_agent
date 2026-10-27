"""Regression tests for Angel PAPER P&L charges sign + exit-price provenance.

Charges must always be a nonnegative cost: net_pnl == gross_pnl - |charges|.
Exit prices are VERIFIED only when a genuine broker-quote receipt time is
fresh at decision time; anything missing/stale stays UNVERIFIED.
"""
import json
import os
from datetime import datetime, timedelta

import pytest

from intraday.models import (
    IntradayConfig, IntradayPosition, IntradayTrade,
    PositionStatus, SignalDirection, SignalClassification,
)
from intraday.paper_executor import PaperTradeExecutor, EXIT_QUOTE_MAX_AGE_S
from intraday.risk_manager import IntradayRiskManager
from intraday.position_sizer import IntradayPositionSizer
from intraday.trade_manager import IntradayTradeManager


def _executor():
    cfg = IntradayConfig()
    rm = IntradayRiskManager(cfg)
    return PaperTradeExecutor(rm, IntradayPositionSizer(cfg), cfg), rm


def _position(direction=SignalDirection.LONG, entry=100.0, qty=10,
              sl=95.0, tgt=110.0, **kw):
    pos = IntradayPosition(
        id=kw.pop('id', 'p1'), symbol=kw.pop('symbol', 'TEST'),
        direction=direction, entry_price=entry, quantity=qty,
        stop_loss=sl, target=tgt, entry_time=datetime.now(),
        current_price=kw.pop('current_price', entry),
        unrealized_pnl=0.0, unrealized_pnl_pct=0.0,
        is_paper=True, **kw,
    )
    return pos


def _exit(executor, rm, pos, price):
    rm.add_position(pos)
    return executor.execute_exit(pos, price, "TEST")


class TestChargesSign:
    """charges = abs(gross_pnl) * 0.001 — always a cost, never a credit."""

    def test_profitable_long(self):
        ex, rm = _executor()
        t = _exit(ex, rm, _position(SignalDirection.LONG, 100.0, 10), 110.0)
        assert t.gross_pnl == pytest.approx(100.0)
        assert t.charges == pytest.approx(0.1)
        assert t.net_pnl == pytest.approx(99.9)
        assert t.net_pnl < t.gross_pnl

    def test_losing_long(self):
        ex, rm = _executor()
        t = _exit(ex, rm, _position(SignalDirection.LONG, 100.0, 10), 90.0)
        assert t.gross_pnl == pytest.approx(-100.0)
        assert t.charges == pytest.approx(0.1)
        assert t.charges > 0
        # Loss must be understated never: net MORE negative than gross
        assert t.net_pnl == pytest.approx(-100.1)
        assert t.net_pnl < t.gross_pnl

    def test_profitable_short(self):
        ex, rm = _executor()
        t = _exit(ex, rm, _position(SignalDirection.SHORT, 100.0, 10,
                                    sl=105.0, tgt=90.0), 90.0)
        assert t.gross_pnl == pytest.approx(100.0)
        assert t.charges == pytest.approx(0.1)
        assert t.net_pnl == pytest.approx(99.9)

    def test_losing_short(self):
        ex, rm = _executor()
        t = _exit(ex, rm, _position(SignalDirection.SHORT, 100.0, 10,
                                    sl=105.0, tgt=90.0), 110.0)
        assert t.gross_pnl == pytest.approx(-100.0)
        assert t.charges == pytest.approx(0.1)
        assert t.net_pnl == pytest.approx(-100.1)
        assert t.net_pnl < t.gross_pnl

    def test_zero_pnl_zero_charges(self):
        ex, rm = _executor()
        t = _exit(ex, rm, _position(SignalDirection.LONG, 100.0, 10), 100.0)
        assert t.gross_pnl == pytest.approx(0.0)
        assert t.charges == pytest.approx(0.0)
        assert t.net_pnl == pytest.approx(0.0)

    def test_charges_never_negative(self):
        ex, rm = _executor()
        for i, (d, px) in enumerate([
                (SignalDirection.LONG, 90.0), (SignalDirection.LONG, 110.0),
                (SignalDirection.SHORT, 110.0), (SignalDirection.SHORT, 90.0)]):
            pos = _position(d, 100.0, 10, id=f'p{i}',
                            sl=(95.0 if d == SignalDirection.LONG else 105.0),
                            tgt=(110.0 if d == SignalDirection.LONG else 90.0))
            t = _exit(ex, rm, pos, px)
            assert t.charges >= 0
            assert t.net_pnl == pytest.approx(t.gross_pnl - abs(t.charges))


def _provenanced(direction=SignalDirection.LONG, exit_price=101.0,
                 symbol='TEST', **kw):
    """Position whose current_price carries full VERIFIED-grade provenance."""
    kw.setdefault('current_price_updated_at', datetime.now())
    kw.setdefault('current_price_quote_symbol', symbol)
    kw.setdefault('current_price_quote_exchange', 'NSE')
    kw.setdefault('current_price', exit_price)
    return _position(direction, symbol=symbol, **kw)


class TestExitPriceProvenance:
    """VERIFIED only for a fresh broker-quote receipt; else UNVERIFIED."""

    def test_fresh_quote_is_verified(self):
        ex, rm = _executor()
        t = _exit(ex, rm, _provenanced(exit_price=101.0), 101.0)
        assert t.exit_price_source == 'VERIFIED'
        assert t.exit_quote_received_at is not None
        assert 0 <= t.exit_quote_age_s <= EXIT_QUOTE_MAX_AGE_S
        assert t.exit_quote_symbol == 'TEST'
        assert t.exit_quote_exchange == 'NSE'

    def test_stale_quote_is_unverified(self):
        ex, rm = _executor()
        pos = _provenanced(exit_price=101.0,
                           current_price_updated_at=datetime.now()
                           - timedelta(seconds=60))
        t = _exit(ex, rm, pos, 101.0)
        assert t.exit_price_source == 'UNVERIFIED'
        assert t.exit_quote_age_s == pytest.approx(60, abs=1)

    def test_missing_quote_time_is_unverified(self):
        ex, rm = _executor()
        pos = _position()  # no provenance at all
        t = _exit(ex, rm, pos, 101.0)
        assert t.exit_price_source == 'UNVERIFIED'
        assert t.exit_quote_received_at is None
        assert t.exit_quote_age_s is None

    def test_boundary_age_verified(self):
        ex, rm = _executor()
        pos = _provenanced(exit_price=101.0,
                           current_price_updated_at=datetime.now()
                           - timedelta(seconds=EXIT_QUOTE_MAX_AGE_S - 1))
        t = _exit(ex, rm, pos, 101.0)
        assert t.exit_price_source == 'VERIFIED'

    def test_exchange_ts_preserved_only_when_real(self):
        ex, rm = _executor()
        exch = datetime.now() - timedelta(seconds=2)
        pos = _provenanced(exit_price=101.0, current_price_exchange_ts=exch)
        t = _exit(ex, rm, pos, 101.0)
        assert t.exit_exchange_ts == exch

        pos2 = _provenanced(id='p2', exit_price=101.0,
                            current_price_exchange_ts=None)
        t2 = _exit(ex, rm, pos2, 101.0)
        assert t2.exit_exchange_ts is None
        # Absence of an exchange timestamp does NOT disqualify verification
        assert t2.exit_price_source == 'VERIFIED'

    def test_mismatched_exit_price_is_unverified(self):
        """Exit price must equal the price the quote produced."""
        ex, rm = _executor()
        pos = _provenanced(exit_price=101.0)
        t = _exit(ex, rm, pos, 102.5)   # not the quote price
        assert t.exit_price_source == 'UNVERIFIED'
        assert t.exit_price == 102.5

    def test_wrong_quote_symbol_is_unverified(self):
        ex, rm = _executor()
        pos = _provenanced(exit_price=101.0,
                           current_price_quote_symbol='OTHER-EQ')
        t = _exit(ex, rm, pos, 101.0)
        assert t.exit_price_source == 'UNVERIFIED'

    def test_wrong_exchange_is_unverified(self):
        ex, rm = _executor()
        pos = _provenanced(exit_price=101.0,
                           current_price_quote_exchange='BSE')
        t = _exit(ex, rm, pos, 101.0)
        assert t.exit_price_source == 'UNVERIFIED'

    def test_missing_symbol_or_exchange_is_unverified(self):
        ex, rm = _executor()
        pos = _provenanced(exit_price=101.0,
                           current_price_quote_symbol=None,
                           current_price_quote_exchange=None)
        t = _exit(ex, rm, pos, 101.0)
        assert t.exit_price_source == 'UNVERIFIED'

    def test_future_quote_timestamp_is_unverified(self):
        ex, rm = _executor()
        pos = _provenanced(exit_price=101.0,
                           current_price_updated_at=datetime.now()
                           + timedelta(seconds=5))
        t = _exit(ex, rm, pos, 101.0)
        assert t.exit_price_source == 'UNVERIFIED'
        assert t.exit_quote_age_s < 0

    def test_suffixed_tradingsymbol_matches(self):
        """Angel trading symbols like 'GRASIM-EQ' match base 'GRASIM'."""
        ex, rm = _executor()
        pos = _provenanced(exit_price=101.0, symbol='GRASIM',
                           current_price_quote_symbol='GRASIM-EQ')
        t = _exit(ex, rm, pos, 101.0)
        assert t.exit_price_source == 'VERIFIED'

    def test_update_positions_stamps_provenance(self):
        ex, rm = _executor()
        pos = _position()
        ex._paper_positions[pos.id] = pos
        exch = datetime.now()
        ex.update_positions({'TEST': 105.0},
                            quotes={'TEST': {'exchange_ts': exch}})
        assert pos.current_price == 105.0
        assert pos.current_price_updated_at is not None
        assert pos.current_price_exchange_ts == exch

    def test_update_positions_no_quote_keeps_receipt_only(self):
        ex, rm = _executor()
        pos = _position()
        ex._paper_positions[pos.id] = pos
        ex.update_positions({'TEST': 105.0})
        assert pos.current_price_updated_at is not None
        assert pos.current_price_exchange_ts is None


class TestProvenancePersistence:
    """New fields round-trip; legacy rows load with UNVERIFIED semantics."""

    def test_trade_round_trip(self, tmp_path):
        f = str(tmp_path / 'trades.json')
        mgr = IntradayTradeManager(data_file=f)
        exch = datetime(2026, 10, 27, 10, 21, 22)
        recv = datetime(2026, 10, 27, 10, 21, 23)
        t = IntradayTrade(
            id='t1', symbol='GRASIM', direction=SignalDirection.SHORT,
            entry_price=2926.7, exit_price=2911.0, quantity=3,
            entry_time=recv - timedelta(minutes=29), exit_time=recv,
            exit_reason='TARGET_HIT', gross_pnl=47.1, charges=0.0471,
            net_pnl=47.0529, pnl_pct=1.6, stop_loss=2929.0, target=2919.81,
            risk_reward=1.0, signal_score=60.2,
            signal_classification=SignalClassification.WATCH,
            strategy='ORB', is_paper=True, duration_minutes=29.0,
            exit_price_source='VERIFIED', exit_exchange_ts=exch,
            exit_quote_received_at=recv, exit_quote_age_s=1.0)
        mgr.add_trade(t)
        mgr2 = IntradayTradeManager(data_file=f)
        t2 = mgr2.get_trades()[0]
        assert t2.exit_price_source == 'VERIFIED'
        assert t2.exit_exchange_ts == exch
        assert t2.exit_quote_received_at == recv
        assert t2.exit_quote_age_s == pytest.approx(1.0)
        assert t2.direction == SignalDirection.SHORT

    def test_legacy_record_loads_without_provenance(self, tmp_path):
        f = str(tmp_path / 'trades.json')
        legacy = [{
            'id': 'old1', 'symbol': 'BPCL', 'direction': 'LONG',
            'entry_price': 290.0, 'exit_price': 291.35, 'quantity': 34,
            'entry_time': '2026-10-27T09:31:31', 'exit_time': '2026-10-27T10:00:46',
            'exit_reason': 'TARGET_HIT', 'gross_pnl': 45.9, 'charges': 0.0459,
            'net_pnl': 45.8541, 'pnl_pct': 0.465, 'stop_loss': 289.59,
            'target': 291.24, 'risk_reward': 0.99, 'signal_score': 77.2,
            'signal_classification': 'GOOD_SETUP', 'strategy': 'ORB',
            'is_paper': True, 'duration_minutes': 29.25,
        }]
        with open(f, 'w') as fh:
            json.dump(legacy, fh)
        mgr = IntradayTradeManager(data_file=f)
        t = mgr.get_trades()[0]
        assert t.exit_price_source is None  # → history renders UNVERIFIED
        assert t.exit_exchange_ts is None
        assert t.net_pnl == pytest.approx(45.8541)
