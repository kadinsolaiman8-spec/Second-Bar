# Walk-Forward Optimization Research Summary

## 1. What Is Walk-Forward Optimization?

**Walk-forward optimization (WFO)** is a rolling train/test validation method for trading strategies. It simulates how a strategy would be periodically re-optimized and deployed in live trading.

**Core loop:**
1. **Optimize** strategy parameters on in-sample (training) data for a specific period
2. **Test** the optimized parameters on subsequent out-of-sample (unseen) data
3. **Roll forward** by shifting the time window and repeating steps 1–2

**Why it matters:**
- Reduces overfitting by testing on truly unseen data
- Accounts for changing markets (parameters re-optimized regularly)
- Simulates realistic re-optimization intervals
- More realistic than a single static train/test split

---

## 2. Implementation Patterns

### Anchored vs Rolling Window

| Pattern | Description | Pros | Cons |
|---------|-------------|------|------|
| **Anchored (Expanding)** | Training start fixed; window grows over time | More training data | Older data may be irrelevant; higher compute |
| **Rolling** | Both start and end move; training window size constant | More weight on recent data; adaptive to regime changes | Less historical context |

### Step Size

- Step size = how far the windows move forward between iterations
- Typical: 21 trading days (1 month) or 1 year
- **Smaller step** → more iterations, more OOS periods, higher compute
- **Larger step** → fewer re-optimizations, lower compute

### Train/Test Ratio

- Common ratios: 80/20, 70/30, or fixed window lengths (e.g., 2 years train / 6 months test)
- Test window size should match how often you would re-optimize in live trading
- More OOS data generally improves robustness assessment

### Embargo

- Small gap between train end and test start to avoid information leakage from serial correlation
- Typical: 1–5 bars between training and test periods

---

## 3. Python Libraries

| Library | Walk-Forward Support | Notes |
|---------|---------------------|-------|
| **VectorBT** | ✅ `rolling_split()`, `ExpandingSplitter` | Vectorized, fast; `price.vbt.rolling_split(n=30, window_len=365*2, set_lens=(180,))` |
| **Backtesting.py** | ⚠️ Manual or via extensions | No built-in WFO; use TED Optimus or custom roll | 
| **bt** | ⚠️ Manual | Flexible tree structure; no native WFO |
| **PyBroker** | ✅ Built-in | `pybroker.walk_forward` for ML models |
| **QuantRocket Moonshot** | ✅ `ml_walkforward()` | `rolling_train="3Y"`, `train="Y"` for ML strategies |

**Recommendation:** VectorBT is best for large-scale parameter search; for your event-driven `evaluate_signal` setup, a custom rolling split is most practical.

---

## 4. Best Practices

### Window Sizing

- **Train window:** 1–3 years minimum; 2 years is common. Must cover at least one full market cycle.
- **Test window:** 1–6 months; 3–6 months typical. Match to re-optimization frequency.
- **Min cycles:** 5+ WFO cycles for meaningful results.

### Overfitting

- Use only OOS data to evaluate performance; never tune on test set.
- Prefer **parameter stability**: if many nearby values (e.g., N=48–52) perform similarly, that’s robust.
- Avoid “perfect” metrics (e.g., 90%+ win rate, 5+ profit factor).
- Consider K-Means clustering on optimal parameters across folds to identify regime clusters.

### Limitations

- Window selection bias: size and start point affect results.
- Regime changes: WFO adapts with a lag; performance can drop during regime shifts.
- Computational cost: multiple optimization + backtest runs per fold.

---

## 5. Code Patterns

### Pseudocode: Generic Walk-Forward

```python
def walk_forward_backtest(df, train_len, test_len, step, optimize_fn, backtest_fn):
    """Generic walk-forward loop."""
    results = []
    start = 0
    while start + train_len + test_len <= len(df):
        train_df = df.iloc[start : start + train_len]
        test_df = df.iloc[start + train_len : start + train_len + test_len]

        # 1. Optimize on train
        best_params = optimize_fn(train_df)

        # 2. Evaluate on test (out-of-sample)
        oos_result = backtest_fn(test_df, best_params)
        results.append(oos_result)

        start += step  # Rolling: step forward
    return results
```

### VectorBT Rolling Split

```python
import vectorbt as vbt

split_kwargs = dict(n=30, window_len=365*2, set_lens=(180,), left_to_right=False)
(in_price, in_indexes), (out_price, out_indexes) = price.vbt.rolling_split(**split_kwargs)
# Returns 30 windows: 2 years each, 180 days for OOS testing
```

### Time-Series Splitter (NumPy)

```python
class TimeSeriesSplitNumpy:
    """
    Rolling or expanding train/test indices for temporal data.
    """
    def __init__(self, n_splits=5, max_train_size=None, embargo_size=0):
        self.n_splits = n_splits
        self.max_train_size = max_train_size  # None = expanding; int = rolling
        self.embargo_size = embargo_size

    def split(self, data):
        n_samples = len(data)
        indices = np.arange(n_samples)
        n_test_samples = n_samples // (self.n_splits + 1)
        start_test_index = n_samples - self.n_splits * n_test_samples

        for k in range(self.n_splits):
            train_end_index = start_test_index - 1 - self.embargo_size
            if train_end_index < 0:
                continue

            train_indices = indices[:train_end_index + 1]
            test_indices = indices[start_test_index : start_test_index + n_test_samples]

            if self.max_train_size and len(train_indices) > self.max_train_size:
                train_indices = train_indices[-self.max_train_size:]  # Rolling

            yield train_indices, test_indices
            start_test_index += n_test_samples
```

---

## 6. Integration with Your Event-Driven Backtest

Your `run_backtest()` uses `evaluate_signal(df, symbol, ...)` bar-by-bar on `bar_df = df.iloc[:i+1]`. It is event-driven and fills at next bar open.

### Integration Strategy

1. **Data slicing:** Use date-based or index-based splits so each fold gets a contiguous `df` slice.
2. **Parameter optimization:** For each train fold, run a grid search over indicator params (e.g., `rsi_oversold`, `rsi_overbought`, `bb_period`) using `run_backtest` on the train slice.
3. **OOS evaluation:** Use the best params from that fold to run `run_backtest` on the test slice.
4. **Warmup:** Ensure `min_warmup` bars are available at the start of each train/test slice.

### Example Integration Pattern

```python
import itertools
import numpy as np
from src.backtest import run_backtest, BacktestResult, _compute_min_warmup
from src.data import fetch_single

def run_walk_forward_optimization(
    symbol: str,
    config: dict,
    period: str = "3y",
    train_bars: int = 252 * 2,   # 2 years
    test_bars: int = 63,          # ~3 months
    step_bars: int = 63,          # roll forward 3 months
    param_grid: dict | None = None,
) -> list[BacktestResult]:
    """
    Walk-forward optimization using your existing run_backtest.
    Requires run_backtest to accept optional df= parameter.
    """
    df = fetch_single(symbol, period=period)
    if df is None or len(df) < train_bars + test_bars:
        return []

    min_warmup = _compute_min_warmup(config)
    param_grid = param_grid or {
        "rsi_oversold": [30, 35, 40],
        "rsi_overbought": [60, 65, 70],
    }

    results = []
    start = min_warmup

    while start + train_bars + test_bars <= len(df):
        # Train slice: need full history up to train_end for bar-by-bar eval
        train_end = start + train_bars
        train_df = df.iloc[: train_end].copy()  # evaluate_signal needs bar_df = df.iloc[:i+1]

        # 1. Optimize on train (grid search)
        best_config = None
        best_metric = -np.inf
        keys = list(param_grid.keys())
        values = [v if isinstance(v, list) else [v] for v in param_grid.values()]

        for params in itertools.product(*values):
            ind_overrides = dict(zip(keys, params))
            cfg = {**config, "indicators": {**config.get("indicators", {}), **ind_overrides}}
            bt = run_backtest(symbol, config=cfg, df=train_df)  # Pass df for WFO
            if bt and bt.total_return > best_metric:
                best_metric = bt.total_return
                best_config = cfg

        # 2. OOS test: include warmup from train (no lookahead) + test period
        if best_config:
            test_start_idx = start + train_bars
            test_end_idx = start + train_bars + test_bars
            # Slice: [train_end - warmup : test_end] so indicators have history
            warmup_start = max(0, test_start_idx - min_warmup)
            test_df = df.iloc[warmup_start : test_end_idx].copy()
            oos = run_backtest(symbol, config=best_config, df=test_df)
            if oos:
                results.append(oos)

        start += step_bars

    return results
```

**Note:** To only score trades that occur in the test window, you may need to filter `BacktestResult.trades` by date or add a `start_date`/`end_date` filter to `run_backtest`.

### Important Cave: `run_backtest` Signature

Your current `run_backtest()` fetches its own data via `fetch_single()`. For WFO you need:

- Either: **Option A** – Add `df: pd.DataFrame | None = None` to `run_backtest`; when provided, use it instead of fetching.
- Or: **Option B** – Create a wrapper that slices `df` and passes only the relevant period (e.g., `period` derived from slice dates).

### Minimal Change for Option A

```python
def run_backtest(
    symbol: str,
    period: str = "1y",
    interval: str = "1d",
    config: dict | None = None,
    df: pd.DataFrame | None = None,  # NEW: if provided, use instead of fetch
    ...
) -> BacktestResult | None:
    if df is None:
        df = fetch_single(symbol, period=period, interval=interval)
    # ... rest unchanged
```

Then you can call `run_backtest(symbol, df=train_df, config=...)` for each fold.

---

## Summary

| Topic | Recommendation |
|-------|----------------|
| **Window type** | Rolling for regime adaptation; anchored if you need more history |
| **Train size** | 1–2 years for daily data |
| **Test size** | 1–3 months; match re-optimization frequency |
| **Step size** | Same as test size or half |
| **Libraries** | VectorBT for vectorized; custom for event-driven `evaluate_signal` |
| **Integration** | Add `df` param to `run_backtest`; implement rolling split loop; optimize on train, evaluate on test |

---

## References

- [Wikipedia: Walk forward optimization](https://en.wikipedia.org/wiki/Walk_forward_optimization)
- [QuantInsti: Walk-Forward Optimization Introduction](https://blog.quantinsti.com/walk-forward-optimization-introduction/)
- [VectorBT: rolling_split, splitters](https://vectorbt.dev/api/generic/splitters/)
- [Quant Beckman: Walk-Forward CVCL optimization (with code)](https://www.quantbeckman.com/p/with-code-walk-forward-cvcl-optimization)
