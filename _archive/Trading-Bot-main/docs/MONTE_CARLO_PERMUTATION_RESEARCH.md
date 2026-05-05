# Monte Carlo Permutation Tests for Trading System Validation

Research summary based on Timothy Masters' "Permutation and Randomization Tests for Trading System Development" and related implementations (MQL5, HangukQuant, etc.).

---

## 1. Purpose

Monte Carlo permutation tests answer: **"Is this strategy's performance meaningfully better than random participation in the same market?"**

They destroy temporal structure in price data while preserving global statistical properties (drift, volatility distribution, intra-bar geometry). By comparing strategy performance on real data vs. permuted data, we assess whether observed edge is genuine or due to chance.

---

## 2. Bar Permutation Algorithm

### Core Idea

Shuffle **relative prices** (OHLC vs open) rather than raw prices. This:

- Preserves first and last price levels (global drift)
- Preserves marginal distribution of returns
- Destroys temporal dependencies and autocorrelation
- Keeps intra-bar structure (high/low/close relative to open) valid

### Two Components to Permute

| Component | Definition | What it represents |
|-----------|------------|---------------------|
| **Inter-bar gaps** | `rel_open = log(O_{t+1}) - log(C_t)` | Jump from close of bar t to open of bar t+1 |
| **Intra-bar deltas** | `rel_high = log(H_t) - log(O_t)`, `rel_low`, `rel_close` | High, low, close relative to open within bar t |

### Algorithm (Log-Space)

1. **Compute relative values** (log differences):
   - `rel_open[i]` = log(open[i+1]) - log(close[i])  for i = 0..N-2 (inter-bar gaps)
   - `rel_high[i]`, `rel_low[i]`, `rel_close[i]` = log(H/L/C) - log(O) for bar i (intra-bar deltas)

2. **Shuffle**:
   - Shuffle `rel_open` (indices 0..N-2) — randomizes bar-to-bar jumps
   - Shuffle `rel_high`, `rel_low`, `rel_close` for indices 1..N-2 only — preserves first bar and last bar geometry

3. **Reconstruct OHLC**:
   - Bar 0: keep original (open, high, low, close)
   - For i = 1..N-1:
     - `open[i] = exp(log(close[i-1]) + rel_open[i-1])`
     - `high[i] = exp(log(open[i]) + rel_high[i])`
     - `low[i]  = exp(log(open[i]) + rel_low[i])`
     - `close[i]= exp(log(open[i]) + rel_close[i])`
   - Last bar (i=N-1): use original last bar's intra-bar deltas to preserve endpoint

### Fisher–Yates Shuffle (for permutation)

```python
def permutation_member(array: np.ndarray) -> np.ndarray:
    """In-place Fisher-Yates shuffle; returns permuted array."""
    arr = array.copy()
    i = len(arr)
    while i > 1:
        j = int(np.random.uniform(0, 1) * i)
        if j >= i:
            j = i - 1
        i -= 1
        arr[i], arr[j] = arr[j], arr[i]
    return arr
```

---

## 3. Pseudocode: Bar Permutation

```
FUNCTION permute_ohlc_bars(df: DataFrame) -> DataFrame:
    N = len(df)
    # 1. Log-transform OHLC
    log_O, log_H, log_L, log_C = log(df.open), log(df.high), log(df.low), log(df.close)

    # 2. Compute relative values
    rel_open  = log_O[1:] - log_C[:-1]           # inter-bar gaps (length N-1)
    rel_high  = log_H - log_O                     # intra-bar deltas (length N)
    rel_low   = log_L - log_O
    rel_close = log_C - log_O

    # 3. Shuffle
    rel_open_shuffled = rel_open[permutation_member(np.arange(N-1))]

    perm_intra = permutation_member(np.arange(1, N-1))  # indices 1..N-2
    rel_high_s  = rel_high.copy(); rel_high_s[1:N-1]  = rel_high[perm_intra]
    rel_low_s   = rel_low.copy();  rel_low_s[1:N-1]   = rel_low[perm_intra]
    rel_close_s = rel_close.copy(); rel_close_s[1:N-1] = rel_close[perm_intra]

    # 4. Reconstruct OHLC (first bar unchanged; last bar keeps original geometry)
    new_O, new_H, new_L, new_C = [log_O[0]], [log_H[0]], [log_L[0]], [log_C[0]]
    last_close = log_C[0]

    FOR i = 1 TO N-1:
        new_open = last_close + rel_open_shuffled[i-1]
        new_h = new_open + rel_high_s[i]
        new_l = new_open + rel_low_s[i]
        new_c = new_open + rel_close_s[i]
        new_O.append(new_open); new_H.append(new_h); new_L.append(new_l); new_C.append(new_c)
        last_close = new_c

    # 5. Exponentiate
    RETURN DataFrame({open: exp(new_O), high: exp(new_H), low: exp(new_L), close: exp(new_C)}, index=df.index)
```

---

## 4. In-Sample Permutation Test

### Procedure

1. Run backtest on **real** training data → measure metric M (e.g. total return, Sharpe, outperformance)
2. For k = 1..N_perm (e.g. 1000):
   - Permute training bars
   - Run backtest on permuted data → M_k
3. **p-value** = count(M_k >= M) / N_perm  (one-tailed: how often permuted beat or tied real)

### Thresholds (Masters)

- **≥ 1000 permutations**
- **p-value < 1%** (0.01) to consider the strategy statistically significant

If p ≥ 1%, the strategy is likely overfitting to noise; reject it before walk-forward.

---

## 5. Walk-Forward Monte Carlo Permutation Test

### Procedure

1. Run walk-forward optimization (train → optimize → test OOS)
2. Collect OOS results for each fold (e.g. total return, Sharpe, outperformance)
3. For each fold (or pooled), generate N_perm permuted versions of the **test** data
4. Run the strategy (with optimized params from that fold) on each permuted test set
5. Compute p-value: proportion of permuted OOS results that exceed or equal real OOS result

### Thresholds (Masters)

- **1 year of OOS data**: p-value < 5% (0.05)
- **Multiple years of OOS data**: p-value < 1% (0.01)

More OOS data → stricter threshold (more evidence required).

---

## 6. Integration with Walk-Forward Optimization

### Current WFO Flow (from `walk_forward.py`)

```
for each fold:
    train_df = df[start : start + train_bars]
    test_df  = df[start + train_bars + embargo : start + train_bars + embargo + test_bars]

    best_params = grid_search(train_df)
    oos_result  = run_backtest(test_df, best_params)
    results.append(oos_result)
```

### Integration Points

| Stage | When | What |
|-------|------|------|
| **In-sample permutation** | After each fold's grid search, before accepting best_params | Permute `train_df` N times, run backtest; if p ≥ 1%, discard fold or flag |
| **Walk-forward permutation** | After each fold's OOS test | Permute `test_df` N times, run backtest with best_params; compute fold-level p-value |
| **Aggregate WF permutation** | After all folds | Pool OOS returns (or use pooled permuted returns), compute overall p-value |

### Pseudocode: WFO + Permutation

```
FUNCTION run_wfo_with_permutation(symbol, config, ...):
    results = []
    for each fold (train_df, test_df):
        # 1. Optimize on train
        best_params = grid_search(train_df)

        # 2. In-sample permutation (optional but recommended)
        real_metric = run_backtest(train_df, best_params).metric
        perm_metrics = []
        for k = 1 to 1000:
            perm_train = permute_ohlc_bars(train_df)
            perm_metrics.append(run_backtest(perm_train, best_params).metric)
        p_in = count(perm_metrics >= real_metric) / 1000
        if p_in >= 0.01:
            log("Fold rejected: in-sample p-value %.3f >= 1%%", p_in)
            continue

        # 3. OOS test on real data
        oos_result = run_backtest(test_df, best_params)

        # 4. Walk-forward permutation on test
        perm_oos_metrics = []
        for k = 1 to 500:  # or 1000
            perm_test = permute_ohlc_bars(test_df)
            perm_oos_metrics.append(run_backtest(perm_test, best_params).metric)
        p_oos = count(perm_oos_metrics >= oos_result.metric) / 500
        oos_result.p_value = p_oos

        results.append((oos_result, best_params, p_in, p_oos))

    # 5. Aggregate: overall p-value from pooled OOS
    # Option A: min across folds
    # Option B: combine test periods, permute full OOS, run once
    return results
```

---

## 7. Implementation Approach for This Codebase

### New Module: `src/permutation.py`

1. **`permute_ohlc_bars(df: pd.DataFrame) -> pd.DataFrame`**
   - Input: DataFrame with columns `open`, `high`, `low`, `close`
   - Output: Permuted DataFrame (same index, OHLC shuffled as above)
   - Preserve `volume` and other columns by copying from original (or permute volume with same intra-bar index)

2. **`run_in_sample_permutation_test(df, config, backtest_fn, n_perm=1000, metric='total_return')`**
   - Returns: (p_value, real_metric, permuted_metrics_list)

3. **`run_wf_permutation_test(wf_results, test_dfs, config, backtest_fn, n_perm=500)`**
   - For each fold: permute test_df, run backtest, collect metrics
   - Returns: list of (fold_idx, p_value, oos_metric, permuted_metrics)

### Integration with `walk_forward.py`

- Add optional `permutation_test: bool = False` and `n_perm: int = 1000` to `run_walk_forward_optimization`
- After grid search: if `permutation_test`, run in-sample permutation; skip fold if p ≥ 0.01
- After OOS backtest: if `permutation_test`, run WF permutation on test window, attach p_value to `WalkForwardResult`

### Data Requirements

- `run_backtest` already accepts `df=`; use sliced train/test DataFrames
- Ensure `permute_ohlc_bars` receives contiguous OHLC; index can be preserved for alignment

---

## 8. Caveats and Limitations

1. **Volatility clustering**: Permutation destroys serial correlation; real markets exhibit volatility clustering. The null is "no predictability," which is appropriate for testing edge.

2. **Multi-instrument**: For portfolios, use a **shared permutation index** across instruments to preserve cross-sectional dependence (HangukQuant).

3. **Computation**: 1000 permutations × 1000 bars × backtest = expensive. Consider caching indicator computations, vectorization, or parallel runs.

4. **Seed**: Use fixed seed for reproducibility when reporting permutation results.

---

## 9. References

- Masters, T. *Permutation and Randomization Tests for Trading System Development*. 2020.
- Masters, T. *Testing and Tuning Market Trading Systems*. O'Reilly.
- [Permuting price bars in MQL5](https://mql5.com/en/articles/13591) — CPermuteRates implementation
- [Monte Carlo Permutation Tests in MetaTrader 5](https://mql5.com/en/articles/13162)
- [HangukQuant: Experimental Control for ML of Temporal Effects](https://hangukquant.substack.com/p/experimental-control-for-machine) — bar permutation in Python
