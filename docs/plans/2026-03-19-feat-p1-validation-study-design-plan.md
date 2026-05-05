# P1 validation study design — implementation plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Implement the verified roadmap **P1** slice only: pre-registered WFO batches, multiplicity-aware interpretation (Benjamini–Hochberg or Bonferroni across tickers), and a documented hold-out asset/period reserved before batch runs.

**Architecture:** Keep **no new runtime dependency in the Discord bot** unless you choose to add `statsmodels`/`scipy` for BH in-repo; a small `scripts/` helper or `notebooks/` cell is enough to read logged p-values and output adjusted q-values. Pre-registration lives as version-controlled markdown under `docs/wfo_batches/` (or one rolling manifest you append to). Hold-out is **policy + config**, not a code feature: list forbidden tickers/windows in the same manifest and enforce by convention in `run_wfo` batches.

**Tech Stack:** Markdown, Python 3.x, existing CLI [`src/run_wfo.py`](../../src/run_wfo.py) (prints bootstrap and permutation **p-values**), optional `pandas` in a script for BH.

---

## Enhancement summary (deepen-plan)

**Deepened on:** 2026-03-19  
**Scope:** P1 only ([`docs/CLAUDE_SONNET_REVIEW_VERIFIED_ROADMAP.md`](../CLAUDE_SONNET_REVIEW_VERIFIED_ROADMAP.md) items 5–7).

### Key improvements captured below

1. **One hypothesis row per ticker** for multiplicity — MR/TF/hybrid on the same symbol count as **one** family branch, not three independent tests ([verified roadmap §Q2](../CLAUDE_SONNET_REVIEW_VERIFIED_ROADMAP.md)).
2. **BH controls FDR** (expected fraction of false discoveries among rejections); **Bonferroni** controls FWER but is conservative when tests are correlated — document which you used.
3. **Hold-out** is defined **before** any grid search on that universe; running hold-out once after “locking” rules is the minimum honest workflow.

### New considerations

- Bootstrap p-value in CLI is **one-sided, smoothed** on concatenated OOS bar-P&L (Sharpe-oriented diagnostic); bar-permutation uses **profit factor** — when logging for BH, **label which test** each p-value belongs to and **do not mix** metrics across rows without explicit rationale.
- If you only ever run **one** official mode per ticker per batch, the number of BH tests = number of tickers (clean). Exploratory second modes must be **excluded** from the BH table (verified roadmap).

---

## Task 1: Pre-registration manifest (markdown)

**Files:**

- Create: `docs/wfo_batches/README.md` (how to use the folder; link to verified roadmap).
- Create: `docs/wfo_batches/YYYY-MM-DD-<batch-id>-prereg.md` (one per batch, **before** the run).

**Step 1: Add README**

Content must state: manifests are **immutable** after the run starts except for appending **run date**, **git commit SHA**, and **paths to log artifacts** at the bottom.

**Step 2: Define template for `*-prereg.md`**

Each file must include these sections (copy-paste template into README or first batch file):

```markdown
# WFO batch pre-registration — <batch_id>

## Commitment
- Date drafted (pre-run): YYYY-MM-DD
- Author:

## Universe
- Tickers (ordered list):
- **Official mode per ticker** (MR | TF | hybrid only if policy allows — see roadmap Q2):

## Strategy / config snapshot
- `config.yaml` walk_forward keys touched (quote or bullet paths):
- Param grid identity (e.g. `walk_forward.param_grid` / TF grid name; paste hash or inline summary):

## Tests and gates (frozen before run)
- Primary: stationary bootstrap on concatenated OOS (CLI default unless `--no-oos-bootstrap`)
- Secondary: bar permutation if enabled (`--permutation-test`)
- In-sample gate: if used, document `--in-sample-permutation` / thresholds

## Pass / fail rules (scorecard)
- Define PASS for primary (e.g. p < 0.05 one-sided) and whether secondary must PASS for MR vs TF caveat language
- Rows **excluded** from multiplicity scorecard (exploratory runs):

## Post-run appendix (append **after** run only)
- Run completed: YYYY-MM-DD HH:MM TZ
- Git commit: `<sha>`
- Log path(s) or paste of CLI summary
```

**Step 3: Write first real batch file**

Fill template for your **next** scheduled batch; do not run WFO until the file exists in git.

**Step 4: Cross-link**

Add one line under **Validation Practices** table in [`Roadmap`](../../Roadmap): “Pre-registration manifests: `docs/wfo_batches/`.”

**Step 5: Commit**

```bash
git add docs/wfo_batches/ Roadmap
git commit -m "docs: add WFO pre-registration batch folder"
```

### Research insights (pre-registration)

- **Best practice:** Pre-specify universe, signals, and success criteria **before** touching results to limit p-hacking and narrative fallacy (same spirit as clinical pre-registration; in quant, see general “researcher degrees of freedom” literature).
- **Pitfall:** Editing the manifest after seeing p-values — treat as misconduct for your own scorecard; only append **post-run** appendix.

---

## Task 2: Multiplicity correction (BH or Bonferroni)

**Files:**

- Create: `scripts/apply_multiple_testing_correction.py` (recommended) **or** `notebooks/wfo_pvalue_fdr.ipynb`
- Optionally modify: [`Roadmap`](../../Roadmap) validation table — add column “Adjusted α / FDR q” with link to script docstring

**Step 1: Define input format**

Use a **CSV or YAML** you fill from CLI output, one row per **independent hypothesis** (official mode only per ticker):

| ticker | test_name | p_value | notes |
|--------|-----------|---------|------|
| SPY | oos_bootstrap | 0.03 | primary |
| QQQ | oos_bootstrap | 0.04 | primary |

Do **not** add a second row for the same ticker’s exploratory TF run.

**Step 2: Implement BH (FDR)**

Minimal dependency-free BH for m p-values sorted ascending:

```python
def benjamini_hochberg_adjusted_pvalues(p_values: list[float]) -> list[float]:
    """BH adjusted p-values (same length as p_values); reject H_i where adj[i] <= q."""
    m = len(p_values)
    order = sorted(range(m), key=lambda i: p_values[i])
    sorted_p = [p_values[i] for i in order]
    temp = [sorted_p[i] * m / (i + 1) for i in range(m)]
    adj_sorted = [0.0] * m
    adj_sorted[m - 1] = min(temp[m - 1], 1.0)
    for i in range(m - 2, -1, -1):
        adj_sorted[i] = min(temp[i], adj_sorted[i + 1], 1.0)
    result = [0.0] * m
    for pos, idx in enumerate(order):
        result[idx] = adj_sorted[pos]
    return result
```

Reject hypothesis *i* where `adj[i] <= q` for chosen FDR level **q**; document **q** in the batch appendix.

**Step 3: Optional Bonferroni one-liner**

`alpha_adj = 0.05 / m` for comparison in printed output.

**Step 4: CLI or README note**

Document: “BH applies to **cross-ticker** rows only; within-ticker MR+TF+hybrid are **not** three tests for this table.” Quote [`docs/CLAUDE_SONNET_REVIEW_VERIFIED_ROADMAP.md`](../CLAUDE_SONNET_REVIEW_VERIFIED_ROADMAP.md) §P1 item 6.

**Step 5: Test**

Create `tests/test_multiple_testing.py`:

```python
def test_bh_simple() -> None:
    from scripts.apply_multiple_testing_correction import benjamini_hochberg_adjusted_pvalues
    p = [0.01, 0.04, 0.10]
    adj = benjamini_hochberg_adjusted_pvalues(p)
    assert len(adj) == 3
    assert all(adj[i] >= p[i] for i in range(3))
```

If importing from `scripts/` is awkward, move the function to `src/stats_utils.py` and test there.

Run: `python -m pytest tests/test_multiple_testing.py -v` — expect PASS.

**Step 6: Commit**

```bash
git add scripts/ tests/test_multiple_testing.py  # or src/stats_utils.py
git commit -m "feat: Benjamini-Hochberg helper for WFO batch p-values"
```

### Research insights (multiplicity)

- **BH (FDR)** is standard when many tickers are screened and **false discovery rate** is acceptable to control; **Bonferroni** is stricter (FWER), fine when *m* is small (e.g. ≤5 tickers).
- **Dependence:** Equity signals on related tickers violate BH independence; BH is often still used as a **practical** screen with the understanding that effective *m* is smaller — note this limitation in the batch appendix.
- **Edge case:** p-value = 0 from printing precision — store raw or higher precision if available; avoid zero p in adjustment corner cases.

---

## Task 3: Hold-out asset and calendar window

**Files:**

- Modify: latest `docs/wfo_batches/*-prereg.md` — add **Hold-out policy** section
- Optional: `config.yaml` comment block listing `holdout_tickers:` / `holdout_calendar:` (comments only, unless you add real enforcement later)

**Step 1: Pick hold-out before any grid tuning**

In the pre-reg file, add:

```markdown
## Hold-out (locked before grid design)
- Reserved ticker(s): never used to **tune** thresholds, grids, or pass/fail rules until final validation run
- Reserved calendar window: e.g. 2024-01-01..2024-06-30 — **not** used in any in-sample tuning
```

**Step 2: “Final run” protocol**

After P1 scorecard rules stabilize, run **once** on hold-out ticker/window; **do not** iterate on hold-out based on its result beyond pre-declared fixes.

**Step 3: Log outcome**

Append to post-run appendix: hold-out PASS/FAIL and whether any rule drift occurred (should be “none”).

**Step 4: Commit**

```bash
git add docs/wfo_batches/
git commit -m "docs: define hold-out ticker and window for batch X"
```

### Research insights (hold-out)

- **Purpose:** Mitigate **data snooping** from repeated use of the same full sample for design and validation.
- **Pitfall:** Using hold-out repeatedly after peeking — converts hold-out into training; true locked hold-out is **one shot** (or strict pre-registration of a single corrective pass).

---

## Acceptance criteria

- [ ] At least one `docs/wfo_batches/*-prereg.md` exists **before** its WFO batch, with official modes and frozen pass/fail rules.
- [ ] BH (or Bonferroni) is applied in a script or notebook with documented **q** or **α_adj**; results referenced from batch appendix.
- [ ] Hold-out ticker and window are named in pre-reg; final hold-out run logged once post-lock.
- [ ] `Roadmap` validation table references pre-reg folder and multiplicity correction location.

---

## References

- [`docs/CLAUDE_SONNET_REVIEW_VERIFIED_ROADMAP.md`](../CLAUDE_SONNET_REVIEW_VERIFIED_ROADMAP.md) — P1 items 5–7, §Q2 hybrid policy
- [`Roadmap`](../../Roadmap) — validation table, “Next (validation track)”
- Benjamini & Hochberg (1995), *Controlling the false discovery rate*

---

Plan complete and saved to `docs/plans/2026-03-19-feat-p1-validation-study-design-plan.md`. Two execution options:

**1. Subagent-Driven (this session)** — dispatch a fresh subagent per task, review between tasks, fast iteration (use **superpowers:subagent-driven-development**).

**2. Parallel Session (separate)** — open a new session with **superpowers:executing-plans**, batch execution with checkpoints.

Which approach?
