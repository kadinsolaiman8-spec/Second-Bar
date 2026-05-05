# Claude Sonnet 4.6 review — verification & roadmap

This document **checks each major claim** from an external model review against **this repository (as of the verification pass)** and turns confirmed gaps into an **action roadmap**. Use it with the original review text side by side.

---

## Verification summary

| Topic | Verdict | Notes |
|--------|---------|--------|
| Multiple testing across tickers/strategies | **Accurate** | No Bonferroni/BH, no pre-registration in code or docs. |
| Bar-permutation bias vs trend-following | **Accurate for current CLI** | In-sample gate + post-WFO test use `_bar_permute_df` (destroys autocorrelation). Docstrings in `walk_forward.py` already acknowledge TF bias. |
| Stationary bootstrap in repo | **Partially accurate** | `run_permutation_test()` uses `StationaryBootstrap` + `arch` — **not** wired from `src/run_wfo.py` (import only). Batch script uses bar permutation. |
| WFO objective = bar-level Sharpe for TF | **Outdated vs `config.yaml`** | `walk_forward.optimize_metric` is **`profit_factor`** in config. Code still supports `sharpe` and DSR uses **bar-return Sharpe** on combined OOS returns (separate from WFO selection metric). |
| “Switch TF objective to profit factor” quick win | **Already done** | See `config.yaml` → `walk_forward.optimize_metric: profit_factor`. |
| Donchian optimize 20 vs 55 / freeze at 55 | **Already addressed** | `trend_following_param_grid` no longer varies Donchian; comments say fixed at 55 (Turtle-style). |
| GLD DXY/TIP regime | **Partially wrong as stated** | **Already implemented**: `backtest.py` gold macro filter + `config.yaml` `GLD` → `gold_macro_filter: true`; `regime.py` notes gold vs VIX. |
| PBO reliability with small grids | **Mostly accurate** | TF grid is **2 combos** (`atr_multiplier` × fixed fields) — PBO runs but is **weak**; simplified CSCV ≠ full Bailey & López de Prado PBO. |
| PBO metric mismatch (important) | **Bug / caveat** | `compute_pbo()` assumes **Sharpe-like** matrix; `all_combo_metrics` stores **`optimize_metric`** (e.g. **profit_factor**). Interpretation of PBO when `optimize_metric != "sharpe"` is **not** the documented meaning. |
| VIX KeyError “throughout all runs” | **Likely outdated / environment-dependent** | `data.py` uses `Ticker.history()` and explicit start/end for long periods **to avoid** `KeyError('chart')`. If `fetch_vix_series` returns empty, regime falls back to SMA-200 — **silent degradation** concern remains. |
| Live vs backtest: next-bar open, long-only | **Accurate** | Matches `backtest.py` loop. |
| Intraday partial bar / Discord timing | **Plausible risk** | Not fully audited here; worth a focused pass on “bar closed?” for live fetches. |
| News headline timing / VADER quality | **Plausible** | yfinance headlines are weakly timestamped; impact likely small but real. |
| yfinance adjustment + disk cache | **Plausible** | Repo does not appear to persist OHLCV to disk by default; still valid if caching is added later. |
| Index survivorship for `/market` | **Accurate** | `Roadmap` Phase 2 already lists point-in-time membership. |
| No pytest suite | **Accurate** | No `tests/` tree. |
| `--save-profile` = fold-best aggregation | **Accurate** | `_aggregate_best_params(results)` over per-fold **in-sample** winners; not OOS-optimal or explicitly “stability-first” beyond plateau inside each fold. |
| `run_permutation_test` unused in CLI | **Verified** | Imported in `run_wfo.py`, never called; post-WFO uses `run_bar_permutation_oos_test`. |
| MinBTL formula / BH on 18 tests | **Unverified here** | Sensible references; confirm MinBTL against primary papers before coding. |
| Hansen SPA | **Not in repo** | Recommendation only. |
| mlfinlab / external repos | **External** | Optional dependency; not evaluated for license/maintenance. |

---

## Architecture decisions (locked)

Policy agreed for validation reporting, hybrid handling, and PBO. Implementation in `run_wfo.py` / `walk_forward.py` should converge here.

### Q1 — Primary vs secondary significance tests

| Role | Test | Question it answers |
|------|------|---------------------|
| **Primary** | **Stationary bootstrap** on the **concatenated OOS** sample (all WFO folds stitched in time order) | Does the strategy’s OOS equity / bar-P&L beat a null that **preserves temporal structure**? |
| **Secondary** | **Bar permutation** (`_bar_permute_df`) | Does the strategy beat a null where **autocorrelation is destroyed**? For **TF**, a FAIL is **expected / uninformative** (biased against TF). A PASS is **strong** (signal survives even without the structure TF usually exploits). |

**Reporting rule:** If **TF** passes stationary bootstrap but **fails** bar permutation → record as **pass with documented caveat** (not ambiguous). Print this interpretation in CLI/embed output so future-you does not re-litigate it.

**Implementation note:** Bootstrap must use **combined OOS** (one concatenated curve), **not** per-fold bootstrap — per-fold samples are too thin for a stable null.

### Q2 — Hybrid and “official” mode per ticker

- Pick **one official hypothesis per ticker before viewing results**. Do **not** run MR + TF + hybrid on the same ticker and pick the winner: that is post-hoc selection; BH correction **does not** fix dependence between correlated modes on the same asset.
- **A priori assignment (defensible priors):**
  - **Commodity-linked ETFs** (e.g. GLD, USO, GDX): **TF** = official.
  - **Broad equity ETFs** (e.g. SPY, QQQ, IWM): **MR** = official.
  - **Sector ETFs** (e.g. XLE, XLU): **ambiguous** — document the chosen official mode *before* running.
- **Non-official modes** on a ticker: run only as **exploratory**; **exclude** from pass/fail scorecard; **do not** drive live routing.
- **Hybrid as a third hypothesis:** **deferred** until **both** MR and TF show validated edge on that name. Hybrid of two unvalidated legs = compounded noise.

### Q3 — PBO

- **Suspended** in reporting/trust until the pipeline is consistent:
  1. WFO selects on the **intended** objective (already **`profit_factor`** in `config.yaml` for current runs).
  2. **PBO is computed only** on the **in-sample distribution of that same metric** (per combo, per fold) — not on Sharpe or any metric that did not select the winner.
- **Do not** “fix” PBO by feeding train Sharpe while WFO optimizes profit factor; the number would not describe the actual selection surface.
- At **~48 combos**, PBO is at the **low end** of what the original literature emphasizes (hundreds–thousands of variants). Treat any PBO point estimate as a **rough** signal with wide uncertainty, once re-enabled.

**Decision summary**

| Topic | Decision |
|--------|----------|
| Primary test | Stationary bootstrap on **concatenated OOS** P&L / equity |
| Secondary | Bar permutation, **labeled** as biased-against-TF |
| TF: bootstrap pass, bar-perm fail | **Pass** + documented caveat |
| Hybrid tickers | **One** official mode per ticker, chosen **a priori** |
| Other modes | Exploratory only; out of scorecard and routing |
| Hybrid MR+TF | **Off** until MR and TF individually validated |
| PBO | **Off** until IS matrix metric matches WFO objective; then use with low weight at 48 combos |

---

## Roadmap (addressing verified items)

### P0 — Correctness of validation math

**Done in codebase (~2026-03):** Concatenated-OOS stationary bootstrap (`run_stationary_bootstrap_oos_bar_returns`, `concatenate_oos_bar_returns`); default post-WFO in `run_wfo.py` with CLI metric banner (WFO selection vs diagnostic Sharpe); bar-permutation gate/OOS + docstrings + MR/TF interpretation lines; PBO gated by `walk_forward.show_pbo` (default off); unused `run_permutation_test` import removed from `run_wfo` (full-OHLCV `run_permutation_test` still in `walk_forward.py` for optional future use). **Tests:** `tests/test_walk_forward_bootstrap.py` (pytest).

Original checklist (retained for traceability):

1. ~~**Stationary bootstrap (primary)** — Implement CLI path … **concatenated OOS** bar-return series … alongside bar permutation.~~ → Implemented on stitched `bar_returns` (not pre-built OHLCV df).
2. ~~**Bar permutation (secondary)** — … standardize **printed labels** …~~ → CLI + docstrings updated.
3. ~~**PBO** — **Hide or no-op** …~~ → `show_pbo: false` default + suppression line.
4. ~~**Wire bootstrap in `run_wfo.py`** …~~ → Primary wired; `run_permutation_test` not required for CLI path.

### P1 — Multiple testing & study design

**In repo (2026-03-19):** Process and tooling for items 5–7 — `docs/wfo_batches/` (`README.md`, **`BEGINNER_EXACT_STEPS.md`** copy-paste workflow, example prereg, `example_pvalues.csv`), **`src/stats_utils.py`** (BH adjusted *p* + Bonferroni α/m), **`scripts/apply_multiple_testing_correction.py`** (CSV → printed table), **`tests/test_multiple_testing.py`**. **`Roadmap`** validation table + Phase 1 bullet updated. **Still manual per batch:** fill prereg before run, paste commit + CLI p-values + script output after; hold-out **ticker** (`DIA` in beginner doc) + **one-shot** final run — WFO CLI uses `--period` only (no separate “hold-out date range” flag); reserve a calendar window in the prereg as a **policy** if you slice data outside this codebase.

5. **Pre-register** — One markdown file listing: tickers, **official mode per ticker**, strategies, `param_grid`, pass/fail rules, before each batch; append run date and commit hash. → **Template + example:** `docs/wfo_batches/README.md`, `2026-03-19-initial-prereg.md`.
6. **Multiplicity correction** — When interpreting cross-ticker p-values, apply **Benjamini–Hochberg** (or Bonferroni) in analysis notebook/script; document adjusted thresholds in `Roadmap` validation table. Do **not** treat MR/TF/hybrid on the **same** ticker as independent hypotheses for this purpose. → **`scripts/apply_multiple_testing_correction.py`** + tests; you build the CSV from CLI output.
7. **Hold-out assets / periods** — Reserve at least one ticker and one calendar window **never** used during grid design; run once after locking rules. → **Documented** hold-out ticker + “final exam” command in `BEGINNER_EXACT_STEPS.md`; calendar hold-out is **documentation-only** unless you add date-bounded fetches elsewhere.

### P2 — Statistical power & reporting

**Done in codebase (~2026-03+):** WFO summary in [`src/run_wfo.py`](src/run_wfo.py) reports **total / average / by-fold** OOS trade counts; heuristic low-power lines via [`oos_trade_power_notes_from_results`](src/walk_forward.py) with `low_power_oos_trades_total` / `low_power_oos_trades_per_fold` in [`config.yaml`](config.yaml). Longer TF training: root `walk_forward.tf_train_bars` (Daily TF only in CLI), optional per-timeframe `tf_train_bars` (e.g. `1W`). **Tests:** [`tests/test_oos_trade_power.py`](tests/test_oos_trade_power.py).

Original checklist (retained):

8. ~~**Trade-count reporting** — …~~ → Implemented (see above).
9. ~~**Longer history / weekly** — …~~ → `tf_train_bars` + `timeframe_overrides` (e.g. `1W.tf_train_bars`).

### P3 — Live vs backtest parity & observability

**Done in codebase (~2026-03+):** CLI `--print-resolved-config` on [`src/run_wfo.py`](src/run_wfo.py) (YAML dump, no fetch). [`src/data.py`](src/data.py) WARNING when VIX current/series history is empty; WFO prints SMA-200-only regime path when VIX series missing (see [`src/regime.py`](src/regime.py) `vix is None`). Parity note [`docs/LIVE_BACKTEST_STOP_PARITY.md`](docs/LIVE_BACKTEST_STOP_PARITY.md); `_compute_stop_tp_levels` default `stop_pct` aligned with backtest (`0` when key absent). Startup hybrid vs official MR warning: [`src/validation_routing.py`](src/validation_routing.py), [`main.py`](main.py).

Original checklist (retained):

10. ~~**Resolved config dump** — …~~ → `run_wfo --print-resolved-config`.
11. ~~**VIX / regime visibility** — …~~ → WARNING on empty/failed VIX fetch; explicit WFO line.
12. ~~**Stops: live vs backtest** — …~~ → `docs/LIVE_BACKTEST_STOP_PARITY.md` + default `stop_pct` alignment.

### P4 — Engineering

**Partial (~2026-03+):** Tier-1-style tests — [`tests/test_config_resolver.py`](tests/test_config_resolver.py), [`tests/test_indicator_scores_smoke.py`](tests/test_indicator_scores_smoke.py), [`tests/test_walk_forward_synthetic.py`](tests/test_walk_forward_synthetic.py), [`tests/test_validation_routing.py`](tests/test_validation_routing.py). README official-mode table + startup warning (item 15). Remaining: broader WFO invariants, SignalResult refactor (14).

13. ~~**Pytest tiers** (starter) — …~~ → See tests above; expand as needed.
14. **Separate SignalResult vs Discord** — Refactor toward a single dataclass for numeric outputs, then formatters (`recap`, `stock`) — larger refactor; track under Phase 1/2 in main `Roadmap`.
15. ~~**Live routing vs validation** (narrow) — …~~ → `docs/wfo_batches/README.md` table + `warn_hybrid_vs_official_validation` in `main.py`.

### P5 — Deferred / already tracked

16. **VectorBT** — Already Phase 3 in `Roadmap`.
17. **Point-in-time index data** — Already Phase 2 in `Roadmap`.
18. **Profit factor for WFO** — Already in `config.yaml`; **CLI alignment done** for primary bootstrap vs selection metric and bar permutation (PF). **Remaining:** re-enable PBO only after CSCV uses the same metric as `optimize_metric`; then print **48-combo** caveat.

---

## Claims to treat as advice, not facts

- Exact **“7–39 trades”** or **“5% power”** figures depend on the specific run logs; the **low-trade-count / low-power** concern is directionally sound.
- **“High school project”** framing is subjective; ignore for prioritization.
- **MinBTL formula** as quoted — verify against Bailey & López de Prado before implementing.

---

## Related files

- `src/walk_forward.py` — WFO, `_bar_permute_df`, `run_bar_permutation_*`, `run_permutation_test`, `compute_pbo`, `_bootstrap_df`
- `src/run_wfo.py` — CLI, primary OOS bootstrap, gate, permutation OOS, PBO gate, VIX fetch message
- `src/data.py` — `fetch_vix_series`, `_vix_ticker_history`
- `config.yaml` — `walk_forward`, `trend_following_param_grid`, `regime`, GLD profile
- `Roadmap` — product phases; see pointer below

---

*Verification performed against repository source; re-run this checklist after major changes to WFO or data providers.*
