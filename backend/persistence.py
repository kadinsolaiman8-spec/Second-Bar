"""SQLite persistence for last scan snapshot and paper journal."""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path

from backend import state

logger = logging.getLogger(__name__)

KV_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS kv (
    k TEXT PRIMARY KEY,
    v TEXT NOT NULL
)
"""

SCAN_KEY = "last_scan"
TICKER_STATE_KEY = "ticker_state"
JOURNAL_KEY = "journal_state"
DAILY_RESET_KEY = "last_daily_reset_iso"
MAX_PERSISTED_TICKER_ROWS = 50


def _repository_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _sqlite_path() -> Path:
    data_directory = _repository_root() / "data"
    data_directory.mkdir(parents=True, exist_ok=True)
    return data_directory / "app_state.sqlite3"


def init_db() -> None:
    database_file = _sqlite_path()
    connection = sqlite3.connect(str(database_file))
    connection.execute(KV_TABLE_SQL)
    connection.commit()
    connection.close()


def save_scan_snapshot(
    signals: list[dict],
    last_scan_at: str | None,
    last_error: str | None,
) -> None:
    blob = json.dumps(
        {"signals": signals, "last_scan_at": last_scan_at, "last_error": last_error},
        default=str,
    )
    database_file = _sqlite_path()
    connection = sqlite3.connect(str(database_file))
    connection.execute(KV_TABLE_SQL)
    connection.execute(
        "INSERT OR REPLACE INTO kv (k, v) VALUES (?, ?)", (SCAN_KEY, blob)
    )
    connection.commit()
    connection.close()


def load_scan_snapshot() -> bool:
    database_file = _sqlite_path()
    if not database_file.exists():
        return False
    connection = sqlite3.connect(str(database_file))
    try:
        cursor = connection.execute("SELECT v FROM kv WHERE k = ?", (SCAN_KEY,))
        row = cursor.fetchone()
    finally:
        connection.close()
    if not row:
        return False
    try:
        payload = json.loads(row[0])
    except json.JSONDecodeError:
        logger.warning("Persisted scan JSON invalid — skipping restore")
        return False
    signals_raw = payload.get("signals") or []
    if not isinstance(signals_raw, list):
        signals_raw = []
    signals_clean = [state.json_safe(signal_row) for signal_row in signals_raw]
    ts_raw = payload.get("last_scan_at")
    ts_iso: str | None = str(ts_raw) if ts_raw else None
    err_raw = payload.get("last_error")
    error_str: str | None = str(err_raw) if err_raw else None
    state.set_scan_result(signals_clean, ts_iso=ts_iso, error=error_str)
    return True


def save_ticker_state(ticker_state_blob: dict[str, object]) -> None:
    """Persist top rows of scanner ticker_state for cold-start suggested trades."""
    if not isinstance(ticker_state_blob, dict):
        return

    def _row_score(pair: tuple[str, object]) -> int:
        _, row = pair
        if isinstance(row, dict):
            try:
                return int(row.get("score") or 0)
            except (TypeError, ValueError):
                return 0
        return 0

    pairs = [(str(k), v) for k, v in ticker_state_blob.items() if isinstance(v, dict)]
    pairs.sort(key=_row_score, reverse=True)
    capped: dict[str, object] = dict(pairs[:MAX_PERSISTED_TICKER_ROWS])
    blob = json.dumps(state.json_safe(capped), default=str)
    database_file = _sqlite_path()
    connection = sqlite3.connect(str(database_file))
    connection.execute(KV_TABLE_SQL)
    connection.execute(
        "INSERT OR REPLACE INTO kv (k, v) VALUES (?, ?)", (TICKER_STATE_KEY, blob)
    )
    connection.commit()
    connection.close()


def load_ticker_state() -> dict[str, dict]:
    database_file = _sqlite_path()
    if not database_file.exists():
        return {}
    connection = sqlite3.connect(str(database_file))
    try:
        cursor = connection.execute("SELECT v FROM kv WHERE k = ?", (TICKER_STATE_KEY,))
        row = cursor.fetchone()
    finally:
        connection.close()
    if not row:
        return {}
    try:
        payload = json.loads(row[0])
    except json.JSONDecodeError:
        logger.warning("Persisted ticker_state JSON invalid — skipping restore")
        return {}
    if not isinstance(payload, dict):
        return {}
    out: dict[str, dict] = {}
    for key_raw, val in payload.items():
        if isinstance(val, dict):
            out[str(key_raw)] = state.json_safe(val)
    return out


def save_journal_snapshot() -> None:
    from scanner_core import journal as journal_mod

    blob = json.dumps(journal_mod.dump_for_persistence(), default=str)
    database_file = _sqlite_path()
    connection = sqlite3.connect(str(database_file))
    connection.execute(KV_TABLE_SQL)
    connection.execute(
        "INSERT OR REPLACE INTO kv (k, v) VALUES (?, ?)", (JOURNAL_KEY, blob)
    )
    connection.commit()
    connection.close()


def read_kv(key: str) -> str | None:
    database_file = _sqlite_path()
    if not database_file.exists():
        return None
    connection = sqlite3.connect(str(database_file))
    try:
        cursor = connection.execute("SELECT v FROM kv WHERE k = ?", (key,))
        row = cursor.fetchone()
    finally:
        connection.close()
    return row[0] if row else None


def write_kv(key: str, value: str) -> None:
    database_file = _sqlite_path()
    connection = sqlite3.connect(str(database_file))
    connection.execute(KV_TABLE_SQL)
    connection.execute(
        "INSERT OR REPLACE INTO kv (k, v) VALUES (?, ?)", (key, value)
    )
    connection.commit()
    connection.close()


def load_journal_snapshot() -> bool:
    from scanner_core import journal as journal_mod

    database_file = _sqlite_path()
    if not database_file.exists():
        return False
    connection = sqlite3.connect(str(database_file))
    try:
        cursor = connection.execute("SELECT v FROM kv WHERE k = ?", (JOURNAL_KEY,))
        row = cursor.fetchone()
    finally:
        connection.close()
    if not row:
        return False
    try:
        payload = json.loads(row[0])
    except json.JSONDecodeError:
        logger.warning("Persisted journal JSON invalid — skipping restore")
        return False
    if not isinstance(payload, dict):
        return False
    journal_mod.load_from_persistence(payload)
    return True
