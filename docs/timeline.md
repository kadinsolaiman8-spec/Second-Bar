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

- **Shipping stack**: FastAPI + static dashboard + `scanner_core` scans (manual + scheduled) + `quant` backtest/WFO and related docs.
- **Scanner data**: Rule checks, scores, indicators, and levels are already produced for detailed views; surfacing a consistent **plain-language story** and **skill-tier UX** is incomplete.
- **Edge & validation**: Whether setups are *economically* reliable is a research outcome, not a shipped toggle—see [`STRATEGY_AND_EDGE.md`](STRATEGY_AND_EDGE.md), [`WFO_COMMANDS.md`](WFO_COMMANDS.md), [`wfo_batches/`](wfo_batches/).

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
