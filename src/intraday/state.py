"""
Intraday State Persistence Module

File-backed state for the Intraday engine, stored under data/intraday/.
Completely separate namespace from Swing Trading state/database.
"""
import json
import logging
import os
import threading
from datetime import datetime, date
from typing import Any, Dict, List, Optional

from .models import (
    IntradayPosition, PositionStatus, SignalDirection, SignalClassification
)

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()


def default_state_dir() -> str:
    """Default state directory: <project>/data/intraday/"""
    return os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "data", "intraday"
    )


def _serialize_position(pos: IntradayPosition) -> Dict[str, Any]:
    return {
        'id': pos.id,
        'symbol': pos.symbol,
        'direction': pos.direction.value,
        'entry_price': pos.entry_price,
        'quantity': pos.quantity,
        'stop_loss': pos.stop_loss,
        'target': pos.target,
        'entry_time': pos.entry_time.isoformat() if isinstance(pos.entry_time, datetime) else pos.entry_time,
        'current_price': pos.current_price,
        'unrealized_pnl': pos.unrealized_pnl,
        'unrealized_pnl_pct': pos.unrealized_pnl_pct,
        'status': pos.status.value if hasattr(pos.status, 'value') else str(pos.status),
        'exit_price': pos.exit_price,
        'exit_time': pos.exit_time.isoformat() if isinstance(pos.exit_time, datetime) else pos.exit_time,
        'exit_reason': pos.exit_reason,
        'realized_pnl': pos.realized_pnl,
        'signal_score': pos.signal_score,
        'signal_classification': pos.signal_classification.value if hasattr(pos.signal_classification, 'value') else str(pos.signal_classification),
        'strategy': pos.strategy,
        'is_paper': pos.is_paper,
    }


def _deserialize_position(data: Dict[str, Any]) -> IntradayPosition:
    """Rebuild an IntradayPosition from a serialized dict."""
    def _dt(value):
        if value is None or isinstance(value, datetime):
            return value
        try:
            return datetime.fromisoformat(value)
        except Exception:
            return None

    return IntradayPosition(
        id=data['id'],
        symbol=data['symbol'],
        direction=SignalDirection(data['direction']),
        entry_price=float(data['entry_price']),
        quantity=int(data['quantity']),
        stop_loss=float(data['stop_loss']),
        target=float(data['target']),
        entry_time=_dt(data.get('entry_time')) or datetime.now(),
        current_price=float(data.get('current_price', data['entry_price'])),
        unrealized_pnl=float(data.get('unrealized_pnl', 0.0)),
        unrealized_pnl_pct=float(data.get('unrealized_pnl_pct', 0.0)),
        status=PositionStatus(data.get('status', 'OPEN')),
        exit_price=data.get('exit_price'),
        exit_time=_dt(data.get('exit_time')),
        exit_reason=data.get('exit_reason'),
        realized_pnl=float(data.get('realized_pnl', 0.0)),
        signal_score=float(data.get('signal_score', 0.0)),
        signal_classification=SignalClassification(data.get('signal_classification', 'NO_TRADE')),
        strategy=data.get('strategy', 'UNKNOWN'),
        is_paper=bool(data.get('is_paper', True)),
    )


class IntradayStateStore:
    """
    Small JSON file store for intraday engine state.

    Files (all under state_dir):
        daily_state.json    - daily risk counters (resets each trading day)
        positions.json      - open paper positions
        engine_status.json  - kill switch, market data status, timestamps
    """

    def __init__(self, state_dir: Optional[str] = None):
        self.state_dir = state_dir or default_state_dir()
        self._daily_path = os.path.join(self.state_dir, "daily_state.json")
        self._positions_path = os.path.join(self.state_dir, "positions.json")
        self._status_path = os.path.join(self.state_dir, "engine_status.json")

    # ── low-level IO ────────────────────────────────────────────────
    def _read_json(self, path: str) -> Optional[Any]:
        try:
            if not os.path.exists(path):
                return None
            with open(path, 'r') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"[INTRADAY STATE] Read error {path}: {e}")
            return None

    def _write_json(self, path: str, payload: Any):
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            tmp = f"{path}.tmp"
            with open(tmp, 'w') as f:
                json.dump(payload, f, indent=2)
            os.replace(tmp, path)
        except Exception as e:
            logger.error(f"[INTRADAY STATE] Write error {path}: {e}")

    # ── daily risk state ────────────────────────────────────────────
    def load_daily_state(self) -> Optional[Dict[str, Any]]:
        """Load daily risk state if it belongs to today, else None."""
        data = self._read_json(self._daily_path)
        if not data:
            return None
        if data.get('date') != date.today().isoformat():
            return None
        return data

    def save_daily_state(self, state: Dict[str, Any]):
        state = dict(state)
        state['date'] = date.today().isoformat()
        with _LOCK:
            self._write_json(self._daily_path, state)

    # ── open positions ──────────────────────────────────────────────
    def load_positions(self) -> List[IntradayPosition]:
        raw = self._read_json(self._positions_path) or []
        positions = []
        for item in raw:
            try:
                positions.append(_deserialize_position(item))
            except Exception as e:
                logger.error(f"[INTRADAY STATE] Position decode error: {e}")
        return positions

    def save_positions(self, positions: List[IntradayPosition]):
        with _LOCK:
            self._write_json(
                self._positions_path,
                [_serialize_position(p) for p in positions]
            )

    # ── engine status (kill switch, market data status, opportunities) ──
    def load_engine_status(self) -> Dict[str, Any]:
        return self._read_json(self._status_path) or {}

    def save_engine_status(self, status: Dict[str, Any]):
        with _LOCK:
            self._write_json(self._status_path, status)
