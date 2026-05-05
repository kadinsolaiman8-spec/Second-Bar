# market_state.py — Market State Classification Engine
# Classifies current market into STRONG_TREND / WEAK_TREND / RANGING / HIGH_VOLATILITY
# Updated every 5 minutes; drives strategy selection, thresholds, and alert gating.

import asyncio
import logging
import math
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from typing import Optional

import numpy as np
import pandas as pd
import pytz
import yfinance as yf

from config import (
    MARKET_TIMEZONE, SPY_TICKER,
    MS_ADX_STRONG_TREND, MS_ADX_WEAK_TREND, MS_ADX_RANGING,
    MS_RSI_STRONG_MIN, MS_RSI_STRONG_MAX,
    MS_RSI_RANGING_MIN, MS_RSI_RANGING_MAX,
    MS_ATR_HIGH_VOL_RATIO, MS_SPY_MOVE_HIGH_VOL,
    MS_VIX_HIGH_VOL, MS_UPDATE_INTERVAL_SECONDS,
    MS_STRONG_TREND_MAX_ALERTS_PER_HOUR,
    MS_WEAK_TREND_MAX_ALERTS_PER_HOUR,
    MS_RANGING_MAX_ALERTS_PER_HOUR,
    MS_STRONG_TREND_MIN_CONFIDENCE,
    MS_WEAK_TREND_MIN_CONFIDENCE,
    MS_RANGING_MIN_CONFIDENCE,
    STRATEGY_VWAP_BREAKOUT, STRATEGY_ORB,
    STRATEGY_EMA_PULLBACK, STRATEGY_FIBONACCI, STRATEGY_BB_SQUEEZE,
    VIX_TICKER,
)

logger = logging.getLogger(__name__)
ET = pytz.timezone(MARKET_TIMEZONE)

_executor = ThreadPoolExecutor(max_workers=2)

# ── State constants ────────────────────────────────────────────────────────
STATE_STRONG_TREND   = "STRONG_TREND"
STATE_WEAK_TREND     = "WEAK_TREND"
STATE_RANGING        = "RANGING"
STATE_HIGH_VOLATILITY = "HIGH_VOLATILITY"

# ── Global state ───────────────────────────────────────────────────────────
_current_state: str = STATE_WEAK_TREND
_state_entered_at: datetime = datetime.now(ET)
_state_data: dict = {}          # full supporting data from last classification
_alert_count_this_hour: int = 0
_hour_window_start: datetime = datetime.now(ET)
_last_update: float = 0.0       # unix timestamp of last classification


# ══════════════════════════════════════════════════════════════════════════
# DATA FETCHING
# ══════════════════════════════════════════════════════════════════════════

def _fetch_spy_data() -> Optional[pd.DataFrame]:
    """Fetch SPY 5m data synchronously."""
    try:
        df = yf.download(SPY_TICKER, period="5d", interval="5m",
                         progress=False, auto_adjust=True)
        if df is not None and isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        return df if df is not None and len(df) >= 30 else None
    except Exception as e:
        logger.debug(f"[MS] SPY fetch failed: {e}")
        return None


def _fetch_spy_15m() -> Optional[pd.DataFrame]:
    try:
        df = yf.download(SPY_TICKER, period="5d", interval="15m",
                         progress=False, auto_adjust=True)
        if df is not None and isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        return df if df is not None and len(df) >= 20 else None
    except Exception as e:
        logger.debug(f"[MS] SPY 15m fetch failed: {e}")
        return None


def _fetch_vix() -> float:
    try:
        df = yf.download(VIX_TICKER, period="1d", interval="5m",
                         progress=False, auto_adjust=True)
        if df is not None and len(df) > 0:
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            return float(df["Close"].iloc[-1])
    except Exception:
        pass
    return 0.0


# ══════════════════════════════════════════════════════════════════════════
# INDICATOR CALCULATIONS
# ══════════════════════════════════════════════════════════════════════════

def _calc_adx(df: pd.DataFrame, period: int = 14) -> float:
    """Calculate ADX from OHLC DataFrame."""
    if df is None or len(df) < period + 5:
        return 0.0
    try:
        high = df["High"].values
        low = df["Low"].values
        close = df["Close"].values

        plus_dm = []
        minus_dm = []
        tr_list = []
        for i in range(1, len(high)):
            h_diff = high[i] - high[i - 1]
            l_diff = low[i - 1] - low[i]
            plus_dm.append(h_diff if h_diff > l_diff and h_diff > 0 else 0)
            minus_dm.append(l_diff if l_diff > h_diff and l_diff > 0 else 0)
            tr_list.append(max(high[i] - low[i],
                               abs(high[i] - close[i - 1]),
                               abs(low[i] - close[i - 1])))

        plus_dm = np.array(plus_dm, dtype=float)
        minus_dm = np.array(minus_dm, dtype=float)
        tr_arr = np.array(tr_list, dtype=float)

        atr_s = pd.Series(tr_arr).ewm(span=period, adjust=False).mean()
        plus_di = 100 * pd.Series(plus_dm).ewm(span=period, adjust=False).mean() / (atr_s + 1e-9)
        minus_di = 100 * pd.Series(minus_dm).ewm(span=period, adjust=False).mean() / (atr_s + 1e-9)
        dx = (abs(plus_di - minus_di) / (abs(plus_di + minus_di) + 1e-9)) * 100
        adx = dx.ewm(span=period, adjust=False).mean()
        return float(adx.iloc[-1])
    except Exception:
        return 0.0


def _calc_rsi(df: pd.DataFrame, period: int = 14) -> float:
    if df is None or len(df) < period + 1:
        return 50.0
    try:
        closes = pd.Series(df["Close"].values, dtype=float)
        delta = closes.diff()
        gain = delta.clip(lower=0).ewm(com=period - 1, adjust=False).mean()
        loss = (-delta.clip(upper=0)).ewm(com=period - 1, adjust=False).mean()
        rs = gain / (loss + 1e-9)
        rsi = 100 - (100 / (1 + rs))
        return float(rsi.iloc[-1])
    except Exception:
        return 50.0


def _calc_ema(series: pd.Series, period: int) -> float:
    if series is None or len(series) < period:
        return 0.0
    try:
        return float(series.ewm(span=period, adjust=False).mean().iloc[-1])
    except Exception:
        return 0.0


def _calc_atr(df: pd.DataFrame, period: int = 14) -> float:
    if df is None or len(df) < period + 1:
        return 0.0
    try:
        high = df["High"].values
        low = df["Low"].values
        close = df["Close"].values
        trs = [max(high[i] - low[i],
                   abs(high[i] - close[i - 1]),
                   abs(low[i] - close[i - 1]))
               for i in range(1, len(high))]
        return float(np.mean(trs[-period:]))
    except Exception:
        return 0.0


def _higher_highs_higher_lows(df: pd.DataFrame, lookback: int = 8) -> bool:
    """Return True if df shows higher highs and higher lows over last N candles."""
    if df is None or len(df) < lookback:
        return False
    try:
        window = df.tail(lookback)
        highs = window["High"].values
        lows = window["Low"].values
        half = lookback // 2
        return (highs[:half].max() < highs[half:].max() and
                lows[:half].min() < lows[half:].min())
    except Exception:
        return False


# ══════════════════════════════════════════════════════════════════════════
# CLASSIFICATION ENGINE
# ══════════════════════════════════════════════════════════════════════════

def classify_market_state(spy_5m: pd.DataFrame = None,
                          spy_15m: pd.DataFrame = None,
                          vix: float = 0.0) -> dict:
    """
    Classify current market state from SPY data.
    Returns dict with 'state', 'criteria_met', supporting indicator values.
    """
    if spy_5m is None or len(spy_5m) < 30:
        return {"state": STATE_WEAK_TREND, "criteria_met": 0,
                "adx": 0.0, "rsi": 50.0, "ema20": 0.0, "ema50": 0.0,
                "atr": 0.0, "atr_avg": 0.0, "vix": vix,
                "reason": "Insufficient SPY data"}

    # Calculate indicators
    closes = spy_5m["Close"]
    adx = _calc_adx(spy_5m)
    rsi = _calc_rsi(spy_5m)
    ema20 = _calc_ema(closes, 20)
    ema50 = _calc_ema(closes, 50)
    current_price = float(closes.iloc[-1])

    atr_current = _calc_atr(spy_5m)
    # 20-period average ATR (look at older slices)
    atr_vals = []
    for k in range(max(0, len(spy_5m) - 40), len(spy_5m) - 10, 5):
        atr_vals.append(_calc_atr(spy_5m.iloc[k:k + 15]))
    atr_avg = float(np.mean(atr_vals)) if atr_vals else atr_current
    atr_ratio = atr_current / (atr_avg + 1e-9)

    # 30-minute price move (6 bars of 5m)
    spy_30m_move = 0.0
    if len(spy_5m) >= 6:
        price_30m_ago = float(spy_5m["Close"].iloc[-6])
        if price_30m_ago > 0:
            spy_30m_move = abs((current_price - price_30m_ago) / price_30m_ago * 100)

    hh_hl = _higher_highs_higher_lows(spy_15m if spy_15m is not None else spy_5m)

    # ── STATE 4: HIGH VOLATILITY CHAOS ───────────────────────────────
    high_vol_signals = 0
    if atr_ratio > MS_ATR_HIGH_VOL_RATIO:
        high_vol_signals += 1
    if vix > 0 and vix > MS_VIX_HIGH_VOL:
        high_vol_signals += 1
    if spy_30m_move > MS_SPY_MOVE_HIGH_VOL:
        high_vol_signals += 1

    if high_vol_signals >= 2:
        return {
            "state": STATE_HIGH_VOLATILITY,
            "criteria_met": high_vol_signals,
            "adx": round(adx, 1), "rsi": round(rsi, 1),
            "ema20": round(ema20, 2), "ema50": round(ema50, 2),
            "atr": round(atr_current, 4), "atr_avg": round(atr_avg, 4),
            "atr_ratio": round(atr_ratio, 2), "vix": round(vix, 1),
            "spy_30m_move": round(spy_30m_move, 2),
            "reason": f"ATR {atr_ratio:.1f}x avg, VIX {vix:.0f}, 30m move {spy_30m_move:.1f}%",
        }

    # ── STATE 3: RANGING ─────────────────────────────────────────────
    ranging_criteria = 0
    if adx < MS_ADX_RANGING:
        ranging_criteria += 1
    if ema20 > 0 and abs(current_price - ema20) / ema20 < 0.005:
        ranging_criteria += 1  # Price within 0.5% of EMA20
    if MS_RSI_RANGING_MIN <= rsi <= MS_RSI_RANGING_MAX:
        ranging_criteria += 1
    if atr_ratio < 0.85:
        ranging_criteria += 1  # ATR contracting

    if ranging_criteria >= 3:
        return {
            "state": STATE_RANGING,
            "criteria_met": ranging_criteria,
            "adx": round(adx, 1), "rsi": round(rsi, 1),
            "ema20": round(ema20, 2), "ema50": round(ema50, 2),
            "atr": round(atr_current, 4), "atr_avg": round(atr_avg, 4),
            "atr_ratio": round(atr_ratio, 2), "vix": round(vix, 1),
            "reason": f"ADX {adx:.0f}, RSI {rsi:.0f}, ATR contracting",
        }

    # ── STATE 1: STRONG TREND ────────────────────────────────────────
    strong_criteria = 0
    if adx >= MS_ADX_STRONG_TREND:
        strong_criteria += 1
    if ema20 > 0 and ema50 > 0 and current_price > ema20 > ema50:
        strong_criteria += 1
    if MS_RSI_STRONG_MIN <= rsi <= MS_RSI_STRONG_MAX:
        strong_criteria += 1
    if hh_hl:
        strong_criteria += 1

    if strong_criteria >= 3:
        return {
            "state": STATE_STRONG_TREND,
            "criteria_met": strong_criteria,
            "adx": round(adx, 1), "rsi": round(rsi, 1),
            "ema20": round(ema20, 2), "ema50": round(ema50, 2),
            "atr": round(atr_current, 4), "atr_avg": round(atr_avg, 4),
            "atr_ratio": round(atr_ratio, 2), "vix": round(vix, 1),
            "hh_hl": hh_hl,
            "reason": f"ADX {adx:.0f}, RSI {rsi:.0f}, EMA aligned, HH/HL: {hh_hl}",
        }

    # ── STATE 2: WEAK TREND (default) ────────────────────────────────
    weak_criteria = 0
    if adx >= MS_ADX_WEAK_TREND:
        weak_criteria += 1
    if ema20 > 0 and current_price > ema20:
        weak_criteria += 1
    if 48 <= rsi <= 70:
        weak_criteria += 1

    return {
        "state": STATE_WEAK_TREND,
        "criteria_met": weak_criteria,
        "adx": round(adx, 1), "rsi": round(rsi, 1),
        "ema20": round(ema20, 2), "ema50": round(ema50, 2),
        "atr": round(atr_current, 4), "atr_avg": round(atr_avg, 4),
        "atr_ratio": round(atr_ratio, 2), "vix": round(vix, 1),
        "hh_hl": hh_hl,
        "reason": f"ADX {adx:.0f}, RSI {rsi:.0f}, partial conditions",
    }


# ══════════════════════════════════════════════════════════════════════════
# UPDATE ENGINE
# ══════════════════════════════════════════════════════════════════════════

async def update_market_state(alert_channel=None, bot=None) -> str:
    """
    Fetch SPY data and reclassify market state.
    Sends a transition message to alert_channel if state changed.
    Returns the new state string.
    """
    global _current_state, _state_entered_at, _state_data, _last_update

    try:
        loop = asyncio.get_event_loop()
        spy_5m, spy_15m, vix = await asyncio.gather(
            loop.run_in_executor(_executor, _fetch_spy_data),
            loop.run_in_executor(_executor, _fetch_spy_15m),
            loop.run_in_executor(_executor, _fetch_vix),
        )

        data = classify_market_state(spy_5m, spy_15m, vix)
        new_state = data["state"]
        old_state = _current_state

        _last_update = time.time()
        _state_data = data

        if new_state != old_state:
            _current_state = new_state
            _state_entered_at = datetime.now(ET)
            print(f"[MS] State transition: {old_state} → {new_state}")
            print(f"[MS] Data: {data.get('reason', '')}")

            if alert_channel is not None:
                msg = _format_transition_message(old_state, new_state, data)
                try:
                    await alert_channel.send(msg)
                except Exception as e:
                    logger.debug(f"[MS] Channel send failed: {e}")
        else:
            print(f"[MS] State stable: {_current_state} | ADX={data.get('adx', 0):.0f} RSI={data.get('rsi', 0):.0f}")

        return new_state

    except Exception as e:
        logger.error(f"[MS] update_market_state failed: {e}")
        return _current_state


def _format_transition_message(old_state: str, new_state: str, data: dict) -> str:
    state_labels = {
        STATE_STRONG_TREND: "STRONG TREND \U0001f4c8",
        STATE_WEAK_TREND: "WEAK TREND \u26a0\ufe0f",
        STATE_RANGING: "RANGING \u2194\ufe0f",
        STATE_HIGH_VOLATILITY: "HIGH VOLATILITY \u26d4",
    }
    old_label = state_labels.get(old_state, old_state)
    new_label = state_labels.get(new_state, new_state)

    lines = [
        f"\U0001f4ca **Market State Changed: {old_label} \u2192 {new_label}**",
    ]

    if new_state == STATE_HIGH_VOLATILITY:
        atr_ratio = data.get("atr_ratio", 0)
        lines.append(f"\u26d4 **HIGH VOLATILITY DETECTED**")
        lines.append(f"Win rate in this condition: ~18%")
        lines.append(f"All trend alerts paused until market calms.")
        if atr_ratio > 0:
            lines.append(f"Current SPY ATR expansion: {atr_ratio:.0%}")
        lines.append(f"ADX: {data.get('adx', 0):.1f} | RSI: {data.get('rsi', 0):.1f} | VIX: {data.get('vix', 0):.1f}")
    elif new_state == STATE_RANGING:
        lines.append(f"\U0001f4ca **RANGING MARKET DETECTED**")
        lines.append(f"Win rate for trend signals in this condition: 0%")
        lines.append(f"Trend alerts paused. Watching for mean reversion setups.")
        lines.append(f"ADX: {data.get('adx', 0):.1f} | RSI: {data.get('rsi', 0):.1f}")
    elif new_state == STATE_WEAK_TREND:
        lines.append(f"EMA Pullback ONLY active. Confidence threshold: 75. Max 2 alerts/hour.")
        lines.append(f"ADX: {data.get('adx', 0):.1f} | RSI: {data.get('rsi', 0):.1f}")
    elif new_state == STATE_STRONG_TREND:
        lines.append(f"\u2705 All strategies now active. Confidence threshold: 65. Up to 5 alerts/hour.")
        lines.append(f"ADX: {data.get('adx', 0):.1f} | RSI: {data.get('rsi', 0):.1f}")

    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════
# STATE QUERY FUNCTIONS
# ══════════════════════════════════════════════════════════════════════════

def get_current_market_state() -> str:
    """Return the current market state string."""
    return _current_state


def get_state_data() -> dict:
    """Return full state data dict from last classification."""
    return dict(_state_data)


def get_state_entered_at() -> datetime:
    return _state_entered_at


def get_time_in_state_minutes() -> int:
    """How many minutes the bot has been in the current state."""
    delta = datetime.now(ET) - _state_entered_at
    return max(0, int(delta.total_seconds() / 60))


def is_alerts_paused() -> bool:
    """True if market is in HIGH_VOLATILITY and all alerts should be paused."""
    return _current_state == STATE_HIGH_VOLATILITY


def get_min_confidence_for_state(state: str = None) -> int:
    """Return minimum confidence threshold for given state (or current)."""
    s = state or _current_state
    thresholds = {
        STATE_STRONG_TREND: 65,
        STATE_WEAK_TREND: 75,           # raised — only best signals
        STATE_RANGING: 75,              # mean-reversion only, must be strong
        STATE_HIGH_VOLATILITY: 999,     # block ALL trend signals
    }
    return thresholds.get(s, 75)


def get_active_strategies_for_state(state: str = None) -> list:
    """Return list of strategy names active in given state (or current).
    HIGH_VOLATILITY / RANGING: empty list — no trend strategies allowed.
    WEAK_TREND: EMA Pullback ONLY (best performer from backtest data).
    STRONG_TREND: all strategies."""
    s = state or _current_state
    all_strats = [STRATEGY_VWAP_BREAKOUT, STRATEGY_ORB,
                  STRATEGY_EMA_PULLBACK, STRATEGY_FIBONACCI, STRATEGY_BB_SQUEEZE]
    if s == STATE_STRONG_TREND:
        return all_strats
    elif s == STATE_WEAK_TREND:
        return [STRATEGY_EMA_PULLBACK]  # ONLY EMA Pullback — best performer
    elif s == STATE_RANGING:
        return []  # Mean reversion only — zero trend strategies
    else:  # HIGH_VOLATILITY
        return []  # Zero alerts of any kind


def is_strategy_allowed_for_state(strategy_name: str, state: str = None) -> bool:
    """Check if a specific strategy is allowed in the given/current market state."""
    allowed = get_active_strategies_for_state(state)
    if not allowed:
        return False
    for a in allowed:
        if a.lower() in strategy_name.lower() or strategy_name.lower() in a.lower():
            return True
    return False


def get_max_alerts_per_hour(state: str = None) -> int:
    s = state or _current_state
    limits = {
        STATE_STRONG_TREND: 5,
        STATE_WEAK_TREND: 2,            # tightened from 3
        STATE_RANGING: 1,               # only mean-reversion, very selective
        STATE_HIGH_VOLATILITY: 0,       # zero
    }
    return limits.get(s, 2)


def get_stop_multiplier_for_state() -> float:
    """Return ATR stop loss multiplier for current state."""
    multipliers = {
        STATE_STRONG_TREND: 1.0,
        STATE_WEAK_TREND: 1.3,
        STATE_RANGING: 0.8,
        STATE_HIGH_VOLATILITY: 1.5,
    }
    return multipliers.get(_current_state, 1.0)


def check_alert_rate_limit() -> bool:
    """
    Returns True if another alert is allowed (rate limit not exceeded).
    Resets count each hour.
    """
    global _alert_count_this_hour, _hour_window_start
    now = datetime.now(ET)
    if (now - _hour_window_start).total_seconds() >= 3600:
        _alert_count_this_hour = 0
        _hour_window_start = now

    max_alerts = get_max_alerts_per_hour()
    if max_alerts == 0:
        return False
    if _alert_count_this_hour >= max_alerts:
        return False
    return True


def record_alert_sent():
    """Call after each alert is sent to increment the hourly counter."""
    global _alert_count_this_hour
    _alert_count_this_hour += 1


def get_state_label(state: str = None) -> str:
    if state is None:
        state = _current_state
    labels = {
        STATE_STRONG_TREND: "\U0001f4c8 STRONG TREND",
        STATE_WEAK_TREND: "\u26a0\ufe0f WEAK TREND",
        STATE_RANGING: "\u2194\ufe0f RANGING",
        STATE_HIGH_VOLATILITY: "\u26d4 HIGH VOLATILITY",
    }
    return labels.get(state, state)


def format_state_card() -> str:
    """Format a full state card for the /state command."""
    state = _current_state
    data = _state_data
    minutes = get_time_in_state_minutes()
    active_strats = get_active_strategies_for_state()
    min_conf = get_min_confidence_for_state()
    max_alerts = get_max_alerts_per_hour()

    label = get_state_label(state)
    lines = [
        "\u2501" * 40,
        f"Market State Engine \u2014 Current Status",
        "\u2501" * 40,
        f"State:          {label}",
        f"In state for:   {minutes} minutes",
        f"",
        f"Supporting Data:",
        f"  SPY ADX:      {data.get('adx', 0):.1f}",
        f"  SPY RSI:      {data.get('rsi', 0):.1f}",
        f"  SPY EMA20:    ${data.get('ema20', 0):.2f}",
        f"  SPY EMA50:    ${data.get('ema50', 0):.2f}",
        f"  ATR Ratio:    {data.get('atr_ratio', 1.0):.2f}x (vs 20-period avg)",
        f"  VIX:          {data.get('vix', 0):.1f}",
        f"",
        f"Active Strategies:  {', '.join(active_strats) if active_strats else 'Mean Reversion Only' if state == STATE_RANGING else 'NONE (paused)'}",
        f"Min Confidence:     {min_conf if min_conf < 999 else 'N/A (paused)'}",
        f"Max Alerts/Hour:    {max_alerts}",
        f"Alerts This Hour:   {_alert_count_this_hour}",
        f"",
        f"Reason: {data.get('reason', 'N/A')}",
    ]

    # Show what would trigger a change
    lines.append("")
    lines.append("State Change Triggers:")
    if state == STATE_STRONG_TREND:
        lines.append(f"  \u2192 WEAK:  ADX drops below {MS_ADX_STRONG_TREND} or RSI exits 52-75")
        lines.append(f"  \u2192 RANGING: ADX below {MS_ADX_RANGING} + RSI in 40-60")
        lines.append(f"  \u2192 CHAOS: ATR > {MS_ATR_HIGH_VOL_RATIO}x avg or VIX > {MS_VIX_HIGH_VOL}")
    elif state == STATE_WEAK_TREND:
        lines.append(f"  \u2192 STRONG: ADX > {MS_ADX_STRONG_TREND} + 3 of 4 criteria met")
        lines.append(f"  \u2192 RANGING: ADX below {MS_ADX_RANGING} + RSI 40-60")
        lines.append(f"  \u2192 CHAOS: ATR > {MS_ATR_HIGH_VOL_RATIO}x avg or VIX > {MS_VIX_HIGH_VOL}")
    elif state == STATE_RANGING:
        lines.append(f"  \u2192 STRONG/WEAK: ADX rises above {MS_ADX_WEAK_TREND}")
        lines.append(f"  \u2192 CHAOS: ATR expands > {MS_ATR_HIGH_VOL_RATIO}x avg")
    elif state == STATE_HIGH_VOLATILITY:
        lines.append(f"  \u2192 Any state: ATR normalizes + VIX drops below {MS_VIX_HIGH_VOL}")

    lines.append("\u2501" * 40)
    return "\n".join(lines)
