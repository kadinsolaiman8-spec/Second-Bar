"""Assemble enriched JSON payloads for `/api/scan/latest` and SSE broadcasts."""

from __future__ import annotations

from backend import state
from scanner_core import scanner as scanner_module
from scanner_core.market_state import get_current_market_state, get_state_label


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


def build_latest_scan_bundle() -> dict[str, object]:
    snapshot_blob = state.snapshot()
    scanner_history = getattr(scanner_module, "scan_history", [])
    history_slice = scanner_history[-25:] if isinstance(scanner_history, list) else []
    regime_blob = scanner_module.get_current_regime()
    market_state_identifier = get_current_market_state()
    best_candidates = scanner_module.get_best_stocks(min_score=0, limit=12)
    movers_for_ui = scanner_module.get_movers(24)
    movers_out = movers_for_ui[:12]
    return {
        **snapshot_blob,
        "scan_history": state.json_safe(history_slice),
        "regime": state.json_safe(regime_blob),
        "circuit_breaker_active": scanner_module.is_circuit_breaker_active(),
        "circuit_resume_time": scanner_module.get_circuit_breaker_resume_time(),
        "breadth": state.json_safe(scanner_module.get_market_breadth()),
        "best": state.json_safe(best_candidates[:8]),
        "movers": state.json_safe(movers_out),
        "sectors": state.json_safe(scanner_module.get_sector_rotation()),
        "stats": state.json_safe(scanner_module.get_scan_stats()),
        "suggested_watchlist": state.json_safe(
            _suggested_watchlist_entries(best_candidates, movers_for_ui)
        ),
        "squeeze": state.json_safe(scanner_module.get_squeeze_stocks()),
        "orb_breakouts": state.json_safe(scanner_module.get_orb_breakouts()),
        "market_state": state.json_safe(
            {
                "code": market_state_identifier,
                "label": get_state_label(market_state_identifier),
            }
        ),
    }
