"""Persist latest scan snapshot and notify SSE subscribers after each scan completes."""

from __future__ import annotations

from backend import state
from backend.persistence import save_scan_snapshot, save_ticker_state
from backend.realtime import broadcast_scan_event
from backend.scan_bundle import build_latest_scan_bundle
from scanner_core import scanner as scanner_module


async def finalize_scan_publish_async() -> None:
    snapshot_blob = state.snapshot()
    save_scan_snapshot(
        snapshot_blob["signals"],
        snapshot_blob.get("last_scan_at"),
        snapshot_blob.get("last_error"),
    )
    save_ticker_state(scanner_module.ticker_state)
    envelope = {"type": "scan", "data": build_latest_scan_bundle()}
    await broadcast_scan_event(envelope)
