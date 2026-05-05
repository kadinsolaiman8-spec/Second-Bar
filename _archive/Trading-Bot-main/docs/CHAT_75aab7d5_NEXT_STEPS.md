# Next Steps for Chat 75aab7d5 (Trend-Following Prototype)

**Context:** The trend-following prototype is implemented (Donchian breakout, ADX filter, ATR trailing stop, WFO `--strategy tf`). The min_confidence experiment proved mean-reversion has no edge on SPY/AAPL; trend-following is the new path.

---

## WFO Results (Trend-Following)

### Daily, 3y (pre-improvements)

| Asset | Avg OOS Return | Avg vs B&H | Total Trades | Zero-Trade Folds |
|-------|----------------|-----------|--------------|------------------|
| **SPY** | +2.0% | -4.6% | 5 | 3 of 7 |
| **AAPL** | +2.8% | -0.5% | 5 | 4 of 7 |
| **GLD** | +3.0% | -6.7% | 7 | 3 of 7 |
| **USO** | -1.6% | +0.7% | 3 | 4 of 7 |

### Daily, 5y (post-improvements: bar-level Sharpe, null ADX, 126-bar OOS)

| Asset | Avg OOS Return | Avg vs B&H | Total Trades | Zero-Trade Folds |
|-------|----------------|-----------|--------------|------------------|
| **SPY** | +3.2% | -3.6% | 15 | 0 of 7 |
| **GLD** | +6.2% | -6.0% | 18 | 1 of 7 |

**SPY folds:** -3.3%, -1.9%, +7.9%, +10.1%, +5.0%, -15.5%, +19.9%. Best params: donchian 10–55, atr 2.0–3.0, adx_threshold None/20/25.

**GLD folds:** 0%, +3.0%, -4.4%, +15.4%, +5.0%, +8.4%, +15.6%. Best params: adx_threshold None in 6 of 7 folds; donchian 10/20/55.

**Sharpe stability:** Bar-level Sharpe used for optimization. Null ADX dominates GLD (6/7 folds); param selection more consistent than before. WFO output does not print Sharpe per fold; stability inferred from param consistency.

### Weekly, 10y (SPY)

**SPY 1W:** 0 folds — backtest rejects train windows with 52 bars (hardcoded min 60 in `backtest.py`). Fix: lower `len(df) < 60` threshold for weekly or use `train_bars >= 60`.

---

## Findings

1. **GLD best OOS** — +3.0% avg, 7 trades; gold ETF trends more than SPY/AAPL.
2. **USO weak** — -1.6% avg, 3 trades, 4 zero-trade folds; oil choppy.
3. **All underperform B&H** — SPY -4.6%, AAPL -0.5%, GLD -6.7%; USO +0.7% vs B&H (strategy lost less in down market).
4. **SPY weekly broken** — 0 folds; insufficient bars for 1W config (train 104, test 26). Need longer period or smaller windows.

---

## Proposed Plan (Confirm Before Proceeding)

### Option A: Fix SPY Weekly + Re-run
- Reduce 1W `train_bars`/`test_bars` or use `period=10y` so enough weekly bars exist.
- Re-run SPY 1W to test if weekly timeframe helps.

### Option B: Prioritize GLD (Best TF Result)
- GLD +3.0% OOS, 7 trades. Consider adding GLD to recap or testing TF on other commodities (e.g. SLV).

### Option C: Improve TF Signal Generation
- **Fewer zero-trade folds:** Widen Donchian range (e.g. 10–55), relax ADX filter (or disable), add `max_hold_bars: 0` to param grid to test “hold until Donchian exit.”
- **Bar-level Sharpe:** Switch WFO `optimize_metric` to bar-level Sharpe (from Roadmap) for more stable param selection.
- **Longer OOS:** Increase `test_bars` to 126 for more meaningful OOS.

### Option D: Add Statistical Validation
- **Monte Carlo permutation:** Run optional permutation test on best fold; if p ≥ 5%, treat result as noise.
- **Parameter stability:** Cluster optimal params across folds; prefer params that appear in multiple folds.

### Option E: Hybrid Strategy
- Use TF for recap when asset is “trending” (e.g. ADX > 25, price > 200 SMA); use MR when choppy.
- Requires regime detection before signal generation.

---

## Recommendation

1. **GLD** — Best TF result (+3.0%, 7 trades). Prioritize for recap or further testing.
2. **SPY weekly** — Fix config (smaller windows or longer period) and re-run.
3. **USO** — Skip; weak and choppy.
4. **Medium term:** Bar-level Sharpe, longer OOS, Monte Carlo permutation (Roadmap).
