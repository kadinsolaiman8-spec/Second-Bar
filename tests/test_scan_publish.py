"""Tests for scan publish pipeline: bundle shape and SSE broadcast."""

from __future__ import annotations

import asyncio
import json

from backend import state
from backend.realtime import broadcast_scan_event, register_sse_subscriber
from backend.scan_bundle import build_latest_scan_bundle
from backend.scan_publish import finalize_scan_publish_async
from scanner_core import scanner as scanner_module


def _patch_sqlite(tmp_path, monkeypatch) -> None:
    db_file = tmp_path / "app_state.sqlite3"
    monkeypatch.setattr("backend.persistence._sqlite_path", lambda: db_file)


def test_finalize_scan_publish_broadcasts_sse(tmp_path, monkeypatch) -> None:
    _patch_sqlite(tmp_path, monkeypatch)
    state.set_scan_result(
        [
            {
                "ticker": "AAPL",
                "direction": "BUY",
                "score": 80,
                "strategy": "ORB Breakout",
                "entry_price": 190.0,
                "stop": 188.0,
                "tp1": 194.0,
            }
        ],
        ts_iso="2026-05-19T14:00:00+00:00",
        error=None,
    )
    scanner_module.ticker_state.clear()
    scanner_module.ticker_state["AAPL"] = {
        "ticker": "AAPL",
        "score": 80,
        "direction": "BUY",
        "strategy": "ORB Breakout",
        "entry_price": 190.0,
        "stop": 188.0,
        "tp1": 194.0,
        "change_pct": 1.5,
    }

    async def _run() -> str:
        queue = await register_sse_subscriber(max_queue_size=4)
        await finalize_scan_publish_async()
        return queue.get_nowait()

    raw = asyncio.run(_run())
    envelope = json.loads(raw)
    assert envelope["type"] == "scan"
    data = envelope["data"]
    assert isinstance(data.get("signals"), list)
    assert data.get("last_scan_at") == "2026-05-19T14:00:00+00:00"
    assert "suggested_watchlist" in data
    assert "best" in data
    assert "market_state" in data


def test_build_latest_scan_bundle_shape(monkeypatch) -> None:
    state.set_scan_result([], ts_iso=None, error=None)
    scanner_module.ticker_state.clear()
    scanner_module.ticker_state["MSFT"] = {
        "ticker": "MSFT",
        "score": 72,
        "direction": "BUY",
        "strategy": "VWAP Momentum",
        "entry_price": 420.0,
        "stop": 415.0,
        "tp1": 430.0,
        "change_pct": 2.1,
    }

    bundle = build_latest_scan_bundle()
    for key in (
        "signals",
        "last_scan_at",
        "last_error",
        "best",
        "movers",
        "suggested_watchlist",
        "market_state",
        "breadth",
    ):
        assert key in bundle
    assert isinstance(bundle["suggested_watchlist"], list)
    tickers = {row.get("ticker") for row in bundle["suggested_watchlist"] if isinstance(row, dict)}
    assert "MSFT" in tickers


def test_trailing_stop_pct_snapshot_levels() -> None:
    from quant.signals import _compute_stop_tp_levels
    from scanner_core.dynamic_stops import snapshot_pct_trailing_stop

    stop_px = snapshot_pct_trailing_stop(100.0, "BUY", 5.0)
    assert stop_px == 95.0

    config = {"backtest": {"trailing_stop_pct": 5, "trailing_stop_atr_multiplier": 0}}
    stop_price, take_profit, stop_pct = _compute_stop_tp_levels("Buy", 100.0, None, 0.0, config)
    assert stop_price == 95.0
    assert stop_pct == -5
    assert take_profit is None


def test_broadcast_scan_event_serializes_envelope() -> None:
    async def _run() -> str:
        queue = await register_sse_subscriber(max_queue_size=2)
        await broadcast_scan_event({"type": "ping", "data": {"ok": True}})
        return queue.get_nowait()

    line = asyncio.run(_run())
    assert json.loads(line)["type"] == "ping"
