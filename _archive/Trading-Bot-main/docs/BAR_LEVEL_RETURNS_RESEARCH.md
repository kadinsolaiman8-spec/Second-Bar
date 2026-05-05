# Bar-Level vs Per-Trade Returns for Backtesting Objective Functions

## Summary

For optimization and objective functions (Sharpe ratio, profit factor, etc.), **bar-level returns** are preferred over **per-trade returns** because they produce more stable, continuous metrics that are less sensitive to small parameter changes and trade count.

---

## 1. Bar-Level Returns Approach

### Definition

- **Bar-level returns**: Close-to-close returns at bar granularity (each bar = one return).
- **Strategy returns** = `position_signal * shifted_returns`

### Formula

```
returns[t] = (close[t] - close[t-1]) / close[t-1]   # or pct_change()
position[t] = signal[t-1]                            # shift(1) to avoid look-ahead
strategy_return[t] = position[t] * returns[t]
```

- `position`: 1 (long), -1 (short), 0 (flat).
- `returns`: bar-to-bar price returns.
- `position` is shifted so the decision at bar `t-1` is applied to returns from bar `t-1` to `t`.

### Implementation (Vectorized)

```python
import pandas as pd

def compute_bar_level_strategy_returns(
    df: pd.DataFrame,
    signal: pd.Series,
    price_col: str = "Close",
) -> pd.Series:
    """
    Compute strategy returns at bar granularity.
    signal: 1=long, -1=short, 0=flat (from strategy logic).
    """
    returns = df[price_col].pct_change()
    position = signal.shift(1)  # Trade on next bar using prior bar's signal
    strategy_return = position * returns
    return strategy_return
```

### Why More Stable

1. **Continuous time series**: One return per bar, regardless of trade count.
2. **Smooth objective functions**: Small parameter changes cause gradual changes in metrics.
3. **Sharpe ratio**: Uses standard deviation of returns over time; bar-level returns give a proper time-series volatility.
4. **Profit factor**: Can be derived from bar-level PnL; less jumpy than per-trade PnL when trade count changes.
5. **Optimization**: Gradients and grid search behave better with smoother objectives.

---

## 2. Per-Trade Returns Approach

### Definition

- **Per-trade returns**: PnL or return for each completed round-trip trade.
- Each trade has one return: `(exit_price - entry_price) / entry_price`.

### How It Works

```python
# Per-trade: one return per completed trade
trade_returns = [(t.exit_price - t.entry_price) / t.entry_price for t in trades]
```

### Characteristics

- **Discrete**: Number of returns = number of trades.
- **Sensitive to trade count**: Fewer trades → fewer samples → higher variance in metrics.
- **Sharpe from per-trade**: `mean(trade_returns) / std(trade_returns) * sqrt(N)` — treats each trade as one “period,” which misaligns with calendar time and volatility.

### Current Project Usage

Your `walk_forward.py` uses per-trade returns for Sharpe:

```python
# src/walk_forward.py lines 27-35
def _compute_sharpe(bt: BacktestResult, bars_per_year: int = 252) -> float:
    returns = np.array([t.pnl_pct / 100 for t in bt.trades])
    if len(returns) < 2 or np.std(returns) == 0:
        return 0.0
    return float(np.mean(returns) / np.std(returns) * np.sqrt(bars_per_year))
```

This uses trade PnL as the return series. With few trades, the Sharpe estimate is noisy and can jump sharply when one trade is added or removed.

---

## 3. Comparison

| Aspect | Bar-Level Returns | Per-Trade Returns |
|--------|-------------------|--------------------|
| **Granularity** | One return per bar | One return per trade |
| **Sample count** | Fixed (number of bars) | Variable (number of trades) |
| **Time alignment** | Aligned with calendar | Not aligned |
| **Sharpe stability** | More stable | Noisy with few trades |
| **Profit factor** | Stable (sum wins / sum losses over bars) | Can jump with trade count |
| **Optimization** | Smoother objective | Rugged, local optima |
| **Vectorization** | Easy (pandas/NumPy) | Requires trade loop |
| **Event-driven backtest** | Needs position series from trades | Natural output |

---

## 4. Implementation Details

### Bar-Level from Event-Driven Backtest

Your `run_backtest` is event-driven and outputs trades. To get bar-level returns:

1. Build a **position series** from trades (1 when long, 0 when flat).
2. Compute bar returns: `close.pct_change()`.
3. Apply: `strategy_return = position.shift(1) * returns`.

```python
def trades_to_position_series(df: pd.DataFrame, trades: list[Trade]) -> pd.Series:
    """Convert trade list to bar-level position series (1=long, 0=flat)."""
    position = pd.Series(0.0, index=df.index)
    for t in trades:
        # Long from entry bar through day before exit (exit at next bar open)
        entry_mask = df.index >= pd.Timestamp(t.entry_date)
        exit_mask = df.index < pd.Timestamp(t.exit_date)
        in_trade = entry_mask & exit_mask
        position.loc[in_trade] = 1.0
    return position

def compute_bar_level_metrics(df: pd.DataFrame, trades: list[Trade]) -> dict:
    """Compute Sharpe, profit factor from bar-level returns."""
    position = trades_to_position_series(df, trades)
    returns = df["Close"].pct_change()
    strategy_returns = position.shift(1) * returns
    strategy_returns = strategy_returns.dropna()

    if len(strategy_returns) < 2:
        return {"sharpe": 0.0, "profit_factor": 0.0}

    excess = strategy_returns - 0  # or subtract risk-free rate
    sharpe = float(excess.mean() / excess.std() * np.sqrt(252))

    wins = strategy_returns[strategy_returns > 0].sum()
    losses = strategy_returns[strategy_returns < 0].sum()
    profit_factor = wins / abs(losses) if losses != 0 else (float("inf") if wins > 0 else 0)

    return {"sharpe": sharpe, "profit_factor": profit_factor}
```

### Pure Vectorized (Signal-Based)

If the strategy is expressed as a signal (no stops, no complex exits):

```python
def vectorized_bar_level_backtest(
    df: pd.DataFrame,
    signal: pd.Series,
    price_col: str = "Close",
) -> pd.Series:
    returns = df[price_col].pct_change()
    position = signal.shift(1).fillna(0)
    return position * returns
```

### Per-Trade (Current Style)

```python
def per_trade_sharpe(trades: list[Trade], bars_per_year: int = 252) -> float:
    if len(trades) < 2:
        return 0.0
    returns = np.array([t.pnl_pct / 100 for t in trades])
    if np.std(returns) == 0:
        return 0.0
    return float(np.mean(returns) / np.std(returns) * np.sqrt(bars_per_year))
```

---

## 5. When to Use Each

### Use Bar-Level Returns When

- Optimizing parameters (grid search, walk-forward).
- Using Sharpe, profit factor, or similar as objective.
- You need stable, comparable metrics across parameter sets.
- You care about time-series volatility and risk-adjusted performance.

### Use Per-Trade Returns When

- Reporting trade-level stats (win rate, avg win/loss, trade distribution).
- Analyzing individual trade quality.
- The strategy has very few trades and bar-level returns would be mostly zeros.
- You need exact PnL per trade for position sizing or risk.

### Hybrid

- **Optimization**: Bar-level returns for objective (Sharpe, profit factor).
- **Reporting**: Per-trade stats (win rate, avg trade, max drawdown from equity curve).
- **Equity curve**: Build from bar-level strategy returns: `(1 + strategy_returns).cumprod()`.

---

## 6. Reference

**Testing and Tuning Market Trading Systems: Algorithms in C++** (Timothy Masters, Apress 2018) emphasizes:

- Rigorous statistical validation over raw backtest PnL.
- Walk-forward analysis and nested validation.
- Avoiding overfitting and selection bias.
- Bar-level (or equivalent time-aligned) returns for objective functions to keep optimization stable.

The pattern `strategy_return = position_signal * shifted_returns` is standard in vectorized backtesting and aligns with Masters’ focus on stable, statistically sound optimization.

---

## 7. Recommendation for This Project

1. **Add bar-level metrics** to `BacktestResult` or a separate helper, using `trades_to_position_series` + `strategy_return = position.shift(1) * returns`.
2. **Use bar-level Sharpe** in `walk_forward.py` when `optimize_metric == "sharpe"` instead of per-trade Sharpe.
3. **Keep per-trade stats** (win rate, num_trades, total_return) for reporting.
4. **Optionally** add `profit_factor` from bar-level returns as an optimization metric.
