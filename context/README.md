# Workspace context for agents

Read this folder before changing runtime behavior in this repo.

## Canonical layout (website-first, no Discord)

| Area | Path | Notes |
|------|------|--------|
| Root README | [README.md](../README.md) | Run commands, expectations |
| HTTP API + static UI | [backend/app.py](../backend/app.py) | FastAPI serves `/api/*` and mounts [`site/`](../site/) |
| Scanner pipeline | [`scanner_core/`](../scanner_core/) | Async scans, regimes, indicators |
| Quant / validation | [`quant/`](../quant/) | Backtest, walk-forward, stats ([`docs/`](../docs/) authoritative) |
| Tests | [`tests/`](../tests/) | `pytest` |

## Supporting docs in this folder

| Document | Purpose |
|----------|---------|
| [trading-bot-dtb.md](trading-bot-dtb.md) | `quant/` package — methodology, config resolver, WFO references |
| [discord-alert-bot-v3.md](discord-alert-bot-v3.md) | `scanner_core/` origins (Discord-era snapshot lives under `_archive/` only) |
| [cross-codebase-notes.md](cross-codebase-notes.md) | Integration boundaries scanner ↔ quant |

## Legacy snapshots

Pre-merge copies of the Discord bots are preserved under [`_archive/`](../_archive/) for archaeology only — **do not** run or extend those trees as the product path.
