# Cross-codebase notes for agents

This workspace is **one** Python product: FastAPI backend + `scanner_core` + `quant`. Discord transports were removed; `_archive/` holds old bots read-only.

## Scanner ↔ quant boundaries

| Concern | `scanner_core` | `quant` |
|---------|----------------|---------|
| Purpose | Short-horizon scan signals (dict payloads, playbook tags) | Research-grade backtests, walk-forward, bootstrap / DSR tooling |
| Indicators | `pandas-ta` heavy pipeline | `ta` / numpy pipeline per archived DTB |
| Config | [`scanner_core/config.py`](../scanner_core/config.py) constants | [`config.yaml`](../config.yaml) + [`quant/config_resolver.py`](../quant/config_resolver.py) |

**Do not** assume signal shapes align: DTB uses structured `Signal` objects internally; scanner emits nested dicts. Bridge only in explicit adapters at HTTP/service boundaries.

## Environment variables

See [`.env.example`](../.env.example). Optional: `FINNHUB_API_KEY`, `SUPABASE_*`, `ALPHA_VANTAGE_API_KEY`, `POLYGON_API_KEY`. No Discord tokens.

## When touching validation math

Run `pytest tests/` after edits under [`quant/`](../quant/) affecting [`quant/backtest.py`](../quant/backtest.py), [`quant/walk_forward.py`](../quant/walk_forward.py), or [`quant/signals.py`](../quant/signals.py).

## When touching scans

Prefer adjusting thresholds in [`scanner_core/config.py`](../scanner_core/config.py); respect batching and sleeps already coded in [`scanner_core/scanner.py`](../scanner_core/scanner.py).
