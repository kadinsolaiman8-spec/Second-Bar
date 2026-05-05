# WFO batch pre-registration — initial-2026-03-19

## Commitment

- Date drafted (pre-run): 2026-03-19
- Author: **Put your name here** (replace this line)

## Universe

Same tickers and modes as [`run_wfo_batch.ps1`](../../run_wfo_batch.ps1) (copy for traceability).

| Ticker | **Official mode** (what the batch uses) |
|--------|----------------------------------------|
| GLD | tf |
| XLE | tf |
| SPY | mr |
| QQQ | mr |
| IWM | mr |

**Exploratory only (not in the CSV for multiplicity):** *(none — add only if you ran extra experiments on purpose)*

## Hold-out (“final exam” — plain English)

- **Reserved ticker:** **DIA** — you run this **once** at the end (see `BEGINNER_EXACT_STEPS.md`). It is **not** in the main table above.
- **What you promise:** You will **not** change `config.yaml` walk-forward grids or rules **because** DIA looked good or bad. You only **record** DIA’s result in the appendix.

## Strategy / config snapshot

- **Batch command:** `.\run_wfo_batch.ps1` from repo root (each line: `--period 10y --in-sample-gate --permutation-test`).
- **Runtime note:** default batch is usually **multi-day** (1000+500 full-history sims per ticker). See `BEGINNER_EXACT_STEPS.md` section **Runtime (WFO batch wall time)**.
- **Config:** whatever `config.yaml` is at commit time when you run (note hash in appendix after run).

## Tests and gates (frozen before run)

- **Primary:** stationary bootstrap on concatenated OOS (printed after WFO unless you skip bootstrap).
- **Secondary:** bar permutation when `--permutation-test` is on (your batch uses it).

## Pass / fail rules (scorecard)

- **Primary:** your printed bootstrap **p-value** ≤ **0.05** = “pass primary” for that ticker (read the CLI label for one-sided wording).
- **TF tickers (GLD, XLE):** if secondary bar-permutation “fails,” that can still be OK if primary passed—read the interpretation line the program prints.
- **Multiplicity:** after all five tickers, run `scripts/apply_multiple_testing_correction.py` on **one row per ticker** (see beginner doc).

## Post-run appendix (append **after** batch only)

- Run completed:
- Git commit: (run `git rev-parse HEAD`)
- Paste primary p-values / or say “see my_results.csv”:
- Multiplicity script output (full paste from terminal):
- Hold-out DIA run: date, primary p-value, **no config changes after**:
