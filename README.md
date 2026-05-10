# Trading dashboard and research stack

Python application with a **FastAPI** server that serves a web dashboard ([`site/`](site/)) and JSON APIs under `/api/*`.

**`scanner_core/`** runs the market scan pipeline (regimes, indicators, scoring). **`quant/`** holds backtesting, walk-forward optimization, statistics, and data helpers. **`backend/app.py`** wires routes, static files, and API endpoints. **`tests/`** contains `pytest` suites; **`scripts/`** has pool validation and multiple-testing utilities.

## Quick start

```powershell
cd c:\Users\__\___\___
pip install -r requirements.txt
copy .env.example .env
# optional: FINNHUB_API_KEY, ALPHA_VANTAGE_API_KEY, POLYGON_API_KEY, SUPABASE_*

python -m uvicorn backend.app:app --host 127.0.0.1 --port 8000
```

Open **http://127.0.0.1:8000/** — same origin serves the UI and APIs (`/api/health`, `/api/scan/latest`, `POST /api/scan/run`, `POST /api/quant/backtest`, `/api/quant/tutorial`).

Or run **[`run_web.ps1`](run_web.ps1)** from the repo folder (starts the same server).

### Browser shows error **-102** or “connection refused”

Nothing is listening on port **8000**. Start the server **first**, keep that terminal open, then reload the page.

### Optional checks

```powershell
curl.exe http://127.0.0.1:8000/api/health
```

Expect JSON with `"status":"ok"`. If embedded previews inside some tools fail but `curl` works, use Chrome or Edge at `http://127.0.0.1:8000/`.

Heavy walk-forward batches (multi-day runs): [`run_wfo_batch.ps1`](run_wfo_batch.ps1) → `python -m quant.run_wfo ...`.

## Layout

| Path | Role |
|------|------|
| [`backend/app.py`](backend/app.py) | FastAPI app, static mount, REST routes |
| [`scanner_core/`](scanner_core/) | Scan pipeline, regimes, indicators |
| [`quant/`](quant/) | Backtest / WFO / stats / data helpers |
| [`site/`](site/) | Dashboard HTML/CSS/JS |
| [`scripts/`](scripts/) | Pool validation, multiple-testing helpers |
| [`tests/`](tests/) | `pytest` |

Strategy methodology and edge notes live in [`docs/STRATEGY_AND_EDGE.md`](docs/STRATEGY_AND_EDGE.md).
