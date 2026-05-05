"""Tests for OOS trade-count / low-power heuristics."""

from __future__ import annotations

from src.backtest import BacktestResult
from src.walk_forward import WalkForwardResult, oos_trade_power_notes_from_results


def _bt(trades: int) -> BacktestResult:
    """Minimal backtest shell with a trade count."""
    return BacktestResult(
        symbol="T",
        total_return=0.0,
        buy_hold_return=0.0,
        num_trades=trades,
        win_rate=0.0,
        max_drawdown=0.0,
        trades=[],
        start_date="x",
        end_date="y",
        bar_returns=[],
        profit_factor=1.0,
    )


def test_oos_trade_power_empty_results() -> None:
    """No results yields no notes."""
    assert oos_trade_power_notes_from_results([], low_total_threshold=10, low_fold_threshold=2) == []


def test_oos_trade_power_total_below_threshold() -> None:
    """Total OOS trades below threshold triggers a note; thin folds add a second note."""
    results = [
        WalkForwardResult(oos_result=_bt(1), best_params={}, fold_index=0),
        WalkForwardResult(oos_result=_bt(2), best_params={}, fold_index=1),
    ]
    notes = oos_trade_power_notes_from_results(
        results,
        low_total_threshold=20,
        low_fold_threshold=3,
    )
    assert len(notes) == 2
    assert "total OOS trades 3" in notes[0]
    assert "fold(s) [0, 1]" in notes[1]


def test_oos_trade_power_clean() -> None:
    """Healthy counts produce no notes."""
    results = [
        WalkForwardResult(oos_result=_bt(12), best_params={}, fold_index=0),
        WalkForwardResult(oos_result=_bt(15), best_params={}, fold_index=1),
    ]
    assert (
        oos_trade_power_notes_from_results(
            results,
            low_total_threshold=20,
            low_fold_threshold=3,
        )
        == []
    )


def test_oos_trade_power_uses_fold_index_in_message() -> None:
    """Thin-fold note lists WFO fold_index, not positional index."""
    results = [
        WalkForwardResult(oos_result=_bt(10), best_params={}, fold_index=2),
        WalkForwardResult(oos_result=_bt(1), best_params={}, fold_index=5),
    ]
    notes = oos_trade_power_notes_from_results(
        results,
        low_total_threshold=5,
        low_fold_threshold=3,
    )
    assert any("[5]" in n for n in notes)
