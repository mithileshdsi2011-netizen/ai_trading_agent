"""
persistence.py — SQLite single source of truth for trading state.

All trading state (positions, trades, orders, signals, daily risk state,
portfolio snapshots and broker state) is stored in a single SQLite file.
JSON columns are used for flexible nested data so existing dictionaries/
dataclasses can be persisted with no schema churn.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import date, datetime
from typing import Any, Dict, List, Optional

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "trading.db"
)


class TradingStore:
    """Central SQLite store for all trading state."""

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or DEFAULT_DB_PATH
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._lock = threading.Lock()
        self._init_schema()

    # ── connection helpers ────────────────────────────────────────────────

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    @contextmanager
    def _conn(self):
        conn = self._connect()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_schema(self):
        with self._conn() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS positions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    status TEXT NOT NULL,
                    data TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_positions_symbol_status
                    ON positions (symbol, status);

                CREATE TABLE IF NOT EXISTS trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    date TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    action TEXT NOT NULL,
                    status TEXT NOT NULL,
                    data TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_trades_symbol_date
                    ON trades (symbol, date);
                CREATE INDEX IF NOT EXISTS idx_trades_status
                    ON trades (status);

                CREATE TABLE IF NOT EXISTS orders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    action TEXT NOT NULL,
                    status TEXT NOT NULL,
                    data TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_orders_symbol
                    ON orders (symbol);

                CREATE TABLE IF NOT EXISTS signals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    action TEXT NOT NULL,
                    data TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS daily_state (
                    date TEXT PRIMARY KEY,
                    daily_pnl REAL NOT NULL DEFAULT 0,
                    daily_blacklist TEXT NOT NULL DEFAULT '[]',
                    last_exit_by_symbol TEXT NOT NULL DEFAULT '{}',
                    daily_trades INTEGER NOT NULL DEFAULT 0,
                    pending_sells TEXT NOT NULL DEFAULT '{}',
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS portfolio_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    data TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS broker_state (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    type TEXT NOT NULL,
                    data TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_broker_type
                    ON broker_state (type);

                CREATE TABLE IF NOT EXISTS market_breadth (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    advance INTEGER NOT NULL DEFAULT 0,
                    decline INTEGER NOT NULL DEFAULT 0,
                    ad_ratio REAL NOT NULL DEFAULT 0,
                    above20 REAL NOT NULL DEFAULT 0,
                    above50 REAL NOT NULL DEFAULT 0,
                    above200 REAL NOT NULL DEFAULT 0,
                    breadth_score REAL NOT NULL DEFAULT 0,
                    market_strength TEXT NOT NULL DEFAULT 'NEUTRAL'
                );

                CREATE INDEX IF NOT EXISTS idx_market_breadth_timestamp
                    ON market_breadth (timestamp);

                CREATE TABLE IF NOT EXISTS sector_rotation (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    nifty_7d REAL NOT NULL DEFAULT 0,
                    nifty_30d REAL NOT NULL DEFAULT 0,
                    top5_strong TEXT NOT NULL DEFAULT '[]',
                    top5_weak TEXT NOT NULL DEFAULT '[]',
                    all_sectors TEXT NOT NULL DEFAULT '[]'
                );

                CREATE INDEX IF NOT EXISTS idx_sector_rotation_timestamp
                    ON sector_rotation (timestamp);

                CREATE TABLE IF NOT EXISTS vix_risk (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    vix REAL NOT NULL DEFAULT 0,
                    volatility_score REAL NOT NULL DEFAULT 0,
                    risk_factor REAL NOT NULL DEFAULT 1.0,
                    risk_level TEXT NOT NULL DEFAULT 'UNKNOWN'
                );

                CREATE INDEX IF NOT EXISTS idx_vix_risk_timestamp
                    ON vix_risk (timestamp);

                CREATE TABLE IF NOT EXISTS fii_dii (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    date TEXT NOT NULL,
                    fii_buy REAL NOT NULL DEFAULT 0,
                    fii_sell REAL NOT NULL DEFAULT 0,
                    dii_buy REAL NOT NULL DEFAULT 0,
                    dii_sell REAL NOT NULL DEFAULT 0,
                    fii_net REAL NOT NULL DEFAULT 0,
                    dii_net REAL NOT NULL DEFAULT 0,
                    net_flow REAL NOT NULL DEFAULT 0,
                    sentiment TEXT NOT NULL DEFAULT 'NEUTRAL'
                );

                CREATE INDEX IF NOT EXISTS idx_fii_dii_timestamp
                    ON fii_dii (timestamp);
                CREATE INDEX IF NOT EXISTS idx_fii_dii_date
                    ON fii_dii (date);

                CREATE TABLE IF NOT EXISTS options_intelligence (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    spot REAL NOT NULL DEFAULT 0,
                    pcr REAL NOT NULL DEFAULT 1.0,
                    max_pain REAL NOT NULL DEFAULT 0,
                    oi_build_up REAL NOT NULL DEFAULT 0,
                    long_buildup INTEGER NOT NULL DEFAULT 0,
                    short_buildup INTEGER NOT NULL DEFAULT 0,
                    put_wall_strike REAL NOT NULL DEFAULT 0,
                    put_wall_oi REAL NOT NULL DEFAULT 0,
                    strong_oi_support INTEGER NOT NULL DEFAULT 0,
                    confidence_boost REAL NOT NULL DEFAULT 0,
                    total_call_oi REAL NOT NULL DEFAULT 0,
                    total_put_oi REAL NOT NULL DEFAULT 0
                );

                CREATE INDEX IF NOT EXISTS idx_options_intelligence_timestamp
                    ON options_intelligence (timestamp);

                CREATE TABLE IF NOT EXISTS event_calendar (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    event_date TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    impact TEXT NOT NULL DEFAULT 'HIGH',
                    description TEXT NOT NULL DEFAULT ''
                );

                CREATE INDEX IF NOT EXISTS idx_event_calendar_timestamp
                    ON event_calendar (timestamp);
                CREATE INDEX IF NOT EXISTS idx_event_calendar_date
                    ON event_calendar (event_date);

                CREATE TABLE IF NOT EXISTS global_markets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    sentiment_score REAL NOT NULL DEFAULT 50.0,
                    raw_score REAL NOT NULL DEFAULT 0.0,
                    assets TEXT NOT NULL DEFAULT '{}'
                );

                CREATE INDEX IF NOT EXISTS idx_global_markets_timestamp
                    ON global_markets (timestamp);

                CREATE TABLE IF NOT EXISTS portfolio_optimizer (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    cash REAL NOT NULL DEFAULT 0,
                    capital_used REAL NOT NULL DEFAULT 0,
                    portfolio_beta REAL NOT NULL DEFAULT 1.0,
                    portfolio_volatility REAL NOT NULL DEFAULT 0.0,
                    diversification_score REAL NOT NULL DEFAULT 0.0,
                    sector_exposure TEXT NOT NULL DEFAULT '{}',
                    industry_exposure TEXT NOT NULL DEFAULT '{}',
                    capital_limit_pct REAL NOT NULL DEFAULT 1.0,
                    max_deployable REAL NOT NULL DEFAULT 0,
                    cash_remaining REAL NOT NULL DEFAULT 0,
                    open_positions INTEGER NOT NULL DEFAULT 0
                );

                CREATE INDEX IF NOT EXISTS idx_portfolio_optimizer_timestamp
                    ON portfolio_optimizer (timestamp);

                CREATE TABLE IF NOT EXISTS correlation_matrix (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    symbols TEXT NOT NULL DEFAULT '[]',
                    matrix TEXT NOT NULL DEFAULT '{}'
                );

                CREATE INDEX IF NOT EXISTS idx_correlation_matrix_timestamp
                    ON correlation_matrix (timestamp);

                CREATE TABLE IF NOT EXISTS portfolio_metrics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    beta REAL NOT NULL DEFAULT 1.0,
                    volatility REAL NOT NULL DEFAULT 0.0,
                    diversification REAL NOT NULL DEFAULT 0.0,
                    capital_used REAL NOT NULL DEFAULT 0,
                    cash_remaining REAL NOT NULL DEFAULT 0
                );

                CREATE INDEX IF NOT EXISTS idx_portfolio_metrics_timestamp
                    ON portfolio_metrics (timestamp);

                CREATE TABLE IF NOT EXISTS allocation_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    allocated_amount REAL NOT NULL DEFAULT 0,
                    reason TEXT NOT NULL DEFAULT ''
                );

                CREATE INDEX IF NOT EXISTS idx_allocation_history_timestamp
                    ON allocation_history (timestamp);

                CREATE TABLE IF NOT EXISTS backtest_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    name TEXT NOT NULL DEFAULT '',
                    initial_capital REAL NOT NULL DEFAULT 0,
                    final_equity REAL NOT NULL DEFAULT 0,
                    total_return_pct REAL NOT NULL DEFAULT 0,
                    trades_count INTEGER NOT NULL DEFAULT 0,
                    meta_json TEXT NOT NULL DEFAULT '{}'
                );

                CREATE INDEX IF NOT EXISTS idx_backtest_runs_timestamp
                    ON backtest_runs (timestamp);

                CREATE TABLE IF NOT EXISTS backtest_results (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    backtest_run_id INTEGER,
                    timestamp TEXT NOT NULL,
                    result_json TEXT NOT NULL DEFAULT '{}',
                    FOREIGN KEY (backtest_run_id) REFERENCES backtest_runs(id)
                );

                CREATE INDEX IF NOT EXISTS idx_backtest_results_timestamp
                    ON backtest_results (timestamp);

                CREATE TABLE IF NOT EXISTS walk_forward_results (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    name TEXT NOT NULL DEFAULT '',
                    result_json TEXT NOT NULL DEFAULT '{}'
                );

                CREATE INDEX IF NOT EXISTS idx_walk_forward_results_timestamp
                    ON walk_forward_results (timestamp);

                CREATE TABLE IF NOT EXISTS monte_carlo_results (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    result_json TEXT NOT NULL DEFAULT '{}'
                );

                CREATE INDEX IF NOT EXISTS idx_monte_carlo_results_timestamp
                    ON monte_carlo_results (timestamp);
                """
            )

    # ── generic JSON serdes ───────────────────────────────────────────────

    @staticmethod
    def _dumps(obj: Any) -> str:
        def _default(o):
            if isinstance(o, datetime):
                return o.isoformat()
            if isinstance(o, date):
                return o.isoformat()
            raise TypeError(f"Object of type {type(o)} is not JSON serializable")
        return json.dumps(obj, default=_default)

    @staticmethod
    def _loads(text: str) -> Any:
        return json.loads(text) if text else {}

    # ── positions ─────────────────────────────────────────────────────────

    def save_position(self, pos: Dict[str, Any]) -> None:
        pos = dict(pos)
        now = datetime.now().isoformat(timespec="seconds")
        pos.setdefault("updated_at", now)
        pos.setdefault("created_at", now)
        with self._lock:
            with self._conn() as conn:
                existing = conn.execute(
                    "SELECT id FROM positions WHERE symbol = ? AND status = ?",
                    (pos.get("symbol"), pos.get("status", "OPEN"))
                ).fetchone()
                if existing:
                    conn.execute(
                        "UPDATE positions SET status = ?, data = ?, updated_at = ? WHERE id = ?",
                        (pos.get("status", "OPEN"), self._dumps(pos), now, existing["id"])
                    )
                else:
                    conn.execute(
                        "INSERT INTO positions (symbol, status, data, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                        (pos.get("symbol"), pos.get("status", "OPEN"), self._dumps(pos), now, now)
                    )

    def save_positions(self, positions: List[Dict[str, Any]], clear: bool = False) -> None:
        with self._lock:
            with self._conn() as conn:
                if clear:
                    conn.execute("DELETE FROM positions")
                now = datetime.now().isoformat(timespec="seconds")
                for pos in positions:
                    pos = dict(pos)
                    pos.setdefault("updated_at", now)
                    pos.setdefault("created_at", now)
                    conn.execute(
                        "INSERT INTO positions (symbol, status, data, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                        (pos.get("symbol"), pos.get("status", "OPEN"), self._dumps(pos), now, now)
                    )

    def load_positions(self, status: Optional[str] = None) -> List[Dict[str, Any]]:
        rows = []
        with self._lock:
            with self._conn() as conn:
                if status:
                    cur = conn.execute(
                        "SELECT id, data FROM positions WHERE status = ? ORDER BY id",
                        (status,)
                    )
                else:
                    cur = conn.execute("SELECT id, data FROM positions ORDER BY id")
                rows = [self._loads(r["data"]) for r in cur.fetchall()]
        return rows

    def update_position(self, symbol: str, status: str, updates: Dict[str, Any]) -> bool:
        with self._lock:
            with self._conn() as conn:
                cur = conn.execute(
                    "SELECT id, data FROM positions WHERE symbol = ? AND status = ? ORDER BY id DESC LIMIT 1",
                    (symbol, status)
                )
                row = cur.fetchone()
                if not row:
                    return False
                data = self._loads(row["data"])
                data.update(updates)
                data["updated_at"] = datetime.now().isoformat(timespec="seconds")
                conn.execute(
                    "UPDATE positions SET data = ?, updated_at = ? WHERE id = ?",
                    (self._dumps(data), data["updated_at"], row["id"])
                )
                return True

    def delete_all_positions(self) -> None:
        with self._lock:
            with self._conn() as conn:
                conn.execute("DELETE FROM positions")

    # ── trades / journal ──────────────────────────────────────────────────

    def add_trade(self, trade: Dict[str, Any]) -> None:
        trade = dict(trade)
        with self._lock:
            with self._conn() as conn:
                conn.execute(
                    """
                    INSERT INTO trades
                    (date, timestamp, symbol, action, status, data)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        trade.get("date") or datetime.now().strftime("%Y-%m-%d"),
                        trade.get("timestamp") or datetime.now().isoformat(timespec="seconds"),
                        trade.get("symbol", ""),
                        trade.get("action", ""),
                        trade.get("status", ""),
                        self._dumps(trade)
                    )
                )

    def get_trades(
        self,
        symbol: Optional[str] = None,
        action: Optional[str] = None,
        status: Optional[str] = None,
        date_from: Optional[str] = None,
        limit: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        query = "SELECT id, data FROM trades WHERE 1=1"
        params: List[Any] = []
        if symbol:
            query += " AND symbol = ?"
            params.append(symbol)
        if action:
            query += " AND action = ?"
            params.append(action)
        if status:
            query += " AND status = ?"
            params.append(status)
        if date_from:
            query += " AND date >= ?"
            params.append(date_from)
        query += " ORDER BY id"
        if limit:
            query += f" LIMIT {int(limit)}"
        with self._lock:
            with self._conn() as conn:
                cur = conn.execute(query, params)
                items = []
                for r in cur.fetchall():
                    item = self._loads(r["data"])
                    item["id"] = r["id"]
                    items.append(item)
                return items

    def all_trades(self) -> List[Dict[str, Any]]:
        return self.get_trades()

    def get_trade_by_id(self, trade_id: int) -> Optional[Dict[str, Any]]:
        with self._lock:
            with self._conn() as conn:
                row = conn.execute("SELECT data FROM trades WHERE id = ?", (trade_id,)).fetchone()
                return self._loads(row["data"]) if row else None

    def update_trade(self, trade_id: int, updates: Dict[str, Any]) -> bool:
        with self._lock:
            with self._conn() as conn:
                row = conn.execute("SELECT data FROM trades WHERE id = ?", (trade_id,)).fetchone()
                if not row:
                    return False
                data = self._loads(row["data"])
                data.update(updates)
                data["updated_at"] = datetime.now().isoformat(timespec="seconds")
                conn.execute(
                    "UPDATE trades SET data = ? WHERE id = ?",
                    (self._dumps(data), trade_id)
                )
                return True

    def update_open_buy(self, symbol: str, updates: Dict[str, Any]) -> bool:
        with self._lock:
            with self._conn() as conn:
                cur = conn.execute(
                    "SELECT id, data FROM trades WHERE symbol = ? AND action = 'BUY' AND status = 'OPEN' ORDER BY id DESC LIMIT 1",
                    (symbol,)
                )
                row = cur.fetchone()
                if not row:
                    return False
                data = self._loads(row["data"])
                data.update(updates)
                data["status"] = "CLOSED"
                conn.execute(
                    "UPDATE trades SET status = ?, data = ? WHERE id = ?",
                    ("CLOSED", self._dumps(data), row["id"])
                )
                return True

    # ── orders ────────────────────────────────────────────────────────────

    def add_order(self, order: Dict[str, Any]) -> None:
        with self._lock:
            with self._conn() as conn:
                conn.execute(
                    "INSERT INTO orders (timestamp, symbol, action, status, data) VALUES (?, ?, ?, ?, ?)",
                    (
                        order.get("timestamp") or datetime.now().isoformat(timespec="seconds"),
                        order.get("symbol", ""),
                        order.get("action", ""),
                        order.get("status", ""),
                        self._dumps(order)
                    )
                )

    def get_orders(
        self,
        symbol: Optional[str] = None,
        status: Optional[str] = None,
        limit: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        query = "SELECT id, data FROM orders WHERE 1=1"
        params: List[Any] = []
        if symbol:
            query += " AND symbol = ?"; params.append(symbol)
        if status:
            query += " AND status = ?"; params.append(status)
        query += " ORDER BY id DESC"
        if limit:
            query += f" LIMIT {int(limit)}"
        with self._lock:
            with self._conn() as conn:
                cur = conn.execute(query, params)
                items = []
                for r in cur.fetchall():
                    item = self._loads(r["data"])
                    item["id"] = r["id"]
                    items.append(item)
                return items

    def update_order_status(self, symbol: str, action: str, new_status: str) -> bool:
        with self._lock:
            with self._conn() as conn:
                cur = conn.execute(
                    "SELECT id, data FROM orders WHERE symbol = ? AND action = ? ORDER BY id DESC LIMIT 1",
                    (symbol, action)
                )
                row = cur.fetchone()
                if not row:
                    return False
                data = self._loads(row["data"])
                data["status"] = new_status
                data["updated_at"] = datetime.now().isoformat(timespec="seconds")
                conn.execute(
                    "UPDATE orders SET status = ?, data = ? WHERE id = ?",
                    (new_status, self._dumps(data), row["id"])
                )
                return True

    # ── signals ───────────────────────────────────────────────────────────

    def add_signal(self, signal: Dict[str, Any]) -> None:
        with self._lock:
            with self._conn() as conn:
                conn.execute(
                    "INSERT INTO signals (timestamp, symbol, action, data) VALUES (?, ?, ?, ?)",
                    (
                        signal.get("timestamp") or datetime.now().isoformat(timespec="seconds"),
                        signal.get("symbol", ""),
                        signal.get("action", "HOLD"),
                        self._dumps(signal)
                    )
                )

    def get_signals(
        self,
        symbol: Optional[str] = None,
        action: Optional[str] = None,
        date_from: Optional[str] = None,
        limit: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        query = "SELECT id, data FROM signals WHERE 1=1"
        params: List[Any] = []
        if symbol:
            query += " AND symbol = ?"; params.append(symbol)
        if action:
            query += " AND action = ?"; params.append(action)
        if date_from:
            query += " AND date(timestamp) >= ?"; params.append(date_from)
        query += " ORDER BY id DESC"
        if limit:
            query += f" LIMIT {int(limit)}"
        with self._lock:
            with self._conn() as conn:
                cur = conn.execute(query, params)
                items = []
                for r in cur.fetchall():
                    item = self._loads(r["data"])
                    item["id"] = r["id"]
                    items.append(item)
                return items

    # ── daily state ───────────────────────────────────────────────────────

    def save_daily_state(
        self,
        state_date: str,
        daily_pnl: float,
        daily_blacklist: List[str],
        last_exit_by_symbol: Dict[str, str],
        daily_trades: int = 0,
        pending_sells: Optional[Dict[str, Any]] = None
    ) -> None:
        now = datetime.now().isoformat(timespec="seconds")
        with self._lock:
            with self._conn() as conn:
                # Preserve existing pending_sells when risk_manager calls without it
                if pending_sells is None:
                    existing = conn.execute(
                        "SELECT pending_sells FROM daily_state WHERE date = ?", (state_date,)
                    ).fetchone()
                    pending_sells = self._loads(existing["pending_sells"]) if existing else {}
                conn.execute(
                    """
                    INSERT INTO daily_state
                    (date, daily_pnl, daily_blacklist, last_exit_by_symbol, daily_trades, pending_sells, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(date) DO UPDATE SET
                        daily_pnl = excluded.daily_pnl,
                        daily_blacklist = excluded.daily_blacklist,
                        last_exit_by_symbol = excluded.last_exit_by_symbol,
                        daily_trades = excluded.daily_trades,
                        pending_sells = excluded.pending_sells,
                        updated_at = excluded.updated_at
                    """,
                    (
                        state_date,
                        daily_pnl,
                        self._dumps(list(daily_blacklist)),
                        self._dumps({k: (v.isoformat() if isinstance(v, datetime) else str(v)) for k, v in last_exit_by_symbol.items()}),
                        daily_trades,
                        self._dumps(pending_sells or {}),
                        now
                    )
                )

    def load_daily_state(self, state_date: Optional[str] = None) -> Dict[str, Any]:
        state_date = state_date or date.today().isoformat()
        with self._lock:
            with self._conn() as conn:
                row = conn.execute(
                    "SELECT * FROM daily_state WHERE date = ?", (state_date,)
                ).fetchone()
        if not row:
            return {
                "date": state_date,
                "daily_pnl": 0.0,
                "daily_blacklist": [],
                "last_exit_by_symbol": {},
                "daily_trades": 0,
                "pending_sells": {},
                "updated_at": ""
            }
        return {
            "date": row["date"],
            "daily_pnl": row["daily_pnl"],
            "daily_blacklist": self._loads(row["daily_blacklist"]),
            "last_exit_by_symbol": self._loads(row["last_exit_by_symbol"]),
            "daily_trades": row["daily_trades"],
            "pending_sells": self._loads(row["pending_sells"]),
            "updated_at": row["updated_at"]
        }

    # ── portfolio snapshots / broker state ────────────────────────────────

    def save_portfolio_snapshot(self, snapshot: Dict[str, Any]) -> None:
        with self._lock:
            with self._conn() as conn:
                conn.execute(
                    "INSERT INTO portfolio_snapshots (timestamp, data) VALUES (?, ?)",
                    (datetime.now().isoformat(timespec="seconds"), self._dumps(snapshot))
                )

    def get_latest_portfolio_snapshot(self) -> Optional[Dict[str, Any]]:
        with self._lock:
            with self._conn() as conn:
                row = conn.execute(
                    "SELECT data FROM portfolio_snapshots ORDER BY id DESC LIMIT 1"
                ).fetchone()
                return self._loads(row["data"]) if row else None

    def save_broker_state(self, type_: str, data: Dict[str, Any]) -> None:
        with self._lock:
            with self._conn() as conn:
                conn.execute(
                    "INSERT INTO broker_state (timestamp, type, data) VALUES (?, ?, ?)",
                    (datetime.now().isoformat(timespec="seconds"), type_, self._dumps(data))
                )

    def get_broker_state(self, type_: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            with self._conn() as conn:
                row = conn.execute(
                    "SELECT data FROM broker_state WHERE type = ? ORDER BY id DESC LIMIT 1",
                    (type_,)
                ).fetchone()
                return self._loads(row["data"]) if row else None

    # ── migration from legacy JSON ────────────────────────────────────────

    def migrate_from_json(
        self,
        positions_path: Optional[str] = None,
        journal_path: Optional[str] = None
    ) -> Dict[str, int]:
        """One-time import from old data/positions.json and data/trade_journal.json."""
        positions_path = positions_path or os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "data", "positions.json"
        )
        journal_path = journal_path or os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "data", "trade_journal.json"
        )

        counts = {"positions": 0, "trades": 0}

        if os.path.exists(positions_path):
            try:
                with open(positions_path) as f:
                    data = json.load(f)
                if data.get("date") == date.today().isoformat():
                    positions = data.get("positions", [])
                    # add status and dates back where missing
                    for p in positions:
                        p["status"] = p.get("status", "OPEN")
                        p.setdefault("created_at", p.get("entry_time"))
                        p.setdefault("updated_at", datetime.now().isoformat())
                    if positions:
                        self.save_positions(positions, clear=True)
                        counts["positions"] = len(positions)
                        logger.info(f"Migrated {len(positions)} positions from JSON")
                else:
                    logger.info("positions.json not from today — skipping migration")
            except Exception as e:
                logger.error(f"Could not migrate positions: {e}")

        if os.path.exists(journal_path):
            try:
                with open(journal_path) as f:
                    entries = json.load(f)
                for e in entries:
                    e.setdefault("date", e.get("timestamp", "")[:10])
                    e.setdefault("status", "OPEN" if e.get("action") == "BUY" and not e.get("exit_price") else e.get("status", "CLOSED"))
                    self.add_trade(e)
                counts["trades"] = len(entries)
                logger.info(f"Migrated {len(entries)} journal trades from JSON")
            except Exception as e:
                logger.error(f"Could not migrate journal: {e}")

        return counts


    # ── market breadth ────────────────────────────────────────────────────

    def save_market_breadth(self, snapshot: Dict[str, Any]) -> None:
        """Persist a market breadth snapshot."""
        with self._lock:
            with self._conn() as conn:
                conn.execute(
                    """
                    INSERT INTO market_breadth
                    (timestamp, advance, decline, ad_ratio, above20, above50, above200, breadth_score, market_strength)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        snapshot.get("timestamp") or datetime.now().isoformat(),
                        int(snapshot.get("advance", 0)),
                        int(snapshot.get("decline", 0)),
                        float(snapshot.get("ad_ratio", 0.0)),
                        float(snapshot.get("above20", 0.0)),
                        float(snapshot.get("above50", 0.0)),
                        float(snapshot.get("above200", 0.0)),
                        float(snapshot.get("breadth_score", 0.0)),
                        str(snapshot.get("market_strength", "NEUTRAL")),
                    ),
                )

    def get_latest_market_breadth(self) -> Optional[Dict[str, Any]]:
        """Return the most recent market breadth snapshot."""
        with self._lock:
            with self._conn() as conn:
                row = conn.execute(
                    """
                    SELECT timestamp, advance, decline, ad_ratio, above20, above50, above200, breadth_score, market_strength
                    FROM market_breadth ORDER BY timestamp DESC LIMIT 1
                    """
                ).fetchone()
                if not row:
                    return None
                return {
                    "timestamp": row["timestamp"],
                    "advance": row["advance"],
                    "decline": row["decline"],
                    "ad_ratio": row["ad_ratio"],
                    "above20": row["above20"],
                    "above50": row["above50"],
                    "above200": row["above200"],
                    "breadth_score": row["breadth_score"],
                    "market_strength": row["market_strength"],
                }

    # ── sector rotation ───────────────────────────────────────────────────

    def save_sector_rotation(self, snapshot: Dict[str, Any]) -> None:
        """Persist a sector rotation snapshot."""
        with self._lock:
            with self._conn() as conn:
                conn.execute(
                    """
                    INSERT INTO sector_rotation
                    (timestamp, nifty_7d, nifty_30d, top5_strong, top5_weak, all_sectors)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        snapshot.get("timestamp") or datetime.now().isoformat(),
                        float(snapshot.get("nifty_7d", 0.0)),
                        float(snapshot.get("nifty_30d", 0.0)),
                        self._dumps(snapshot.get("top5_strong", [])),
                        self._dumps(snapshot.get("top5_weak", [])),
                        self._dumps(snapshot.get("all_sectors", [])),
                    ),
                )

    def get_latest_sector_rotation(self) -> Optional[Dict[str, Any]]:
        """Return the most recent sector rotation snapshot."""
        with self._lock:
            with self._conn() as conn:
                row = conn.execute(
                    """
                    SELECT timestamp, nifty_7d, nifty_30d, top5_strong, top5_weak, all_sectors
                    FROM sector_rotation ORDER BY timestamp DESC LIMIT 1
                    """
                ).fetchone()
                if not row:
                    return None
                return {
                    "timestamp": row["timestamp"],
                    "nifty_7d": row["nifty_7d"],
                    "nifty_30d": row["nifty_30d"],
                    "top5_strong": self._loads(row["top5_strong"]),
                    "top5_weak": self._loads(row["top5_weak"]),
                    "all_sectors": self._loads(row["all_sectors"]),
                }

    # ── vix risk ──────────────────────────────────────────────────────────

    def save_vix_risk(self, snapshot: Dict[str, Any]) -> None:
        """Persist a VIX risk snapshot."""
        with self._lock:
            with self._conn() as conn:
                conn.execute(
                    """
                    INSERT INTO vix_risk
                    (timestamp, vix, volatility_score, risk_factor, risk_level)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        snapshot.get("timestamp") or datetime.now().isoformat(),
                        float(snapshot.get("vix", 0.0)),
                        float(snapshot.get("volatility_score", 0.0)),
                        float(snapshot.get("risk_factor", 1.0)),
                        str(snapshot.get("risk_level", "UNKNOWN")),
                    ),
                )

    def get_latest_vix_risk(self) -> Optional[Dict[str, Any]]:
        """Return the most recent VIX risk snapshot."""
        with self._lock:
            with self._conn() as conn:
                row = conn.execute(
                    """
                    SELECT timestamp, vix, volatility_score, risk_factor, risk_level
                    FROM vix_risk ORDER BY timestamp DESC LIMIT 1
                    """
                ).fetchone()
                if not row:
                    return None
                return {
                    "timestamp": row["timestamp"],
                    "vix": row["vix"],
                    "volatility_score": row["volatility_score"],
                    "risk_factor": row["risk_factor"],
                    "risk_level": row["risk_level"],
                }

    # ── fii/dii flow ──────────────────────────────────────────────────────

    def save_fii_dii(self, snapshot: Dict[str, Any]) -> None:
        """Persist a FII/DII flow snapshot."""
        with self._lock:
            with self._conn() as conn:
                conn.execute(
                    """
                    INSERT INTO fii_dii
                    (timestamp, date, fii_buy, fii_sell, dii_buy, dii_sell,
                     fii_net, dii_net, net_flow, sentiment)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        snapshot.get("timestamp") or datetime.now().isoformat(),
                        snapshot.get("date") or datetime.now().strftime("%Y-%m-%d"),
                        float(snapshot.get("fii_buy", 0.0)),
                        float(snapshot.get("fii_sell", 0.0)),
                        float(snapshot.get("dii_buy", 0.0)),
                        float(snapshot.get("dii_sell", 0.0)),
                        float(snapshot.get("fii_net", 0.0)),
                        float(snapshot.get("dii_net", 0.0)),
                        float(snapshot.get("net_flow", 0.0)),
                        str(snapshot.get("sentiment", "NEUTRAL")),
                    ),
                )

    def get_latest_fii_dii(self) -> Optional[Dict[str, Any]]:
        """Return the most recent FII/DII flow snapshot."""
        with self._lock:
            with self._conn() as conn:
                row = conn.execute(
                    """
                    SELECT timestamp, date, fii_buy, fii_sell, dii_buy, dii_sell,
                           fii_net, dii_net, net_flow, sentiment
                    FROM fii_dii ORDER BY timestamp DESC LIMIT 1
                    """
                ).fetchone()
                if not row:
                    return None
                return {
                    "timestamp": row["timestamp"],
                    "date": row["date"],
                    "fii_buy": row["fii_buy"],
                    "fii_sell": row["fii_sell"],
                    "dii_buy": row["dii_buy"],
                    "dii_sell": row["dii_sell"],
                    "fii_net": row["fii_net"],
                    "dii_net": row["dii_net"],
                    "net_flow": row["net_flow"],
                    "sentiment": row["sentiment"],
                }

    # ── options intelligence ─────────────────────────────────────────────

    def save_options_intelligence(self, snapshot: Dict[str, Any]) -> None:
        """Persist an options intelligence snapshot."""
        with self._lock:
            with self._conn() as conn:
                conn.execute(
                    """
                    INSERT INTO options_intelligence
                    (timestamp, symbol, spot, pcr, max_pain, oi_build_up,
                     long_buildup, short_buildup, put_wall_strike, put_wall_oi,
                     strong_oi_support, confidence_boost, total_call_oi, total_put_oi)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        snapshot.get("timestamp") or datetime.now().isoformat(),
                        str(snapshot.get("symbol", "")),
                        float(snapshot.get("spot", 0.0)),
                        float(snapshot.get("pcr", 1.0)),
                        float(snapshot.get("max_pain", 0.0)),
                        float(snapshot.get("oi_build_up", 0.0)),
                        1 if snapshot.get("long_buildup", False) else 0,
                        1 if snapshot.get("short_buildup", False) else 0,
                        float(snapshot.get("put_wall_strike", 0.0)),
                        float(snapshot.get("put_wall_oi", 0.0)),
                        1 if snapshot.get("strong_oi_support", False) else 0,
                        float(snapshot.get("confidence_boost", 0.0)),
                        float(snapshot.get("total_call_oi", 0.0)),
                        float(snapshot.get("total_put_oi", 0.0)),
                    ),
                )

    def get_latest_options_intelligence(self) -> Optional[Dict[str, Any]]:
        """Return the most recent options intelligence snapshot."""
        with self._lock:
            with self._conn() as conn:
                row = conn.execute(
                    """
                    SELECT timestamp, symbol, spot, pcr, max_pain, oi_build_up,
                           long_buildup, short_buildup, put_wall_strike, put_wall_oi,
                           strong_oi_support, confidence_boost, total_call_oi, total_put_oi
                    FROM options_intelligence ORDER BY timestamp DESC LIMIT 1
                    """
                ).fetchone()
                if not row:
                    return None
                return {
                    "timestamp": row["timestamp"],
                    "symbol": row["symbol"],
                    "spot": row["spot"],
                    "pcr": row["pcr"],
                    "max_pain": row["max_pain"],
                    "oi_build_up": row["oi_build_up"],
                    "long_buildup": bool(row["long_buildup"]),
                    "short_buildup": bool(row["short_buildup"]),
                    "put_wall_strike": row["put_wall_strike"],
                    "put_wall_oi": row["put_wall_oi"],
                    "strong_oi_support": bool(row["strong_oi_support"]),
                    "confidence_boost": row["confidence_boost"],
                    "total_call_oi": row["total_call_oi"],
                    "total_put_oi": row["total_put_oi"],
                }

    # ── economic event calendar ──────────────────────────────────────────

    def save_economic_event(self, event: Dict[str, Any]) -> None:
        """Persist an economic event."""
        with self._lock:
            with self._conn() as conn:
                conn.execute(
                    """
                    INSERT INTO event_calendar
                    (timestamp, event_date, event_type, impact, description)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        event.get("timestamp") or datetime.now().isoformat(),
                        event.get("event_date") or datetime.now().strftime("%Y-%m-%d"),
                        str(event.get("event_type", "")),
                        str(event.get("impact", "HIGH")),
                        str(event.get("description", "")),
                    ),
                )

    def get_upcoming_economic_events(self, after_timestamp: str) -> List[Dict[str, Any]]:
        """Return high-impact events scheduled after the given timestamp."""
        with self._lock:
            with self._conn() as conn:
                cur = conn.execute(
                    """
                    SELECT timestamp, event_date, event_type, impact, description
                    FROM event_calendar
                    WHERE timestamp >= ?
                    ORDER BY timestamp ASC
                    """,
                    (after_timestamp,),
                )
                rows = []
                for r in cur.fetchall():
                    rows.append({
                        "timestamp": r["timestamp"],
                        "event_date": r["event_date"],
                        "event_type": r["event_type"],
                        "impact": r["impact"],
                        "description": r["description"],
                    })
                return rows

    # ── global markets ────────────────────────────────────────────────────

    def save_global_markets(self, snapshot: Dict[str, Any]) -> None:
        """Persist a global market snapshot."""
        with self._lock:
            with self._conn() as conn:
                conn.execute(
                    """
                    INSERT INTO global_markets
                    (timestamp, sentiment_score, raw_score, assets)
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        snapshot.get("timestamp") or datetime.now().isoformat(),
                        float(snapshot.get("sentiment_score", 50.0)),
                        float(snapshot.get("raw_score", 0.0)),
                        self._dumps(snapshot.get("assets", {})),
                    ),
                )

    def get_latest_global_markets(self) -> Optional[Dict[str, Any]]:
        """Return the most recent global market snapshot."""
        with self._lock:
            with self._conn() as conn:
                row = conn.execute(
                    """
                    SELECT timestamp, sentiment_score, raw_score, assets
                    FROM global_markets ORDER BY timestamp DESC LIMIT 1
                    """
                ).fetchone()
                if not row:
                    return None
                return {
                    "timestamp": row["timestamp"],
                    "sentiment_score": row["sentiment_score"],
                    "raw_score": row["raw_score"],
                    "assets": self._loads(row["assets"]),
                }

    # ── portfolio optimizer ───────────────────────────────────────────────

    def save_portfolio_optimizer(self, snapshot: Dict[str, Any]) -> None:
        """Persist a portfolio optimizer snapshot."""
        with self._lock:
            with self._conn() as conn:
                conn.execute(
                    """
                    INSERT INTO portfolio_optimizer
                    (timestamp, cash, capital_used, portfolio_beta, portfolio_volatility,
                    diversification_score, sector_exposure, industry_exposure,
                    capital_limit_pct, max_deployable, cash_remaining, open_positions)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        snapshot.get("timestamp") or datetime.now().isoformat(),
                        float(snapshot.get("cash", 0)),
                        float(snapshot.get("capital_used", 0)),
                        float(snapshot.get("portfolio_beta", 1.0)),
                        float(snapshot.get("portfolio_volatility", 0)),
                        float(snapshot.get("diversification_score", 0)),
                        self._dumps(snapshot.get("sector_exposure", {})),
                        self._dumps(snapshot.get("industry_exposure", {})),
                        float(snapshot.get("capital_limit_pct", 1.0)),
                        float(snapshot.get("max_deployable", 0)),
                        float(snapshot.get("cash_remaining", 0)),
                        int(snapshot.get("open_positions", 0)),
                    ),
                )

    def get_latest_portfolio_optimizer(self) -> Optional[Dict[str, Any]]:
        """Return the most recent portfolio optimizer snapshot."""
        with self._lock:
            with self._conn() as conn:
                row = conn.execute(
                    """
                    SELECT * FROM portfolio_optimizer ORDER BY timestamp DESC LIMIT 1
                    """
                ).fetchone()
                if not row:
                    return None
                return {
                    "timestamp": row["timestamp"],
                    "cash": row["cash"],
                    "capital_used": row["capital_used"],
                    "portfolio_beta": row["portfolio_beta"],
                    "portfolio_volatility": row["portfolio_volatility"],
                    "diversification_score": row["diversification_score"],
                    "sector_exposure": self._loads(row["sector_exposure"]),
                    "industry_exposure": self._loads(row["industry_exposure"]),
                    "capital_limit_pct": row["capital_limit_pct"],
                    "max_deployable": row["max_deployable"],
                    "cash_remaining": row["cash_remaining"],
                    "open_positions": row["open_positions"],
                }

    def save_correlation_matrix(self, snapshot: Dict[str, Any]) -> None:
        with self._lock:
            with self._conn() as conn:
                conn.execute(
                    """
                    INSERT INTO correlation_matrix
                    (timestamp, symbols, matrix)
                    VALUES (?, ?, ?)
                    """,
                    (
                        snapshot.get("timestamp") or datetime.now().isoformat(),
                        self._dumps(snapshot.get("symbols", [])),
                        self._dumps(snapshot.get("matrix", {})),
                    ),
                )

    def get_latest_correlation_matrix(self) -> Optional[Dict[str, Any]]:
        with self._lock:
            with self._conn() as conn:
                row = conn.execute(
                    """
                    SELECT timestamp, symbols, matrix
                    FROM correlation_matrix ORDER BY timestamp DESC LIMIT 1
                    """
                ).fetchone()
                if not row:
                    return None
                return {
                    "timestamp": row["timestamp"],
                    "symbols": self._loads(row["symbols"]),
                    "matrix": self._loads(row["matrix"]),
                }

    def save_portfolio_metrics(self, snapshot: Dict[str, Any]) -> None:
        with self._lock:
            with self._conn() as conn:
                conn.execute(
                    """
                    INSERT INTO portfolio_metrics
                    (timestamp, beta, volatility, diversification, capital_used, cash_remaining)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        snapshot.get("timestamp") or datetime.now().isoformat(),
                        float(snapshot.get("beta", 1.0)),
                        float(snapshot.get("volatility", 0)),
                        float(snapshot.get("diversification", 0)),
                        float(snapshot.get("capital_used", 0)),
                        float(snapshot.get("cash_remaining", 0)),
                    ),
                )

    def get_latest_portfolio_metrics(self) -> Optional[Dict[str, Any]]:
        with self._lock:
            with self._conn() as conn:
                row = conn.execute(
                    """
                    SELECT timestamp, beta, volatility, diversification,
                        capital_used, cash_remaining
                    FROM portfolio_metrics ORDER BY timestamp DESC LIMIT 1
                    """
                ).fetchone()
                if not row:
                    return None
                return {
                    "timestamp": row["timestamp"],
                    "beta": row["beta"],
                    "volatility": row["volatility"],
                    "diversification": row["diversification"],
                    "capital_used": row["capital_used"],
                    "cash_remaining": row["cash_remaining"],
                }

    def save_allocation_history(self, snapshot: Dict[str, Any]) -> None:
        with self._lock:
            with self._conn() as conn:
                conn.execute(
                    """
                    INSERT INTO allocation_history
                    (timestamp, symbol, allocated_amount, reason)
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        snapshot.get("timestamp") or datetime.now().isoformat(),
                        snapshot.get("symbol", ""),
                        float(snapshot.get("allocated_amount", 0)),
                        snapshot.get("reason", ""),
                    ),
                )

    def get_allocation_history(self, limit: int = 50) -> List[Dict[str, Any]]:
        with self._lock:
            with self._conn() as conn:
                cur = conn.execute(
                    """
                    SELECT timestamp, symbol, allocated_amount, reason
                    FROM allocation_history ORDER BY timestamp DESC LIMIT ?
                    """,
                    (limit,),
                )
                return [
                    {
                        "timestamp": r["timestamp"],
                        "symbol": r["symbol"],
                        "allocated_amount": r["allocated_amount"],
                        "reason": r["reason"],
                    }
                    for r in cur.fetchall()
                ]

    # ── backtesting ───────────────────────────────────────────────────────

    def save_backtest_run(self, snapshot: Dict[str, Any]) -> int:
        with self._lock:
            with self._conn() as conn:
                cur = conn.execute(
                    """
                    INSERT INTO backtest_runs
                    (timestamp, name, initial_capital, final_equity, total_return_pct, trades_count, meta_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        snapshot.get("timestamp") or datetime.now().isoformat(),
                        snapshot.get("name", ""),
                        float(snapshot.get("initial_capital", 0)),
                        float(snapshot.get("final_equity", 0)),
                        float(snapshot.get("total_return_pct", 0)),
                        int(snapshot.get("trades_count", 0)),
                        snapshot.get("meta_json", "{}"),
                    ),
                )
                return cur.lastrowid

    def get_latest_backtest_run(self) -> Optional[Dict[str, Any]]:
        with self._lock:
            with self._conn() as conn:
                row = conn.execute(
                    """
                    SELECT id, timestamp, name, initial_capital, final_equity, total_return_pct, trades_count, meta_json
                    FROM backtest_runs ORDER BY timestamp DESC LIMIT 1
                    """
                ).fetchone()
                if not row:
                    return None
                return {
                    "id": row["id"],
                    "timestamp": row["timestamp"],
                    "name": row["name"],
                    "initial_capital": row["initial_capital"],
                    "final_equity": row["final_equity"],
                    "total_return_pct": row["total_return_pct"],
                    "trades_count": row["trades_count"],
                    "meta_json": row["meta_json"],
                }

    def save_backtest_results(self, snapshot: Dict[str, Any]) -> int:
        with self._lock:
            with self._conn() as conn:
                cur = conn.execute(
                    """
                    INSERT INTO backtest_results
                    (backtest_run_id, timestamp, result_json)
                    VALUES (?, ?, ?)
                    """,
                    (
                        snapshot.get("backtest_run_id"),
                        snapshot.get("timestamp") or datetime.now().isoformat(),
                        snapshot.get("result_json", "{}"),
                    ),
                )
                return cur.lastrowid

    def get_latest_backtest_results(self) -> Optional[Dict[str, Any]]:
        with self._lock:
            with self._conn() as conn:
                row = conn.execute(
                    """
                    SELECT r.id, r.timestamp, r.result_json, b.name
                    FROM backtest_results r
                    LEFT JOIN backtest_runs b ON b.id = r.backtest_run_id
                    ORDER BY r.timestamp DESC LIMIT 1
                    """
                ).fetchone()
                if not row:
                    return None
                return {
                    "id": row["id"],
                    "timestamp": row["timestamp"],
                    "name": row["name"],
                    "result_json": row["result_json"],
                }

    def save_walk_forward_results(self, snapshot: Dict[str, Any]) -> None:
        with self._lock:
            with self._conn() as conn:
                conn.execute(
                    """
                    INSERT INTO walk_forward_results
                    (timestamp, name, result_json)
                    VALUES (?, ?, ?)
                    """,
                    (
                        snapshot.get("timestamp") or datetime.now().isoformat(),
                        snapshot.get("name", ""),
                        snapshot.get("result_json", "{}"),
                    ),
                )

    def get_latest_walk_forward_results(self, limit: int = 10) -> List[Dict[str, Any]]:
        with self._lock:
            with self._conn() as conn:
                cur = conn.execute(
                    """
                    SELECT timestamp, name, result_json
                    FROM walk_forward_results ORDER BY timestamp DESC LIMIT ?
                    """,
                    (limit,),
                )
                return [
                    {
                        "timestamp": r["timestamp"],
                        "name": r["name"],
                        "result_json": r["result_json"],
                    }
                    for r in cur.fetchall()
                ]

    def save_monte_carlo_results(self, snapshot: Dict[str, Any]) -> None:
        with self._lock:
            with self._conn() as conn:
                conn.execute(
                    """
                    INSERT INTO monte_carlo_results
                    (timestamp, result_json)
                    VALUES (?, ?)
                    """,
                    (
                        snapshot.get("timestamp") or datetime.now().isoformat(),
                        snapshot.get("result_json", "{}"),
                    ),
                )

    def get_latest_monte_carlo_results(self) -> Optional[Dict[str, Any]]:
        with self._lock:
            with self._conn() as conn:
                row = conn.execute(
                    """
                    SELECT timestamp, result_json
                    FROM monte_carlo_results ORDER BY timestamp DESC LIMIT 1
                    """
                ).fetchone()
                if not row:
                    return None
                return {"timestamp": row["timestamp"], "result_json": row["result_json"]}


# Singleton instance for the process
the_store: Optional[TradingStore] = None


def get_store(db_path: Optional[str] = None) -> TradingStore:
    global the_store
    if the_store is None:
        the_store = TradingStore(db_path)
    return the_store
