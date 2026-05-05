"""
Regime detection: VIX-based ensemble with SMA-200 fallback.

Replaces the blunt 200 SMA regime filter with VIX thresholds as the primary
signal, confirmed by SMA-200 position. Research shows VIX-based regime
filtering is the most out-of-sample robust method for routing between
mean-reversion and trend-following strategies.

Regime classification:
  - VIX < vix_low (default 18): low volatility, range-bound → MR favored
  - VIX > vix_high (default 25): high volatility, trends persist → TF favored
  - VIX between: mixed → SMA-200 as tiebreaker
"""

from dataclasses import dataclass

import pandas as pd


@dataclass
class RegimeState:
    vix: float | None
    vix_regime: str  # "low_vol", "normal", "high_vol", "unknown"
    sma_200_above: bool | None  # True if close > SMA-200, None if unavailable
    composite_regime: str  # "mr_favored", "mixed", "tf_favored"

    @property
    def weight_profile(self) -> str:
        """Map composite regime to indicator weight profile name."""
        return {
            "mr_favored": "bull",
            "tf_favored": "bear",
            "mixed": "mixed",
        }.get(self.composite_regime, "bull")

    @property
    def display_label(self) -> str:
        """Human-readable label for Discord embeds."""
        labels = {
            "mr_favored": "Low Vol (MR)",
            "tf_favored": "High Vol (TF)",
            "mixed": "Mixed",
        }
        label = labels.get(self.composite_regime, "Unknown")
        if self.vix is not None:
            label += f" | VIX {self.vix:.1f}"
        return label


def classify_regime(
    close: float,
    sma_200: float | None,
    vix: float | None = None,
    config: dict | None = None,
) -> RegimeState:
    """
    Classify the current market regime using VIX + SMA-200 ensemble.

    Priority:
      1. VIX thresholds (primary, parameter-light, robust)
      2. SMA-200 position (confirming / tiebreaker)
      3. Fallback to legacy SMA-200-only when VIX unavailable

    Args:
        close: Current price.
        sma_200: 200-period SMA value (None if not enough data).
        vix: Current VIX level (None if unavailable).
        config: Config dict with optional regime.vix_low / regime.vix_high.
    """
    cfg = (config or {}).get("regime", {})
    method = cfg.get("method", "vix_ensemble")
    vix_low = cfg.get("vix_low", 18)
    vix_high = cfg.get("vix_high", 25)

    # Determine SMA-200 position
    sma_200_above = None
    if sma_200 is not None and sma_200 > 0:
        sma_200_above = close > sma_200

    # Legacy mode: SMA-200 only
    if method == "sma_200" or vix is None:
        if sma_200_above is None:
            return RegimeState(
                vix=vix, vix_regime="unknown",
                sma_200_above=None, composite_regime="mixed",
            )
        composite = "mr_favored" if sma_200_above else "tf_favored"
        return RegimeState(
            vix=vix, vix_regime="unknown",
            sma_200_above=sma_200_above, composite_regime=composite,
        )

    # VIX ensemble mode
    if vix < vix_low:
        vix_regime = "low_vol"
        composite = "mr_favored"
    elif vix > vix_high:
        vix_regime = "high_vol"
        composite = "tf_favored"
    else:
        vix_regime = "normal"
        # Mixed zone: use SMA-200 as tiebreaker
        if sma_200_above is True:
            composite = "mr_favored"
        elif sma_200_above is False:
            composite = "tf_favored"
        else:
            composite = "mixed"

    return RegimeState(
        vix=vix, vix_regime=vix_regime,
        sma_200_above=sma_200_above, composite_regime=composite,
    )


def get_gold_macro_filter(
    dxy_series: pd.Series | None,
    tip_series: pd.Series | None,
    sma_period: int = 200,
) -> pd.Series | None:
    """
    Macro regime filter for GLD (gold) Donchian long signals.

    Gold is driven by real rates and USD strength, not equity VIX. Only take
    long Donchian breakouts when DXY is below its SMA-200 (weakening USD) OR
    TIP is above its SMA-200 (rising inflation expectations / falling real rates).
    Either condition is sufficient — both must be absent to block longs.

    Returns a pd.Series of bool indexed by date (True = macro allows longs).
    Returns None if both series are unavailable.

    Note: the DXY/real-rate relationship weakened 2022-2024 due to central bank
    gold buying. Test sub-periods independently when evaluating results.
    """
    if dxy_series is None and tip_series is None:
        return None

    index = None
    if dxy_series is not None and not dxy_series.empty:
        index = dxy_series.index
    if tip_series is not None and not tip_series.empty:
        index = tip_series.index if index is None else index.union(tip_series.index)

    if index is None or len(index) == 0:
        return None

    allow = pd.Series(False, index=index)

    if dxy_series is not None and not dxy_series.empty:
        dxy_aligned = dxy_series.reindex(index).ffill()
        dxy_sma = dxy_aligned.rolling(sma_period, min_periods=sma_period // 2).mean()
        allow = allow | (dxy_aligned < dxy_sma)

    if tip_series is not None and not tip_series.empty:
        tip_aligned = tip_series.reindex(index).ffill()
        tip_sma = tip_aligned.rolling(sma_period, min_periods=sma_period // 2).mean()
        allow = allow | (tip_aligned > tip_sma)

    return allow
