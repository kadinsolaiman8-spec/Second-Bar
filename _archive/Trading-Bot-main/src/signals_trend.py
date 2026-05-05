"""
Trend-following signal logic: Donchian breakout with optional ADX filter.
Buy when close > Donchian upper; Sell when close < Donchian lower.
"""

from typing import Literal

import pandas as pd

from src.indicators import compute_adx, compute_atr, compute_donchian
from src.signals import Signal, _compute_stop_tp_levels


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

    close = df["Close"].dropna()
    high = df["High"].reindex(close.index).ffill().bfill()
    low = df["Low"].reindex(close.index).ffill().bfill()

    min_len = max(donchian_period, atr_period, adx_period) + 5
    if len(close) < min_len:
        return None

    config = config or {}
    tf_cfg = config.get("trend_following", {})
    donchian_period = tf_cfg.get("donchian_period", donchian_period)
    atr_period = tf_cfg.get("atr_period", atr_period)
    adx_period = tf_cfg.get("adx_period", adx_period)
    adx_threshold = tf_cfg.get("adx_threshold", adx_threshold)

    upper, lower = compute_donchian(high, low, period=donchian_period)
    # Use previous bar's bands for breakout: close breaks above prior N-bar high or below prior N-bar low
    upper_prev = upper.shift(1)
    lower_prev = lower.shift(1)
    atr_series = compute_atr(high, low, close, window=atr_period)
    atr_pct = (atr_series / close).replace(0, float("nan")) * 100

    if adx_threshold is not None:
        try:
            adx_series = compute_adx(high, low, close, period=adx_period)
        except (IndexError, ValueError):
            adx_series = None
            adx_threshold = None
    else:
        adx_series = None

    close_val = float(close.iloc[-1])
    upper_prev_val = float(upper_prev.iloc[-1]) if pd.notna(upper_prev.iloc[-1]) else None
    lower_prev_val = float(lower_prev.iloc[-1]) if pd.notna(lower_prev.iloc[-1]) else None
    atr_pct_val = float(atr_pct.iloc[-1]) if pd.notna(atr_pct.iloc[-1]) else 0.0
    atr_val = float(atr_series.iloc[-1]) if pd.notna(atr_series.iloc[-1]) else None

    if upper_prev_val is None or lower_prev_val is None:
        return None

    adx_val = float(adx_series.iloc[-1]) if adx_series is not None and pd.notna(adx_series.iloc[-1]) else None

    if adx_threshold is not None and adx_val is not None and adx_val < adx_threshold:
        return None

    # Macro filter: suppress Buy signals when macro regime is unfavorable.
    # Sell/exit signals always pass through (don't trap existing long positions).
    macro_blocks_buy = False
    if macro_filter is not None and len(close) > 0:
        current_date = close.index[-1]
        try:
            macro_val = macro_filter.asof(current_date) if hasattr(macro_filter, "asof") else macro_filter.get(current_date, True)
            macro_blocks_buy = not bool(macro_val)
        except Exception:
            macro_blocks_buy = False

    # Breakout: close > prior upper (Buy) or close < prior lower (Sell)
    if close_val > upper_prev_val and not macro_blocks_buy:
        signal_type: Literal["Buy", "Sell", "Hold"] = "Buy"
        confidence = int(50 + (adx_val or 0) / 2) if adx_val is not None else 70
    elif close_val < lower_prev_val:
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
