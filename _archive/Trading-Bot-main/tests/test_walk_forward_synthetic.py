"""Walk-forward invariants with synthetic OHLCV (no network)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src import walk_forward as wf


def _synthetic_ohlcv(n: int = 320, seed: int = 42) -> pd.DataFrame:
    """Minimal valid OHLCV for MR backtest."""
    rng = np.random.default_rng(seed)
    close = 100.0 + np.cumsum(rng.standard_normal(n) * 0.5)
    open_ = np.r_[close[0], close[:-1]]
    high = np.maximum(open_, close) + rng.random(n) * 0.3
    low = np.minimum(open_, close) - rng.random(n) * 0.3
    idx = pd.date_range("2020-01-01", periods=n, freq="B")
    return pd.DataFrame(
        {"Open": open_, "High": high, "Low": low, "Close": close, "Volume": rng.integers(1_000, 10_000, n)},
        index=idx,
    )


def test_run_walk_forward_optimization_synthetic_folds(monkeypatch: pytest.MonkeyPatch) -> None:
    """WFO returns folds when fetch_single is patched; embargo does not crash pipeline."""
    df = _synthetic_ohlcv(320)

    def _fake_fetch_single(*args: object, **kwargs: object) -> pd.DataFrame:
        return df

    monkeypatch.setattr(wf, "fetch_single", _fake_fetch_single)

    config = {
        "strategy": "mr",
        "indicators": {},
        "indicator_weights": {},
        "regime_filter": False,
    }
    param_grid = {"rsi_oversold": [30], "rsi_overbought": [70]}
    results = wf.run_walk_forward_optimization(
        "FAKE",
        config=config,
        period="2y",
        interval="1d",
        train_bars=55,
        test_bars=20,
        step_bars=35,
        embargo_bars=5,
        param_grid=param_grid,
        optimize_metric="profit_factor",
        timeframe="Daily",
        strategy="mr",
        vix_series=None,
    )
    assert len(results) >= 1
    for i, r in enumerate(results):
        assert r.fold_index == i
        assert r.oos_result.num_trades >= 0
