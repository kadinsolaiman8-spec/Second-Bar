# Scanner core (`scanner_core/`)

**Path:** [`scanner_core/`](../scanner_core/)  
**Nature:** Pure Python scanning pipeline used by the FastAPI backend (`POST /api/scan/run`, scheduled jobs). Legacy Discord bot sources live only under [`_archive/testkadin-main/`](../_archive/testkadin-main/).

## Runtime (via backend)

See root [README.md](../README.md): `python -m uvicorn backend.app:app ...`

## Stack highlights

- **yfinance**, **pandas**, **numpy**, **pandas-ta**
- **APScheduler** wired from [`backend/scheduler.py`](../backend/scheduler.py)
- Optional **Finnhub** key (`FINNHUB_API_KEY`) via [`scanner_core/config.py`](../scanner_core/config.py)

## Module map

| File | Responsibility |
|------|----------------|
| [`scanner_core/scanner.py`](../scanner_core/scanner.py) | Parallel scans, circuit breaker, ticker state |
| [`scanner_core/strategies.py`](../scanner_core/strategies.py) | Playbook detectors |
| [`scanner_core/scoring.py`](../scanner_core/scoring.py) | Confidence scoring |
| [`scanner_core/indicators.py`](../scanner_core/indicators.py) | TA assembly |
| [`scanner_core/regime.py`](../scanner_core/regime.py), [`scanner_core/market_state.py`](../scanner_core/market_state.py) | Gating |
| [`scanner_core/watchlist.py`](../scanner_core/watchlist.py) | Universe lists |

## Dashboard

[`site/`](../site/) consumes `/api/scan/latest` and `/api/health` — keep JSON shapes backward-compatible when adjusting scanner outputs.
