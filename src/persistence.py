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


# Singleton instance for the process
the_store: Optional[TradingStore] = None


def get_store(db_path: Optional[str] = None) -> TradingStore:
    global the_store
    if the_store is None:
        the_store = TradingStore(db_path)
    return the_store
