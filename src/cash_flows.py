"""Confirmed external cash-flow ledger.

Kite's API exposes no deposit/withdrawal history, so external cash flows
are recorded explicitly here (manual entry, or a Console ledger import).
Only entries with confirmed=True adjust the circuit-breaker equity
comparison — a real trading loss can never be reclassified as a
withdrawal, and missing/malformed records leave the raw equity check
unchanged.
"""
import json
import logging
import os
import tempfile
import threading
from datetime import datetime
from typing import Any, Dict, List, Optional

import pytz

from config import data_path

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_IST = pytz.timezone('Asia/Kolkata')

VALID_TYPES = ('deposit', 'withdrawal')


def _path() -> str:
    return data_path('cash_flows.json')


def _today() -> str:
    return datetime.now(_IST).strftime('%Y-%m-%d')


def _load() -> List[Dict[str, Any]]:
    try:
        with open(_path()) as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save(flows: List[Dict[str, Any]]) -> None:
    path = _path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix='.tmp')
    try:
        with os.fdopen(fd, 'w') as f:
            json.dump(flows, f, indent=2)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def record_flow(
    flow_type: str,
    amount: float,
    source: str = 'manual',
    confirmed: bool = True,
    note: str = '',
    date_str: Optional[str] = None,
    flow_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Append a confirmed external cash-flow record. Returns the entry.

    `flow_id` is optional idempotency: if a record with the same id already
    exists, the existing entry is returned and nothing is double-counted.
    """
    if flow_type not in VALID_TYPES:
        raise ValueError(f"flow_type must be one of {VALID_TYPES}")
    amount = float(amount)
    if amount <= 0:
        raise ValueError("amount must be positive")
    entry = {
        'date': date_str or _today(),
        'time': datetime.now(_IST).isoformat(),
        'type': flow_type,
        'amount': amount,
        'source': source,
        'confirmed': bool(confirmed),
        'note': note,
        'flow_id': flow_id,
    }
    with _lock:
        flows = _load()
        if flow_id is not None:
            for existing in flows:
                if existing.get('flow_id') == flow_id:
                    logger.info(f"Cash flow {flow_id} already recorded — skipping duplicate")
                    return existing
        flows.append(entry)
        _save(flows)
    logger.info(f"Cash flow recorded: {flow_type} ₹{amount:,.2f} ({source})")
    return entry


def flows_on(date_str: Optional[str] = None) -> List[Dict[str, Any]]:
    """Confirmed flows for a date (default: today, IST)."""
    day = date_str or _today()
    return [
        f for f in _load()
        if f.get('date') == day and f.get('confirmed', False)
    ]


def net_outflow_on(date_str: Optional[str] = None) -> float:
    """Net confirmed cash leaving the account on a date.

    Positive = net withdrawal (equity reduced by external flow);
    negative = net deposit. Add this to current equity to obtain the
    cash-flow-adjusted equity comparable to the day's peak baseline.
    """
    total = 0.0
    for f in flows_on(date_str):
        amt = float(f.get('amount') or 0)
        total += amt if f.get('type') == 'withdrawal' else -amt
    return total
