# What to do (plain English)

1. **Open** [`2026-03-19-validation-sprint-prereg.md`](2026-03-19-validation-sprint-prereg.md). Change the author line, ticker list, and hold-out if they do not match what you are actually about to run. Do not change pass/fail rules after you start the run.

2. **Run** walk-forward the way you already do (e.g. `python -m src.run_wfo` with your usual flags). The console will print **primary** (bootstrap) and optional **secondary** (bar permutation) **p-values**. If you use `run_wfo_batch.ps1` with the default **1000 + 500** full-history sims per ticker, expect **multi-day** wall time — see [BEGINNER_EXACT_STEPS.md — Runtime](BEGINNER_EXACT_STEPS.md#runtime-wfo-batch-wall-time).

3. **Make a small CSV** with one row per ticker for the **official** mode only — columns `ticker`, `test_name`, `p_value`. Do not add extra rows for “I also tried TF on SPY for fun”; those are exploratory.

4. **Run** from the project folder:
   `python scripts/apply_multiple_testing_correction.py yourfile.csv --q 0.05`  
   This tells you which tickers still look significant after correcting for testing many tickers.

5. **Append** to the bottom of the same prereg file: when you finished, git commit hash, and paste the script output (or say where the log lives).

6. **Hold-out:** Pick one symbol and one date range ahead of time that you will **not** use while tuning. After your rules feel final, run WFO on that symbol/period **once** and record pass/fail. Do not keep tweaking the strategy based on that result unless you already wrote down that exception.

That is the whole workflow. The bot does not run these steps for you; it is your research notebook discipline.
