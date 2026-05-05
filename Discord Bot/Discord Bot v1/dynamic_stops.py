# dynamic_stops.py — Adaptive stop loss system that adjusts to market conditions
# Replaces fixed 1.0x ATR stop with context-aware stops and trailing stop logic.

import logging
import math

logger = logging.getLogger(__name__)


def _safe_float(val, default=0.0) -> float:
    try:
        f = float(val)
        return default if math.isnan(f) or math.isinf(f) else f
    except (TypeError, ValueError):
        return default


# ══════════════════════════════════════════════════════════════════════════
# STATE MULTIPLIERS
# ══════════════════════════════════════════════════════════════════════════

_STATE_MULTIPLIERS = {
    "STRONG_TREND":    1.0,
    "WEAK_TREND":      1.3,
    "RANGING":         0.8,
    "HIGH_VOLATILITY": 1.5,
}


# ══════════════════════════════════════════════════════════════════════════
# DYNAMIC STOP CALCULATOR
# ══════════════════════════════════════════════════════════════════════════

def calculate_dynamic_stop(ticker: str, entry_price: float,
                            atr: float, market_state: str,
                            indicators: dict,
                            direction: str = "BUY") -> dict:
    """
    Calculate a dynamic stop loss adapted to current market conditions.

    Adjustments applied in order:
    1. Market state multiplier
    2. ATR volatility regime (atr_ratio from indicators)
    3. Support level snapping (EMA21, EMA50, VWAP)
    4. Hard caps: never > 3% or < 0.3% of price

    Returns dict with stop, tp1, tp2, tp3, risk_amount, risk_percent,
    rr_ratio, stop_type.
    """
    entry_price = _safe_float(entry_price, 0.0)
    atr = _safe_float(atr, 0.0)

    if entry_price <= 0:
        return _fallback_stops(entry_price, atr)

    if atr <= 0:
        atr = entry_price * 0.01  # fallback: 1% of price

    base_stop_distance = atr

    # ── Adjustment 1: Market state ────────────────────────────────────
    multiplier = _STATE_MULTIPLIERS.get(market_state, 1.0)
    stop_distance = base_stop_distance * multiplier
    stop_type = f"Dynamic ({multiplier:.1f}x ATR, {market_state})"

    # ── Adjustment 2: Volatility regime ───────────────────────────────
    atr_ratio = 1.0
    if isinstance(indicators, dict):
        atr_ratio = _safe_float(
            indicators.get("atr", {}).get("atr_ratio",
            indicators.get("atr_ratio", 1.0)), 1.0)
    if atr_ratio > 1.5:
        stop_distance *= 1.2
        stop_type += " +vol"
    elif atr_ratio < 0.7:
        stop_distance *= 0.85
        stop_type += " -vol"

    # ── Adjustment 3: Support level snapping ──────────────────────────
    ema21 = 0.0
    ema50 = 0.0
    vwap_val = 0.0
    if isinstance(indicators, dict):
        ema_data = indicators.get("ema_stack", indicators.get("ema", {}))
        ema21 = _safe_float(ema_data.get("ema21", ema_data.get("ema_21", 0)))
        ema50 = _safe_float(ema_data.get("ema50", ema_data.get("ema_50", 0)))
        vwap_data = indicators.get("vwap", {})
        vwap_val = _safe_float(vwap_data.get("value", vwap_data.get("vwap", 0)))

    calculated_stop = entry_price - stop_distance

    support_levels = [l for l in [ema21, ema50, vwap_val]
                      if l > 0 and l < entry_price]

    for level in support_levels:
        level_distance = entry_price - level
        if abs(level_distance - stop_distance) < base_stop_distance * 0.3:
            snapped = level - base_stop_distance * 0.1
            print(f"  [DSTOP] {ticker}: Stop snapped to support "
                  f"at {level:.2f} → stop {snapped:.2f}")
            calculated_stop = snapped
            stop_type += " (support-snapped)"
            break

    # ── Adjustment 4: Hard caps ───────────────────────────────────────
    max_stop_dist = entry_price * 0.03   # 3% max
    min_stop_dist = entry_price * 0.003  # 0.3% min

    final_stop = max(
        entry_price - max_stop_dist,
        min(calculated_stop, entry_price - min_stop_dist)
    )
    final_stop = round(final_stop, 2)

    actual_risk = entry_price - final_stop

    # ── Targets based on actual risk ─────────────────────────────────
    tp1 = round(entry_price + actual_risk * 2.0, 2)
    tp2 = round(entry_price + actual_risk * 3.5, 2)
    tp3 = round(entry_price + actual_risk * 5.0, 2)
    rr = round((tp1 - entry_price) / actual_risk, 2) if actual_risk > 0 else 0.0

    return {
        "stop": final_stop,
        "tp1": tp1,
        "tp2": tp2,
        "tp3": tp3,
        "risk_amount": round(actual_risk, 4),
        "risk_percent": round((actual_risk / entry_price) * 100, 2),
        "rr_ratio": rr,
        "stop_type": stop_type,
        "multiplier": multiplier,
    }


def _fallback_stops(entry_price: float, atr: float) -> dict:
    """Minimal safe fallback when entry_price is 0."""
    return {
        "stop": 0.0, "tp1": 0.0, "tp2": 0.0, "tp3": 0.0,
        "risk_amount": 0.0, "risk_percent": 0.0,
        "rr_ratio": 0.0, "stop_type": "Fallback", "multiplier": 1.0,
    }


# ══════════════════════════════════════════════════════════════════════════
# TRAILING STOP ENGINE
# ══════════════════════════════════════════════════════════════════════════

def update_trailing_stop(current_price: float, entry_price: float,
                          original_stop: float, tp1: float,
                          atr: float,
                          highest_price_since_entry: float) -> tuple:
    """
    Three-phase trailing stop system.

    Phase 1 (before TP1): Fixed original stop.
    Phase 2 (just after TP1): Move stop to breakeven + 0.1 ATR.
    Phase 3 (well past TP1): Trail by 1 ATR below highest price.

    Returns (new_stop, phase_label).
    """
    current_price = _safe_float(current_price, 0.0)
    entry_price = _safe_float(entry_price, 0.0)
    original_stop = _safe_float(original_stop, 0.0)
    tp1 = _safe_float(tp1, 0.0)
    atr = _safe_float(atr, 0.0)
    highest = _safe_float(highest_price_since_entry, current_price)

    if atr <= 0:
        atr = entry_price * 0.01 if entry_price > 0 else 1.0

    # Phase 1: Before TP1 — fixed stop
    if tp1 <= 0 or current_price < tp1:
        return original_stop, "FIXED"

    # Phase 2: Just past TP1 (within 1 ATR above TP1)
    if current_price < tp1 + atr:
        breakeven_stop = entry_price + (atr * 0.1)
        new_stop = round(max(breakeven_stop, original_stop), 2)
        return new_stop, "BREAKEVEN"

    # Phase 3: Well past TP1 — trail by 1 ATR below highest
    trailing = highest - atr
    new_stop = round(max(trailing, entry_price), 2)  # never move below breakeven
    return new_stop, "TRAILING"


def simulate_trailing_stop_exit(entry: float, original_stop: float,
                                 tp1: float, tp2: float, atr: float,
                                 future_bars: list) -> dict:
    """
    Simulate trailing stop on a list of (high, low, close) tuples.
    Returns exit dict with price, reason, r_multiple, bars_held.
    """
    if not future_bars or entry <= 0:
        return {"exit_price": entry, "reason": "NO_DATA", "r_multiple": 0.0, "bars_held": 0}

    current_stop = original_stop
    highest = entry
    phase = "FIXED"
    risk = abs(entry - original_stop)

    for i, bar in enumerate(future_bars):
        try:
            h = _safe_float(bar[0], entry)
            l = _safe_float(bar[1], entry)
            c = _safe_float(bar[2], entry)
        except (IndexError, TypeError):
            continue

        highest = max(highest, h)

        # Update trailing stop
        current_stop, phase = update_trailing_stop(
            c, entry, original_stop, tp1, atr, highest
        )

        # Check exits
        if l <= current_stop:
            r_mult = round((current_stop - entry) / risk, 3) if risk > 0 else 0.0
            return {
                "exit_price": current_stop,
                "reason": f"SL ({phase})",
                "r_multiple": r_mult,
                "bars_held": i + 1,
            }
        if h >= tp2:
            r_mult = round((tp2 - entry) / risk, 3) if risk > 0 else 0.0
            return {
                "exit_price": tp2,
                "reason": "TP2",
                "r_multiple": r_mult,
                "bars_held": i + 1,
            }

    # Timeout: exit at last close
    c = _safe_float(future_bars[-1][2], entry) if future_bars else entry
    r_mult = round((c - entry) / risk, 3) if risk > 0 else 0.0
    return {
        "exit_price": c,
        "reason": f"TIMEOUT ({phase})",
        "r_multiple": r_mult,
        "bars_held": len(future_bars),
    }
