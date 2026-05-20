"""
Trend-following signal logic: Donchian breakout with optional ADX filter.
Buy when close > Donchian upper; Sell when close < Donchian lower.
"""

from typing import Literal

import numpy as np
import pandas as pd

from quant.indicators import compute_adx, compute_atr, compute_donchian
from quant.signals import Signal, _compute_stop_tp_levels


def _breakout_min_len(donchian_period: int, atr_period: int, adx_period: int) -> int:
    return max(donchian_period, atr_period, adx_period) + 5


def _valid_close_count(close_arr: np.ndarray, i: int) -> int:
    if i < 0:
        return 0
    return int(np.sum(np.isfinite(close_arr[: i + 1])))


def precompute_breakout(
    df: pd.DataFrame,
    donchian_period: int = 20,
    atr_period: int = 14,
    adx_period: int = 14,
    adx_threshold: float | None = 25,
    config: dict | None = None,
) -> dict | None:
    """Precompute Donchian/ATR/ADX arrays aligned to df rows."""
    if df is None or df.empty or "Close" not in df.columns:
        return None
    if "High" not in df.columns or "Low" not in df.columns:
        return None

    config = config or {}
    tf_cfg = config.get("trend_following", {})
    donchian_period = tf_cfg.get("donchian_period", donchian_period)
    atr_period = tf_cfg.get("atr_period", atr_period)
    adx_period = tf_cfg.get("adx_period", adx_period)
    adx_threshold = tf_cfg.get("adx_threshold", adx_threshold)

    close = df["Close"].dropna()
    high = df["High"].reindex(close.index).ffill().bfill()
    low = df["Low"].reindex(close.index).ffill().bfill()

    min_len = _breakout_min_len(donchian_period, atr_period, adx_period)
    if len(close) < min_len:
        return None

    upper, lower = compute_donchian(high, low, period=donchian_period)
    upper_prev = upper.shift(1)
    lower_prev = lower.shift(1)
    atr_series = compute_atr(high, low, close, window=atr_period)
    atr_pct = (atr_series / close).replace(0, float("nan")) * 100

    adx_arr: np.ndarray | None = None
    if adx_threshold is not None:
        try:
            adx_series = compute_adx(high, low, close, period=adx_period)
            adx_arr = adx_series.reindex(df.index).to_numpy(copy=False)
        except (IndexError, ValueError):
            adx_threshold = None

    idx = df.index
    return {
        "close": close.reindex(idx).to_numpy(dtype=float, copy=False),
        "upper_prev": upper_prev.reindex(idx).to_numpy(dtype=float, copy=False),
        "lower_prev": lower_prev.reindex(idx).to_numpy(dtype=float, copy=False),
        "atr": atr_series.reindex(idx).to_numpy(dtype=float, copy=False),
        "atr_pct": atr_pct.reindex(idx).to_numpy(dtype=float, copy=False),
        "adx": adx_arr,
        "min_len": min_len,
        "n": len(df),
        "adx_threshold": adx_threshold,
    }


def breakout_signal_at(
    i: int,
    precomp: dict,
    symbol: str,
    df: pd.DataFrame,
    config: dict | None = None,
    macro_filter: "pd.Series | None" = None,
) -> Signal | None:
    """Trend-following signal at bar index i using precomputed arrays."""
    if i < 0 or i >= precomp["n"]:
        return None

    close_arr = precomp["close"]
    if _valid_close_count(close_arr, i) < precomp["min_len"]:
        return None

    close_val = float(close_arr[i])
    if not np.isfinite(close_val):
        return None

    upper_prev_val = precomp["upper_prev"][i]
    lower_prev_val = precomp["lower_prev"][i]
    if not np.isfinite(upper_prev_val) or not np.isfinite(lower_prev_val):
        return None

    atr_pct_arr = precomp["atr_pct"]
    atr_arr = precomp["atr"]
    atr_pct_val = float(atr_pct_arr[i]) if np.isfinite(atr_pct_arr[i]) else 0.0
    atr_val = float(atr_arr[i]) if np.isfinite(atr_arr[i]) else None

    adx_threshold = precomp.get("adx_threshold")
    adx_arr = precomp.get("adx")
    adx_val: float | None = None
    if adx_arr is not None:
        v = adx_arr[i]
        adx_val = float(v) if np.isfinite(v) else None

    if adx_threshold is not None and adx_val is not None and adx_val < adx_threshold:
        return None

    macro_blocks_buy = False
    if macro_filter is not None:
        current_date = df.index[i]
        try:
            macro_val = (
                macro_filter.asof(current_date)
                if hasattr(macro_filter, "asof")
                else macro_filter.get(current_date, True)
            )
            macro_blocks_buy = not bool(macro_val)
        except Exception:
            macro_blocks_buy = False

    config = config or {}

    if close_val > float(upper_prev_val) and not macro_blocks_buy:
        signal_type: Literal["Buy", "Sell", "Hold"] = "Buy"
        confidence = int(50 + (adx_val or 0) / 2) if adx_val is not None else 70
    elif close_val < float(lower_prev_val):
        signal_type = "Sell"
        confidence = int(50 + (adx_val or 0) / 2) if adx_val is not None else 70
    else:
        return None

    confidence = max(1, min(100, confidence))

    stop_price, take_profit_price, stop_pct = _compute_stop_tp_levels(
        signal_type=signal_type,
        price=close_val,
        atr=atr_val,
        atr_pct=atr_pct_val,
        config=config,
    )

    return Signal(
        symbol=symbol,
        signal_type=signal_type,
        confidence=confidence,
        rsi=0.0,
        macd_hist=0.0,
        price=close_val,
        atr_pct=atr_pct_val,
        net_score=None,
        weighted_scores=None,
        stop_price=stop_price,
        take_profit_price=take_profit_price,
        stop_pct=stop_pct,
    )


def evaluate_breakout_signal(
    df: pd.DataFrame,
    symbol: str,
    donchian_period: int = 20,
    atr_period: int = 14,
    adx_period: int = 14,
    adx_threshold: float | None = 25,
    config: dict | None = None,
    macro_filter: "pd.Series | None" = None,
) -> Signal | None:
    """
    Trend-following: Buy when close > Donchian upper; Sell when close < Donchian lower.
    Optional ADX filter: only trade when ADX > threshold (filters choppy regimes).
    Optional macro_filter: pd.Series of bool indexed by date — if the current bar's date
    resolves to False, Buy signals are suppressed (but Sell/exit signals pass through).
    Returns Signal or None if insufficient data.
    """
    if df is None or df.empty or "Close" not in df.columns:
        return None
    if "High" not in df.columns or "Low" not in df.columns:
        return None

    config = config or {}
    tf_cfg = config.get("trend_following", {})
    precomp = precompute_breakout(
        df,
        donchian_period=tf_cfg.get("donchian_period", donchian_period),
        atr_period=tf_cfg.get("atr_period", atr_period),
        adx_period=tf_cfg.get("adx_period", adx_period),
        adx_threshold=tf_cfg.get("adx_threshold", adx_threshold),
        config=config,
    )
    if precomp is None:
        return None
    return breakout_signal_at(
        len(df) - 1,
        precomp,
        symbol,
        df,
        config=config,
        macro_filter=macro_filter,
    )
