"""Monitoring FII/DII freshness correction (dashboard-side).

The fii_dii table stores display-format timestamps ('31-Aug-2026'), so
ORDER BY timestamp DESC can pick a months-old row while a fresh
observation exists. _correct_fii_dii_feed re-derives the feed's age from
the newest observation date — the monitoring panel must reflect the
actual latest row, not the lexical sort artifact.
"""
import os
import sys
import sqlite3
import importlib.util
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

_spec = importlib.util.spec_from_file_location(
    'dashboard_mod',
    os.path.join(os.path.dirname(__file__), '..', 'dashboard.py'))
_dash = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_dash)


def _store_with_fii_dii(tmp_path, rows):
    """Build a minimal store exposing _conn() over a seeded fii_dii table."""
    db = tmp_path / 't.db'
    conn = sqlite3.connect(str(db))
    conn.execute(
        "CREATE TABLE fii_dii (id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "timestamp TEXT, date TEXT, fii_net REAL)")
    conn.executemany(
        "INSERT INTO fii_dii (timestamp, date, fii_net) VALUES (?, ?, ?)", rows)
    conn.commit()
    conn.close()

    class _Store:
        from contextlib import contextmanager

        @contextmanager
        def _conn(self):
            c = sqlite3.connect(str(db))
            try:
                yield c
                c.commit()
            except Exception:
                c.rollback()
                raise
            finally:
                c.close()

    return _Store()


def _metrics(status='STALE', age=1389.0):
    return {'data_quality': {'fii_dii': {
        'critical': False, 'max_age_hours': 72,
        'status': status, 'age_hours': age}},
        'data_quality_score': 90}


class TestFiiDiiFreshnessCorrection:
    def test_lexical_trap_picks_fresh_date(self, tmp_path):
        """'31-Aug-2026' sorts above '27-Oct-2026' as text — ORDER BY date
        must still return the newest observation."""
        today = datetime.now().strftime('%Y-%m-%d')
        old = (datetime.now() - timedelta(days=58)).strftime('%Y-%m-%d')
        old_disp = (datetime.now() - timedelta(days=58)).strftime('%d-%b-%Y')
        store = _store_with_fii_dii(tmp_path, [
            (old_disp, old, 100.0),      # lexically-larger timestamp, old date
            (datetime.now().strftime('%d-%b-%Y'), today, 200.0),
        ])
        m = _metrics()
        _dash._correct_fii_dii_feed(m, store)
        f = m['data_quality']['fii_dii']
        assert f['status'] == 'OK'
        assert f['age_hours'] < 24

    def test_genuinely_old_date_stays_stale(self, tmp_path):
        old = (datetime.now() - timedelta(days=90)).strftime('%Y-%m-%d')
        store = _store_with_fii_dii(tmp_path, [('x', old, 100.0)])
        m = _metrics(status='OK', age=1.0)
        _dash._correct_fii_dii_feed(m, store)
        f = m['data_quality']['fii_dii']
        assert f['status'] == 'STALE'
        assert f['age_hours'] > 2000

    def test_missing_feed_is_noop(self, tmp_path):
        store = _store_with_fii_dii(tmp_path, [])
        m = {'data_quality': {}, 'data_quality_score': 90}
        _dash._correct_fii_dii_feed(m, store)
        assert m['data_quality_score'] == 90

    def test_score_rescored_after_correction(self, tmp_path):
        today = datetime.now().strftime('%Y-%m-%d')
        store = _store_with_fii_dii(tmp_path, [('x', today, 1.0)])
        m = _metrics()
        m['data_quality_score'] = 90  # -10 non-critical penalty
        _dash._correct_fii_dii_feed(m, store)
        assert m['data_quality_score'] == 100

    def test_bad_store_does_not_raise(self):
        class _Broken:
            pass
        m = _metrics()
        _dash._correct_fii_dii_feed(m, _Broken())  # must swallow errors
        assert m['data_quality']['fii_dii']['status'] == 'STALE'
