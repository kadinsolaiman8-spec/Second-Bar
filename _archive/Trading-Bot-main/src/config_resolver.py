"""
Config resolver: per-ticker parameter selection (Option D: Hybrid).
Resolves config from ticker_profiles > asset_class_profiles > base config.
"""

from copy import deepcopy
from typing import Any

# Index ID -> asset class (used when index_id is known, e.g. from /market)
INDEX_TO_ASSET_CLASS: dict[str, str] = {
    "sp500": "us_large_cap",
    "dow_jones": "us_large_cap",
    "nasdaq": "us_tech",
    "dax": "european_index",
    "ftse100": "european_index",
    "cac40": "european_index",
}

# Known sector ETFs (static list; Phase 1)
SECTOR_ETFS: frozenset[str] = frozenset({
    "XLE", "XLI", "XLK", "XLF", "XLV", "XLY", "XLP", "XLB", "XBI", "XLU",
    "SMH", "KRE", "KBE", "XHB", "ITB", "VNQ", "IYR",
})

# Broad market ETFs
BROAD_ETFS: frozenset[str] = frozenset({
    "SPY", "QQQ", "IWM", "DIA", "VOO", "VTI", "IVV",
})


def _deep_merge(override: dict[str, Any], base: dict[str, Any]) -> dict[str, Any]:
    """
    Deep merge override into base. Nested dicts are merged by key; lists are replaced.
    Returns a new dict; does not mutate inputs.
    """
    result = deepcopy(base)
    for key, value in override.items():
        if key not in result:
            result[key] = deepcopy(value)
        elif isinstance(value, dict) and isinstance(result[key], dict):
            result[key] = _deep_merge(value, result[key])
        else:
            result[key] = deepcopy(value)
    return result


def classify_ticker(symbol: str, index_id: str | None = None) -> str:
    """
    Classify a ticker into an asset class for profile lookup.
    When index_id is provided (e.g. from /market), use INDEX_TO_ASSET_CLASS.
    Otherwise use static ticker lists.
    """
    sym = (symbol or "").upper().strip()
    if not sym:
        return "single_stock"

    if index_id:
        asset_class = INDEX_TO_ASSET_CLASS.get(index_id)
        if asset_class:
            return asset_class

    if sym in SECTOR_ETFS:
        return "sector_etf"
    if sym in BROAD_ETFS:
        return "broad_etf"

    return "single_stock"


def get_config_for_ticker(
    symbol: str,
    base_config: dict,
    timeframe: str | None = None,
    index_id: str | None = None,
) -> dict:
    """
    Resolve config for a ticker using hybrid resolution:
    1. Ticker profile (if exists)
    2. Asset-class profile (from classify_ticker)
    3. Base config

    Nested dicts (indicator_weights, etc.) are merged; top-level keys override.
    Returns a new config dict; does not mutate base_config.
    """
    ticker_profiles = base_config.get("ticker_profiles") or {}
    asset_class_profiles = base_config.get("asset_class_profiles") or {}

    sym = (symbol or "").upper().strip()

    # 1. Ticker profile
    if sym and sym in ticker_profiles:
        profile = ticker_profiles[sym]
        if profile:
            return _deep_merge(profile, base_config)

    # 2. Asset-class profile
    asset_class = classify_ticker(symbol, index_id)
    if asset_class in asset_class_profiles:
        profile = asset_class_profiles[asset_class]
        if profile:
            return _deep_merge(profile, base_config)

    # 3. Base config (return copy to avoid mutation)
    return deepcopy(base_config)
