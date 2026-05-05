"""Tests for validation vs live routing warnings."""

from __future__ import annotations

import logging

import pytest

from src.validation_routing import warn_hybrid_vs_official_validation


def test_warn_hybrid_vs_official_logs_for_spy_hybrid(caplog: pytest.LogCaptureFixture) -> None:
    """Hybrid on MR-official ticker emits a warning."""
    prof = {"SPY": {"strategy": "hybrid", "min_net_score": 0.5}}
    with caplog.at_level(logging.WARNING):
        warn_hybrid_vs_official_validation(prof)
    assert caplog.records
    assert any("SPY" in r.getMessage() for r in caplog.records)


def test_warn_hybrid_vs_official_no_mr_warning_for_gld(caplog: pytest.LogCaptureFixture) -> None:
    """GLD official mode is TF — no 'official validation mode is mr' warning."""
    prof = {"GLD": {"strategy": "hybrid"}}
    with caplog.at_level(logging.WARNING):
        warn_hybrid_vs_official_validation(prof)
    assert not any("official validation mode is mr" in r.getMessage().lower() for r in caplog.records)
