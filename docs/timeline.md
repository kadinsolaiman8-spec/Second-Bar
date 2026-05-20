# Product timeline

Roadmap for the **single trading assistant**: one process you run on your computer or server; open it in the browser; the same program serves the page and the API. Shipping path is **web-first**—legacy bot trees under `_archive/` and similar folders are reference only. Architecture notes: [`context/README.md`](../context/README.md), root [`README.md`](../README.md).

---

## North star

| Lane | Role |
|------|------|
| **Scanner** | While markets matter for *your* setup, watch your stock list, apply technical and rules-based logic, and surface **day-trading-style setups**: what looked interesting, **why**, and **what to do** (levels and checks—not personalized investment advice). Over time, the product should **adapt presentation** to your knowledge and skill (density of detail, coaching vs raw data). |
| **Quant / research** | Same codebase, different job: backtest, walk-forward, statistics—“**did this idea hold up historically?**” Use outputs to inform **longer horizons**, risk framing, and how much confidence to place in ideas—not the same cadence as “what’s live right now.” |

Both lanes stay in **one** repo and **one** runtime; they are two sides of the assistant, not separate apps.

---

## Baseline (today)

- **Shipping stack**: FastAPI + static dashboard + `scanner_core` scans (manual + scheduled) + `quant` backtest/WFO and related docs. GitHub Actions runs `python -m pytest tests/ -v` on push/PR.
- **Scanner assistant (Phase A + B shipped)**: Server-built `assistant` payloads on scan rows and ticker detail (`backend/assistant_narrative.py`). Home feed, suggested trades, and stock detail render **Headline → Why → Checklist → Levels** via `site/modules/narrative.js`. Experience level toggle (`beginner` | `standard` | `advanced`) persists in localStorage (`site/modules/skill-mode.js`). Beginner coaching tips load from `site/data/coaching.json` (`site/modules/coaching.js`).
- **Phase 2 v2 — frontend integration (shipped)**: Closes remaining assistant UX gaps without backend changes. **Client assistant lookup** unifies home feed and watchlist playbooks (prefer row `assistant`, else match top picks / suggested list by symbol, else a single client-built playbook from fields already on the row). **Context panels** on Home surface opening-range breakouts, squeeze names, and sector leaders from the latest scan bundle. **Quant bridge**: “Check history” prefills Backtest from scanner symbols; in-app tutorial panel from the existing tutorial API; backtest results add a trade sparkline and a scan cross-line when the symbol matches the latest snapshot. **Deferred:** per-row `assistant` on every `signals[]` entry from the server—only if client lookup is not enough in practice.
- **Session clock & ops trust (Phase A + D slice)**: `GET /api/market/session` drives the Home clock (NYSE calendar; simplified Mon–Fri fallback with “Using simplified hours” when the API is unavailable). When the backend is down, Home shows a **Sample data—start the server** banner instead of silent mock rows. Tests cover scan publish/SSE, SQLite persistence restart, market session API, and trailing-stop level exposure.
- **Edge & validation (Phase C UX bridge)**: Whether setups are *economically* reliable remains a research outcome—not a shipped toggle—see [`STRATEGY_AND_EDGE.md`](STRATEGY_AND_EDGE.md), [`WFO_COMMANDS.md`](WFO_COMMANDS.md), [`wfo_batches/`](wfo_batches/). The Backtest tab includes walk-forward **“What this means for you”** copy via `site/modules/research-bridge.js` (dynamic import from `site/app.js`) and a bundled **example** export at `data/wfo-results/example-summary.json` so the view is not empty on first open.

### Freshness & session (trader-facing)

Use this wording in product surfaces—not internal module names.

- **Market clock:** Home shows whether the **regular session** is open, closed, or not yet open today, plus a countdown to the next open or close. If calendar data is unavailable, the clock falls back to simplified weekday hours and says so.
- **Latest snapshot:** Scanner rows reflect the **most recent completed scan**, with a timestamp when the service is connected. Treat alerts as a snapshot of that run—not a live quote feed—until a new scan finishes.
- **When new trade alerts pause:** The scanner does not emit new **trade-style alerts** outside regular session hours. While the service is connected, automatic scans may still run on a schedule; you can also run a manual scan from Home. If the scanner service is not running, you will see **sample data**—start the app locally before relying on rows.
- **Temporary alert pause:** After a streak of losing outcomes, the scanner may **pause new alerts for a short window** (circuit breaker). System health shows whether that pause is active and when it is expected to lift.
- **Staying current:** Keep the dashboard open while you work; scanner and chart areas update from the connected service without refreshing the whole page.

### Non-goals (this phase)

- **Proving economic edge:** Backtest and walk-forward outputs inform how you think about robustness and risk; they do **not** certify that live alerts will be profitable. See validation docs under `docs/` for methodology—not a product guarantee.

---


## Phase A — Scanner clarity & operational trust

**Goal:** The scanner feels legible and dependable as a **day-trade-oriented assistant**, not only as a data dump.

- Intraday / snapshot semantics: stale data, session boundaries, and “bar closed?” behavior documented and consistent where scans depend on them.
- Default UI paths emphasize **why** (short narrative + rule checklist) and **what to do** (levels, invalidation, simple checklist)—advanced JSON/details stay available but optional.
- Scheduler + persistence + streaming paths stable enough for daily use (recover cleanly after restarts).

---

## Phase B — Adaptive to skill level

**Goal:** Same signals, **different coaching depth**—without maintaining two products.

- Explicit modes (e.g. beginner ↔ advanced): jargon toggles, expanded definitions, progressive disclosure of raw fields.
- Lightweight preferences (saved locally or via backend): emphasis areas the assistant remembers for copy and layout—not a substitute for regulated advice.

---

## Phase C — Quant informs longer-term decisions

**Goal:** Research outputs connect to **how you think about holding period, universe, and risk**—still educational tooling.

- Dashboard or guided summaries that tie WFO/backtest lessons to **portfolio-level questions** (robustness, drawdowns, when an idea fails)—without blurring into live alert noise.
- Continue tightening methodology (multiple testing, pre-registration, batch discipline) per [`wfo_batches/`](wfo_batches/) and validation docs.

---

## Phase D — Polish & honesty

- Copy and empty states read like **product** messaging for traders (see repo UI copy rules).
- Tests and smoke checks cover critical API + scanner publish paths.
- Documentation stays honest about limits: **software reliability ≠ guaranteed edge**.

---

## How this doc is used

- **Planning:** Pick one phase; ship vertical slices (one narrative improvement + one reliability fix) rather than scattering tasks.
- **Scope guard:** Features that only made sense for a chat-bot transport stay out of the shipping path unless they clearly serve the browser assistant.

When phases complete, update **Baseline** and shift milestones forward—this file is descriptive, not a release calendar.
