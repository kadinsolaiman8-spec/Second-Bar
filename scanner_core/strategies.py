# strategies.py — 5 complete trading strategies with detection and simulation

import logging
import math
from datetime import datetime, time as dtime
from typing import Optional
import pandas as pd
import numpy as np
import pytz

from scanner_core.config import (
    STRATEGY_VWAP_BREAKOUT, STRATEGY_ORB, STRATEGY_EMA_PULLBACK,
    STRATEGY_FIBONACCI, STRATEGY_BB_SQUEEZE, ALL_STRATEGIES,
    RSI_PERIOD, RSI_OVERBOUGHT, RSI_OVERSOLD,
    MACD_FAST, MACD_SLOW, MACD_SIGNAL,
    EMA_FAST, EMA_MID, EMA_SLOW, EMA_TREND,
    ADX_TRENDING, ADX_STRONG,
    ATR_STOP_LOSS_MULTIPLIER, ATR_TAKE_PROFIT_1_MULTIPLIER, ATR_TAKE_PROFIT_2_MULTIPLIER,
    BB_PERIOD, BB_STD,
    RVOL_HIGH, RVOL_VERY_HIGH,
    ORB_END_HOUR, ORB_END_MINUTE,
    STOCH_OVERBOUGHT, STOCH_OVERSOLD,
    FIB_LEVELS,
    MAX_SIGNAL_AGE_CANDLES, MAX_PRICE_EXTENSION_ATR,
    MIN_RR_RATIO, MARKET_TIMEZONE, BACKTEST_MAX_HOLD_BARS,
)

logger = logging.getLogger(__name__)
ET = pytz.timezone(MARKET_TIMEZONE)


# ══════════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════════

def _sg(d, *keys, default=0.0):
    """Safely get nested dict value."""
    v = d
    for k in keys:
        if not isinstance(v, dict):
            return default
        v = v.get(k, default)
    return v if v is not None else default


def _sf(val, default=0.0):
    """Safe float."""
    try:
        f = float(val)
        return default if math.isnan(f) or math.isinf(f) else f
    except (TypeError, ValueError):
        return default


def _empty_result(strategy: str) -> dict:
    return {
        "triggered": False, "direction": "", "strategy": strategy,
        "entry": 0, "stop": 0, "tp1": 0, "tp2": 0, "rr": 0,
        "signal_count": 0, "conditions_met": [], "conditions_failed": [],
    }


def _compute_rr(entry, stop, target):
    risk = abs(entry - stop)
    reward = abs(target - entry)
    return round(reward / risk, 2) if risk > 0 else 0


def _crosses_above(series_vals, level, lookback=2):
    """Did series cross above level in last N candles?"""
    if len(series_vals) < lookback + 1:
        return False
    for i in range(-lookback, 0):
        if series_vals[i - 1] <= level < series_vals[i]:
            return True
    return False


def _crosses_below(series_vals, level, lookback=2):
    if len(series_vals) < lookback + 1:
        return False
    for i in range(-lookback, 0):
        if series_vals[i - 1] >= level > series_vals[i]:
            return True
    return False


def _recent_touch(prices, level, tolerance_pct=0.5, lookback=5):
    """Did price come within tolerance% of level in last N candles?"""
    if len(prices) < lookback:
        return False
    for p in prices[-lookback:]:
        if level > 0 and abs(p - level) / level * 100 <= tolerance_pct:
            return True
    return False


# ══════════════════════════════════════════════════════════════════════════
# 1. VWAP MOMENTUM BREAKOUT
# ══════════════════════════════════════════════════════════════════════════

def detect_vwap_breakout(df_5m, indicators, orb_data=None) -> dict:
    """VWAP Momentum Breakout: price breaks above VWAP with volume."""
    result = _empty_result(STRATEGY_VWAP_BREAKOUT)
    met = result["conditions_met"]
    failed = result["conditions_failed"]

    try:
        if df_5m is None or len(df_5m) < 10:
            failed.append("insufficient_data")
            return result

        close = df_5m["Close"].values.astype(float)
        entry = close[-1]
        vwap = _sf(_sg(indicators, "vwap", "value"))
        rsi = _sf(_sg(indicators, "rsi", "value"), 50)
        rvol = _sf(_sg(indicators, "rvol", "rvol"), 0)
        macd_bull = _sg(indicators, "macd", "bullish", default=False)
        macd_hist = _sf(_sg(indicators, "macd", "histogram"))
        st_bull = _sg(indicators, "supertrend", "bullish", default=False)
        atr = _sf(_sg(indicators, "atr", "value"))

        # Condition 1: Close > VWAP
        if vwap > 0 and entry > vwap:
            met.append("above_vwap")
        else:
            failed.append("below_vwap")

        # Condition 2: Crossed above VWAP recently
        if vwap > 0 and _crosses_above(close.tolist(), vwap, MAX_SIGNAL_AGE_CANDLES):
            met.append("vwap_crossover")
        else:
            failed.append("no_vwap_crossover")

        # Condition 3: RVOL >= 1.5
        if rvol >= RVOL_HIGH:
            met.append("volume_confirmation")
        else:
            failed.append("low_volume")

        # Condition 4: RSI 40-70
        if 40 <= rsi <= 70:
            met.append("rsi_healthy")
        else:
            failed.append("rsi_exhausted")

        # Condition 5: MACD bullish or histogram positive
        if macd_bull or macd_hist > 0:
            met.append("macd_bullish")
        else:
            failed.append("macd_bearish")

        # Condition 6: SuperTrend bullish
        if st_bull:
            met.append("supertrend_bullish")
        else:
            failed.append("supertrend_bearish")

        # Need at least 4 of 6 conditions
        if len(met) >= 4 and "above_vwap" in met and atr > 0:
            stop = round(entry - ATR_STOP_LOSS_MULTIPLIER * atr, 2)
            tp1 = round(entry + ATR_TAKE_PROFIT_1_MULTIPLIER * atr, 2)
            tp2 = round(entry + ATR_TAKE_PROFIT_2_MULTIPLIER * atr, 2)
            result.update({
                "triggered": True, "direction": "BUY", "entry": entry,
                "stop": stop, "tp1": tp1, "tp2": tp2,
                "rr": _compute_rr(entry, stop, tp1),
                "signal_count": len(met),
            })
    except Exception as e:
        logger.debug(f"VWAP strategy error: {e}")

    return result


# ══════════════════════════════════════════════════════════════════════════
# 2. OPENING RANGE BREAKOUT (ORB)
# ══════════════════════════════════════════════════════════════════════════

def detect_orb(df_5m, indicators, orb_data=None) -> dict:
    """Opening Range Breakout: price breaks above/below the 9:30-9:45 range."""
    result = _empty_result(STRATEGY_ORB)
    met = result["conditions_met"]
    failed = result["conditions_failed"]

    try:
        if df_5m is None or len(df_5m) < 5:
            failed.append("insufficient_data")
            return result
        if not orb_data or not orb_data.get("complete"):
            failed.append("orb_not_complete")
            return result

        orb_high = _sf(orb_data.get("high", 0))
        orb_low = _sf(orb_data.get("low", 0))
        if orb_high <= 0 or orb_low <= 0 or orb_high <= orb_low:
            failed.append("orb_data_invalid")
            return result

        close = df_5m["Close"].values.astype(float)
        entry = close[-1]
        rsi = _sf(_sg(indicators, "rsi", "value"), 50)
        atr = _sf(_sg(indicators, "atr", "value"))
        rvol = _sf(_sg(indicators, "rvol", "rvol"), 0)
        orb_range = orb_high - orb_low

        # BUY: Close > ORB high
        if entry > orb_high:
            met.append("above_orb_high")

            if _crosses_above(close.tolist(), orb_high, MAX_SIGNAL_AGE_CANDLES):
                met.append("recent_breakout")
            else:
                failed.append("stale_breakout")

            if rsi < RSI_OVERBOUGHT:
                met.append("rsi_not_overbought")
            else:
                failed.append("rsi_overbought")

            if rvol >= 1.0:
                met.append("volume_ok")
            else:
                failed.append("low_volume")

            if len(met) >= 3:
                stop = orb_low
                tp1 = round(entry + 1.5 * orb_range, 2)
                tp2 = round(entry + 2.5 * orb_range, 2)
                result.update({
                    "triggered": True, "direction": "BUY", "entry": entry,
                    "stop": stop, "tp1": tp1, "tp2": tp2,
                    "rr": _compute_rr(entry, stop, tp1),
                    "signal_count": len(met),
                })

        # Below ORB low or inside range — no BUY signal
        else:
            failed.append("below_or_inside_orb_range")

    except Exception as e:
        logger.debug(f"ORB strategy error: {e}")

    return result


# ══════════════════════════════════════════════════════════════════════════
# 3. EMA PULLBACK IN TREND
# ══════════════════════════════════════════════════════════════════════════

def detect_ema_pullback(df_5m, indicators, df_15m=None) -> dict:
    """EMA Pullback: price pulls back to a RISING EMA21 in a strong uptrend.
    Enhanced with: rising-EMA check, pullback-depth filter, volume-decline
    confirmation, and bullish-candle requirement at the bounce."""
    result = _empty_result(STRATEGY_EMA_PULLBACK)
    met = result["conditions_met"]
    failed = result["conditions_failed"]

    try:
        if df_5m is None or len(df_5m) < 20:
            failed.append("insufficient_data")
            return result

        close = df_5m["Close"].values.astype(float)
        high = df_5m["High"].values.astype(float)
        low = df_5m["Low"].values.astype(float)
        volume = df_5m["Volume"].values.astype(float)
        entry = close[-1]

        ema9 = _sf(_sg(indicators, "ema_stack", "ema9"))
        ema21 = _sf(_sg(indicators, "ema_stack", "ema21"))
        ema50 = _sf(_sg(indicators, "ema_stack", "ema50"))
        adx = _sf(_sg(indicators, "adx", "value"))
        rsi = _sf(_sg(indicators, "rsi", "value"), 50)
        st_bull = _sg(indicators, "supertrend", "bullish", default=False)
        macd_bull = _sg(indicators, "macd", "bullish", default=False)
        atr = _sf(_sg(indicators, "atr", "value"))

        # Condition 1: EMA stack aligned (9 > 21 > 50)
        if ema9 > ema21 > ema50 > 0:
            met.append("ema_stack_bullish")
        else:
            failed.append("ema_stack_not_aligned")

        # Condition 2: ADX > 25 (trending)
        if adx >= ADX_TRENDING:
            met.append("adx_trending")
        else:
            failed.append("adx_not_trending")

        # Condition 3: Price pulled back to EMA21 within last 5 candles
        pullback = False
        if ema21 > 0:
            for i in range(-5, 0):
                if i < -len(low):
                    continue
                if low[i] <= ema21 * 1.003:
                    pullback = True
                    break
        if pullback and entry > ema21:
            met.append("pullback_to_ema21")
        else:
            failed.append("no_pullback")

        # Condition 4: RSI 40-60 (pullback zone)
        if 40 <= rsi <= 60:
            met.append("rsi_pullback_zone")
        else:
            failed.append("rsi_not_pullback")

        # Condition 5: SuperTrend bullish
        if st_bull:
            met.append("supertrend_bullish")
        else:
            failed.append("supertrend_bearish")

        # Condition 6: MACD above signal
        if macd_bull:
            met.append("macd_bullish")
        else:
            failed.append("macd_bearish")

        # ── NEW REQUIREMENT 1: EMA21 must be RISING ──────────────────
        # slope = (ema_current - ema_5_candles_ago) / 5
        ema21_rising = False
        if ema21 > 0 and len(close) >= 10:
            # Approximate EMA21 5 candles ago from price data
            ema21_5ago = float(pd.Series(close[:-5]).ewm(span=21, adjust=False).mean().iloc[-1]) if len(close) > 25 else ema21 * 0.999
            slope = (ema21 - ema21_5ago) / 5
            if slope > 0.0001 * ema21:  # positive slope relative to price
                ema21_rising = True
                met.append("ema21_rising")
            else:
                failed.append("ema21_flat_or_falling")
        else:
            failed.append("ema21_slope_unknown")

        # ── NEW REQUIREMENT 2: Pullback depth 25-75% of prior move ───
        pullback_depth_ok = False
        if len(high) >= 15 and len(low) >= 15:
            # Find swing high and swing low in last 15 candles
            swing_high = float(np.max(high[-15:]))
            # swing low = the low before the swing high
            sh_idx = int(np.argmax(high[-15:]))
            swing_low = float(np.min(low[-15:-15+max(sh_idx, 1)])) if sh_idx > 0 else float(np.min(low[-15:-10]))
            prev_move = swing_high - swing_low
            if prev_move > 0:
                depth = (swing_high - entry) / prev_move
                if 0.25 <= depth <= 0.75:
                    pullback_depth_ok = True
                    met.append("pullback_depth_valid")
                else:
                    failed.append(f"pullback_depth_{depth:.0%}")
            else:
                failed.append("no_prior_move")
        else:
            failed.append("insufficient_swing_data")

        # ── NEW REQUIREMENT 3: Volume declining during pullback ──────
        volume_declining = False
        if len(volume) >= 8:
            pullback_vol = float(np.mean(volume[-3:]))
            upmove_vol = float(np.mean(volume[-6:-3]))
            if upmove_vol > 0 and pullback_vol < upmove_vol * 0.8:
                volume_declining = True
                met.append("volume_declining_pullback")
            else:
                failed.append("volume_not_declining")
        else:
            failed.append("insufficient_volume_data")

        # ── NEW REQUIREMENT 4: Bullish candle at bounce ──────────────
        bullish_bounce = False
        if len(close) >= 2 and len(high) >= 2 and len(low) >= 2:
            last_body = abs(close[-1] - close[-2]) if len(close) >= 2 else abs(close[-1] - low[-1])
            last_range = high[-1] - low[-1]
            avg_vol_20 = float(np.mean(volume[-min(20, len(volume)):])) if len(volume) >= 5 else 0
            if last_range > 0:
                body_pct = abs(close[-1] - (close[-2] if len(close) >= 2 else low[-1])) / last_range
                close_position = (close[-1] - low[-1]) / last_range
                if (body_pct >= 0.60 and close_position >= 0.60
                        and close[-1] > close[-2]
                        and (avg_vol_20 <= 0 or volume[-1] >= avg_vol_20)):
                    bullish_bounce = True
                    met.append("bullish_bounce_candle")
                else:
                    failed.append("no_bullish_bounce")
            else:
                failed.append("zero_range_candle")
        else:
            failed.append("insufficient_candle_data")

        # ── TRIGGER: Need core conditions + at least 2 of 4 new reqs ──
        core_ok = ("ema_stack_bullish" in met and "pullback_to_ema21" in met)
        new_reqs_met = sum([ema21_rising, pullback_depth_ok, volume_declining, bullish_bounce])

        if (len(met) >= 6 and core_ok and new_reqs_met >= 2 and atr > 0):
            stop = round(max(entry - ATR_STOP_LOSS_MULTIPLIER * atr, ema50 - 0.01 * atr), 2)
            tp1 = round(entry + ATR_TAKE_PROFIT_1_MULTIPLIER * atr, 2)
            tp2 = round(entry + ATR_TAKE_PROFIT_2_MULTIPLIER * atr, 2)
            result.update({
                "triggered": True, "direction": "BUY", "entry": entry,
                "stop": stop, "tp1": tp1, "tp2": tp2,
                "rr": _compute_rr(entry, stop, tp1),
                "signal_count": len(met),
            })

    except Exception as e:
        logger.debug(f"EMA pullback strategy error: {e}")

    return result


# ══════════════════════════════════════════════════════════════════════════
# 4. FIBONACCI CONFLUENCE REVERSAL
# ══════════════════════════════════════════════════════════════════════════

def detect_fibonacci(df_5m, indicators, df_15m=None) -> dict:
    """Fibonacci Confluence: price bounces off key Fib level with confirmation."""
    result = _empty_result(STRATEGY_FIBONACCI)
    met = result["conditions_met"]
    failed = result["conditions_failed"]

    try:
        source = df_15m if df_15m is not None and len(df_15m) >= 30 else df_5m
        if source is None or len(source) < 20:
            failed.append("insufficient_data")
            return result

        high_arr = source["High"].values.astype(float)
        low_arr = source["Low"].values.astype(float)

        # Find swing high and low (last 50 bars)
        lookback = min(50, len(source))
        swing_high = float(np.max(high_arr[-lookback:]))
        swing_low = float(np.min(low_arr[-lookback:]))

        if swing_high <= swing_low:
            failed.append("no_swing_range")
            return result

        # Calculate Fib levels
        diff = swing_high - swing_low
        fib_levels = {}
        for f in FIB_LEVELS:
            fib_levels[f] = round(swing_high - f * diff, 4)

        if df_5m is None or df_5m.empty:
            failed.append("no_5m_data")
            return result

        entry = float(df_5m["Close"].iloc[-1])
        atr = _sf(_sg(indicators, "atr", "value"))
        rsi = _sf(_sg(indicators, "rsi", "value"), 50)
        macd_bull = _sg(indicators, "macd", "bullish", default=False)
        rvol = _sf(_sg(indicators, "rvol", "rvol"), 0)

        # Condition 1: Price near key Fib level (0.382, 0.5, 0.618)
        near_fib = None
        key_fibs = [0.382, 0.5, 0.618]
        for f in key_fibs:
            level = fib_levels.get(f, 0)
            if level > 0 and abs(entry - level) / level * 100 <= 0.5:
                near_fib = f
                break

        if near_fib:
            met.append(f"near_fib_{near_fib}")
        else:
            failed.append("not_near_fib_level")

        # Condition 2: RSI showing reversal
        if rsi > 35 and rsi < 55:
            met.append("rsi_reversal_zone")
        else:
            failed.append("rsi_not_reversal")

        # Condition 3: Bullish candle pattern
        if len(df_5m) >= 3:
            o = float(df_5m["Open"].iloc[-1])
            h = float(df_5m["High"].iloc[-1])
            l = float(df_5m["Low"].iloc[-1])
            c = float(df_5m["Close"].iloc[-1])
            body = abs(c - o)
            candle = h - l
            lower_wick = min(o, c) - l
            if c > o or (candle > 0 and lower_wick >= 0.5 * candle):
                met.append("bullish_candle")
            else:
                failed.append("no_bullish_candle")
        else:
            failed.append("insufficient_candles")

        # Condition 4: MACD turning positive
        if macd_bull:
            met.append("macd_bullish")
        else:
            failed.append("macd_bearish")

        # Condition 5: Volume pickup
        if rvol >= 1.0:
            met.append("volume_pickup")
        else:
            failed.append("low_volume")

        # Need near_fib + at least 3 other conditions
        if near_fib and len(met) >= 4 and atr > 0:
            fib_below = 0
            for f in sorted(key_fibs + [0.786, 1.0]):
                level = fib_levels.get(f, 0)
                if level < entry:
                    fib_below = level
                    break
            if fib_below <= 0:
                fib_below = entry - ATR_STOP_LOSS_MULTIPLIER * atr

            fib_above = 0
            for f in sorted(key_fibs + [0.236, 0.0], reverse=True):
                level = fib_levels.get(f, 0)
                if level > entry:
                    fib_above = level
                    break
            if fib_above <= 0:
                fib_above = entry + ATR_TAKE_PROFIT_1_MULTIPLIER * atr

            stop = round(fib_below, 2)
            tp1 = round(fib_above, 2)
            tp2 = round(entry + ATR_TAKE_PROFIT_2_MULTIPLIER * atr, 2)

            result.update({
                "triggered": True, "direction": "BUY", "entry": entry,
                "stop": stop, "tp1": tp1, "tp2": tp2,
                "rr": _compute_rr(entry, stop, tp1),
                "signal_count": len(met),
            })

    except Exception as e:
        logger.debug(f"Fibonacci strategy error: {e}")

    return result


# ══════════════════════════════════════════════════════════════════════════
# 5. BOLLINGER BAND SQUEEZE BREAKOUT
# ══════════════════════════════════════════════════════════════════════════

def detect_bb_squeeze(df_5m, indicators, df_15m=None) -> dict:
    """BB Squeeze: bandwidth narrows then price breaks out."""
    result = _empty_result(STRATEGY_BB_SQUEEZE)
    met = result["conditions_met"]
    failed = result["conditions_failed"]

    try:
        if df_5m is None or len(df_5m) < 25:
            failed.append("insufficient_data")
            return result

        close = df_5m["Close"].values.astype(float)
        entry = close[-1]

        bb_upper = _sf(_sg(indicators, "bollinger", "upper"))
        bb_lower = _sf(_sg(indicators, "bollinger", "lower"))
        bb_mid = _sf(_sg(indicators, "bollinger", "middle"))
        bb_width = _sf(_sg(indicators, "bollinger", "bandwidth"))
        atr = _sf(_sg(indicators, "atr", "value"))
        rvol = _sf(_sg(indicators, "rvol", "rvol"), 0)
        macd_bull = _sg(indicators, "macd", "bullish", default=False)
        macd_hist = _sf(_sg(indicators, "macd", "histogram"))

        # Condition 1: BB squeeze detected
        squeeze = _sg(indicators, "bollinger", "squeeze", default=False)
        if squeeze or (bb_width > 0 and bb_width < 0.04):
            met.append("bb_squeeze")
        else:
            failed.append("no_squeeze")

        # Condition 2: Price broke above upper BB
        if bb_upper > 0 and entry > bb_upper:
            met.append("above_upper_bb")
        else:
            failed.append("inside_bands")

        # Condition 3: Volume surge
        if rvol >= RVOL_HIGH:
            met.append("volume_surge")
        else:
            failed.append("low_volume")

        # Condition 4: MACD turning positive
        if macd_bull or macd_hist > 0:
            met.append("macd_positive")
        else:
            failed.append("macd_negative")

        # Condition 5: Was inside bands recently
        if bb_upper > 0 and bb_lower > 0:
            was_inside = False
            for i in range(-4, -1):
                if i >= -len(close) and bb_lower <= close[i] <= bb_upper:
                    was_inside = True
                    break
            if was_inside:
                met.append("recent_inside_bands")
            else:
                failed.append("not_recent_squeeze")

        # Need at least 3 conditions with breakout
        if len(met) >= 3 and "above_upper_bb" in met and atr > 0:
            stop = round(bb_mid if bb_mid > 0 else entry - ATR_STOP_LOSS_MULTIPLIER * atr, 2)
            tp1 = round(entry + 2.0 * atr, 2)
            tp2 = round(entry + 3.5 * atr, 2)
            result.update({
                "triggered": True, "direction": "BUY", "entry": entry,
                "stop": stop, "tp1": tp1, "tp2": tp2,
                "rr": _compute_rr(entry, stop, tp1),
                "signal_count": len(met),
            })

    except Exception as e:
        logger.debug(f"BB squeeze strategy error: {e}")

    return result


# ══════════════════════════════════════════════════════════════════════════
# MASTER DETECTOR
# ══════════════════════════════════════════════════════════════════════════

def detect_strategy(df_5m, indicators, orb_data=None, df_15m=None) -> dict:
    """Try all 5 strategies. Return the best triggered result."""
    best = None
    best_signals = 0

    detectors = [
        lambda: detect_vwap_breakout(df_5m, indicators, orb_data),
        lambda: detect_orb(df_5m, indicators, orb_data),
        lambda: detect_ema_pullback(df_5m, indicators, df_15m),
        lambda: detect_fibonacci(df_5m, indicators, df_15m),
        lambda: detect_bb_squeeze(df_5m, indicators, df_15m),
    ]

    for detector in detectors:
        try:
            result = detector()
            if result.get("triggered") and result.get("signal_count", 0) > best_signals:
                best = result
                best_signals = result["signal_count"]
        except Exception as e:
            logger.debug(f"Strategy detector error: {e}")

    return best if best else _empty_result("None")


# ══════════════════════════════════════════════════════════════════════════
# NEAREST STRATEGY CLASSIFIER
# ══════════════════════════════════════════════════════════════════════════

def classify_nearest_strategy(indicators: dict) -> str:
    """Return which strategy is closest to triggering."""
    scores = {}

    vwap_bull = bool(_sg(indicators, "vwap", "bullish", default=False))
    rvol = _sf(_sg(indicators, "rvol", "rvol"), 0)
    scores[STRATEGY_VWAP_BREAKOUT] = (1 if vwap_bull else 0) + min(rvol / RVOL_HIGH, 1)

    scores[STRATEGY_ORB] = 0.5

    e9 = _sf(_sg(indicators, "ema_stack", "ema9"))
    e21 = _sf(_sg(indicators, "ema_stack", "ema21"))
    e50 = _sf(_sg(indicators, "ema_stack", "ema50"))
    adx = _sf(_sg(indicators, "adx", "value"))
    stack_ok = 1 if (e9 > e21 > e50 > 0) else 0
    adx_s = min(adx / ADX_TRENDING, 1) if adx > 0 else 0
    scores[STRATEGY_EMA_PULLBACK] = stack_ok + adx_s

    rsi = _sf(_sg(indicators, "rsi", "value"), 50)
    scores[STRATEGY_FIBONACCI] = 1.0 if 35 < rsi < 55 else 0.3

    squeeze = bool(_sg(indicators, "bollinger", "squeeze", default=False))
    scores[STRATEGY_BB_SQUEEZE] = 1.5 if squeeze else 0.3

    return max(scores, key=scores.get)


# ══════════════════════════════════════════════════════════════════════════
# STRATEGY SIMULATION
# ══════════════════════════════════════════════════════════════════════════

def simulate_strategy(strategy_name: str, entry: float, stop: float,
                      tp1: float, tp2: float, df_future) -> dict:
    """Walk forward through df_future to simulate trade outcome."""
    result = {"outcome": "timeout", "exit_price": entry, "r_multiple": 0.0, "bars_held": 0}

    if df_future is None or df_future.empty or entry <= 0:
        return result

    # All signals are BUY only (SELL removed)
    risk = abs(entry - stop)
    if risk <= 0:
        return result

    max_bars = min(len(df_future), BACKTEST_MAX_HOLD_BARS)

    for i in range(max_bars):
        try:
            high = float(df_future["High"].iloc[i])
            low = float(df_future["Low"].iloc[i])
            close = float(df_future["Close"].iloc[i])
        except (IndexError, KeyError):
            break

        if low <= stop:
            return {"outcome": "loss", "exit_price": stop,
                    "r_multiple": round((stop - entry) / risk, 2), "bars_held": i + 1}
        if high >= tp2:
            return {"outcome": "win", "exit_price": tp2,
                    "r_multiple": round((tp2 - entry) / risk, 2), "bars_held": i + 1}
        if high >= tp1:
            return {"outcome": "win", "exit_price": tp1,
                    "r_multiple": round((tp1 - entry) / risk, 2), "bars_held": i + 1}

        result["bars_held"] = i + 1

    # Timeout exit
    try:
        last_close = float(df_future["Close"].iloc[min(max_bars - 1, len(df_future) - 1)])
        result["r_multiple"] = round((last_close - entry) / risk, 2)
        result["exit_price"] = last_close
    except Exception:
        pass

    return result


# ══════════════════════════════════════════════════════════════════════════
# CLOSED-MARKET SETUP LABEL - disabled
# ══════════════════════════════════════════════════════════════════════════

def get_afterhours_setup_label(ticker: str, rsi=None, vwap_pct=None,
                               price_change=None) -> str:
    """
    Legacy compatibility hook. Closed-market setup labels are disabled.
    """
    return "\u26aa No Active Setup"

    try:
        rsi = float(rsi) if rsi is not None else 50.0
        vwap_pct = float(vwap_pct) if vwap_pct is not None else 0.0
        price_change = float(price_change) if price_change is not None else 0.0
    except (TypeError, ValueError):
        rsi, vwap_pct, price_change = 50.0, 0.0, 0.0

    if rsi < 35 and price_change < -2.0:
        return "\U0001f440 Watch: Oversold Bounce Setup"
    elif rsi > 60 and vwap_pct > 1.0 and price_change > 1.0:
        return "\U0001f440 Watch: VWAP Momentum Continuation"
    elif rsi < 45 and vwap_pct < -1.0:
        return "\U0001f440 Watch: VWAP Reclaim Setup"
    elif 45 < rsi < 60 and vwap_pct > 0:
        return "\U0001f440 Watch: EMA Pullback Setup"
    elif price_change > 2.0:
        return "\U0001f440 Watch: Gap and Go Continuation"
    else:
        return "\u26aa No Active Setup"
