"""Hybrid live vs backtest path parity (no network)."""

from __future__ import annotations

import numpy as np
import pandas as pd

from quant.hybrid import evaluate_hybrid, hybrid_signal_at
from quant.indicators import precompute_indicators
from quant.signals_trend import precompute_breakout


def _synthetic_daily_df(rows: int = 130) -> pd.DataFrame:
    rng = pd.date_range("2024-01-02", periods=rows, freq="B")
    rs = np.random.RandomState(7)
    close = 100.0 + np.cumsum(rs.randn(rows) * 0.8)
    open_ = np.roll(close, 1)
    open_[0] = close[0]
    high = np.maximum(open_, close) + rs.rand(rows) * 0.5
    low = np.minimum(open_, close) - rs.rand(rows) * 0.5
    vol = rs.randint(500_000, 2_000_000, rows)
    return pd.DataFrame(
        {"Open": open_, "High": high, "Low": low, "Close": close, "Volume": vol},
        index=rng,
    )


def test_evaluate_hybrid_matches_hybrid_signal_at_on_last_bar() -> None:
    df = _synthetic_daily_df()
    config = {"strategy": "hybrid", "trend_following": {"adx_threshold": 25}}
    ind = config.get("indicators", {})
    tf_cfg = config.get("trend_following", {})

    precomp_mr = precompute_indicators(df)
    precomp_tf = precompute_breakout(
        df,
        adx_threshold=tf_cfg.get("adx_threshold", 25),
        config=config,
    )
    assert precomp_mr is not None
    assert precomp_tf is not None

    live = evaluate_hybrid(df, "SYN", config=config)
    indexed = hybrid_signal_at(
        len(df) - 1,
        precomp_mr,
        precomp_tf,
        "SYN",
        df,
        config=config,
    )

    if live is None:
        assert indexed is None
        return

    assert indexed is not None
    assert live.signal_type == indexed.signal_type
    assert live.confidence == indexed.confidence


def test_hybrid_bar_by_bar_live_matches_indexed_path() -> None:
    df = _synthetic_daily_df()
    config = {"strategy": "hybrid", "trend_following": {"adx_threshold": 25}}

    precomp_mr = precompute_indicators(df)
    precomp_tf = precompute_breakout(df, adx_threshold=25, config=config)
    assert precomp_mr is not None
    assert precomp_tf is not None

    warmup = max(precomp_mr["min_len"], precomp_tf["min_len"])
    mismatches = 0
    for i in range(warmup, len(df)):
        slice_df = df.iloc[: i + 1]
        live = evaluate_hybrid(slice_df, "SYN", config=config)
        indexed = hybrid_signal_at(i, precomp_mr, precomp_tf, "SYN", df, config=config)
        live_type = live.signal_type if live else None
        indexed_type = indexed.signal_type if indexed else None
        if live_type != indexed_type:
            mismatches += 1

    assert mismatches == 0
