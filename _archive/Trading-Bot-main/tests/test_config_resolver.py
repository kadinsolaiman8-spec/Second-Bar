"""Tests for config_resolver merge and precedence."""

from __future__ import annotations

from src.config_resolver import get_config_for_ticker


def test_get_config_for_ticker_ticker_profile_overrides_base() -> None:
    """Ticker profile deep-merges over base; nested indicator_weights merged."""
    base = {
        "indicator_weights": {"rsi": 1.5, "trend": 1.5},
        "indicators": {"rsi_period": 14},
        "min_confidence": 60,
        "ticker_profiles": {
            "TEST": {
                "strategy": "mr",
                "indicator_weights": {"rsi": 99.0},
                "indicators": {"rsi_oversold": 25},
            },
        },
        "asset_class_profiles": {},
    }
    out = get_config_for_ticker("TEST", base, timeframe="Daily")
    assert out["indicator_weights"]["rsi"] == 99.0
    assert out["indicator_weights"]["trend"] == 1.5
    assert out["indicators"]["rsi_period"] == 14
    assert out["indicators"]["rsi_oversold"] == 25
    assert out["min_confidence"] == 60


def test_get_config_for_ticker_falls_back_to_asset_class() -> None:
    """When no ticker profile, asset_class profile applies."""
    base = {
        "foo": 1,
        "ticker_profiles": {},
        "asset_class_profiles": {
            "broad_etf": {"min_confidence": 10, "indicator_weights": {"rsi": 3.0}},
        },
    }
    out = get_config_for_ticker("SPY", base, timeframe="Daily")
    assert out["min_confidence"] == 10
    assert out["indicator_weights"]["rsi"] == 3.0
    assert out["foo"] == 1
