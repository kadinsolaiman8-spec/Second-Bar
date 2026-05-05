# institutional_flow.py — Detect institutional money flow using free data sources
# Three detectors: unusual volume signature, options activity, short interest

import logging
import math
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

_executor = ThreadPoolExecutor(max_workers=4)


def _safe_float(val, default=0.0) -> float:
    try:
        f = float(val)
        return default if math.isnan(f) or math.isinf(f) else f
    except (TypeError, ValueError):
        return default


# ══════════════════════════════════════════════════════════════════════════
# DETECTOR 1: UNUSUAL VOLUME SIGNATURE
# ══════════════════════════════════════════════════════════════════════════

def detect_institutional_volume(df_5m: pd.DataFrame) -> list:
    """
    Scan recent candles for institutional volume signatures:
    - ACCUMULATION: high volume, small price range (quiet buying)
    - MOMENTUM_BUY: high volume + strong up candle (conviction buying)
    Returns list of signal dicts (may be empty).
    """
    signals = []
    if df_5m is None or len(df_5m) < 10:
        return signals

    try:
        # Only look at last 10 candles for recency
        window = df_5m.tail(10)
        closes = window["Close"].values
        opens = window["Open"].values
        highs = window["High"].values
        lows = window["Low"].values
        volumes = window["Volume"].values
        times = window.index

        for i in range(3, len(window)):
            candle_vol = float(volumes[i])
            prev_vol = float(np.mean(volumes[max(0, i - 3):i]))
            if prev_vol <= 0:
                continue
            vol_ratio = candle_vol / prev_vol

            price_range = float(highs[i] - lows[i])
            prev_ranges = [float(highs[j] - lows[j]) for j in range(max(0, i - 3), i)]
            avg_range = float(np.mean(prev_ranges)) if prev_ranges else price_range
            range_ratio = price_range / avg_range if avg_range > 0 else 1.0

            # Accumulation: high volume, small range
            if vol_ratio > 2.5 and range_ratio < 0.7:
                signals.append({
                    "type": "ACCUMULATION",
                    "volume_ratio": round(vol_ratio, 2),
                    "time": str(times[i]),
                    "confidence": min(100, int(vol_ratio * 30)),
                    "bars_ago": len(window) - 1 - i,
                })

            # Momentum buy: high volume + strong up candle
            if float(opens[i]) > 0:
                close_pct = (float(closes[i]) - float(opens[i])) / float(opens[i]) * 100
            else:
                close_pct = 0.0
            if vol_ratio > 3.0 and close_pct > 0.5:
                signals.append({
                    "type": "MOMENTUM_BUY",
                    "volume_ratio": round(vol_ratio, 2),
                    "time": str(times[i]),
                    "confidence": min(100, int(vol_ratio * 25)),
                    "bars_ago": len(window) - 1 - i,
                })

    except Exception as e:
        logger.debug(f"[FLOW] Volume detection error: {e}")

    return signals


# ══════════════════════════════════════════════════════════════════════════
# DETECTOR 2: OPTIONS ACTIVITY
# ══════════════════════════════════════════════════════════════════════════

def check_options_activity(ticker: str) -> tuple:
    """
    Check for unusual call option buying via yfinance.
    Returns (signal_dict or None, bonus_points 0-15).
    """
    try:
        import yfinance as yf
        stock = yf.Ticker(ticker)

        expirations = stock.options
        if not expirations:
            return None, 0

        nearest = expirations[0]
        try:
            chain = stock.option_chain(nearest)
            calls = chain.calls
        except Exception:
            return None, 0

        if calls is None or len(calls) == 0:
            return None, 0

        # Compute vol/OI ratio safely
        calls = calls.copy()
        calls["vol_oi_ratio"] = (
            calls["volume"].fillna(0) / (calls["openInterest"].fillna(0) + 1)
        )

        # Filter: meaningful volume AND ratio unusual
        unusual = calls[
            (calls["vol_oi_ratio"] > 2.0) &
            (calls["volume"].fillna(0) > 100)
        ]

        if len(unusual) == 0:
            return None, 0

        best = unusual.loc[unusual["vol_oi_ratio"].idxmax()]
        ratio = _safe_float(best.get("vol_oi_ratio", 0))
        strike = _safe_float(best.get("strike", 0))
        volume = int(_safe_float(best.get("volume", 0)))
        oi = int(_safe_float(best.get("openInterest", 0)))

        bonus = min(15, int(ratio * 3))

        return {
            "strike": round(strike, 2),
            "volume": volume,
            "open_interest": oi,
            "ratio": round(ratio, 2),
            "expiry": nearest,
        }, bonus

    except Exception as e:
        logger.debug(f"[FLOW] Options check failed for {ticker}: {e}")
        return None, 0


# ══════════════════════════════════════════════════════════════════════════
# DETECTOR 3: SHORT INTEREST
# ══════════════════════════════════════════════════════════════════════════

def get_short_interest(ticker: str) -> tuple:
    """
    Get short interest data from yfinance.
    Returns (short_ratio, short_percent_float, squeeze_score 0-15).
    """
    try:
        import yfinance as yf
        stock = yf.Ticker(ticker)
        info = stock.info

        short_ratio = _safe_float(info.get("shortRatio", 0))
        short_pct = _safe_float(info.get("shortPercentOfFloat", 0))

        squeeze_score = 0

        # Days to cover
        if short_ratio > 5:
            squeeze_score += 8
        elif short_ratio > 3:
            squeeze_score += 4

        # Float short percentage
        if short_pct > 0.20:
            squeeze_score += 7
        elif short_pct > 0.15:
            squeeze_score += 4
        elif short_pct > 0.10:
            squeeze_score += 2

        return short_ratio, short_pct, min(squeeze_score, 15)

    except Exception as e:
        logger.debug(f"[FLOW] Short interest failed for {ticker}: {e}")
        return 0.0, 0.0, 0


# ══════════════════════════════════════════════════════════════════════════
# MASTER FLOW ANALYSIS
# ══════════════════════════════════════════════════════════════════════════

def analyze_institutional_flow(ticker: str, df_5m: pd.DataFrame,
                                check_options: bool = True,
                                check_shorts: bool = True) -> dict:
    """
    Run all three flow detectors and return a combined result dict.
    Returns:
      'volume_signals': list of volume signal dicts
      'options': signal dict or None
      'short_ratio': float
      'short_pct': float
      'flow_bonus': total bonus points (0-15+)
      'flow_summary': display string for alerts
    """
    result = {
        "volume_signals": [],
        "options": None,
        "short_ratio": 0.0,
        "short_pct": 0.0,
        "squeeze_score": 0,
        "options_bonus": 0,
        "flow_bonus": 0,
        "flow_summary": "",
    }

    try:
        # Volume signals (always checked, fast)
        vol_signals = detect_institutional_volume(df_5m)
        result["volume_signals"] = vol_signals

        vol_bonus = 0
        if any(s["type"] == "ACCUMULATION" for s in vol_signals):
            vol_bonus += 5
        if any(s["type"] == "MOMENTUM_BUY" for s in vol_signals):
            vol_bonus += 5
        # Bonus for multiple signals
        if len(vol_signals) >= 3:
            vol_bonus += 3

        # Options activity (network call, optional)
        options_signal = None
        options_bonus = 0
        if check_options:
            try:
                options_signal, options_bonus = check_options_activity(ticker)
            except Exception:
                pass
        result["options"] = options_signal
        result["options_bonus"] = options_bonus

        # Short interest (network call, optional)
        short_ratio = short_pct = squeeze_score = 0.0
        if check_shorts:
            try:
                short_ratio, short_pct, squeeze_score = get_short_interest(ticker)
            except Exception:
                pass
        result["short_ratio"] = short_ratio
        result["short_pct"] = short_pct
        result["squeeze_score"] = squeeze_score

        # Total bonus
        total_bonus = vol_bonus + options_bonus + squeeze_score
        result["flow_bonus"] = min(total_bonus, 25)  # cap at 25

        # Build summary string for alerts
        parts = []
        if vol_signals:
            types = set(s["type"] for s in vol_signals)
            if "ACCUMULATION" in types:
                parts.append("Accumulation detected")
            if "MOMENTUM_BUY" in types:
                parts.append("Momentum buy detected")
        if options_signal and options_bonus > 0:
            parts.append(
                f"Options: {options_signal['ratio']:.1f}x unusual call vol "
                f"(${options_signal['strike']:.0f} strike)"
            )
        if short_pct > 0.10:
            parts.append(f"Short Interest: {short_pct * 100:.0f}% (squeeze potential)")

        if parts:
            result["flow_summary"] = "\U0001f4b0 Inst. Flow: " + " | ".join(parts)
        else:
            result["flow_summary"] = ""

        print(f"  [FLOW] {ticker}: bonus={total_bonus} vol={vol_bonus} "
              f"options={options_bonus} squeeze={squeeze_score}")

    except Exception as e:
        logger.debug(f"[FLOW] analyze_institutional_flow error for {ticker}: {e}")

    return result
