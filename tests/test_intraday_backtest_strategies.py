"""Unit tests for intraday day-trading backtest branch (no network)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from quant.backtest import run_backtest
from quant.intraday_backtest_strategies import (
    ALLOWED_DAY_STRATEGY_IDS,
    day_trade_signal_at,
    precompute_intraday,
)


def _synthetic_hourly_df(rows: int = 160) -> pd.DataFrame:
    rng = pd.date_range("2025-01-02 09:30", periods=rows, freq="1h", tz="America/New_York")
    rs = np.random.RandomState(42)
    close = 100.0 + np.cumsum(rs.randn(rows) * 0.15)
    open_ = np.roll(close, 1)
    open_[0] = close[0]
    high = np.maximum(open_, close) + rs.rand(rows) * 0.4
    low = np.minimum(open_, close) - rs.rand(rows) * 0.4
    vol = rs.randint(800_000, 4_000_000, rows)
    return pd.DataFrame(
        {"Open": open_, "High": high, "Low": low, "Close": close, "Volume": vol},
        index=rng,
    )


@pytest.mark.parametrize("strategy_id", sorted(ALLOWED_DAY_STRATEGY_IDS))
def test_run_backtest_day_trading_each_strategy(strategy_id: str) -> None:
    df = _synthetic_hourly_df()
    result = run_backtest(
        "SYN",
        period="60d",
        interval="1h",
        config={"backtest": {"commission_pct": 0.0, "slippage_pct": 0.0}},
        df=df,
        trading_mode="day_trading",
        day_strategy_id=strategy_id,
    )
    assert result is not None
    assert result.symbol == "SYN"
    assert result.num_trades >= 0


def test_day_trade_signal_uses_distinct_rules() -> None:
    """Sanity check: not all strategies share identical trigger logic on a fixed path."""
    df = _synthetic_hourly_df(200)
    pre_orb = precompute_intraday(df, "opening_range_breakout")
    pre_rb = precompute_intraday(df, "range_breakout")
    warmup = max(pre_orb["min_warmup"], pre_rb["min_warmup"])
    hits_orb = sum(
        1
        for i in range(warmup, len(df) - 1)
        if day_trade_signal_at(i, pre_orb, "X", df) is not None
    )
    hits_rb = sum(
        1
        for i in range(warmup, len(df) - 1)
        if day_trade_signal_at(i, pre_rb, "X", df) is not None
    )
    assert hits_orb != hits_rb
