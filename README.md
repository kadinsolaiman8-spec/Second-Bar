# Unified trading stack (web UI + scanner + DTB validation)

Website-first Python app: **no Discord**. The dashboard under [`site/`](site/) is served by **FastAPI** together with JSON APIs under `/api/*`.

This tree merges **scanner logic** derived from the former Discord alert bot (`scanner_core/`) with **quantitative validation** vendored from the archived Trading-Bot project (`quant/`, [`docs/`](docs/), [`tests/`](tests/)).

## Expectations (research honesty)

Rigorous walk-forward and bootstrap testing on the original DTB strategies did **not** find a durable edge versus buy-and-hold for those designs—see [`docs/STRATEGY_AND_EDGE.md`](docs/STRATEGY_AND_EDGE.md) and the upstream README narrative. Treat this codebase as **methodology + tooling**, not a profit guarantee.

## Quick start

```powershell
cd c:\Users\kadin\Downloads\tradingbotv3
pip install -r requirements.txt
copy .env.example .env
# optional: FINNHUB_API_KEY, ALPHA_VANTAGE_API_KEY, POLYGON_API_KEY, SUPABASE_*

python -m uvicorn backend.app:app --host 127.0.0.1 --port 8000
```

Open **http://127.0.0.1:8000/** — same origin serves static UI and APIs (`/api/health`, `/api/scan/latest`, `POST /api/scan/run`, `POST /api/quant/backtest`, `/api/quant/tutorial`).

Or run **[`run_web.ps1`](run_web.ps1)** from the repo folder (starts the same server).

### Browser shows error **-102** or “connection refused”

That means nothing answered on port **8000**. Start the server **first**, keep that terminal open, then reload the page. If you closed the terminal or never ran `uvicorn`, the site will not load.

### Optional checks

```powershell
curl.exe http://127.0.0.1:8000/api/health
```

Expect JSON with `"status":"ok"`. If embedded previews inside some tools fail but `curl` works, use Chrome or Edge pointing at `http://127.0.0.1:8000/`.

Heavy walk-forward batches (multi-day runs): [`run_wfo_batch.ps1`](run_wfo_batch.ps1) → `python -m quant.run_wfo ...`.

## Layout

| Path | Role |
|------|------|
| [`backend/app.py`](backend/app.py) | FastAPI app, static mount, REST routes |
| [`scanner_core/`](scanner_core/) | Scan pipeline, regimes, indicators (no Discord transport) |
| [`quant/`](quant/) | Backtest / WFO / stats / data helpers |
| [`site/`](site/) | Dashboard HTML/CSS/JS |
| [`scripts/`](scripts/) | Pool validation, multiple-testing helpers |
| [`tests/`](tests/) | `pytest` |

## Legacy snapshots

Original Discord-era trees were moved to [`_archive/`](_archive/) after this merge for reference only.
