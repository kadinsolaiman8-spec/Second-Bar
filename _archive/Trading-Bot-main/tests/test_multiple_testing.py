"""Tests for Benjamini--Hochberg and Bonferroni helpers."""

from __future__ import annotations

import pytest

from src.stats_utils import (
    benjamini_hochberg_adjusted_pvalues,
    bonferroni_per_test_alpha,
)


def test_benjamini_hochberg_manual_example() -> None:
    """BH adjusted p-values for a small ordered example (hand-checked step-up)."""
    p_values = [0.01, 0.04, 0.10]
    adj = benjamini_hochberg_adjusted_pvalues(p_values)
    assert len(adj) == 3
    assert adj == pytest.approx([0.03, 0.06, 0.1])
    assert all(adj[i] >= p_values[i] for i in range(3))


def test_benjamini_hochberg_preserves_empty() -> None:
    """Empty input yields empty output."""
    assert benjamini_hochberg_adjusted_pvalues([]) == []


def test_benjamini_hochberg_rejects_invalid_p() -> None:
    """p outside [0, 1] raises ValueError."""
    with pytest.raises(ValueError, match="p-values must lie"):
        benjamini_hochberg_adjusted_pvalues([-0.01])
    with pytest.raises(ValueError, match="p-values must lie"):
        benjamini_hochberg_adjusted_pvalues([1.1])


def test_bonferroni_per_test_alpha() -> None:
    """Bonferroni scales family-wise alpha by m."""
    assert bonferroni_per_test_alpha(0.05, 5) == pytest.approx(0.01)


def test_bonferroni_invalid() -> None:
    """Invalid inputs raise ValueError."""
    with pytest.raises(ValueError, match="num_tests"):
        bonferroni_per_test_alpha(0.05, 0)
    with pytest.raises(ValueError, match="familywise_alpha"):
        bonferroni_per_test_alpha(0.0, 3)
