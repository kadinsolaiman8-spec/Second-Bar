"""
Validation vs live routing: official WFO mode per ticker (scorecard) vs ticker_profiles.strategy.

See docs/wfo_batches/README.md and docs/CLAUDE_SONNET_REVIEW_VERIFIED_ROADMAP.md Q2.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Official hypothesis for the default WFO batch (run_wfo_batch.ps1); MR scorecard tickers.
OFFICIAL_VALIDATION_STRATEGY: dict[str, str] = {
    "GLD": "tf",
    "XLE": "tf",
    "GDX": "tf",
    "SPY": "mr",
    "QQQ": "mr",
    "IWM": "mr",
}


def warn_hybrid_vs_official_validation(
    ticker_profiles: dict[str, Any] | None,
) -> None:
    """
    Log a warning when a ticker uses live strategy ``hybrid`` but the official validation
    mode for that symbol is mean-reversion (MR). Does not change routing.
    """
    if not ticker_profiles:
        return
    for sym, prof in ticker_profiles.items():
        if not isinstance(prof, dict):
            continue
        if prof.get("strategy") != "hybrid":
            continue
        key = (sym or "").upper().strip()
        official = OFFICIAL_VALIDATION_STRATEGY.get(key)
        if official == "mr":
            logger.warning(
                "%s: ticker_profiles strategy is hybrid but official validation mode is %s — "
                "live signals may differ from WFO MR scorecard; see docs/wfo_batches/README.md",
                key,
                official,
            )
