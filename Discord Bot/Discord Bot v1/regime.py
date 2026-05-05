# regime.py — SPY-based market regime detection and strategy enablement

import logging
import math
from datetime import datetime, timedelta
from typing import Optional
import pandas as pd
import numpy as np
import pytz

from config import (
    REGIME_TRENDING, REGIME_RANGING, REGIME_HIGH_VOLATILITY,
    REGIME_ADX_TRENDING, REGIME_ADX_RANGING, REGIME_ATR_EXPANSION,
    TEST_REGIME_OVERRIDE_MINUTES,
    REGIME_RANGING_MIN_CONFIDENCE, REGIME_HIGH_VOL_MIN_CONFIDENCE,
    STRATEGY_VWAP_BREAKOUT, STRATEGY_ORB, STRATEGY_EMA_PULLBACK,
    STRATEGY_FIBONACCI, STRATEGY_BB_SQUEEZE, ALL_STRATEGIES,
    ADX_PERIOD, ATR_PERIOD, EMA_TREND, MARKET_TIMEZONE,
)

logger = logging.getLogger(__name__)
ET = pytz.timezone(MARKET_TIMEZONE)


# ══════════════════════════════════════════════════════════════════════════
# GLOBAL STATE
# ══════════════════════════════════════════════════════════════════════════

current_regime: dict = {
    "label": REGIME_TRENDING,
    "spy_adx": 0.0,
    "ema200_slope": "unknown",
    "atr_current": 0.0,
    "atr_avg_20": 0.0,
    "atr_ratio": 1.0,
    "description": "Initializing...",
    "last_updated": None,
    "duration_minutes": 0,
}

regime_history: list = []        # [(timestamp, label)]
regime_time_tracker: dict = {
    REGIME_TRENDING: 0,
    REGIME_RANGING: 0,
    REGIME_HIGH_VOLATILITY: 0,
}
_regime_last_ts: Optional[datetime] = None

# Test override
_test_override_label: Optional[str] = None
_test_override_expiry: Optional[datetime] = None


# ══════════════════════════════════════════════════════════════════════════
# INTERNAL CALCULATIONS
# ══════════════════════════════════════════════════════════════════════════

def _calc_adx(df: pd.DataFrame, period: int = ADX_PERIOD) -> float:
    """Calculate ADX from DataFrame using numpy."""
    try:
        if df is None or len(df) < period + 2:
            return 0.0
        high = df["High"].values.astype(float)
        low = df["Low"].values.astype(float)
        close = df["Close"].values.astype(float)

        tr = np.maximum(high[1:] - low[1:],
             np.maximum(np.abs(high[1:] - close[:-1]),
                        np.abs(low[1:] - close[:-1])))

        plus_dm = np.where((high[1:] - high[:-1]) > (low[:-1] - low[1:]),
                           np.maximum(high[1:] - high[:-1], 0), 0)
        minus_dm = np.where((low[:-1] - low[1:]) > (high[1:] - high[:-1]),
                            np.maximum(low[:-1] - low[1:], 0), 0)

        # Wilder smoothing
        atr = np.zeros(len(tr))
        atr[period - 1] = np.mean(tr[:period])
        for i in range(period, len(tr)):
            atr[i] = (atr[i - 1] * (period - 1) + tr[i]) / period

        plus_di = np.zeros(len(plus_dm))
        minus_di_arr = np.zeros(len(minus_dm))
        plus_di[period - 1] = np.mean(plus_dm[:period])
        minus_di_arr[period - 1] = np.mean(minus_dm[:period])
        for i in range(period, len(plus_dm)):
            plus_di[i] = (plus_di[i - 1] * (period - 1) + plus_dm[i]) / period
            minus_di_arr[i] = (minus_di_arr[i - 1] * (period - 1) + minus_dm[i]) / period

        # DI+ and DI-
        pdi = np.where(atr > 0, 100 * plus_di / atr, 0)
        mdi = np.where(atr > 0, 100 * minus_di_arr / atr, 0)

        dx = np.where((pdi + mdi) > 0, 100 * np.abs(pdi - mdi) / (pdi + mdi), 0)

        # ADX = smoothed DX
        adx_arr = np.zeros(len(dx))
        start = 2 * period - 1
        if start < len(dx):
            adx_arr[start] = np.mean(dx[period:start + 1])
            for i in range(start + 1, len(dx)):
                adx_arr[i] = (adx_arr[i - 1] * (period - 1) + dx[i]) / period

        val = adx_arr[-1]
        return round(float(val), 2) if not math.isnan(val) else 0.0
    except Exception as e:
        logger.debug(f"ADX calc error: {e}")
        return 0.0


def _calc_atr(df: pd.DataFrame, period: int = ATR_PERIOD) -> float:
    """Calculate current ATR."""
    try:
        if df is None or len(df) < period + 1:
            return 0.0
        high = df["High"].values.astype(float)
        low = df["Low"].values.astype(float)
        close = df["Close"].values.astype(float)

        tr = np.maximum(high[1:] - low[1:],
             np.maximum(np.abs(high[1:] - close[:-1]),
                        np.abs(low[1:] - close[:-1])))

        atr = np.mean(tr[-period:])
        return round(float(atr), 4) if not math.isnan(atr) else 0.0
    except Exception:
        return 0.0


def _calc_atr_avg(df: pd.DataFrame, period: int = ATR_PERIOD, lookback: int = 20) -> float:
    """Calculate 20-period average ATR for comparison."""
    try:
        if df is None or len(df) < period + lookback:
            return _calc_atr(df, period)
        high = df["High"].values.astype(float)
        low = df["Low"].values.astype(float)
        close = df["Close"].values.astype(float)

        tr = np.maximum(high[1:] - low[1:],
             np.maximum(np.abs(high[1:] - close[:-1]),
                        np.abs(low[1:] - close[:-1])))

        atrs = []
        for i in range(lookback):
            end = len(tr) - i
            start = max(0, end - period)
            atrs.append(np.mean(tr[start:end]))

        avg = np.mean(atrs)
        return round(float(avg), 4) if not math.isnan(avg) else 0.0
    except Exception:
        return _calc_atr(df, period)


def _calc_ema200_slope(df: pd.DataFrame) -> str:
    """Calculate EMA200 slope direction."""
    try:
        if df is None or len(df) < EMA_TREND + 5:
            return "unknown"
        close = df["Close"].values.astype(float)
        ema = np.zeros(len(close))
        ema[0] = close[0]
        k = 2.0 / (EMA_TREND + 1)
        for i in range(1, len(close)):
            ema[i] = close[i] * k + ema[i - 1] * (1 - k)

        recent = ema[-5:]
        if recent[-1] > recent[0]:
            return "rising"
        elif recent[-1] < recent[0]:
            return "falling"
        return "flat"
    except Exception:
        return "unknown"


# ══════════════════════════════════════════════════════════════════════════
# REGIME UPDATE
# ══════════════════════════════════════════════════════════════════════════

def update_regime(spy_df: pd.DataFrame) -> dict:
    """
    Classify market regime from SPY data.
    - HIGH VOLATILITY: ATR > 1.5x average
    - TRENDING: ADX >= 25
    - RANGING: ADX < 20
    - Between 20-25: keep previous
    """
    global current_regime, _regime_last_ts

    _update_time_tracking()

    if spy_df is None or len(spy_df) < 30:
        current_regime["description"] = "Insufficient SPY data"
        return current_regime

    now = datetime.now(ET)

    adx = _calc_adx(spy_df)
    atr_curr = _calc_atr(spy_df)
    atr_avg = _calc_atr_avg(spy_df)
    atr_ratio = round(atr_curr / atr_avg, 2) if atr_avg > 0 else 1.0
    ema_slope = _calc_ema200_slope(spy_df)

    prev_label = current_regime["label"]

    # Classify
    if atr_ratio >= REGIME_ATR_EXPANSION:
        label = REGIME_HIGH_VOLATILITY
    elif adx >= REGIME_ADX_TRENDING:
        label = REGIME_TRENDING
    elif adx < REGIME_ADX_RANGING:
        label = REGIME_RANGING
    else:
        label = prev_label  # hysteresis zone 20-25

    # Duration
    duration = 0
    if regime_history:
        last_change = regime_history[-1][0]
        duration = int((now - last_change).total_seconds() / 60)

    # Update state
    current_regime.update({
        "label": label,
        "spy_adx": adx,
        "ema200_slope": ema_slope,
        "atr_current": atr_curr,
        "atr_avg_20": atr_avg,
        "atr_ratio": atr_ratio,
        "description": get_regime_description(label),
        "last_updated": now,
        "duration_minutes": duration,
    })

    # Record history if changed
    if not regime_history or regime_history[-1][1] != label:
        regime_history.append((now, label))
        if len(regime_history) > 100:
            regime_history.pop(0)
        logger.info(f"Regime changed: {prev_label} → {label} (ADX={adx}, ATR ratio={atr_ratio})")

    _regime_last_ts = now
    return current_regime


# ══════════════════════════════════════════════════════════════════════════
# QUERY FUNCTIONS
# ══════════════════════════════════════════════════════════════════════════

def get_current_regime() -> dict:
    """Return current regime, checking test override first."""
    _check_override_expiry()
    if _test_override_label is not None:
        override = current_regime.copy()
        override["label"] = _test_override_label
        override["description"] = f"TEST OVERRIDE: {_test_override_label}"
        return override
    return current_regime


def get_regime_label() -> str:
    """Return just the regime label string."""
    return get_current_regime()["label"]


def get_active_strategies(regime_label: str = None) -> list:
    """Return which strategies are enabled in the given regime."""
    label = regime_label or get_regime_label()
    if label == REGIME_TRENDING:
        return list(ALL_STRATEGIES)
    elif label == REGIME_RANGING:
        return [STRATEGY_FIBONACCI, STRATEGY_BB_SQUEEZE, STRATEGY_EMA_PULLBACK]
    elif label == REGIME_HIGH_VOLATILITY:
        return [STRATEGY_VWAP_BREAKOUT, STRATEGY_ORB]
    return list(ALL_STRATEGIES)


def is_strategy_enabled(strategy_name: str, regime_label: str = None) -> bool:
    """Check if a specific strategy is active in current regime."""
    return strategy_name in get_active_strategies(regime_label)


def get_regime_description(regime_label: str = None) -> str:
    """Human-readable regime description."""
    label = regime_label or get_regime_label()
    if label == REGIME_TRENDING:
        return "Market is trending — all strategies active. Favor breakouts and momentum."
    elif label == REGIME_RANGING:
        return "Market is range-bound — using mean-reversion strategies. Fibonacci, BB Squeeze, EMA Pullback."
    elif label == REGIME_HIGH_VOLATILITY:
        return "High volatility detected — only momentum strategies. Wider stops recommended."
    return "Unknown regime state."


def get_regime_time_pct() -> dict:
    """Return percentage of session spent in each regime."""
    total = sum(regime_time_tracker.values())
    if total <= 0:
        return {k: 0.0 for k in regime_time_tracker}
    return {k: round(v / total * 100, 1) for k, v in regime_time_tracker.items()}


def get_regime_history(n: int = 20) -> list:
    """Return last n regime changes as (timestamp, label) tuples."""
    return regime_history[-n:]


# ══════════════════════════════════════════════════════════════════════════
# TEST OVERRIDE
# ══════════════════════════════════════════════════════════════════════════

def test_regime_override(label: str) -> dict:
    """Override regime for testing. Returns info dict."""
    global _test_override_label, _test_override_expiry
    valid = [REGIME_TRENDING, REGIME_RANGING, REGIME_HIGH_VOLATILITY]
    if label not in valid:
        return {"error": f"Invalid regime. Choose from: {valid}"}
    _test_override_label = label
    _test_override_expiry = datetime.now(ET) + timedelta(minutes=TEST_REGIME_OVERRIDE_MINUTES)
    logger.info(f"Regime override set: {label} for {TEST_REGIME_OVERRIDE_MINUTES}m")
    return {
        "label": label,
        "expiry": _test_override_expiry.strftime("%I:%M %p ET"),
        "minutes": TEST_REGIME_OVERRIDE_MINUTES,
    }


def clear_regime_override():
    """Clear any test override."""
    global _test_override_label, _test_override_expiry
    _test_override_label = None
    _test_override_expiry = None
    logger.info("Regime override cleared")


def _check_override_expiry():
    """Clear override if expired."""
    global _test_override_label, _test_override_expiry
    if _test_override_expiry and datetime.now(ET) >= _test_override_expiry:
        logger.info("Regime override expired")
        _test_override_label = None
        _test_override_expiry = None


# ══════════════════════════════════════════════════════════════════════════
# TIME TRACKING
# ══════════════════════════════════════════════════════════════════════════

def _update_time_tracking():
    """Update seconds spent in current regime."""
    global _regime_last_ts
    now = datetime.now(ET)
    if _regime_last_ts is not None:
        elapsed = (now - _regime_last_ts).total_seconds()
        label = current_regime["label"]
        if label in regime_time_tracker:
            regime_time_tracker[label] += elapsed
    _regime_last_ts = now


# ══════════════════════════════════════════════════════════════════════════
# RESET & SUMMARY
# ══════════════════════════════════════════════════════════════════════════

def reset_regime():
    """Reset all regime data to defaults."""
    global current_regime, _regime_last_ts
    current_regime = {
        "label": REGIME_TRENDING,
        "spy_adx": 0.0,
        "ema200_slope": "unknown",
        "atr_current": 0.0,
        "atr_avg_20": 0.0,
        "atr_ratio": 1.0,
        "description": "Reset",
        "last_updated": None,
        "duration_minutes": 0,
    }
    regime_history.clear()
    for k in regime_time_tracker:
        regime_time_tracker[k] = 0
    _regime_last_ts = None
    clear_regime_override()
    logger.info("Regime data reset")


def get_regime_summary() -> str:
    """Multi-line regime summary for Discord display."""
    r = get_current_regime()
    strats = get_active_strategies()
    pcts = get_regime_time_pct()

    lines = [
        f"Market Regime: {r['label']}",
        f"SPY ADX: {r['spy_adx']:.1f} | EMA200: {r['ema200_slope'].title()}",
        f"ATR: ${r['atr_current']:.2f} ({r['atr_ratio']:.1f}x avg)",
        f"Active Strategies: {len(strats)}/{len(ALL_STRATEGIES)}",
        f"  " + ", ".join(s.split()[0] for s in strats),
        f"Duration: {r['duration_minutes']}m",
        f"Time Split: T={pcts.get(REGIME_TRENDING, 0):.0f}% "
        f"R={pcts.get(REGIME_RANGING, 0):.0f}% "
        f"HV={pcts.get(REGIME_HIGH_VOLATILITY, 0):.0f}%",
    ]
    return "\n".join(lines)
