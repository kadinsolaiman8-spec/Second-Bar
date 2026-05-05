"""Statistical utilities for batch analysis (multiple-testing correction).

Used for post-WFO interpretation: Benjamini--Hochberg FDR and Bonferroni FWER
when combining **official-mode** p-values across tickers. Not used by the
Discord runtime.
"""

from __future__ import annotations


def benjamini_hochberg_adjusted_pvalues(p_values: list[float]) -> list[float]:
    """Compute Benjamini--Hochberg adjusted p-values (Benjamini & Hochberg, 1995).

    Reject hypothesis ``i`` when ``adjusted[i] <= q`` for a chosen FDR level
    ``q`` (e.g. 0.05).

    Args:
        p_values: Raw p-values in arbitrary order.

    Returns:
        Adjusted p-values at the same indices as ``p_values``.

    Raises:
        ValueError: If any p-value is outside ``[0, 1]``.
    """
    m = len(p_values)
    if m == 0:
        return []
    for p in p_values:
        if p < 0.0 or p > 1.0:
            msg = f"p-values must lie in [0, 1], got {p!r}"
            raise ValueError(msg)
    order = sorted(range(m), key=lambda i: p_values[i])
    sorted_p = [p_values[i] for i in order]
    temp = [sorted_p[i] * m / (i + 1) for i in range(m)]
    adj_sorted = [0.0] * m
    adj_sorted[m - 1] = min(temp[m - 1], 1.0)
    for i in range(m - 2, -1, -1):
        adj_sorted[i] = min(temp[i], adj_sorted[i + 1], 1.0)
    result = [0.0] * m
    for pos, idx in enumerate(order):
        result[idx] = adj_sorted[pos]
    return result


def bonferroni_per_test_alpha(familywise_alpha: float, num_tests: int) -> float:
    """Bonferroni per-hypothesis alpha controlling FWER at ``familywise_alpha``.

    Args:
        familywise_alpha: Desired family-wise error rate (e.g. 0.05).
        num_tests: Number of hypotheses (``m``).

    Returns:
        Per-test significance level ``familywise_alpha / num_tests``.

    Raises:
        ValueError: If ``num_tests < 1`` or ``familywise_alpha`` not in ``(0, 1]``.
    """
    if num_tests < 1:
        msg = "num_tests must be at least 1"
        raise ValueError(msg)
    if familywise_alpha <= 0.0 or familywise_alpha > 1.0:
        msg = "familywise_alpha must lie in (0, 1]"
        raise ValueError(msg)
    return familywise_alpha / num_tests
