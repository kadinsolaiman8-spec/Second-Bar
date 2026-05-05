# adaptive_scoring.py — Dynamic indicator weight learning system
# Tracks which indicators actually predicted profitable trades and adjusts weights.
# Weights update every 50 closed trades.

import json
import logging
import os
import math
from datetime import datetime
from typing import Optional

import numpy as np
import pytz

from scanner_core.config import MARKET_TIMEZONE

logger = logging.getLogger(__name__)
ET = pytz.timezone(MARKET_TIMEZONE)

_WEIGHTS_FILE = os.path.join(os.path.dirname(__file__), "adaptive_weights.json")

# ── Tracked indicators ─────────────────────────────────────────────────────
TRACKED_INDICATORS = [
    "rsi_healthy",
    "macd_bullish",
    "supertrend_bull",
    "ema_stack",
    "above_vwap",
    "high_rvol",
    "adx_trending",
    "williams_positive",
    "bb_not_overextended",
    "price_above_sma50",
]

# ── In-memory accuracy tracking ───────────────────────────────────────────
_indicator_accuracy: dict = {k: {"wins": 0, "total": 0} for k in TRACKED_INDICATORS}
_closed_trade_count: int = 0
_dynamic_weights: dict = {k: 1.0 for k in TRACKED_INDICATORS}
_last_weight_update: int = 0   # trade count at last weight update
_WEIGHT_UPDATE_INTERVAL = 50   # recalculate every 50 trades


# ══════════════════════════════════════════════════════════════════════════
# INDICATOR STATE EXTRACTION
# ══════════════════════════════════════════════════════════════════════════

def _safe_float(val, default=0.0) -> float:
    try:
        f = float(val)
        return default if math.isnan(f) or math.isinf(f) else f
    except (TypeError, ValueError):
        return default


def extract_indicator_states(indicators: dict, entry_price: float) -> dict:
    """
    Extract boolean states for each tracked indicator from the indicators dict.
    Returns dict like {'rsi_healthy': True, 'macd_bullish': False, ...}
    """
    states = {}

    # RSI in healthy zone 45-65
    rsi = _safe_float(indicators.get("rsi", {}).get("value"), 50.0)
    states["rsi_healthy"] = 45 <= rsi <= 65

    # MACD bullish
    macd = indicators.get("macd", {})
    states["macd_bullish"] = bool(macd.get("bullish", False))

    # SuperTrend bullish
    st = indicators.get("supertrend", {})
    states["supertrend_bull"] = bool(st.get("bullish", False))

    # EMA stack: EMA9 > EMA21
    ema = indicators.get("ema_stack", indicators.get("ema", {}))
    e9 = _safe_float(ema.get("ema9", ema.get("ema_9", 0)))
    e21 = _safe_float(ema.get("ema21", ema.get("ema_21", 0)))
    states["ema_stack"] = e9 > 0 and e21 > 0 and e9 > e21

    # Price above VWAP
    vwap_data = indicators.get("vwap", {})
    vwap_val = _safe_float(vwap_data.get("value", vwap_data.get("vwap", 0)))
    ep = _safe_float(entry_price)
    states["above_vwap"] = vwap_val > 0 and ep > vwap_val

    # High RVOL >= 1.5
    rvol_data = indicators.get("rvol", {})
    rvol = _safe_float(rvol_data.get("rvol", rvol_data.get("value", 1.0)))
    states["high_rvol"] = rvol >= 1.5

    # ADX trending >= 25
    adx = _safe_float(indicators.get("adx", {}).get("value", 0))
    states["adx_trending"] = adx >= 25

    # Williams %R above -50 (positive territory)
    wr = _safe_float(indicators.get("williams_r", {}).get("value", -50))
    states["williams_positive"] = wr > -50

    # BB not overextended (price not above upper band)
    bb = indicators.get("bbands", indicators.get("bollinger", {}))
    bb_upper = _safe_float(bb.get("upper", 0))
    states["bb_not_overextended"] = bb_upper <= 0 or ep <= bb_upper

    # Price above SMA50 (from EMA stack data or approximation)
    e50 = _safe_float(ema.get("ema50", ema.get("ema_50", 0)))
    states["price_above_sma50"] = e50 > 0 and ep > e50

    return states


# ══════════════════════════════════════════════════════════════════════════
# WEIGHT CALCULATION
# ══════════════════════════════════════════════════════════════════════════

def calculate_dynamic_weights() -> dict:
    """
    Recalculate weights from current accuracy data.
    weight = max(0, (accuracy - 0.30) / 0.20)
    - 30% accuracy → 0.0 (ignore this indicator)
    - 50% accuracy → 1.0 (neutral)
    - 70% accuracy → 2.0 (double weight)
    """
    weights = {}
    for ind, data in _indicator_accuracy.items():
        if data["total"] >= 10:
            accuracy = data["wins"] / data["total"]
            weight = max(0.0, (accuracy - 0.30) / 0.20)
            weights[ind] = round(min(weight, 3.0), 2)  # cap at 3x
        else:
            weights[ind] = 1.0  # default until enough data
    return weights


def get_weight(indicator: str) -> float:
    """Return the current dynamic weight for an indicator (default 1.0)."""
    return _dynamic_weights.get(indicator, 1.0)


# ══════════════════════════════════════════════════════════════════════════
# TRADE RECORDING
# ══════════════════════════════════════════════════════════════════════════

def record_trade_outcome(indicator_states: dict, is_win: bool):
    """
    Record the outcome of a closed trade.
    indicator_states: dict from extract_indicator_states()
    is_win: True if the trade was profitable
    """
    global _closed_trade_count, _dynamic_weights, _last_weight_update

    for ind in TRACKED_INDICATORS:
        if indicator_states.get(ind, False):
            _indicator_accuracy[ind]["total"] += 1
            if is_win:
                _indicator_accuracy[ind]["wins"] += 1

    _closed_trade_count += 1

    # Recalculate weights every N trades
    if _closed_trade_count - _last_weight_update >= _WEIGHT_UPDATE_INTERVAL:
        _dynamic_weights = calculate_dynamic_weights()
        _last_weight_update = _closed_trade_count
        _save_weights()
        print(f"[ADAPTIVE] Weights updated after {_closed_trade_count} trades:")
        for ind, w in sorted(_dynamic_weights.items(), key=lambda x: -x[1]):
            acc = _indicator_accuracy[ind]
            if acc["total"] > 0:
                pct = acc["wins"] / acc["total"] * 100
                print(f"  {ind:<28} {pct:.0f}% accurate | weight {w:.2f}x")


# ══════════════════════════════════════════════════════════════════════════
# PERSISTENCE
# ══════════════════════════════════════════════════════════════════════════

def _save_weights():
    """Persist accuracy data and weights to disk."""
    try:
        data = {
            "accuracy": _indicator_accuracy,
            "weights": _dynamic_weights,
            "trade_count": _closed_trade_count,
            "updated_at": datetime.now(ET).isoformat(),
        }
        with open(_WEIGHTS_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        logger.debug(f"[ADAPTIVE] Save failed: {e}")


def load_weights():
    """Load persisted weights from disk on startup."""
    global _indicator_accuracy, _dynamic_weights, _closed_trade_count, _last_weight_update
    try:
        if os.path.exists(_WEIGHTS_FILE):
            with open(_WEIGHTS_FILE) as f:
                data = json.load(f)
            _indicator_accuracy = data.get("accuracy", _indicator_accuracy)
            _dynamic_weights = data.get("weights", _dynamic_weights)
            _closed_trade_count = data.get("trade_count", 0)
            _last_weight_update = _closed_trade_count
            print(f"[ADAPTIVE] Loaded weights from {_closed_trade_count} historical trades")
    except Exception as e:
        logger.debug(f"[ADAPTIVE] Load failed: {e}")


# ══════════════════════════════════════════════════════════════════════════
# WEIGHTED SCORING HELPERS
# ══════════════════════════════════════════════════════════════════════════

def weighted_add(base_points: float, indicator: str) -> float:
    """Multiply points by dynamic weight of the indicator."""
    return base_points * get_weight(indicator)


def get_total_trades() -> int:
    return _closed_trade_count


def get_accuracy_summary() -> list:
    """Return sorted list of (indicator, accuracy_pct, weight, total) for /weights command."""
    rows = []
    for ind, data in _indicator_accuracy.items():
        if data["total"] > 0:
            acc = data["wins"] / data["total"] * 100
        else:
            acc = 0.0
        rows.append({
            "indicator": ind,
            "accuracy_pct": round(acc, 1),
            "weight": _dynamic_weights.get(ind, 1.0),
            "total_trades": data["total"],
            "wins": data["wins"],
        })
    rows.sort(key=lambda x: -x["accuracy_pct"])
    return rows


def format_weights_card() -> str:
    """Format the /weights command output."""
    total = get_total_trades()
    rows = get_accuracy_summary()

    lines = [
        "\u2501" * 40,
        "\U0001f4ca ADAPTIVE INDICATOR WEIGHTS",
        "\u2501" * 40,
        f"Based on {total} closed paper trades",
        "",
    ]

    if total < 10:
        lines.append("Not enough trades yet (need 10+). Using default weights.")
        lines.append("Weights will adapt as paper trades close.")
    else:
        qualified = [r for r in rows if r["total_trades"] >= 10]
        unqualified = [r for r in rows if r["total_trades"] < 10]

        if qualified:
            top = qualified[:3]
            bottom = qualified[-3:]

            lines.append("Most Reliable:")
            medals = ["\U0001f947", "\U0001f948", "\U0001f949"]
            for i, r in enumerate(top):
                medal = medals[i] if i < 3 else "  "
                lines.append(
                    f"{medal} {r['indicator']:<28} "
                    f"{r['accuracy_pct']:.0f}% accurate | "
                    f"Weight: {r['weight']:.1f}x | "
                    f"n={r['total_trades']}"
                )

            lines.append("")
            lines.append("Least Reliable:")
            for r in bottom:
                lines.append(
                    f"\u26a0\ufe0f {r['indicator']:<28} "
                    f"{r['accuracy_pct']:.0f}% accurate | "
                    f"Weight: {r['weight']:.1f}x | "
                    f"n={r['total_trades']}"
                )

        if unqualified:
            lines.append("")
            lines.append(f"Pending (need 10+ trades):")
            for r in unqualified:
                lines.append(f"  {r['indicator']:<28} n={r['total_trades']}")

    lines.append("")
    lines.append(f"Note: Weights update every {_WEIGHT_UPDATE_INTERVAL} trades automatically")
    lines.append("\u2501" * 40)
    return "\n".join(lines)


# Load on import
load_weights()
