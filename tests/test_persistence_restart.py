"""Cold-start persistence: scan snapshot and suggested watchlist restore."""

from __future__ import annotations

from backend import state
from backend.persistence import (
    init_db,
    load_scan_snapshot,
    load_ticker_state,
    save_scan_snapshot,
    save_ticker_state,
)
from backend.scan_bundle import build_latest_scan_bundle
from scanner_core import scanner as scanner_module


def _patch_sqlite(tmp_path, monkeypatch) -> None:
    db_file = tmp_path / "app_state.sqlite3"
    monkeypatch.setattr("backend.persistence._sqlite_path", lambda: db_file)


def test_sqlite_reload_restores_scan_and_suggested_watchlist(tmp_path, monkeypatch) -> None:
    _patch_sqlite(tmp_path, monkeypatch)
    init_db()

    signals = [
        {
            "ticker": "NVDA",
            "direction": "BUY",
            "score": 88,
            "strategy": "ORB Breakout",
            "entry_price": 900.0,
            "stop": 890.0,
            "tp1": 920.0,
        }
    ]
    save_scan_snapshot(signals, "2026-05-19T15:30:00+00:00", None)

    ticker_blob = {
        "NVDA": {
            "ticker": "NVDA",
            "score": 88,
            "direction": "BUY",
            "strategy": "ORB Breakout",
            "entry_price": 900.0,
            "stop": 890.0,
            "tp1": 920.0,
            "change_pct": 2.5,
        },
        "AMD": {
            "ticker": "AMD",
            "score": 65,
            "direction": "BUY",
            "strategy": "EMA Pullback",
            "entry_price": 160.0,
            "stop": 157.0,
            "tp1": 165.0,
            "change_pct": 1.2,
        },
    }
    save_ticker_state(ticker_blob)

    state.set_scan_result([], ts_iso=None, error=None)
    scanner_module.ticker_state.clear()

    assert load_scan_snapshot() is True
    restored_tickers = load_ticker_state()
    assert "NVDA" in restored_tickers
    assert restored_tickers["NVDA"]["score"] == 88

    scanner_module.ticker_state.update(restored_tickers)
    snap = state.snapshot()
    assert snap["last_scan_at"] == "2026-05-19T15:30:00+00:00"
    assert any(s.get("ticker") == "NVDA" for s in snap["signals"])

    bundle = build_latest_scan_bundle()
    suggested = bundle.get("suggested_watchlist") or []
    assert isinstance(suggested, list)
    symbols = {row.get("ticker") for row in suggested if isinstance(row, dict)}
    assert "NVDA" in symbols
