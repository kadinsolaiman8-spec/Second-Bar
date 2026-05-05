"""
Shared indicator scoring: weighted buy/sell scores for consensus-based confidence
and /stock display. Single source of truth for indicator logic (MACD+ST consolidated).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.regime import RegimeState

DEFAULT_WEIGHTS = {
    "rsi": 1.5,
    "bollinger": 1.0,
    "trend": 1.5,
    "stochastic": 1.0,
    "williams_r": 1.0,
    "ema": 1.2,
    "volatility": 0.8,
    "news": 1.0,
    "expert": 1.0,
    "vwap": 1.0,
    "obv": 0.8,
    "cmf": 0.6,
}


@dataclass
class WeightedScores:
    buy_weight: float
    sell_weight: float
    net_score: float
    total_weight: float
    max_possible_weight: float  # sum of all indicator weights (for consensus denominator)
    breakdown: list[tuple[str, float, str]]  # (label, weight, "buy"|"sell")
    checkmarks: list[str]
    buy_count: int
    sell_count: int


def _get_weights(
    config: dict,
    timeframe: str | None = None,
    regime: str | None = None,
) -> dict[str, float]:
    """Get indicator weights from config, with defaults.
    When timeframe is set, uses timeframe_indicator_weights[timeframe] merged over DEFAULT_WEIGHTS.
    When regime is 'bull' or 'bear' and regime_filter is on, uses regime_indicator_weights[regime].
    """
    if regime and config.get("regime_filter", False):
        regime_weights = config.get("regime_indicator_weights", {}).get(regime, {})
        if regime_weights:
            base = {k: regime_weights.get(k, v) for k, v in DEFAULT_WEIGHTS.items()}
            # Allow indicator_weights to override (e.g. WFO rsi_weight, trend_weight)
            overrides = config.get("indicator_weights", {})
            for k in base:
                if k in overrides:
                    base[k] = overrides[k]
            return base
    if timeframe:
        tf_weights = config.get("timeframe_indicator_weights", {}).get(timeframe, {})
        if tf_weights:
            return {k: tf_weights.get(k, v) for k, v in DEFAULT_WEIGHTS.items()}
    weights_cfg = config.get("indicator_weights", {})
    return {k: weights_cfg.get(k, v) for k, v in DEFAULT_WEIGHTS.items()}


def _get_config_value(config: dict, key: str, default: float) -> float:
    """Get value from config, checking both top-level and indicators section."""
    if key in config:
        return config[key]
    return config.get("indicators", {}).get(key, default)


def compute_weighted_scores(
    indicators: dict,
    config: dict,
    ignore_volatility: bool = False,
    news_sentiment: float | None = None,
    expert_sentiment: float | None = None,
    timeframe: str | None = None,
    regime_state: RegimeState | None = None,
) -> WeightedScores:
    """
    Compute weighted buy/sell scores from raw indicators.
    Uses stock.py-style consolidated logic (MACD+SuperTrend as one slot).
    Mixed MACD+ST -> 0.75 buy, 0.75 sell.
    news_sentiment: optional -1 to +1; when provided and news weight > 0, adds to buy/sell.
    expert_sentiment: optional -1 to +1; when provided and expert weight > 0, adds to buy/sell.
    timeframe: optional Daily/1W/1H; when set, uses per-timeframe indicator weights.
    regime_state: optional RegimeState from src.regime; when provided, uses VIX-based
        regime classification. Falls back to legacy SMA-200 when not provided.
    """
    rsi_oversold = _get_config_value(config, "rsi_oversold", 35)
    rsi_overbought = _get_config_value(config, "rsi_overbought", 65)
    stoch_oversold = _get_config_value(config, "stoch_oversold", 20)
    stoch_overbought = _get_config_value(config, "stoch_overbought", 80)
    willr_oversold = _get_config_value(config, "willr_oversold", -80)
    willr_overbought = _get_config_value(config, "willr_overbought", -20)

    rsi = indicators["rsi"]
    macd_hist = indicators["macd_hist"]
    close = indicators["close"]

    # Regime determination: use external RegimeState if provided, else legacy SMA-200
    if regime_state is not None:
        regime = regime_state.weight_profile  # "bull", "bear", or "mixed"
        regime_suppress_mr_buys = regime_state.composite_regime == "tf_favored"
    else:
        # Legacy fallback: inline SMA-200 regime detection
        sma_200 = indicators.get("sma_200", float("nan"))
        is_sma_nan = sma_200 is None or (
            isinstance(sma_200, float) and math.isnan(sma_200)
        )
        regime_filter = config.get("regime_filter", False)
        if is_sma_nan:
            regime_suppress_mr_buys = False
            regime = None
        else:
            regime_suppress_mr_buys = regime_filter and (close < sma_200)
            regime = "bear" if regime_suppress_mr_buys else "bull"

    weights = _get_weights(config, timeframe=timeframe, regime=regime)

    bb_lower = indicators["bb_lower"]
    bb_upper = indicators["bb_upper"]
    st_direction = indicators.get("supertrend_direction", 0)
    stoch_k = indicators.get("stoch_k", 50)
    williams_r = indicators.get("williams_r", -50)
    ema_bullish = indicators.get("ema_bullish", 0)

    buy_weight = 0.0
    sell_weight = 0.0
    buy_count = 0
    sell_count = 0
    breakdown: list[tuple[str, float, str]] = []
    checkmarks: list[str] = []

    # RSI (mean-reversion: gate buy when bear regime)
    w = weights["rsi"]
    if rsi < rsi_oversold:
        if not regime_suppress_mr_buys:
            buy_weight += w
            buy_count += 1
            breakdown.append(("RSI", w, "buy"))
            checkmarks.append("• Momentum oversold (may bounce)")
    elif rsi > rsi_overbought:
        sell_weight += w
        sell_count += 1
        breakdown.append(("RSI", w, "sell"))
        checkmarks.append("• Momentum overbought (may pull back)")

    # Bollinger Bands (mean-reversion: gate buy when bear regime)
    w = weights["bollinger"]
    if close <= bb_lower * 1.002:
        if not regime_suppress_mr_buys:
            buy_weight += w
            buy_count += 1
            breakdown.append(("BB", w, "buy"))
            checkmarks.append("• Price near support")
    elif close >= bb_upper * 0.998:
        sell_weight += w
        sell_count += 1
        breakdown.append(("BB", w, "sell"))
        checkmarks.append("• Price near resistance")

    # MACD + SuperTrend combined
    w_full = weights["trend"]
    w_half = w_full / 2
    macd_bullish = macd_hist > 0
    macd_bearish = macd_hist < 0
    st_bullish = st_direction == 1
    st_bearish = st_direction == -1

    if macd_bullish and st_bullish:
        buy_weight += w_full
        buy_count += 2
        breakdown.append(("Trend", w_full, "buy"))
        checkmarks.append("• Trend bullish")
    elif macd_bearish and st_bearish:
        sell_weight += w_full
        sell_count += 2
        breakdown.append(("Trend", w_full, "sell"))
        checkmarks.append("• Trend bearish")
    elif macd_bullish and st_bearish:
        buy_weight += w_half
        sell_weight += w_half
        buy_count += 1
        sell_count += 1
        breakdown.append(("Trend", w_half, "buy"))
        breakdown.append(("Trend", w_half, "sell"))
        checkmarks.append("• Momentum turning up (trend still bearish)")
    elif macd_bearish and st_bullish:
        buy_weight += w_half
        sell_weight += w_half
        buy_count += 1
        sell_count += 1
        breakdown.append(("Trend", w_half, "buy"))
        breakdown.append(("Trend", w_half, "sell"))
        checkmarks.append("• Momentum turning down (trend still bullish)")

    # Stochastic (mean-reversion: gate buy when bear regime)
    w = weights["stochastic"]
    if stoch_k < stoch_oversold:
        if not regime_suppress_mr_buys:
            buy_weight += w
            buy_count += 1
            breakdown.append(("Stoch", w, "buy"))
            if "Momentum oversold" not in " ".join(checkmarks):
                checkmarks.append("• Momentum oversold (may bounce)")
    elif stoch_k > stoch_overbought:
        sell_weight += w
        sell_count += 1
        breakdown.append(("Stoch", w, "sell"))
        if "Momentum overbought" not in " ".join(checkmarks):
            checkmarks.append("• Momentum overbought (may pull back)")

    # Williams %R (mean-reversion: gate buy when bear regime)
    w = weights["williams_r"]
    if williams_r < willr_oversold:
        if not regime_suppress_mr_buys:
            buy_weight += w
            buy_count += 1
            breakdown.append(("WillR", w, "buy"))
            if "Momentum oversold" not in " ".join(checkmarks):
                checkmarks.append("• Momentum oversold (may bounce)")
    elif williams_r > willr_overbought:
        sell_weight += w
        sell_count += 1
        breakdown.append(("WillR", w, "sell"))
        if "Momentum overbought" not in " ".join(checkmarks):
            checkmarks.append("• Momentum overbought (may pull back)")

    # EMA
    w = weights["ema"]
    if ema_bullish == 1:
        buy_weight += w
        buy_count += 1
        breakdown.append(("EMA", w, "buy"))
        checkmarks.append("• Short-term trend up")
    elif ema_bullish == -1:
        sell_weight += w
        sell_count += 1
        breakdown.append(("EMA", w, "sell"))
        checkmarks.append("• Short-term trend down")

    # Volatility
    if not ignore_volatility:
        w = weights["volatility"]
        atr_pct_vs_avg = indicators.get("atr_pct_vs_avg", 0.0)
        if atr_pct_vs_avg < -0.3:
            buy_weight += w
            buy_count += 1
            breakdown.append(("Vol", w, "buy"))
            checkmarks.append("• Volatility low (breakout setup)")
        elif atr_pct_vs_avg > 0.3:
            sell_weight += w
            sell_count += 1
            breakdown.append(("Vol", w, "sell"))
            checkmarks.append("• Volatility high (caution)")

    # News (optional; only when news_sentiment provided and weight > 0)
    news_weight_val = weights.get("news", 0)
    if news_sentiment is not None and news_weight_val > 0:
        if news_sentiment > 0.1:
            buy_weight += news_weight_val
            buy_count += 1
            breakdown.append(("News", news_weight_val, "buy"))
            checkmarks.append("• News sentiment bullish")
        elif news_sentiment < -0.1:
            sell_weight += news_weight_val
            sell_count += 1
            breakdown.append(("News", news_weight_val, "sell"))
            checkmarks.append("• News sentiment bearish")

    # Expert (optional; only when expert_sentiment provided and weight > 0)
    expert_weight_val = weights.get("expert", 0)
    if expert_sentiment is not None and expert_weight_val > 0:
        if expert_sentiment > 0.1:
            buy_weight += expert_weight_val
            buy_count += 1
            breakdown.append(("Expert", expert_weight_val, "buy"))
            checkmarks.append("• Expert view bullish")
        elif expert_sentiment < -0.1:
            sell_weight += expert_weight_val
            sell_count += 1
            breakdown.append(("Expert", expert_weight_val, "sell"))
            checkmarks.append("• Expert view bearish")

    # Volume indicators: VWAP, OBV, CMF — NOT regime-gated (orthogonal to price oscillators)
    vwap_deviation = indicators.get("vwap_deviation")
    vwap_threshold = _get_config_value(config, "vwap_deviation_threshold", 1.5)
    if vwap_deviation is not None:
        w = weights.get("vwap", 0)
        if w > 0:
            if vwap_deviation < -vwap_threshold:
                buy_weight += w
                buy_count += 1
                breakdown.append(("VWAP", w, "buy"))
                checkmarks.append("• Price below VWAP (volume-weighted support)")
            elif vwap_deviation > vwap_threshold:
                sell_weight += w
                sell_count += 1
                breakdown.append(("VWAP", w, "sell"))
                checkmarks.append("• Price above VWAP (volume-weighted resistance)")

    obv_divergence = indicators.get("obv_divergence")
    if obv_divergence is not None and obv_divergence != 0:
        w = weights.get("obv", 0)
        if w > 0:
            if obv_divergence == 1:
                buy_weight += w
                buy_count += 1
                breakdown.append(("OBV", w, "buy"))
                checkmarks.append("• Volume divergence bullish (accumulation)")
            elif obv_divergence == -1:
                sell_weight += w
                sell_count += 1
                breakdown.append(("OBV", w, "sell"))
                checkmarks.append("• Volume divergence bearish (distribution)")

    cmf_val = indicators.get("cmf")
    cmf_threshold = _get_config_value(config, "cmf_threshold", 0.1)
    if cmf_val is not None:
        w = weights.get("cmf", 0)
        if w > 0:
            if cmf_val > cmf_threshold:
                buy_weight += w
                buy_count += 1
                breakdown.append(("CMF", w, "buy"))
                checkmarks.append("• Money flow positive (buying pressure)")
            elif cmf_val < -cmf_threshold:
                sell_weight += w
                sell_count += 1
                breakdown.append(("CMF", w, "sell"))
                checkmarks.append("• Money flow negative (selling pressure)")

    net_score = buy_weight - sell_weight
    total_weight = buy_weight + sell_weight
    max_possible_weight = sum(
        w for k, w in weights.items()
        if not (ignore_volatility and k == "volatility")
        and not (k == "news" and news_sentiment is None)
        and not (k == "expert" and expert_sentiment is None)
        and not (k == "vwap" and vwap_deviation is None)
        and not (k == "obv" and (obv_divergence is None or obv_divergence == 0))
        and not (k == "cmf" and cmf_val is None)
    )

    return WeightedScores(
        buy_weight=buy_weight,
        sell_weight=sell_weight,
        net_score=net_score,
        total_weight=total_weight,
        max_possible_weight=max_possible_weight,
        breakdown=breakdown,
        checkmarks=checkmarks,
        buy_count=buy_count,
        sell_count=sell_count,
    )
