"""
Technical indicators: RSI, MACD, Bollinger Bands, SuperTrend, Stochastic, Williams %R, EMA,
VWAP, OBV, CMF. Uses the ta library (pure Python, no TA-Lib C dependency).
"""

import numpy as np
import pandas as pd
from ta.momentum import RSIIndicator, StochasticOscillator, WilliamsRIndicator
from ta.trend import ADXIndicator, MACD, EMAIndicator
from ta.volatility import BollingerBands, AverageTrueRange
from ta.volume import ChaikinMoneyFlowIndicator, OnBalanceVolumeIndicator, VolumeWeightedAveragePrice


def compute_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Compute RSI. Returns series; last value is most recent."""
    rsi = RSIIndicator(close=close, window=period)
    return rsi.rsi()


def compute_macd(
    close: pd.Series,
    window_slow: int = 26,
    window_fast: int = 12,
    window_sign: int = 9,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """
    Compute MACD line, signal line, and histogram.
    Returns (macd_line, signal_line, histogram).
    """
    macd = MACD(
        close=close,
        window_slow=window_slow,
        window_fast=window_fast,
        window_sign=window_sign,
    )
    return macd.macd(), macd.macd_signal(), macd.macd_diff()


def compute_atr(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    window: int = 14,
) -> pd.Series:
    """
    Compute Average True Range (ATR).
    Returns series; last value is most recent.
    """
    atr_ind = AverageTrueRange(high=high, low=low, close=close, window=window)
    return atr_ind.average_true_range()


def compute_atr_pct(close: pd.Series, atr: pd.Series) -> pd.Series:
    """
    Compute ATR as percentage of price: ATR / Close * 100.
    """
    return (atr / close).replace(0, float("nan")) * 100


def compute_sma_200(close: pd.Series) -> pd.Series:
    """Compute 200-period simple moving average. Returns series; first 199 values are NaN."""
    return close.rolling(200).mean()


def compute_donchian(
    high: pd.Series,
    low: pd.Series,
    period: int = 20,
) -> tuple[pd.Series, pd.Series]:
    """
    Compute Donchian channel: upper = rolling max of high, lower = rolling min of low.
    Returns (upper, lower).
    """
    upper = high.rolling(window=period, min_periods=period).max()
    lower = low.rolling(window=period, min_periods=period).min()
    return upper, lower


def compute_adx(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    period: int = 14,
) -> pd.Series:
    """
    Compute ADX (Average Directional Index). Used to filter choppy regimes.
    ADX > 20-25 suggests trending; below suggests ranging.
    """
    adx_ind = ADXIndicator(high=high, low=low, close=close, window=period)
    return adx_ind.adx()


def compute_bollinger(
    close: pd.Series,
    window: int = 20,
    window_dev: float = 2.0,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """
    Compute Bollinger Bands: upper, middle (SMA), lower.
    Returns (upper, middle, lower).
    """
    bb = BollingerBands(close=close, window=window, window_dev=window_dev)
    return bb.bollinger_hband(), bb.bollinger_mavg(), bb.bollinger_lband()


def compute_stochastic(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    window: int = 14,
    smooth_window: int = 3,
) -> tuple[pd.Series, pd.Series]:
    """
    Compute Stochastic Oscillator.
    Returns (stoch_k, stoch_d) where stoch_k is %K and stoch_d is %D (signal).
    """
    stoch = StochasticOscillator(
        high=high, low=low, close=close, window=window, smooth_window=smooth_window
    )
    return stoch.stoch(), stoch.stoch_signal()


def compute_williams_r(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    lbp: int = 14,
) -> pd.Series:
    """
    Compute Williams %R. Oscillates from 0 to -100.
    Oversold < -80, overbought > -20.
    """
    willr = WilliamsRIndicator(high=high, low=low, close=close, lbp=lbp)
    return willr.williams_r()


def compute_ema_crossover(
    close: pd.Series,
    fast: int = 9,
    slow: int = 21,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """
    Compute EMA crossover. Returns (ema_fast, ema_slow, ema_bullish).
    ema_bullish: 1 when fast > slow, -1 when fast < slow.
    """
    ema_fast_ind = EMAIndicator(close=close, window=fast)
    ema_slow_ind = EMAIndicator(close=close, window=slow)
    ema_fast = ema_fast_ind.ema_indicator()
    ema_slow = ema_slow_ind.ema_indicator()
    ema_bullish = pd.Series(0, index=close.index)
    ema_bullish[ema_fast > ema_slow] = 1
    ema_bullish[ema_fast < ema_slow] = -1
    return ema_fast, ema_slow, ema_bullish


def compute_supertrend(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    period: int = 10,
    multiplier: float = 3.0,
) -> tuple[pd.Series, pd.Series]:
    """
    Compute SuperTrend indicator.
    Returns (supertrend_value, supertrend_direction) where direction is 1=bullish, -1=bearish.
    Bullish: close above SuperTrend line. Bearish: close below SuperTrend line.
    """
    atr_ind = AverageTrueRange(high=high, low=low, close=close, window=period)
    atr = atr_ind.average_true_range()

    hl2 = (high + low) / 2
    basic_ub = hl2 + multiplier * atr
    basic_lb = hl2 - multiplier * atr

    n = len(close)
    final_ub = [0.0] * n
    final_lb = [0.0] * n
    supertrend = [0.0] * n

    for i in range(n):
        if i < period:
            final_ub[i] = float(basic_ub.iloc[i]) if i < len(basic_ub) else 0.0
            final_lb[i] = float(basic_lb.iloc[i]) if i < len(basic_lb) else 0.0
            supertrend[i] = float("nan")
        else:
            # Final Upper Band
            if basic_ub.iloc[i] < final_ub[i - 1] or close.iloc[i - 1] > final_ub[i - 1]:
                final_ub[i] = float(basic_ub.iloc[i])
            else:
                final_ub[i] = final_ub[i - 1]

            # Final Lower Band
            if basic_lb.iloc[i] > final_lb[i - 1] or close.iloc[i - 1] < final_lb[i - 1]:
                final_lb[i] = float(basic_lb.iloc[i])
            else:
                final_lb[i] = final_lb[i - 1]

            # SuperTrend value
            prev_st = supertrend[i - 1]
            prev_fub = final_ub[i - 1]
            prev_flb = final_lb[i - 1]
            curr_fub = final_ub[i]
            curr_flb = final_lb[i]
            curr_close = float(close.iloc[i])

            if prev_st == prev_fub and curr_close <= curr_fub:
                supertrend[i] = curr_fub
            elif prev_st == prev_fub and curr_close > curr_fub:
                supertrend[i] = curr_flb
            elif prev_st == prev_flb and curr_close >= curr_flb:
                supertrend[i] = curr_flb
            elif prev_st == prev_flb and curr_close < curr_flb:
                supertrend[i] = curr_fub
            else:
                supertrend[i] = curr_flb

    st_series = pd.Series(supertrend, index=close.index)
    direction = pd.Series(1, index=close.index)
    direction[close < st_series] = -1

    return st_series, direction


def compute_vwap_deviation(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    volume: pd.Series,
    window: int = 14,
) -> pd.Series:
    """
    Compute % deviation of close from VWAP.
    Positive = price above VWAP; negative = price below VWAP.
    """
    vwap = VolumeWeightedAveragePrice(
        high=high, low=low, close=close, volume=volume, window=window
    )
    vwap_series = vwap.volume_weighted_average_price()
    return ((close - vwap_series) / vwap_series.replace(0, np.nan)) * 100


def compute_obv_divergence(
    close: pd.Series,
    volume: pd.Series,
    sma_period: int = 20,
) -> pd.Series:
    """
    Detect OBV divergence: +1 when OBV trending up but price trending down (bullish),
    -1 when OBV trending down but price trending up (bearish), 0 otherwise.
    """
    obv = OnBalanceVolumeIndicator(close=close, volume=volume)
    obv_series = obv.on_balance_volume()
    obv_sma = obv_series.rolling(window=sma_period, min_periods=1).mean()
    price_sma = close.rolling(window=sma_period, min_periods=1).mean()

    obv_slope = obv_sma.diff(5)
    price_slope = price_sma.diff(5)

    divergence = pd.Series(0, index=close.index)
    # Bullish divergence: OBV rising, price falling
    divergence[(obv_slope > 0) & (price_slope < 0)] = 1
    # Bearish divergence: OBV falling, price rising
    divergence[(obv_slope < 0) & (price_slope > 0)] = -1
    return divergence


def compute_cmf(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    volume: pd.Series,
    window: int = 20,
) -> pd.Series:
    """Compute Chaikin Money Flow (-1 to +1). Positive = buying pressure."""
    cmf = ChaikinMoneyFlowIndicator(
        high=high, low=low, close=close, volume=volume, window=window
    )
    return cmf.chaikin_money_flow()


def get_latest_indicators(
    df: pd.DataFrame,
    rsi_period: int = 14,
    macd_fast: int = 12,
    macd_slow: int = 26,
    macd_signal: int = 9,
    bb_period: int = 20,
    bb_std: float = 2.0,
    supertrend_period: int = 10,
    supertrend_multiplier: float = 3.0,
    stoch_window: int = 14,
    stoch_smooth: int = 3,
    willr_period: int = 14,
    ema_fast: int = 9,
    ema_slow: int = 21,
    atr_period: int = 14,
    atr_avg_period: int = 20,
) -> dict[str, float] | None:
    """
    Compute all indicators and return latest values for the most recent bar.
    df must have 'High', 'Low', 'Close' columns.

    Returns dict with keys: rsi, macd_hist, bb_upper, bb_middle, bb_lower, close,
    supertrend_value, supertrend_direction, stoch_k, stoch_d, williams_r,
    ema_fast, ema_slow, ema_bullish (1=bullish, -1=bearish),
    atr, atr_pct, atr_pct_vs_avg (current ATR % minus its avg; positive=high vol).
    Returns None if insufficient data.
    """
    if df is None or df.empty or "Close" not in df.columns:
        return None
    if "High" not in df.columns or "Low" not in df.columns:
        return None

    close = df["Close"].dropna()
    high = df["High"].reindex(close.index).ffill().bfill()
    low = df["Low"].reindex(close.index).ffill().bfill()

    min_len = max(
        rsi_period, macd_slow, bb_period, supertrend_period,
        stoch_window, willr_period, ema_slow, atr_period + atr_avg_period
    ) + 15
    if len(close) < min_len:
        return None

    rsi_series = compute_rsi(close, period=rsi_period)
    _, _, macd_hist = compute_macd(close, macd_slow, macd_fast, macd_signal)
    bb_upper, bb_mid, bb_lower = compute_bollinger(close, bb_period, bb_std)
    st_series, st_direction = compute_supertrend(
        high, low, close, period=supertrend_period, multiplier=supertrend_multiplier
    )
    stoch_k, stoch_d = compute_stochastic(
        high, low, close, window=stoch_window, smooth_window=stoch_smooth
    )
    willr_series = compute_williams_r(high, low, close, lbp=willr_period)
    ema_fast_series, ema_slow_series, ema_bullish_series = compute_ema_crossover(
        close, fast=ema_fast, slow=ema_slow
    )
    atr_series = compute_atr(high, low, close, window=atr_period)
    atr_pct_series = compute_atr_pct(close, atr_series)
    atr_pct_avg = atr_pct_series.rolling(window=atr_avg_period, min_periods=1).mean()
    atr_pct_vs_avg_series = atr_pct_series - atr_pct_avg
    sma_200_series = compute_sma_200(close)

    last = close.index[-1]
    rsi_val = rsi_series.loc[last] if last in rsi_series.index else rsi_series.iloc[-1]
    hist_val = macd_hist.loc[last] if last in macd_hist.index else macd_hist.iloc[-1]
    upper_val = bb_upper.loc[last] if last in bb_upper.index else bb_upper.iloc[-1]
    mid_val = bb_mid.loc[last] if last in bb_mid.index else bb_mid.iloc[-1]
    lower_val = bb_lower.loc[last] if last in bb_lower.index else bb_lower.iloc[-1]
    close_val = float(close.iloc[-1])
    st_val = st_series.loc[last] if last in st_series.index else st_series.iloc[-1]
    st_dir = int(st_direction.loc[last]) if last in st_direction.index else int(st_direction.iloc[-1])
    stoch_k_val = stoch_k.loc[last] if last in stoch_k.index else stoch_k.iloc[-1]
    stoch_d_val = stoch_d.loc[last] if last in stoch_d.index else stoch_d.iloc[-1]
    willr_val = willr_series.loc[last] if last in willr_series.index else willr_series.iloc[-1]
    ema_fast_val = ema_fast_series.loc[last] if last in ema_fast_series.index else ema_fast_series.iloc[-1]
    ema_slow_val = ema_slow_series.loc[last] if last in ema_slow_series.index else ema_slow_series.iloc[-1]
    ema_bullish_val = ema_bullish_series.loc[last] if last in ema_bullish_series.index else ema_bullish_series.iloc[-1]
    atr_val = atr_series.loc[last] if last in atr_series.index else atr_series.iloc[-1]
    atr_pct_val = atr_pct_series.loc[last] if last in atr_pct_series.index else atr_pct_series.iloc[-1]
    atr_pct_vs_avg_val = atr_pct_vs_avg_series.loc[last] if last in atr_pct_vs_avg_series.index else atr_pct_vs_avg_series.iloc[-1]
    sma_200_val = sma_200_series.loc[last] if last in sma_200_series.index else sma_200_series.iloc[-1]
    sma_200_float = float(sma_200_val) if pd.notna(sma_200_val) else float("nan")

    result = {
        "rsi": float(rsi_val),
        "macd_hist": float(hist_val),
        "bb_upper": float(upper_val),
        "bb_middle": float(mid_val),
        "bb_lower": float(lower_val),
        "close": close_val,
        "supertrend_value": float(st_val),
        "supertrend_direction": st_dir,
        "stoch_k": float(stoch_k_val),
        "stoch_d": float(stoch_d_val),
        "williams_r": float(willr_val),
        "ema_fast": float(ema_fast_val),
        "ema_slow": float(ema_slow_val),
        "ema_bullish": int(ema_bullish_val),
        "atr": float(atr_val),
        "atr_pct": float(atr_pct_val),
        "atr_pct_vs_avg": float(atr_pct_vs_avg_val),
        "sma_200": sma_200_float,
        "vwap_deviation": None,
        "obv_divergence": None,
        "cmf": None,
    }

    # Volume indicators — only when volume data is available and non-zero
    if "Volume" in df.columns:
        volume = df["Volume"].reindex(close.index).fillna(0)
        if volume.sum() > 0:
            try:
                vwap_dev = compute_vwap_deviation(high, low, close, volume)
                vwap_dev_val = vwap_dev.iloc[-1]
                result["vwap_deviation"] = float(vwap_dev_val) if pd.notna(vwap_dev_val) else None
            except Exception:
                pass
            try:
                obv_div = compute_obv_divergence(close, volume)
                obv_div_val = obv_div.iloc[-1]
                result["obv_divergence"] = int(obv_div_val)
            except Exception:
                pass
            try:
                cmf_series = compute_cmf(high, low, close, volume)
                cmf_val = cmf_series.iloc[-1]
                result["cmf"] = float(cmf_val) if pd.notna(cmf_val) else None
            except Exception:
                pass

    return result
