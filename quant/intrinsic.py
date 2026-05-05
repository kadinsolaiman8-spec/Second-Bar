"""
Graham intrinsic value calculator.
Display-only — does not factor into Buy/Sell/Hold consensus signal.

Graham formula: V = EPS × (8.5 + 2g) × (4.4 / Y)
  - EPS: trailing 12-month earnings per share
  - g: expected earnings growth rate (decimal)
  - Y: current AAA corporate bond yield (decimal)
"""

import logging

logger = logging.getLogger(__name__)


def compute_graham_value(
    eps: float, growth_rate: float, bond_yield: float
) -> float | None:
    """Compute Graham intrinsic value per share.

    Returns None if inputs are invalid (negative EPS, zero bond yield, etc.).
    """
    if eps <= 0 or bond_yield <= 0:
        return None
    # Cap growth rate to avoid extreme values
    g = max(0.0, min(growth_rate * 100, 25.0))  # convert decimal to %, cap at 25%
    value = eps * (8.5 + 2 * g) * (4.4 / (bond_yield * 100))
    if value <= 0:
        return None
    return round(value, 2)


def classify_valuation(
    price: float, intrinsic: float
) -> tuple[str, float]:
    """Classify stock valuation vs Graham intrinsic value.

    Returns (label, margin_of_safety_pct).
    margin_of_safety_pct: positive = undervalued, negative = overvalued.
    """
    if intrinsic <= 0 or price <= 0:
        return ("N/A", 0.0)
    margin = (intrinsic - price) / intrinsic * 100
    if margin > 15:
        label = "Undervalued"
    elif margin < -15:
        label = "Overvalued"
    else:
        label = "Fair Value"
    return (label, round(margin, 1))
