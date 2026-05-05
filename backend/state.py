"""Shared mutable state for latest scan results (in-process)."""

from __future__ import annotations

import asyncio
import math
from typing import Any

scan_lock = asyncio.Lock()

_latest_signals: list[dict[str, Any]] = []
_last_scan_at: str | None = None
_last_scan_error: str | None = None


def json_safe(obj: Any) -> Any:
    """Convert numpy/pandas-ish values for JSON serialization."""
    if obj is None:
        return None
    if isinstance(obj, dict):
        return {str(k): json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [json_safe(v) for v in obj]
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


def set_scan_result(signals: list[dict[str, Any]], *, ts_iso: str, error: str | None = None) -> None:
    global _latest_signals, _last_scan_at, _last_scan_error
    _latest_signals = [json_safe(s) for s in signals]
    _last_scan_at = ts_iso
    _last_scan_error = error


def snapshot() -> dict[str, Any]:
    return {
        "signals": list(_latest_signals),
        "last_scan_at": _last_scan_at,
        "last_error": _last_scan_error,
    }
