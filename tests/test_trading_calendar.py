"""NSE trading calendar — shared holiday source for Swing LIVE and
Angel PAPER. Verifies the corrected 2026 list, fail-closed unknown
years, special-session handling and cross-module consistency."""
import os
import sys
from datetime import datetime

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import trading_calendar as tc

IST = tc.IST


def _ist(y, m, d, hh=12, mm=0):
    return IST.localize(datetime(y, m, d, hh, mm))


class TestTradingCalendar:

    @pytest.mark.parametrize("holiday", [
        "2026-01-15",   # Municipal Corp Election (Maharashtra) — added
        "2026-01-26", "2026-03-03", "2026-03-26", "2026-03-31",
        "2026-04-03", "2026-04-14", "2026-05-01", "2026-05-28",
        "2026-06-26", "2026-09-14", "2026-10-02", "2026-10-20",
        "2026-11-10", "2026-11-24", "2026-12-25",
    ])
    def test_weekday_holidays_blocked(self, holiday):
        d = datetime.strptime(holiday, "%Y-%m-%d")
        assert d.weekday() < 5, f"{holiday} falls on a weekend?"
        assert tc.is_trading_day(_ist(d.year, d.month, d.day)) is False
        assert tc.is_holiday(_ist(d.year, d.month, d.day)) is True

    @pytest.mark.parametrize("wrong", [
        "2026-03-20", "2026-04-02", "2026-10-29", "2026-11-13",
    ])
    def test_former_incorrect_dates_removed(self, wrong):
        """Dates that were wrongly listed as 2026 holidays now trade."""
        d = datetime.strptime(wrong, "%Y-%m-%d")
        if d.weekday() < 5:
            assert tc.is_trading_day(_ist(d.year, d.month, d.day)) is True
            assert tc.is_holiday(_ist(d.year, d.month, d.day)) is False

    def test_ordinary_trading_weekday(self):
        assert tc.is_trading_day(_ist(2026, 10, 5)) is True   # Monday
        assert tc.is_trading_day(_ist(2026, 10, 26)) is True  # Monday

    def test_weekend_never_trading(self):
        assert tc.is_trading_day(_ist(2026, 10, 24)) is False  # Sat
        assert tc.is_trading_day(_ist(2026, 10, 25)) is False  # Sun

    def test_unknown_year_fails_closed(self):
        """2027 (not yet in the list) is NOT assumed tradable."""
        assert tc.is_trading_day(_ist(2027, 1, 5)) is False    # Tue
        assert tc.is_trading_day(_ist(2030, 6, 3)) is False    # Mon

    def test_muhurat_is_special_not_trading_day(self):
        """Nov 8, 2026 (Sunday) Muhurat session — recorded but does NOT
        enable ordinary Sunday trading."""
        assert tc.is_special_session(_ist(2026, 11, 8)) is True
        assert tc.is_trading_day(_ist(2026, 11, 8)) is False

    def test_weekend_is_not_holiday(self):
        """A Saturday is non-trading but not a declared 'holiday'."""
        assert tc.is_holiday(_ist(2026, 10, 24)) is False


class TestSharedSourceConsistency:
    """Both market_data copies and the Swing/Angel paths must resolve
    to the same authoritative set — no divergent duplicates."""

    def test_both_fetchers_share_the_set(self):
        from market_data import MarketDataFetcher
        from market_data_optimized import MarketDataFetcher as Opt
        assert MarketDataFetcher._NSE_HOLIDAYS is NSE_SET or \
            set(MarketDataFetcher._NSE_HOLIDAYS) == NSE_SET
        assert set(Opt._NSE_HOLIDAYS) == NSE_SET

    def test_market_data_fetcher_holiday_blocks_session(
            self, monkeypatch):
        """Swing LIVE gate: a declared weekday holiday → market closed."""
        from market_data import MarketDataFetcher
        f = MarketDataFetcher(kite=object())   # skip TokenManager init
        holiday = _ist(2026, 12, 25, 10, 0)    # Friday, Christmas
        real_dt = datetime

        class _FrozenDT:
            @staticmethod
            def now(tz=None):
                return holiday

            @staticmethod
            def strptime(s, fmt):
                return real_dt.strptime(s, fmt)

        # market_data re-exports market_data_optimized.MarketDataFetcher,
        # whose is_market_open uses a module-bound `datetime` — patch the
        # attribute where the method actually resolves it. Patching both
        # module paths keeps the test correct if the alias flips back.
        monkeypatch.setattr('market_data_optimized.datetime', _FrozenDT)
        monkeypatch.setattr('market_data.datetime', _FrozenDT)
        assert f.is_market_open() is False

    def test_intraday_validator_uses_shared_calendar(self):
        from intraday.validators import IntradayValidator
        from intraday.models import IntradayConfig
        v = IntradayValidator(IntradayConfig())
        assert v.is_trading_day(_ist(2026, 12, 25)) is False   # holiday
        assert v.is_trading_day(_ist(2026, 10, 26)) is True    # Monday
        assert v.is_trading_day(_ist(2027, 1, 5)) is False     # uncovered


NSE_SET = set(tc.NSE_TRADING_HOLIDAYS)
