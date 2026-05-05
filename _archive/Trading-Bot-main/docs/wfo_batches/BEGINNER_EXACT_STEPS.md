# Exact steps (no jargon)

## What “hold-out” means here (one sentence)

You pick **one extra ticker** (we use **DIA**) that you will run **only after** the main batch. You **do not** change your strategy settings because DIA looked bad or good—you just write down the result. That way you are not “studying for the test” on the same names you already optimized.

---

## Part A — Edit **one** file before you run anything

**File (open in Notepad or Cursor):**  
`docs/wfo_batches/2026-03-19-validation-sprint-prereg.md`

**Change only these if you want:**

| Find this line | Put this (or keep as-is) |
|----------------|---------------------------|
| `Author:` | Your name or Discord name (anything, it is just a label) |

Everything else in that file is already filled with safe **example** values that match `run_wfo_batch.ps1`. You can leave them.

**Optional:** If you do **not** want DIA as the final check, change the line that says **DIA** to another symbol that is **not** GLD, XLE, SPY, QQQ, or IWM.

---

## Runtime (WFO batch wall time)

The old “~15–25 minutes per ticker” note was **wrong** for this script. Each ticker runs:

1. **In-sample bar-permutation gate** — default **1000** simulations; each one runs a **full** backtest over the whole history (~2500 daily bars for `--period 10y`).
2. **Walk-forward optimization** — many folds × parameter grid (MR uses **48** combos per fold; TF uses **2**).
3. **OOS bar-permutation test** — default **500** full-history backtests.

On a typical machine, one full-history backtest in those loops is often on the order of **~2–3+ minutes**. So **1500** gate+perm backtests alone can be **~2–3+ days of wall time per MR ticker** before counting WFO. TF tickers have cheaper WFO but **the same 1000+500** full-history passes — still often **many hours each**. A **full five-ticker batch is usually a multi-day job**, not ~90–120 minutes.

**Faster exploratory runs** (not for a locked prereg scorecard unless you pre-register the reduced sim counts): omit `--in-sample-gate` and/or `--permutation-test`, shorten `--period` (e.g. `5y`), and/or lower `--in-sample-sims` and `--permutation-samples` on manual `python -m src.run_wfo ...` commands. Example quick check (one ticker):

```powershell
python -m src.run_wfo SPY --strategy mr --period 5y --permutation-test --permutation-samples 100 --in-sample-sims 200
```

(Adjust numbers; smaller = faster but weaker inference.)

---

## Part B — Run the batch (exact commands)

**Folder:** `c:\Users\kadin\DiscordTradingBot`

**PowerShell (copy-paste):**

```powershell
Set-Location c:\Users\kadin\DiscordTradingBot
.\run_wfo_batch.ps1
```

That script runs, in order:

- GLD (trend / tf)
- XLE (trend / tf)
- SPY (mean reversion / mr)
- QQQ (mr)
- IWM (mr)

Each run can take **many hours to multiple days** — read **Runtime** above. Let it finish unless you intentionally stop it.

**If PowerShell blocks scripts** and you see an execution policy error, use this once, then run the batch again:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

**To run a single ticker by hand** (same settings as the batch script):

```powershell
Set-Location c:\Users\kadin\DiscordTradingBot
python -m src.run_wfo GLD --strategy tf --period 10y --in-sample-gate --permutation-test
```

Swap `GLD` / `tf` for `SPY` / `mr` etc. as needed.

---

## Part C — After the batch: copy p-values into a CSV

**1.** Create or open this file:  
`docs/wfo_batches/my_results.csv`

**2.** Put **exactly** this header on line 1:

```text
ticker,test_name,p_value
```

**3.** For **each** ticker you ran, add **one** line. The **primary** p-value is from the console block titled something like **`=== Stationary bootstrap (primary)`** — use the number after `p-value:`.

**Example** (these numbers are fake—replace with what **your** window printed):

```text
ticker,test_name,p_value
GLD,oos_bootstrap,0.12
XLE,oos_bootstrap,0.08
SPY,oos_bootstrap,0.03
QQQ,oos_bootstrap,0.04
IWM,oos_bootstrap,0.06
```

Rules:

- Use **`oos_bootstrap`** in the middle column for the primary test.
- **Do not** add extra rows for “I tried another mode on the same stock.”

**4.** Save the CSV as UTF-8 (Notepad: Save As → UTF-8).

---

## Part D — Run the “many tickers at once” correction (exact command)

**PowerShell:**

```powershell
Set-Location c:\Users\kadin\DiscordTradingBot
python scripts/apply_multiple_testing_correction.py docs/wfo_batches/my_results.csv --q 0.05
```

You will get a table printed in the terminal. **Copy all of that text.**

---

## Part E — Paste into the same prereg file (bottom section)

**File:** `docs/wfo_batches/2026-03-19-validation-sprint-prereg.md`

Scroll to **## Post-run appendix** and fill in:

- **Run completed:** today’s date and time
- **Git commit:** run `git rev-parse HEAD` in the project folder and paste the hash
- **Log path:** optional (e.g. “see terminal scrollback”)
- **Multiplicity:** paste the full output from Part D

---

## Part F — “Final exam” ticker (hold-out), exact command

**Only after** Part B is done and you are **not** planning to change grids or rules again:

```powershell
Set-Location c:\Users\kadin\DiscordTradingBot
python -m src.run_wfo DIA --strategy mr --period 10y --in-sample-gate --permutation-test
```

Write DIA’s primary p-value in the prereg file under **Hold-out final run**.

**Important:** If DIA looks bad, the honest move is **not** to tweak the bot until DIA passes—it is only a final check you log.

---

## Files checklist

| Action | File |
|--------|------|
| Edit name (optional) | `docs/wfo_batches/2026-03-19-validation-sprint-prereg.md` |
| You create after runs | `docs/wfo_batches/my_results.csv` |
| Append results | same prereg file, **Post-run appendix** |

---

## If something errors

Run tests to confirm Python is fine:

```powershell
Set-Location c:\Users\kadin\DiscordTradingBot
python -m pytest tests/ -v
```

Install deps if needed:

```powershell
pip install -r requirements.txt
```
