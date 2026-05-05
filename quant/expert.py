"""
Expert sentiment: weighted analyst input for consensus scoring.
Per-ticker sentiment from config ticker_profiles only. Acts as an indicator like news.
"""

from __future__ import annotations

import logging
import math

logger = logging.getLogger(__name__)


def _clamp_sentiment(value: float | None) -> float | None:
    """Clamp sentiment to [-1, 1]. Return None if invalid."""
    if value is None:
        return None
    try:
        v = float(value)
        return max(-1.0, min(1.0, v)) if not math.isnan(v) else None
    except (TypeError, ValueError):
        return None


def get_expert_sentiments(
    tickers: list[str],
    config: dict,
) -> dict[str, float]:
    """
    Return ticker -> sentiment (-1 to +1) for given tickers.
    Reads from config ticker_profiles only. Acts as an indicator like news_sentiment.
    """
    result: dict[str, float] = {}
    expert_cfg = config.get("expert_input", {})
    if not expert_cfg.get("enabled", True):
        return result

    profiles = config.get("ticker_profiles", {})
    for ticker in tickers:
        sym = ticker.upper().strip()
        if sym in profiles:
            val = profiles[sym].get("expert_sentiment")
            if val is not None:
                clamped = _clamp_sentiment(val)
                if clamped is not None:
                    result[sym] = clamped

    return result
