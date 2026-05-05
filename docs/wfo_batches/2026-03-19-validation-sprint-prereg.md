# WFO batch pre-registration — validation-sprint-2026-03-19

## Commitment

- Date drafted (pre-run): 2026-03-19
- Author: **Put your name here** (replace this line)
- Git commit (pre-run): **TBD** (run `git rev-parse HEAD` after committing this file, then fill before Part B)

## Universe

Same tickers and modes as [`run_wfo_batch.ps1`](../../run_wfo_batch.ps1).

| Ticker | **Official mode** (what the batch uses) |
|--------|----------------------------------------|
| GLD | tf |
| XLE | tf |
| SPY | mr |
| QQQ | mr |
| IWM | mr |

**Exploratory only (not in the CSV for multiplicity):** *(none — add only if you ran extra experiments on purpose)*

## Hold-out (“final exam”)

- **Reserved ticker:** **DIA** — run **once** at the end (see `BEGINNER_EXACT_STEPS.md`). Not in the main table above.
- **Promise:** Do **not** change `config.yaml` walk-forward grids or rules **because** DIA looked good or bad. Only **record** DIA’s result in the appendix.

## Strategy / config snapshot

- **Batch command:** `.\run_wfo_batch.ps1` from repo root (`--period 10y --in-sample-gate --permutation-test` per ticker).
- **Runtime note:** default script is **multi-day** wall time (1000+500 full-history backtests per ticker). Document any reduced sims/period in this prereg if you deviate. See `BEGINNER_EXACT_STEPS.md` section **Runtime (WFO batch wall time)**.
- **Config:** `config.yaml` (+ merged `data/ticker_profiles.yaml`) at the git SHA in the commitment above.

## Tests and gates (frozen before run)

- **Primary:** stationary bootstrap on concatenated OOS (default unless `--no-oos-bootstrap`).
- **Secondary:** bar permutation with `--permutation-test` (batch enables this).
- **In-sample gate:** `--in-sample-gate` (batch enables this).

## Pass / fail rules (scorecard)

- **Primary:** printed bootstrap **p-value** ≤ **0.05** = pass primary for that ticker.
- **TF (GLD, XLE):** secondary bar-perm FAIL can be OK if primary passed — read CLI interpretation.
- **Multiplicity:** after all five tickers, run `scripts/apply_multiple_testing_correction.py` on **one row per ticker** (official mode only).

## Post-run appendix (append **after** batch only)

- Run completed:
- Git commit: (run `git rev-parse HEAD`)
- Primary p-values / CSV path:
- Multiplicity script output (full paste):
- Hold-out DIA run: date, primary p-value, **no config changes after**:
