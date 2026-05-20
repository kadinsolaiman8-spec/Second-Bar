"""Shared mutable state for latest scan results (in-process)."""

from __future__ import annotations

import asyncio
import math
from typing import Any

scan_lock = asyncio.Lock()

_latest_signals: list[dict[str, Any]] = []
_last_scan_at: str | None = None
_last_scan_error: str | None = None

_SIGNAL_KEYS_OMIT_FROM_BROADCAST = frozenset({"indicators"})


def slim_signal_for_broadcast(signal: dict[str, Any]) -> dict[str, Any]:
    """Drop bulky fields from signals stored for SSE, snapshots, and SQLite."""
    if not isinstance(signal, dict):
        return signal
    return {k: v for k, v in signal.items() if k not in _SIGNAL_KEYS_OMIT_FROM_BROADCAST}


def json_safe(obj: Any, *, _seen: set[int] | None = None) -> Any:
    """Convert numpy/pandas-ish values for JSON serialization."""
    if _seen is None:
        _seen = set()
    obj_id = id(obj)
    if obj_id in _seen and isinstance(obj, (dict, list, tuple)):
        return obj
    if obj is None:
        return None
    if isinstance(obj, dict):
        _seen.add(obj_id)
        return {str(k): json_safe(v, _seen=_seen) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        _seen.add(obj_id)
        return [json_safe(v, _seen=_seen) for v in obj]
    if isinstance(obj, bool):
        return obj
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, int):
        return obj
    if isinstance(obj, str):
        return obj
    try:
        import numpy as np

        if isinstance(obj, np.generic):
            return json_safe(obj.item())
    except ImportError:
        pass
    return str(obj)


def set_scan_result(
    signals: list[dict[str, Any]],
    *,
    ts_iso: str | None = None,
    error: str | None = None,
) -> None:
    global _latest_signals, _last_scan_at, _last_scan_error
    _latest_signals = [
        json_safe(slim_signal_for_broadcast(s)) for s in signals if isinstance(s, dict)
    ]
    _last_scan_at = ts_iso if ts_iso else None
    _last_scan_error = error


def set_scan_error(*, ts_iso: str | None = None, error: str | None = None) -> None:
    """Update scan timestamps/error without clearing cached signals (failed scan path)."""
    global _last_scan_at, _last_scan_error
    _last_scan_at = ts_iso if ts_iso else None
    _last_scan_error = error


def snapshot() -> dict[str, Any]:
    return {
        "signals": list(_latest_signals),
        "last_scan_at": _last_scan_at,
        "last_error": _last_scan_error,
    }
