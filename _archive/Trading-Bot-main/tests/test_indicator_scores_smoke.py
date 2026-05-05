"""Smoke tests for indicator_scores (no DataFrame fetch)."""

from __future__ import annotations

from src.indicator_scores import WeightedScores, compute_weighted_scores


def test_compute_weighted_scores_neutral_inputs() -> None:
    """Neutral indicator snapshot yields finite WeightedScores."""
    indicators = {
        "rsi": 50.0,
        "macd_hist": 0.0,
        "close": 100.0,
        "bb_lower": 90.0,
        "bb_upper": 110.0,
        "supertrend_direction": 0,
        "stoch_k": 50.0,
        "williams_r": -50.0,
        "ema_bullish": 0,
        "sma_200": 100.0,
    }
    config: dict = {"regime_filter": False}
    ws = compute_weighted_scores(
        indicators,
        config,
        ignore_volatility=True,
        timeframe="Daily",
    )
    assert isinstance(ws, WeightedScores)
    assert ws.total_weight >= 0
    assert isinstance(ws.net_score, float)
