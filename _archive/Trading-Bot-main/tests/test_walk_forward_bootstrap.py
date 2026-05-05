"""Tests for concatenated OOS stationary bootstrap helpers."""

from __future__ import annotations

import numpy as np
import pytest

from src.backtest import BacktestResult
from src.walk_forward import (
    WalkForwardResult,
    annualized_sharpe_from_bar_returns,
    concatenate_oos_bar_returns,
    run_stationary_bootstrap_oos_bar_returns,
)


def _minimal_bt(bar_returns: list[float] | None) -> BacktestResult:
    """Minimal BacktestResult for unit tests."""
    return BacktestResult(
        symbol="TEST",
        total_return=0.0,
        buy_hold_return=0.0,
        num_trades=0,
        win_rate=0.0,
        max_drawdown=0.0,
        trades=[],
        start_date="2020-01-01",
        end_date="2020-12-31",
        bar_returns=bar_returns,
        profit_factor=1.0,
    )


def test_concatenate_oos_bar_returns_stitches_folds() -> None:
    """Stitched bar_returns match fold order."""
    r1 = WalkForwardResult(
        oos_result=_minimal_bt([0.01, -0.005]),
        best_params={},
        fold_index=0,
    )
    r2 = WalkForwardResult(
        oos_result=_minimal_bt([0.002]),
        best_params={},
        fold_index=1,
    )
    assert concatenate_oos_bar_returns([r1, r2]) == [0.01, -0.005, 0.002]


def test_annualized_sharpe_from_bar_returns_zero_vol() -> None:
    """Zero volatility yields 0 Sharpe."""
    arr = np.array([1.0, 1.0, 1.0], dtype=np.float64)
    assert annualized_sharpe_from_bar_returns(arr, 252) == 0.0


def test_run_stationary_bootstrap_oos_bar_returns_smoke() -> None:
    """Bootstrap returns valid p-value in [0, 1] with fixed seed."""
    pytest.importorskip("arch")
    rng = np.random.default_rng(42)
    n = 80
    x = rng.standard_normal(n) * 0.01
    out = run_stationary_bootstrap_oos_bar_returns(
        x,
        n_samples=50,
        bars_per_year=252,
        alpha=0.05,
        seed=12345,
    )
    assert "error" not in out
    assert 0.0 <= out["p_value"] <= 1.0
    assert out["n_samples"] == 50
    assert "observed_sharpe" in out


def test_run_stationary_bootstrap_rejects_non_finite() -> None:
    """Non-finite inputs return an error dict."""
    pytest.importorskip("arch")
    bad = np.array([0.01, np.nan, 0.02])
    out = run_stationary_bootstrap_oos_bar_returns(
        bad,
        n_samples=5,
        bars_per_year=252,
        seed=1,
    )
    assert "error" in out


def test_run_stationary_bootstrap_insufficient_length() -> None:
    """Short series returns error."""
    pytest.importorskip("arch")
    out = run_stationary_bootstrap_oos_bar_returns(
        [0.01] * 10,
        n_samples=5,
        bars_per_year=252,
        seed=1,
    )
    assert "error" in out
