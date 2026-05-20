"""Assemble enriched JSON payloads for `/api/scan/latest` and SSE broadcasts."""

from __future__ import annotations

from backend import state
from backend.assistant_narrative import enrich_ticker_row_with_assistant
from scanner_core import scanner as scanner_module
from scanner_core.market_state import get_current_market_state, get_state_label

ASSISTANT_ENRICH_LIMIT = 8
_SSE_HISTORY_LIMIT = 10

_cached_bundle: dict[str, object] | None = None
_cached_bundle_scan_at: str | None = None
_cached_sse_bundle: dict[str, object] | None = None


def invalidate_scan_bundle_cache() -> None:
    global _cached_bundle, _cached_bundle_scan_at, _cached_sse_bundle
    _cached_bundle = None
    _cached_bundle_scan_at = None
    _cached_sse_bundle = None


def _signal_index(signals: list) -> dict[str, dict]:
    index: dict[str, dict] = {}
    for sig in signals:
        if not isinstance(sig, dict) or not sig.get("ticker"):
            continue
        ticker = str(sig["ticker"]).upper().strip()
        index[ticker] = sig
    return index


def _merge_row_with_signal_and_cache(row: dict, signal_index: dict[str, dict]) -> dict:
    """Attach score_data, conditions, and levels from latest signal or ticker cache."""
    if not isinstance(row, dict) or not row.get("ticker"):
        return row
    merged = dict(row)
    ticker = str(row["ticker"]).upper().strip()
    sig = signal_index.get(ticker)
    if sig:
        for key in (
            "score_data",
            "conditions_met",
            "conditions_failed",
            "entry_price",
            "stop",
            "tp1",
            "tp2",
            "rr",
            "strategy",
            "direction",
        ):
            if key in sig and sig[key] is not None:
                merged[key] = sig[key]
    cached = scanner_module.get_ticker_state(ticker)
    if cached:
        if not merged.get("score_data") and cached.get("score_data"):
            merged["score_data"] = cached["score_data"]
        for key in ("entry_price", "stop", "tp1", "direction", "strategy"):
            if merged.get(key) in (None, "", 0) and cached.get(key) not in (None, ""):
                merged[key] = cached[key]
        if not merged.get("conditions_met") and cached.get("conditions_met"):
            merged["conditions_met"] = cached["conditions_met"]
        if not merged.get("conditions_failed") and cached.get("conditions_failed"):
            merged["conditions_failed"] = cached["conditions_failed"]
    return merged


def enrich_ticker_detail_state(
    ticker_state: dict,
    symbol: str,
    signals: list,
    market_state: dict,
) -> dict:
    """Merge signal/cache fields and attach ``assistant`` for ticker detail API."""
    row = dict(ticker_state)
    row["ticker"] = symbol.upper().strip()
    merged = _merge_row_with_signal_and_cache(row, _signal_index(signals))
    return enrich_ticker_row_with_assistant(merged, market_state)


def _enrich_rows(
    rows: list,
    market_state: dict,
    signal_index: dict[str, dict],
    *,
    assistant_limit: int = ASSISTANT_ENRICH_LIMIT,
) -> list:
    enriched: list = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            enriched.append(row)
            continue
        merged = _merge_row_with_signal_and_cache(row, signal_index)
        if index < assistant_limit:
            enriched.append(enrich_ticker_row_with_assistant(merged, market_state))
        else:
            enriched.append(merged)
    return enriched


def _suggested_watchlist_entries(best_rows: list, mover_rows: list) -> list:
    """Home ghost rows after first scan: score traction and/or noticeable session move."""
    merged: dict[str, dict] = {}
    for row in best_rows:
        if isinstance(row, dict) and row.get("ticker"):
            ticker = str(row["ticker"]).upper().strip()
            merged[ticker] = dict(row)
            merged[ticker]["ticker"] = ticker
    for row in mover_rows:
        if not isinstance(row, dict) or not row.get("ticker"):
            continue
        ticker = str(row["ticker"]).upper().strip()
        existing = merged.get(ticker)
        if existing:
            existing["score"] = max(int(existing.get("score") or 0), int(row.get("score") or 0))
            try:
                if abs(float(row.get("change_pct") or 0)) > abs(float(existing.get("change_pct") or 0)):
                    existing["change_pct"] = row.get("change_pct", existing.get("change_pct"))
            except (TypeError, ValueError):
                pass
        else:
            merged[ticker] = {
                "ticker": ticker,
                "score": int(row.get("score") or 0),
                "direction": row.get("direction", "NEUTRAL"),
                "strategy": row.get("strategy") or "No Active Setup",
                "change_pct": row.get("change_pct", 0),
                "price": row.get("price", 0),
            }

    filtered: list[dict] = []
    for row in merged.values():
        try:
            score_v = int(row.get("score") or 0)
        except (TypeError, ValueError):
            score_v = 0
        try:
            change_v = abs(float(row.get("change_pct") or 0))
        except (TypeError, ValueError):
            change_v = 0.0
        if score_v <= 0 and change_v < 0.015:
            continue
        filtered.append(row)
    filtered.sort(
        key=lambda x: (
            int(x.get("score") or 0),
            abs(float(x.get("change_pct") or 0)),
        ),
        reverse=True,
    )
    return filtered[:8]


def _sse_lightweight_bundle(full_bundle: dict[str, object]) -> dict[str, object]:
    """Smaller SSE payload: omit bulky optional sections and trim history."""
    lite = dict(full_bundle)
    for key in ("squeeze", "orb_breakouts", "sectors"):
        lite.pop(key, None)
    history = lite.get("scan_history")
    if isinstance(history, list) and len(history) > _SSE_HISTORY_LIMIT:
        lite["scan_history"] = history[-_SSE_HISTORY_LIMIT:]
    return lite


def _assemble_scan_bundle(*, for_sse: bool) -> dict[str, object]:
    snapshot_blob = state.snapshot()
    scanner_history = getattr(scanner_module, "scan_history", [])
    history_limit = _SSE_HISTORY_LIMIT if for_sse else 25
    history_slice = scanner_history[-history_limit:] if isinstance(scanner_history, list) else []
    regime_blob = scanner_module.get_current_regime()
    market_state_identifier = get_current_market_state()
    market_state_blob = {
        "code": market_state_identifier,
        "label": get_state_label(market_state_identifier),
    }
    signals = snapshot_blob.get("signals") or []
    signal_index = _signal_index(signals if isinstance(signals, list) else [])
    best_candidates = scanner_module.get_best_stocks(
        min_score=0, limit=12, rescore_zeros=False
    )
    movers_for_ui = scanner_module.get_movers(24)
    movers_out = movers_for_ui[:12]
    best_enriched = _enrich_rows(best_candidates[:8], market_state_blob, signal_index)
    suggested_raw = _suggested_watchlist_entries(best_candidates, movers_for_ui)
    suggested_enriched = _enrich_rows(suggested_raw, market_state_blob, signal_index)
    bundle: dict[str, object] = {
        **snapshot_blob,
        "scan_history": history_slice,
        "regime": regime_blob,
        "circuit_breaker_active": scanner_module.is_circuit_breaker_active(),
        "circuit_resume_time": scanner_module.get_circuit_breaker_resume_time(),
        "breadth": scanner_module.get_market_breadth(),
        "best": best_enriched,
        "movers": movers_out,
        "stats": scanner_module.get_scan_stats(),
        "suggested_watchlist": suggested_enriched,
        "market_state": market_state_blob,
    }
    if not for_sse:
        bundle["sectors"] = scanner_module.get_sector_rotation()
        bundle["squeeze"] = scanner_module.get_squeeze_stocks()
        bundle["orb_breakouts"] = scanner_module.get_orb_breakouts()
    return state.json_safe(bundle)


def build_latest_scan_bundle(*, force: bool = False, for_sse: bool = False) -> dict[str, object]:
    snapshot_blob = state.snapshot()
    scan_at = snapshot_blob.get("last_scan_at")
    global _cached_bundle, _cached_bundle_scan_at, _cached_sse_bundle

    if not force and _cached_bundle is not None and _cached_bundle_scan_at == scan_at:
        if for_sse and _cached_sse_bundle is not None:
            return _cached_sse_bundle
        if not for_sse:
            return _cached_bundle

    if force or _cached_bundle is None or _cached_bundle_scan_at != scan_at:
        full_bundle = _assemble_scan_bundle(for_sse=False)
        _cached_bundle = full_bundle
        _cached_bundle_scan_at = scan_at
        _cached_sse_bundle = _sse_lightweight_bundle(full_bundle)

    if for_sse:
        return _cached_sse_bundle or _cached_bundle or {}
    return _cached_bundle or {}
