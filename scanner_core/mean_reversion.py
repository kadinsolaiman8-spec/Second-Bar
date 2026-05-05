# mean_reversion.py — Three mean reversion strategies for RANGING market state
# Only active during STATE_RANGING. Completely separate from trend-following strategies.

import logging
import math
from typing import Optional

import numpy as np
import pandas as pd

from scanner_core.config import (
    MARKET_TIMEZONE,
    ATR_STOP_LOSS_MULTIPLIER,
    MR_RSI_OVERSOLD, MR_RVOL_MIN, MR_DOWNTREND_MAX_CANDLES,
    MR_VWAP_ATR_DISTANCE, MR_VWAP_RSI_MAX, MR_VWAP_RVOL_MIN,
    MR_EMA_RSI_MIN, MR_EMA_RSI_MAX, MR_EMA_LOOKBACK_CANDLES,
    MR_WILLIAMS_OS,
)

logger = logging.getLogger(__name__)

# ── Strategy name constants ────────────────────────────────────────────────
MR_STRATEGY_OVERSOLD_BOUNCE = "MR: Oversold Bounce"
MR_STRATEGY_VWAP_REVERSION  = "MR: VWAP Reclaim"
MR_STRATEGY_EMA_BOUNCE      = "MR: EMA Bounce"

ALL_MR_STRATEGIES = [MR_STRATEGY_OVERSOLD_BOUNCE,
                     MR_STRATEGY_VWAP_REVERSION,
                     MR_STRATEGY_EMA_BOUNCE]


# ══════════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════════

def _safe_float(val, default=0.0) -> float:
    try:
        f = float(val)
        return default if math.isnan(f) or math.isinf(f) else f
    except (TypeError, ValueError):
        return default


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


def _calc_bb(df: pd.DataFrame, period: int = 20, std: float = 2.0) -> tuple:
    """Return (upper, middle, lower) Bollinger Bands."""
    if df is None or len(df) < period:
        return 0.0, 0.0, 0.0
    try:
        closes = pd.Series(df["Close"].values, dtype=float)
        sma = closes.rolling(period).mean().iloc[-1]
        stddev = closes.rolling(period).std().iloc[-1]
        return float(sma + std * stddev), float(sma), float(sma - std * stddev)
    except Exception:
        return 0.0, 0.0, 0.0


def _calc_vwap(df: pd.DataFrame) -> float:
    if df is None or len(df) < 3:
        return 0.0
    try:
        typical = (df["High"] + df["Low"] + df["Close"]) / 3
        cum_vol = df["Volume"].cumsum()
        cum_tp_vol = (typical * df["Volume"]).cumsum()
        vwap = (cum_tp_vol / cum_vol.replace(0, np.nan)).iloc[-1]
        return float(vwap) if not math.isnan(float(vwap)) else 0.0
    except Exception:
        return 0.0


def _calc_ema(df: pd.DataFrame, period: int) -> float:
    if df is None or len(df) < period:
        return 0.0
    try:
        closes = pd.Series(df["Close"].values, dtype=float)
        return float(closes.ewm(span=period, adjust=False).mean().iloc[-1])
    except Exception:
        return 0.0


def _calc_williams_r(df: pd.DataFrame, period: int = 14) -> float:
    if df is None or len(df) < period:
        return -50.0
    try:
        highs = df["High"].tail(period)
        lows = df["Low"].tail(period)
        close = float(df["Close"].iloc[-1])
        hh = float(highs.max())
        ll = float(lows.min())
        if hh == ll:
            return -50.0
        return float((hh - close) / (hh - ll) * -100)
    except Exception:
        return -50.0


def _calc_rvol(df: pd.DataFrame, period: int = 20) -> float:
    if df is None or len(df) < period + 1:
        return 1.0
    try:
        current_vol = float(df["Volume"].iloc[-1])
        avg_vol = float(df["Volume"].iloc[-period - 1:-1].mean())
        return current_vol / avg_vol if avg_vol > 0 else 1.0
    except Exception:
        return 1.0


def _count_consecutive_red_candles(df: pd.DataFrame) -> int:
    """Count consecutive bearish candles from the most recent bar backwards."""
    if df is None or len(df) < 2:
        return 0
    count = 0
    closes = df["Close"].values
    opens = df["Open"].values
    for i in range(len(closes) - 1, -1, -1):
        if closes[i] < opens[i]:
            count += 1
        else:
            break
    return count


def _was_above_vwap_recently(df: pd.DataFrame, lookback: int = 24) -> bool:
    """Check if price was above VWAP at some point in the last N candles."""
    if df is None or len(df) < lookback:
        return True  # can't tell, assume yes
    try:
        window = df.tail(lookback)
        typical = (window["High"] + window["Low"] + window["Close"]) / 3
        cum_vol = window["Volume"].cumsum()
        cum_tp_vol = (typical * window["Volume"]).cumsum()
        vwap_series = cum_tp_vol / cum_vol.replace(0, np.nan)
        return bool((window["Close"] > vwap_series).any())
    except Exception:
        return True


def _had_prior_uptrend(df: pd.DataFrame, lookback: int = 24) -> bool:
    """Check if stock had higher highs in the 2 hours before current bar."""
    if df is None or len(df) < lookback + 5:
        return False
    try:
        prior = df.iloc[-(lookback + 5):-5]
        highs = prior["High"].values
        half = len(highs) // 2
        return highs[:half].max() < highs[half:].max()
    except Exception:
        return False


def _has_hammer_or_engulfing(df: pd.DataFrame) -> bool:
    """Check if the last candle is a hammer or bullish engulfing."""
    if df is None or len(df) < 2:
        return False
    try:
        last = df.iloc[-1]
        prev = df.iloc[-2]
        body = abs(float(last["Close"]) - float(last["Open"]))
        wick_lo = min(float(last["Close"]), float(last["Open"])) - float(last["Low"])
        wick_hi = float(last["High"]) - max(float(last["Close"]), float(last["Open"]))

        # Hammer: body small, long lower wick
        if wick_lo > body * 2.0 and wick_hi < body:
            return True

        # Bullish engulfing
        if (float(last["Close"]) > float(last["Open"]) and
                float(last["Close"]) > float(prev["Open"]) and
                float(last["Open"]) < float(prev["Close"])):
            return True

        return False
    except Exception:
        return False


def _get_prior_swing_high(df: pd.DataFrame, lookback: int = 30) -> float:
    """Return the highest high in the prior N candles."""
    if df is None or len(df) < lookback:
        return 0.0
    try:
        return float(df.tail(lookback)["High"].max())
    except Exception:
        return 0.0


# ══════════════════════════════════════════════════════════════════════════
# STRATEGY 1: OVERSOLD BOUNCE
# ══════════════════════════════════════════════════════════════════════════

def detect_oversold_bounce(df_5m: pd.DataFrame,
                           spy_indicators: dict = None) -> Optional[dict]:
    """
    Trigger when RSI oversold, price at/below lower BB, Williams %R extreme,
    volume above average, not a falling knife, SPY not in downtrend.
    """
    if df_5m is None or len(df_5m) < 30:
        return None
    try:
        rsi = _calc_rsi(df_5m)
        bb_upper, bb_mid, bb_lower = _calc_bb(df_5m)
        wr = _calc_williams_r(df_5m)
        rvol = _calc_rvol(df_5m)
        atr = _calc_atr(df_5m)
        price = _safe_float(df_5m["Close"].iloc[-1])

        # SPY RSI check
        spy_rsi = 50.0
        if spy_indicators:
            spy_rsi = _safe_float(
                spy_indicators.get("rsi", {}).get("value", 50), 50.0)

        # All conditions must be true
        conditions = {
            "rsi_oversold": rsi <= MR_RSI_OVERSOLD,
            "at_lower_bb": bb_lower > 0 and price <= bb_lower * 1.005,
            "williams_extreme": wr <= MR_WILLIAMS_OS,
            "volume_elevated": rvol >= MR_RVOL_MIN,
            "not_falling_knife": _count_consecutive_red_candles(df_5m) <= MR_DOWNTREND_MAX_CANDLES,
            "spy_not_downtrend": spy_rsi >= 40,
        }

        if not all(conditions.values()):
            failed = [k for k, v in conditions.items() if not v]
            logger.debug(f"[MR] OversoldBounce failed: {failed}")
            return None

        if atr <= 0 or price <= 0:
            return None

        stop = round(price - 0.8 * atr, 2)
        tp1 = round(bb_mid, 2) if bb_mid > price else round(price + 1.5 * atr, 2)
        tp2 = round(bb_upper, 2) if bb_upper > price else round(price + 2.0 * atr, 2)
        tp3 = round(price + 2.0 * atr, 2)
        risk = abs(price - stop)
        rr = round((tp1 - price) / risk, 2) if risk > 0 else 0.0

        return {
            "triggered": True,
            "strategy": MR_STRATEGY_OVERSOLD_BOUNCE,
            "entry_price": price,
            "stop": stop,
            "tp1": tp1,
            "tp2": tp2,
            "tp3": tp3,
            "rr": rr,
            "stop_multiplier": 0.8,
            "conditions_met": list(conditions.keys()),
            "indicators": {
                "rsi": round(rsi, 1), "rvol": round(rvol, 2),
                "williams_r": round(wr, 1), "bb_lower": round(bb_lower, 2),
                "bb_mid": round(bb_mid, 2), "atr": round(atr, 4),
            },
        }
    except Exception as e:
        logger.debug(f"[MR] OversoldBounce error: {e}")
        return None


# ══════════════════════════════════════════════════════════════════════════
# STRATEGY 2: VWAP REVERSION
# ══════════════════════════════════════════════════════════════════════════

def detect_vwap_reversion(df_5m: pd.DataFrame) -> Optional[dict]:
    """
    Trigger when price is 2x ATR below VWAP, RSI oversold, stock was
    above VWAP recently, current candle has rejection wick, volume elevated.
    """
    if df_5m is None or len(df_5m) < 30:
        return None
    try:
        vwap = _calc_vwap(df_5m)
        rsi = _calc_rsi(df_5m)
        rvol = _calc_rvol(df_5m)
        atr = _calc_atr(df_5m)
        price = _safe_float(df_5m["Close"].iloc[-1])

        if vwap <= 0 or atr <= 0 or price <= 0:
            return None

        dist_from_vwap = (vwap - price) / atr  # in ATR units

        conditions = {
            "far_below_vwap": dist_from_vwap >= MR_VWAP_ATR_DISTANCE,
            "rsi_low": rsi <= MR_VWAP_RSI_MAX,
            "was_above_vwap": _was_above_vwap_recently(df_5m, lookback=24),
            "rejection_candle": _has_hammer_or_engulfing(df_5m),
            "volume_elevated": rvol >= MR_VWAP_RVOL_MIN,
        }

        if not all(conditions.values()):
            failed = [k for k, v in conditions.items() if not v]
            logger.debug(f"[MR] VWAPReversion failed: {failed}")
            return None

        stop = round(price - 0.7 * atr, 2)
        tp1 = round(vwap, 2)
        tp2 = round(vwap + 0.5 * atr, 2)
        tp3 = round(price + 2.0 * atr, 2)
        risk = abs(price - stop)
        rr = round((tp1 - price) / risk, 2) if risk > 0 else 0.0

        return {
            "triggered": True,
            "strategy": MR_STRATEGY_VWAP_REVERSION,
            "entry_price": price,
            "stop": stop,
            "tp1": tp1,
            "tp2": tp2,
            "tp3": tp3,
            "rr": rr,
            "stop_multiplier": 0.7,
            "conditions_met": list(conditions.keys()),
            "indicators": {
                "rsi": round(rsi, 1), "rvol": round(rvol, 2),
                "vwap": round(vwap, 2), "dist_atr": round(dist_from_vwap, 2),
                "atr": round(atr, 4),
            },
        }
    except Exception as e:
        logger.debug(f"[MR] VWAPReversion error: {e}")
        return None


# ══════════════════════════════════════════════════════════════════════════
# STRATEGY 3: EMA BOUNCE
# ══════════════════════════════════════════════════════════════════════════

def detect_ema_bounce(df_5m: pd.DataFrame, df_15m: pd.DataFrame = None) -> Optional[dict]:
    """
    Trigger when price touches EMA50 on 15m, EMA50 is flat/rising,
    RSI pulled back to 35-50, hammer/engulfing forms, prior uptrend exists.
    """
    source_df = df_15m if df_15m is not None and len(df_15m) >= 50 else df_5m
    if source_df is None or len(source_df) < 50:
        return None
    try:
        ema50 = _calc_ema(source_df, 50)
        rsi = _calc_rsi(source_df)
        atr = _calc_atr(source_df)
        price = _safe_float(source_df["Close"].iloc[-1])

        if ema50 <= 0 or atr <= 0 or price <= 0:
            return None

        # EMA50 is flat or rising (compare last 5 bars)
        ema50_prev = _calc_ema(source_df.iloc[:-5], 50) if len(source_df) > 55 else ema50
        ema50_slope = (ema50 - ema50_prev) / (ema50_prev + 1e-9) * 100

        conditions = {
            "touching_ema50": abs(price - ema50) / atr <= 0.5,
            "ema50_not_falling": ema50_slope > -0.5,
            "rsi_pulled_back": MR_EMA_RSI_MIN <= rsi <= MR_EMA_RSI_MAX,
            "bullish_candle": _has_hammer_or_engulfing(source_df),
            "prior_uptrend": _had_prior_uptrend(source_df, lookback=MR_EMA_LOOKBACK_CANDLES),
        }

        if not all(conditions.values()):
            failed = [k for k, v in conditions.items() if not v]
            logger.debug(f"[MR] EMABounce failed: {failed}")
            return None

        stop_candidate1 = price - 1.0 * atr
        stop_candidate2 = ema50 - 0.1 * atr
        stop = round(max(stop_candidate1, stop_candidate2), 2)
        tp1 = round(price + 1.5 * atr, 2)
        swing_high = _get_prior_swing_high(source_df, lookback=30)
        tp2 = round(swing_high, 2) if swing_high > price + 0.5 * atr else round(price + 2.5 * atr, 2)
        tp3 = round(price + 3.5 * atr, 2)
        risk = abs(price - stop)
        rr = round((tp1 - price) / risk, 2) if risk > 0 else 0.0

        return {
            "triggered": True,
            "strategy": MR_STRATEGY_EMA_BOUNCE,
            "entry_price": price,
            "stop": stop,
            "tp1": tp1,
            "tp2": tp2,
            "tp3": tp3,
            "rr": rr,
            "stop_multiplier": 1.0,
            "conditions_met": list(conditions.keys()),
            "indicators": {
                "rsi": round(rsi, 1), "ema50": round(ema50, 2),
                "ema50_slope": round(ema50_slope, 3), "atr": round(atr, 4),
            },
        }
    except Exception as e:
        logger.debug(f"[MR] EMABounce error: {e}")
        return None


# ══════════════════════════════════════════════════════════════════════════
# MASTER MEAN REVERSION DETECTOR
# ══════════════════════════════════════════════════════════════════════════

def detect_mean_reversion(df_5m: pd.DataFrame, df_15m: pd.DataFrame = None,
                          spy_indicators: dict = None) -> Optional[dict]:
    """
    Try all three mean reversion strategies in priority order.
    Returns the first triggered result, or None if none fire.
    """
    # Try VWAP Reversion first (requires least conditions)
    result = detect_vwap_reversion(df_5m)
    if result:
        return result

    # Then Oversold Bounce
    result = detect_oversold_bounce(df_5m, spy_indicators)
    if result:
        return result

    # Then EMA Bounce (needs 15m data ideally)
    result = detect_ema_bounce(df_5m, df_15m)
    if result:
        return result

    return None


def is_mean_reversion_strategy(strategy_name: str) -> bool:
    """Return True if the strategy is a mean reversion strategy."""
    return strategy_name in ALL_MR_STRATEGIES
