# WFO batch pre-registration

**Start here:** [BEGINNER_EXACT_STEPS.md](BEGINNER_EXACT_STEPS.md) — exact files, copy-paste commands, CSV template. Short checklist: [QUICKSTART.md](QUICKSTART.md). In Cursor, the project skill **wfo-batch-record** (`.cursor/skills/wfo-batch-record/SKILL.md`) guides agent-assisted structured batch records (prereg, CSV, appendix, hold-out).

Manifests document **what** you will run **before** you run it: tickers, official mode per symbol, grids, gates, and pass/fail rules. This limits researcher degrees of freedom when interpreting results.

## Official mode (WFO scorecard vs live)

Batch scripts (e.g. [`run_wfo_batch.ps1`](../../run_wfo_batch.ps1)) use **one official mode per ticker** for primary/secondary tests and multiplicity. **`ticker_profiles.strategy: hybrid`** in [`config.yaml`](../../config.yaml) affects **live** `/stock` / `/recap` routing only; it can differ from the official WFO mode. The bot logs a **WARNING** at startup if a ticker is **hybrid** in profiles but **MR** in the table below.

| Ticker | Official validation mode | Batch note |
|--------|---------------------------|------------|
| GLD | TF | Commodity-linked ETF |
| XLE | TF | Sector energy |
| GDX | TF | If included in a batch; same class as GLD |
| SPY | MR | Broad equity |
| QQQ | MR | Broad tech-heavy |
| IWM | MR | Broad small-cap |

### Batch wall time (`run_wfo_batch.ps1`)

Default batch: **1000** in-sample gate sims + **500** OOS bar-permutation sims **per ticker**, each sim = full-history backtest (~10y daily). On typical hardware that is often **~2–3+ minutes per sim**, so **one MR ticker alone can be ~2–3+ days** of wall time; the full five-ticker sequence is usually **multi-day**, not ~90 minutes. See [BEGINNER_EXACT_STEPS.md — Runtime](BEGINNER_EXACT_STEPS.md#runtime-wfo-batch-wall-time) for why and for faster exploratory flags.

## Rules

- Draft a new `*-prereg.md` **before** starting the batch (commit it first if possible).
- After the run **starts**, the body of the manifest is **immutable** — do not change thresholds, universes, or rules in response to p-values.
- **Append only** to the **Post-run appendix**: completion timestamp, git SHA, log paths, multiplicity table (BH/Bonferroni), and hold-out outcome if applicable.

## Policy references

- [`docs/CLAUDE_SONNET_REVIEW_VERIFIED_ROADMAP.md`](../CLAUDE_SONNET_REVIEW_VERIFIED_ROADMAP.md) — P1 study design, one **official** mode per ticker, hybrid/exploratory rules.
- [`Roadmap`](../Roadmap) — validation practices; multiplicity via `scripts/apply_multiple_testing_correction.py` and [`src/stats_utils.py`](../../src/stats_utils.py).

## Template (copy into `YYYY-MM-DD-<batch-id>-prereg.md`)

```markdown
# WFO batch pre-registration — <batch_id>

## Commitment
- Date drafted (pre-run): YYYY-MM-DD
- Author:

## Universe
- Tickers (ordered list):
- **Official mode per ticker** (MR | TF | hybrid only if policy allows — see verified roadmap Q2):

## Hold-out (locked before grid design)
- Reserved ticker(s): not used to tune thresholds, grids, or pass/fail rules until the final validation run
- Reserved calendar window: e.g. YYYY-MM-DD .. YYYY-MM-DD — not used for in-sample tuning

## Strategy / config snapshot
- `config.yaml` walk_forward keys touched (quote or bullet paths):
- Param grid identity (e.g. `walk_forward.param_grid` / TF grid name; paste hash or inline summary):

## Tests and gates (frozen before run)
- Primary: stationary bootstrap on concatenated OOS (CLI default unless `--no-oos-bootstrap`)
- Secondary: bar permutation if enabled (`--permutation-test`)
- In-sample gate: if used, document flags / thresholds

## Pass / fail rules (scorecard)
- PASS for primary (e.g. p < 0.05 one-sided) and MR vs TF expectations for secondary
- Rows **excluded** from multiplicity scorecard (exploratory runs):

## Post-run appendix (append **after** run only)
- Run completed: YYYY-MM-DD HH:MM TZ
- Git commit: `<sha>`
- Log path(s) or paste of CLI summary
- BH/FDR q or Bonferroni α_adj: … (see script output)
```

## Multiplicity correction (cross-ticker)

Use **one row per ticker** for the **official** mode only. Do not enter MR, TF, and hybrid on the same symbol as three separate p-values.

From repo root:

```bash
python scripts/apply_multiple_testing_correction.py docs/wfo_batches/example_pvalues.csv --q 0.05
```

The script adds the repo root to `sys.path` so `PYTHONPATH` is optional. CSV columns: `ticker`, `test_name`, `p_value` (header required). See script `--help`.
