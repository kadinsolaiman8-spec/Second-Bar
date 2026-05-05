# indicators.py — Complete 12-indicator suite with pandas-ta and manual fallbacks
#
# Every indicator returns a dict with at minimum:
#   value (or relevant metric), bullish (bool), bearish (bool)
# so downstream code has a uniform interface.

import logging
import math
from datetime import datetime, time as dtime
from typing import Optional

import numpy as np
import pandas as pd
import pytz

from config import (
    RSI_PERIOD, RSI_OVERBOUGHT, RSI_OVERSOLD,
    MACD_FAST, MACD_SLOW, MACD_SIGNAL,
    SUPERTREND_PERIOD, SUPERTREND_MULT,
    STOCH_K_PERIOD, STOCH_D_PERIOD, STOCH_SMOOTH, STOCH_OVERBOUGHT, STOCH_OVERSOLD,
    EMA_FAST, EMA_MID, EMA_SLOW, EMA_TREND,
    VWAP_RESET_HOUR, VWAP_RESET_MINUTE,
    ADX_PERIOD, ADX_TRENDING, ADX_WEAK,
    ATR_PERIOD,
    BB_PERIOD, BB_STD,
    RVOL_PERIOD, RVOL_HIGH, RVOL_VERY_HIGH,
    WILLIAMS_PERIOD, WILLIAMS_OB, WILLIAMS_OS,
    MARKET_TIMEZONE,
)

logger = logging.getLogger(__name__)
ET = pytz.timezone(MARKET_TIMEZONE)

# Try to import pandas_ta; fall back to manual calculations
try:
    import pandas_ta as ta
    HAS_PANDAS_TA = True
except ImportError:
    HAS_PANDAS_TA = False
    logger.warning("pandas_ta not available — using manual indicator calculations")


# ══════════════════════════════════════════════════════════════════════════
# HELPER: safe float extraction
# ══════════════════════════════════════════════════════════════════════════

def _sf(val, default=0.0):
    """Safely convert to float, returning *default* on NaN / None."""
    if val is None:
        return default
    try:
        f = float(val)
        return default if math.isnan(f) or math.isinf(f) else f
    except (TypeError, ValueError):
        return default


def _last(series, default=0.0):
    """Return last non-NaN value in a pandas Series."""
    if series is None or series.empty:
        return default
    v = series.dropna()
    return _sf(v.iloc[-1], default) if len(v) > 0 else default


# ══════════════════════════════════════════════════════════════════════════
# 1. RSI (14)
# ══════════════════════════════════════════════════════════════════════════

def calc_rsi(df: pd.DataFrame) -> dict:
    """Relative Strength Index (14-period)."""
    result = {"value": 50.0, "bullish": False, "bearish": False,
              "overbought": False, "oversold": False}
    try:
        close = df["Close"]
        if len(close) < RSI_PERIOD + 1:
            return result

        if HAS_PANDAS_TA:
            rsi_s = ta.rsi(close, length=RSI_PERIOD)
            val = _last(rsi_s, 50.0)
        else:
            delta = close.diff()
            gain = delta.where(delta > 0, 0.0).rolling(RSI_PERIOD).mean()
            loss = (-delta.where(delta < 0, 0.0)).rolling(RSI_PERIOD).mean()
            rs = gain / loss.replace(0, np.nan)
            rsi_s = 100 - (100 / (1 + rs))
            val = _last(rsi_s, 50.0)

        result["value"] = round(val, 2)
        result["overbought"] = val >= RSI_OVERBOUGHT
        result["oversold"] = val <= RSI_OVERSOLD
        result["bullish"] = 40 < val < RSI_OVERBOUGHT
        result["bearish"] = val >= RSI_OVERBOUGHT or val <= RSI_OVERSOLD

        # Check for RSI divergence hint: rising price + falling RSI
        if len(close) >= 10:
            price_rising = float(close.iloc[-1]) > float(close.iloc[-5])
            rsi_vals = rsi_s.dropna().tail(5)
            if len(rsi_vals) >= 5:
                rsi_falling = float(rsi_vals.iloc[-1]) < float(rsi_vals.iloc[0])
                result["bearish_divergence"] = price_rising and rsi_falling
            else:
                result["bearish_divergence"] = False
        else:
            result["bearish_divergence"] = False

    except Exception as e:
        logger.debug(f"RSI calc error: {e}")
    return result


# ══════════════════════════════════════════════════════════════════════════
# 2. MACD (12, 26, 9)
# ══════════════════════════════════════════════════════════════════════════

def calc_macd(df: pd.DataFrame) -> dict:
    result = {"macd": 0.0, "signal": 0.0, "histogram": 0.0,
              "bullish": False, "bearish": False,
              "candles_since_crossover": 999}
    try:
        close = df["Close"]
        if len(close) < MACD_SLOW + MACD_SIGNAL:
            return result

        if HAS_PANDAS_TA:
            macd_df = ta.macd(close, fast=MACD_FAST, slow=MACD_SLOW, signal=MACD_SIGNAL)
            if macd_df is not None and not macd_df.empty:
                cols = macd_df.columns.tolist()
                macd_val = _last(macd_df[cols[0]])
                signal_val = _last(macd_df[cols[1]])
                hist_val = _last(macd_df[cols[2]])
            else:
                raise ValueError("pandas_ta MACD returned empty")
        else:
            ema_fast = close.ewm(span=MACD_FAST, adjust=False).mean()
            ema_slow = close.ewm(span=MACD_SLOW, adjust=False).mean()
            macd_line = ema_fast - ema_slow
            signal_line = macd_line.ewm(span=MACD_SIGNAL, adjust=False).mean()
            hist_line = macd_line - signal_line
            macd_val = _last(macd_line)
            signal_val = _last(signal_line)
            hist_val = _last(hist_line)

        result["macd"] = round(macd_val, 6)
        result["signal"] = round(signal_val, 6)
        result["histogram"] = round(hist_val, 6)
        result["bullish"] = macd_val > signal_val
        result["bearish"] = macd_val < signal_val

        # Candles since crossover
        if not HAS_PANDAS_TA:
            diff = macd_line - signal_line
        else:
            diff = macd_df[cols[0]] - macd_df[cols[1]] if macd_df is not None else pd.Series([0])
        signs = diff.dropna().apply(lambda x: 1 if x > 0 else -1)
        if len(signs) >= 2:
            current_sign = int(signs.iloc[-1])
            count = 0
            for i in range(len(signs) - 1, -1, -1):
                if int(signs.iloc[i]) == current_sign:
                    count += 1
                else:
                    break
            result["candles_since_crossover"] = count

    except Exception as e:
        logger.debug(f"MACD calc error: {e}")
    return result


# ══════════════════════════════════════════════════════════════════════════
# 3. SuperTrend (10, 3)
# ══════════════════════════════════════════════════════════════════════════

def calc_supertrend(df: pd.DataFrame) -> dict:
    result = {"value": 0.0, "bullish": False, "bearish": False, "direction": 0}
    try:
        if len(df) < SUPERTREND_PERIOD + 1:
            return result

        if HAS_PANDAS_TA:
            st = ta.supertrend(df["High"], df["Low"], df["Close"],
                               length=SUPERTREND_PERIOD, multiplier=SUPERTREND_MULT)
            if st is not None and not st.empty:
                cols = st.columns.tolist()
                # pandas_ta returns: SUPERT_*, SUPERTd_*, SUPERTl_*, SUPERTs_*
                st_val = _last(st[cols[0]])
                st_dir = _last(st[cols[1]], 0)
                result["value"] = round(st_val, 4)
                result["direction"] = int(st_dir)
                result["bullish"] = st_dir > 0
                result["bearish"] = st_dir < 0
                return result

        # Manual SuperTrend
        hl2 = (df["High"] + df["Low"]) / 2
        atr = _manual_atr(df, SUPERTREND_PERIOD)
        upper_band = hl2 + SUPERTREND_MULT * atr
        lower_band = hl2 - SUPERTREND_MULT * atr

        supertrend = pd.Series(index=df.index, dtype=float)
        direction = pd.Series(index=df.index, dtype=float)

        supertrend.iloc[0] = upper_band.iloc[0]
        direction.iloc[0] = -1

        for i in range(1, len(df)):
            if df["Close"].iloc[i] > upper_band.iloc[i - 1]:
                direction.iloc[i] = 1
            elif df["Close"].iloc[i] < lower_band.iloc[i - 1]:
                direction.iloc[i] = -1
            else:
                direction.iloc[i] = direction.iloc[i - 1]

            if direction.iloc[i] == 1:
                supertrend.iloc[i] = lower_band.iloc[i]
                if lower_band.iloc[i] < supertrend.iloc[i - 1] and direction.iloc[i - 1] == 1:
                    supertrend.iloc[i] = supertrend.iloc[i - 1]
            else:
                supertrend.iloc[i] = upper_band.iloc[i]
                if upper_band.iloc[i] > supertrend.iloc[i - 1] and direction.iloc[i - 1] == -1:
                    supertrend.iloc[i] = supertrend.iloc[i - 1]

        result["value"] = round(_last(supertrend), 4)
        result["direction"] = int(_last(direction, -1))
        result["bullish"] = result["direction"] > 0
        result["bearish"] = result["direction"] < 0

    except Exception as e:
        logger.debug(f"SuperTrend calc error: {e}")
    return result


# ══════════════════════════════════════════════════════════════════════════
# 4. Stochastic Oscillator (14, 3, 3)
# ══════════════════════════════════════════════════════════════════════════

def calc_stochastic(df: pd.DataFrame) -> dict:
    result = {"k": 50.0, "d": 50.0, "bullish": False, "bearish": False,
              "overbought": False, "oversold": False}
    try:
        if len(df) < STOCH_K_PERIOD + STOCH_D_PERIOD:
            return result

        if HAS_PANDAS_TA:
            stoch = ta.stoch(df["High"], df["Low"], df["Close"],
                             k=STOCH_K_PERIOD, d=STOCH_D_PERIOD, smooth_k=STOCH_SMOOTH)
            if stoch is not None and not stoch.empty:
                cols = stoch.columns.tolist()
                k_val = _last(stoch[cols[0]], 50)
                d_val = _last(stoch[cols[1]], 50)
            else:
                raise ValueError("empty stoch")
        else:
            low_min = df["Low"].rolling(STOCH_K_PERIOD).min()
            high_max = df["High"].rolling(STOCH_K_PERIOD).max()
            denom = high_max - low_min
            denom = denom.replace(0, np.nan)
            k_raw = 100 * (df["Close"] - low_min) / denom
            k_val_s = k_raw.rolling(STOCH_SMOOTH).mean()
            d_val_s = k_val_s.rolling(STOCH_D_PERIOD).mean()
            k_val = _last(k_val_s, 50)
            d_val = _last(d_val_s, 50)

        result["k"] = round(k_val, 2)
        result["d"] = round(d_val, 2)
        result["overbought"] = k_val >= STOCH_OVERBOUGHT
        result["oversold"] = k_val <= STOCH_OVERSOLD
        result["bullish"] = k_val > d_val and not result["overbought"]
        result["bearish"] = k_val < d_val or result["overbought"]

    except Exception as e:
        logger.debug(f"Stochastic calc error: {e}")
    return result


# ══════════════════════════════════════════════════════════════════════════
# 5. EMA Stack (9, 21, 50, 200)
# ══════════════════════════════════════════════════════════════════════════

def calc_ema_stack(df: pd.DataFrame) -> dict:
    result = {
        "ema9": 0.0, "ema21": 0.0, "ema50": 0.0, "ema200": 0.0,
        "bullish": False, "bearish": False,
        "price_above_200": False, "aligned_bull": False, "aligned_bear": False,
    }
    try:
        close = df["Close"]
        e9  = _last(close.ewm(span=EMA_FAST, adjust=False).mean())
        e21 = _last(close.ewm(span=EMA_MID, adjust=False).mean())
        e50 = _last(close.ewm(span=EMA_SLOW, adjust=False).mean())
        e200 = _last(close.ewm(span=EMA_TREND, adjust=False).mean()) if len(close) >= EMA_TREND else 0.0

        price = _sf(close.iloc[-1])

        result["ema9"] = round(e9, 4)
        result["ema21"] = round(e21, 4)
        result["ema50"] = round(e50, 4)
        result["ema200"] = round(e200, 4)
        result["price_above_200"] = price > e200 if e200 > 0 else False

        # Bullish alignment: 9 > 21 > 50
        if e9 > e21 > e50 > 0:
            result["aligned_bull"] = True
            result["bullish"] = True
        elif e9 < e21 < e50 and e9 > 0:
            result["aligned_bear"] = True
            result["bearish"] = True
        else:
            # Partial — use price vs 200
            result["bullish"] = price > e200 if e200 > 0 else e9 > e21
            result["bearish"] = not result["bullish"]

    except Exception as e:
        logger.debug(f"EMA stack calc error: {e}")
    return result


# ══════════════════════════════════════════════════════════════════════════
# 6. VWAP (session-anchored at 9:30 AM ET)
# ══════════════════════════════════════════════════════════════════════════

def calc_vwap(df: pd.DataFrame) -> dict:
    result = {"value": 0.0, "above": False, "bullish": False, "bearish": False,
              "distance_pct": 0.0}
    try:
        if "Volume" not in df.columns or df["Volume"].sum() == 0:
            return result

        # Determine session start
        typical = (df["High"] + df["Low"] + df["Close"]) / 3
        cum_vol = df["Volume"].cumsum()
        cum_tp_vol = (typical * df["Volume"]).cumsum()

        # Avoid division by zero
        cum_vol_safe = cum_vol.replace(0, np.nan)
        vwap_s = cum_tp_vol / cum_vol_safe

        val = _last(vwap_s)
        price = _sf(df["Close"].iloc[-1])

        result["value"] = round(val, 4) if val > 0 else 0.0
        result["above"] = price > val if val > 0 else False
        result["bullish"] = result["above"]
        result["bearish"] = not result["above"] and val > 0

        if val > 0 and price > 0:
            result["distance_pct"] = round((price - val) / val * 100, 2)

    except Exception as e:
        logger.debug(f"VWAP calc error: {e}")
    return result


# ══════════════════════════════════════════════════════════════════════════
# 7. ADX (14) with +DI / -DI
# ══════════════════════════════════════════════════════════════════════════

def calc_adx(df: pd.DataFrame) -> dict:
    result = {"value": 0.0, "plus_di": 0.0, "minus_di": 0.0,
              "bullish": False, "bearish": False, "trending": False}
    try:
        if len(df) < ADX_PERIOD * 2:
            return result

        if HAS_PANDAS_TA:
            adx_df = ta.adx(df["High"], df["Low"], df["Close"], length=ADX_PERIOD)
            if adx_df is not None and not adx_df.empty:
                cols = adx_df.columns.tolist()
                adx_val = _last(adx_df[cols[0]])
                dmp = _last(adx_df[cols[1]])
                dmn = _last(adx_df[cols[2]])
            else:
                raise ValueError("empty ADX")
        else:
            adx_val, dmp, dmn = _manual_adx(df, ADX_PERIOD)

        result["value"] = round(adx_val, 2)
        result["plus_di"] = round(dmp, 2)
        result["minus_di"] = round(dmn, 2)
        result["trending"] = adx_val >= ADX_TRENDING
        result["bullish"] = dmp > dmn and adx_val >= ADX_WEAK
        result["bearish"] = dmn > dmp and adx_val >= ADX_WEAK

    except Exception as e:
        logger.debug(f"ADX calc error: {e}")
    return result


def _manual_adx(df: pd.DataFrame, period: int):
    """Manual ADX calculation."""
    high = df["High"]
    low = df["Low"]
    close = df["Close"]

    plus_dm = high.diff()
    minus_dm = -low.diff()

    plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0.0)
    minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0.0)

    tr = _manual_true_range(df)
    atr = tr.rolling(period).mean()
    atr_safe = atr.replace(0, np.nan)

    plus_di = 100 * (plus_dm.rolling(period).mean() / atr_safe)
    minus_di = 100 * (minus_dm.rolling(period).mean() / atr_safe)

    dx_denom = (plus_di + minus_di).replace(0, np.nan)
    dx = 100 * abs(plus_di - minus_di) / dx_denom
    adx = dx.rolling(period).mean()

    return _last(adx), _last(plus_di), _last(minus_di)


# ══════════════════════════════════════════════════════════════════════════
# 8. ATR (14)
# ══════════════════════════════════════════════════════════════════════════

def calc_atr(df: pd.DataFrame) -> dict:
    result = {"value": 0.0, "bullish": False, "bearish": False, "pct": 0.0}
    try:
        if len(df) < ATR_PERIOD + 1:
            return result

        if HAS_PANDAS_TA:
            atr_s = ta.atr(df["High"], df["Low"], df["Close"], length=ATR_PERIOD)
            val = _last(atr_s)
        else:
            val = _last(_manual_atr(df, ATR_PERIOD))

        price = _sf(df["Close"].iloc[-1])
        result["value"] = round(val, 4)
        result["pct"] = round(val / price * 100, 2) if price > 0 else 0.0
        # ATR doesn't have a directional bias per se; we mark it neutral
        result["bullish"] = False
        result["bearish"] = False

    except Exception as e:
        logger.debug(f"ATR calc error: {e}")
    return result


def _manual_true_range(df: pd.DataFrame) -> pd.Series:
    high = df["High"]
    low = df["Low"]
    prev_close = df["Close"].shift(1)
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    return pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)


def _manual_atr(df: pd.DataFrame, period: int) -> pd.Series:
    tr = _manual_true_range(df)
    return tr.rolling(period).mean()


# ══════════════════════════════════════════════════════════════════════════
# 9. Bollinger Bands (20, 2)
# ══════════════════════════════════════════════════════════════════════════

def calc_bollinger(df: pd.DataFrame) -> dict:
    result = {"upper": 0.0, "middle": 0.0, "lower": 0.0,
              "width": 0.0, "pct_b": 0.5,
              "squeeze": False, "bullish": False, "bearish": False}
    try:
        close = df["Close"]
        if len(close) < BB_PERIOD:
            return result

        if HAS_PANDAS_TA:
            bb = ta.bbands(close, length=BB_PERIOD, std=BB_STD)
            if bb is not None and not bb.empty:
                cols = bb.columns.tolist()
                lower = _last(bb[cols[0]])
                mid = _last(bb[cols[1]])
                upper = _last(bb[cols[2]])
                bw = _last(bb[cols[3]]) if len(cols) > 3 else 0
                pctb = _last(bb[cols[4]]) if len(cols) > 4 else 0.5
            else:
                raise ValueError("empty BB")
        else:
            mid = _last(close.rolling(BB_PERIOD).mean())
            std = _last(close.rolling(BB_PERIOD).std())
            upper = mid + BB_STD * std
            lower = mid - BB_STD * std
            bw = (upper - lower) / mid if mid > 0 else 0
            price = _sf(close.iloc[-1])
            pctb = (price - lower) / (upper - lower) if (upper - lower) > 0 else 0.5

        price = _sf(close.iloc[-1])
        result["upper"] = round(upper, 4)
        result["middle"] = round(mid, 4)
        result["lower"] = round(lower, 4)
        result["width"] = round(bw, 6)
        result["pct_b"] = round(pctb, 4)

        # Squeeze: bandwidth in bottom 20% of its 120-bar range
        if len(close) >= 120:
            bw_series = close.rolling(BB_PERIOD).std() * 2 / close.rolling(BB_PERIOD).mean()
            bw_series = bw_series.dropna()
            if len(bw_series) >= 50:
                bw_min = bw_series.tail(120).min()
                bw_max = bw_series.tail(120).max()
                bw_range = bw_max - bw_min
                if bw_range > 0:
                    bw_pct = (bw - bw_min) / bw_range
                    result["squeeze"] = bw_pct < 0.2
                else:
                    result["squeeze"] = False

        result["bullish"] = price > mid
        result["bearish"] = price < mid

    except Exception as e:
        logger.debug(f"BB calc error: {e}")
    return result


# ══════════════════════════════════════════════════════════════════════════
# 10. Relative Volume (RVOL)
# ══════════════════════════════════════════════════════════════════════════

def calc_rvol(df: pd.DataFrame) -> dict:
    result = {"rvol": 1.0, "bullish": False, "bearish": False,
              "increasing_3": False, "current_vol": 0, "avg_vol": 0}
    try:
        if "Volume" not in df.columns or len(df) < RVOL_PERIOD + 1:
            return result

        vol = df["Volume"]
        current = _sf(vol.iloc[-1])
        avg = _sf(vol.tail(RVOL_PERIOD + 1).iloc[:-1].mean())  # exclude current bar

        if avg > 0:
            rvol = current / avg
        else:
            rvol = 1.0

        result["rvol"] = round(rvol, 2)
        result["current_vol"] = int(current)
        result["avg_vol"] = int(avg)
        result["bullish"] = rvol >= RVOL_HIGH
        result["bearish"] = False  # Low volume is ambiguous, not bearish

        # Check if volume increasing last 3 bars
        if len(vol) >= 4:
            v1, v2, v3 = float(vol.iloc[-3]), float(vol.iloc[-2]), float(vol.iloc[-1])
            result["increasing_3"] = v3 > v2 > v1

    except Exception as e:
        logger.debug(f"RVOL calc error: {e}")
    return result


# ══════════════════════════════════════════════════════════════════════════
# 11. Williams %R (14)
# ══════════════════════════════════════════════════════════════════════════

def calc_williams_r(df: pd.DataFrame) -> dict:
    result = {"value": -50.0, "bullish": False, "bearish": False,
              "overbought": False, "oversold": False}
    try:
        if len(df) < WILLIAMS_PERIOD:
            return result

        if HAS_PANDAS_TA:
            wr = ta.willr(df["High"], df["Low"], df["Close"], length=WILLIAMS_PERIOD)
            val = _last(wr, -50)
        else:
            highest = df["High"].rolling(WILLIAMS_PERIOD).max()
            lowest = df["Low"].rolling(WILLIAMS_PERIOD).min()
            denom = (highest - lowest).replace(0, np.nan)
            wr = -100 * (highest - df["Close"]) / denom
            val = _last(wr, -50)

        result["value"] = round(val, 2)
        result["overbought"] = val > WILLIAMS_OB
        result["oversold"] = val < WILLIAMS_OS
        result["bullish"] = WILLIAMS_OS < val < WILLIAMS_OB  # In neutral healthy zone or rising
        result["bearish"] = val > WILLIAMS_OB  # Overbought = bearish signal

        # Check for divergence: price higher high but WR lower high
        if len(df) >= 10:
            prices = df["Close"].tail(10)
            if not HAS_PANDAS_TA:
                wr_s = wr.tail(10).dropna()
            else:
                wr_s = ta.willr(df["High"], df["Low"], df["Close"], length=WILLIAMS_PERIOD)
                wr_s = wr_s.tail(10).dropna() if wr_s is not None else pd.Series()

            if len(wr_s) >= 5 and len(prices) >= 5:
                price_new_high = float(prices.iloc[-1]) > float(prices.iloc[-5])
                wr_lower_high = float(wr_s.iloc[-1]) < float(wr_s.iloc[len(wr_s)//2]) if len(wr_s) > 2 else False
                result["divergence"] = price_new_high and wr_lower_high
            else:
                result["divergence"] = False
        else:
            result["divergence"] = False

    except Exception as e:
        logger.debug(f"Williams %R calc error: {e}")
    return result


# ══════════════════════════════════════════════════════════════════════════
# 12. OBV (On-Balance Volume)
# ══════════════════════════════════════════════════════════════════════════

def calc_obv(df: pd.DataFrame) -> dict:
    result = {"trending_up": False, "bullish": False, "bearish": False}
    try:
        if "Volume" not in df.columns or len(df) < 10:
            return result

        close = df["Close"]
        volume = df["Volume"]

        sign = close.diff().apply(lambda x: 1 if x > 0 else (-1 if x < 0 else 0))
        obv = (sign * volume).cumsum()

        if len(obv.dropna()) < 10:
            return result

        # Compare OBV over last 10 bars
        obv_recent = obv.dropna().tail(10)
        obv_start = float(obv_recent.iloc[0])
        obv_end = float(obv_recent.iloc[-1])

        trending_up = obv_end > obv_start
        result["trending_up"] = trending_up
        result["bullish"] = trending_up
        result["bearish"] = not trending_up

        # Slope — simple linear fit
        if len(obv_recent) >= 5:
            x = np.arange(len(obv_recent))
            y = obv_recent.values.astype(float)
            try:
                slope = np.polyfit(x, y, 1)[0]
                result["slope"] = round(slope, 2)
            except Exception:
                result["slope"] = 0.0

    except Exception as e:
        logger.debug(f"OBV calc error: {e}")
    return result


# ══════════════════════════════════════════════════════════════════════════
# MASTER: run_all_indicators
# ══════════════════════════════════════════════════════════════════════════

def run_all_indicators(df: pd.DataFrame) -> dict:
    """
    Run all 12 indicators on a DataFrame and return a unified dict.
    DataFrame must have OHLCV columns: Open, High, Low, Close, Volume.
    """
    if df is None or df.empty or len(df) < 5:
        return _empty_indicators()

    # Flatten MultiIndex columns if yfinance returned them
    if isinstance(df.columns, pd.MultiIndex):
        df = df.copy()
        df.columns = df.columns.get_level_values(0)

    return {
        "rsi":              calc_rsi(df),
        "macd":             calc_macd(df),
        "supertrend":       calc_supertrend(df),
        "stochastic":       calc_stochastic(df),
        "ema_stack":        calc_ema_stack(df),
        "vwap":             calc_vwap(df),
        "adx":              calc_adx(df),
        "atr":              calc_atr(df),
        "bollinger":        calc_bollinger(df),
        "rvol":             calc_rvol(df),
        "williams_r":       calc_williams_r(df),
        "obv":              calc_obv(df),
    }


def _empty_indicators() -> dict:
    """Return a zeroed-out indicator dict."""
    return {
        "rsi":        {"value": 50, "bullish": False, "bearish": False, "overbought": False, "oversold": False},
        "macd":       {"macd": 0, "signal": 0, "histogram": 0, "bullish": False, "bearish": False, "candles_since_crossover": 999},
        "supertrend": {"value": 0, "bullish": False, "bearish": False, "direction": 0},
        "stochastic": {"k": 50, "d": 50, "bullish": False, "bearish": False, "overbought": False, "oversold": False},
        "ema_stack":  {"ema9": 0, "ema21": 0, "ema50": 0, "ema200": 0, "bullish": False, "bearish": False, "price_above_200": False, "aligned_bull": False, "aligned_bear": False},
        "vwap":       {"value": 0, "above": False, "bullish": False, "bearish": False, "distance_pct": 0},
        "adx":        {"value": 0, "plus_di": 0, "minus_di": 0, "bullish": False, "bearish": False, "trending": False},
        "atr":        {"value": 0, "bullish": False, "bearish": False, "pct": 0},
        "bollinger":  {"upper": 0, "middle": 0, "lower": 0, "width": 0, "pct_b": 0.5, "squeeze": False, "bullish": False, "bearish": False},
        "rvol":       {"rvol": 1.0, "bullish": False, "bearish": False, "increasing_3": False, "current_vol": 0, "avg_vol": 0},
        "williams_r": {"value": -50, "bullish": False, "bearish": False, "overbought": False, "oversold": False},
        "obv":        {"trending_up": False, "bullish": False, "bearish": False},
    }


# ══════════════════════════════════════════════════════════════════════════
# SIGNAL COUNTING
# ══════════════════════════════════════════════════════════════════════════

def count_bullish_signals(indicators: dict) -> int:
    """Count how many of the 12 indicators are bullish."""
    count = 0
    for key, data in indicators.items():
        if isinstance(data, dict) and data.get("bullish"):
            count += 1
    return count


def count_bearish_signals(indicators: dict) -> int:
    """Count how many of the 12 indicators are bearish."""
    count = 0
    for key, data in indicators.items():
        if isinstance(data, dict) and data.get("bearish"):
            count += 1
    return count


def get_indicator_summary(indicators: dict) -> str:
    """One-line summary of bullish/bearish/neutral counts."""
    bull = count_bullish_signals(indicators)
    bear = count_bearish_signals(indicators)
    neut = 12 - bull - bear
    return f"{bull} bullish, {bear} bearish, {neut} neutral"


def get_indicator_direction_label(indicators: dict) -> str:
    """Return 'Bullish', 'Bearish', or 'Mixed'."""
    bull = count_bullish_signals(indicators)
    bear = count_bearish_signals(indicators)
    if bull >= 8:
        return "Strongly Bullish"
    if bull >= 6:
        return "Bullish"
    if bear >= 8:
        return "Strongly Bearish"
    if bear >= 6:
        return "Bearish"
    return "Mixed"
