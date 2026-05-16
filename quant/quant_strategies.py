"""
Quant (non-intraday) strategy IDs aligned with DTB-style naming:

- Hybrid — MR + TF voting (``quant.hybrid.evaluate_hybrid``)
- Mean-Reversion (MR) — weighted indicator consensus
- Trend-Following (TF) — Donchian / ADX breakout path

Preset maps to the ``strategy`` field on the merged ticker config (``mr`` | ``tf`` | ``hybrid``).
"""

from __future__ import annotations

import copy
from typing import Literal

# IDs accepted by POST /api/quant/backtest as ``quant_strategy_id`` (quant mode only).
QuantStrategyId = Literal["hybrid", "mean_reversion", "trend_following"]

QUANT_STRATEGY_LABELS: dict[str, str] = {
    "hybrid": "Hybrid",
    "mean_reversion": "Mean-Reversion (MR)",
    "trend_following": "Trend-Following (TF)",
}

# Maps API id → config ``strategy`` value used by ``quant.backtest.run_backtest``.
_QUANT_TO_STRATEGY_KEY: dict[str, str] = {
    "hybrid": "hybrid",
    "mean_reversion": "mr",
    "trend_following": "tf",
}

ALLOWED_QUANT_STRATEGY_IDS: frozenset[str] = frozenset(_QUANT_TO_STRATEGY_KEY.keys())

# WFO CLI supports mr and tf only. hybrid is single-backtest-only: it merges MR+TF signals
# with a conflict resolution policy (see config.yaml hybrid.conflict_action) that does not
# map cleanly to a single-strategy parameter grid. To add hybrid WFO support, extend
# quant/walk_forward.py _run_fold to accept a merged MR+TF grid — tracked as a future item.
WFO_SUPPORTED_STRATEGIES: frozenset[str] = frozenset({"mr", "tf"})


def is_wfo_supported(strategy_id: str) -> bool:
    """Return True if walk-forward optimization supports this strategy.

    WFO supports ``mr`` (mean-reversion) and ``tf`` (trend-following).
    ``hybrid`` is excluded: it uses a combined MR+TF signal voting model that
    does not reduce to a single-strategy parameter grid for WFO fold optimization.
    """
    return strategy_id in WFO_SUPPORTED_STRATEGIES


def wfo_supported_for_quant_strategy_api_id(quant_strategy_id: str) -> bool:
    """True when ``quant_strategy_id`` maps to MR or TF (WFO-supported), not hybrid."""
    key = _QUANT_TO_STRATEGY_KEY.get((quant_strategy_id or "").strip())
    return bool(key and key in WFO_SUPPORTED_STRATEGIES)


def apply_quant_strategy_to_config(config: dict, quant_strategy_id: str | None) -> dict:
    """
    Return a copy of ``config`` with ``strategy`` set from ``quant_strategy_id``.
    If ``quant_strategy_id`` is None or empty, returns a deep copy unchanged (ticker profile wins).
    """
    out = copy.deepcopy(config)
    qid = (quant_strategy_id or "").strip()
    if not qid:
        return out
    if qid not in _QUANT_TO_STRATEGY_KEY:
        raise ValueError(f"Invalid quant_strategy_id: {quant_strategy_id!r}")
    out["strategy"] = _QUANT_TO_STRATEGY_KEY[qid]
    return out
