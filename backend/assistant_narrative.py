"""Server-side playbook narrative for scanner signal rows."""

from __future__ import annotations

import re
from typing import Any

_CONDITION_LABELS: dict[str, str] = {
    "above_vwap": "Price is above VWAP",
    "below_vwap": "Price is below VWAP",
    "vwap_cross": "Recent cross above VWAP",
    "volume_surge": "Volume is elevated vs average",
    "rsi_healthy": "RSI is in a healthy range",
    "macd_bullish": "MACD supports the move",
    "supertrend_bull": "SuperTrend is bullish",
    "insufficient_data": "Not enough bar history yet",
}


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if parsed != parsed or parsed in (float("inf"), float("-inf")):  # NaN / inf
        return None
    if parsed <= 0:
        return None
    return parsed


def _as_str_list(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, (list, tuple)):
        out: list[str] = []
        for item in value:
            text = str(item).strip()
            if text:
                out.append(text)
        return out
    return []


def _humanize_condition(key: str) -> str:
    text = str(key).strip()
    if not text:
        return ""
    if text in _CONDITION_LABELS:
        return _CONDITION_LABELS[text]
    readable = re.sub(r"[_\-]+", " ", text).strip()
    return readable[:1].upper() + readable[1:] if readable else text


def _normalize_direction(direction: Any) -> str:
    raw = str(direction or "NEUTRAL").upper().strip()
    if raw in ("BUY", "LONG", "BULL", "BULLISH"):
        return "LONG"
    if raw in ("SELL", "SHORT", "BEAR", "BEARISH"):
        return "SHORT"
    return "NEUTRAL"


def _clean_strategy(strategy: Any) -> str:
    text = str(strategy or "").strip()
    if not text or text.lower() in ("none", "no active setup"):
        return ""
    return text


def _market_state_code(market_state: dict[str, Any] | None) -> str:
    if not market_state:
        return ""
    code = market_state.get("code")
    if code:
        return str(code).upper().strip()
    return str(market_state.get("state") or "").upper().strip()


def _is_market_closed(market_state: dict[str, Any] | None, score_data: dict[str, Any]) -> bool:
    if market_state:
        if market_state.get("is_session") is False:
            return True
        if market_state.get("session_open") is False:
            return True
        code = _market_state_code(market_state)
        if code in ("CLOSED", "MARKET_CLOSED", "AFTER_HOURS"):
            return True
    explanation = str(score_data.get("explanation") or "").lower()
    if "market closed" in explanation:
        return True
    if score_data.get("suppress") and score_data.get("total", 1) == 0:
        headline = str(score_data.get("headline") or "").lower()
        if "closed" in headline:
            return True
    return False


def _extract_levels(ticker_row: dict[str, Any], score_data: dict[str, Any]) -> dict[str, float | None]:
    entry = _safe_float(ticker_row.get("entry_price"))
    if entry is None:
        entry = _safe_float(ticker_row.get("price"))
    stop = _safe_float(ticker_row.get("stop"))
    if stop is None:
        stop = _safe_float(score_data.get("stop_loss"))
    target = _safe_float(ticker_row.get("tp1"))
    if target is None:
        target = _safe_float(score_data.get("tp1"))
    rr = _safe_float(ticker_row.get("rr"))
    if rr is None:
        rr = _safe_float(score_data.get("rr_ratio"))
    if rr is None and entry and stop and target:
        risk = abs(entry - stop)
        reward = abs(target - entry)
        if risk > 0:
            rr = round(reward / risk, 2)
    return {"entry": entry, "stop": stop, "target": target, "risk_reward": rr}


def _format_price(value: float | None) -> str:
    if value is None:
        return "—"
    if value >= 100:
        return f"${value:,.2f}"
    return f"${value:.2f}"


def _why_bullets(
    explanation: str,
    conditions_met: list[str],
    conditions_failed: list[str],
    *,
    max_items: int = 4,
) -> list[str]:
    bullets: list[str] = []
    if explanation:
        parts = [p.strip() for p in re.split(r"[;.]\s*", explanation) if p.strip()]
        for part in parts[:2]:
            if part and part not in bullets:
                bullets.append(part if part.endswith(".") else f"{part}.")
    for key in conditions_met[:3]:
        label = _humanize_condition(key)
        if label and label not in bullets:
            bullets.append(label)
    if len(bullets) < max_items:
        for key in conditions_failed[:2]:
            label = _humanize_condition(key)
            if not label:
                continue
            text = f"Still waiting on: {label.lower()}"
            if text not in bullets:
                bullets.append(text)
    if not bullets:
        bullets.append("Scan is still building context for this symbol.")
    return bullets[:max_items]


def _levels_block(levels: dict[str, float | None]) -> dict[str, float | None]:
    return {
        "entry": levels.get("entry"),
        "stop": levels.get("stop"),
        "target": levels.get("target"),
        "risk_reward": levels.get("risk_reward"),
    }


def _skill_hints(
    *,
    direction: str,
    strategy: str,
    score: int,
    market_label: str,
    has_levels: bool,
) -> dict[str, str]:
    strat_part = f" ({strategy})" if strategy else ""
    if direction == "LONG":
        beginner = (
            f"This is a bullish setup{strat_part}. Wait for price to hold near your planned entry "
            "before sizing in, and risk only what you can lose on the stop."
        )
        advanced = (
            f"Score {score}/100 · {market_label or 'Session active'}. "
            "Confirm trigger on your timeframe; trail only after partial target or structure break."
        )
    elif direction == "SHORT":
        beginner = (
            f"This is a bearish read{strat_part}. Short setups need crisp risk control—"
            "define exit before entry and avoid averaging into strength."
        )
        advanced = (
            f"Score {score}/100 · favor failed rallies into resistance; "
            "tighten risk if breadth flips positive."
        )
    else:
        beginner = (
            "No active trade signal yet. Use the watchlist to track names, "
            "and wait for score and strategy to align before planning a trade."
        )
        advanced = (
            f"Score {score}/100 · neutral posture; map levels if you stalk a breakout, "
            "otherwise preserve capital."
        )
    if not has_levels and direction in ("LONG", "SHORT"):
        beginner += " Levels are still forming—refresh after the next scan."
    return {"beginner": beginner, "advanced": advanced}


def _action_checklist(
    *,
    direction: str,
    strategy: str,
    levels: dict[str, float | None],
    has_signal: bool,
) -> list[str]:
    if not has_signal:
        return [
            "Stay flat until a strategy tag and score line up.",
            "Note support and resistance on your chart before the next scan.",
            "Set alerts instead of forcing a trade in a quiet tape.",
        ]
    entry = levels.get("entry")
    stop = levels.get("stop")
    target = levels.get("target")
    items: list[str] = []
    if direction == "LONG":
        items.append(
            f"Plan entry near {_format_price(entry)} only if price accepts above your trigger."
        )
        items.append(f"Place stop at {_format_price(stop)} and size so a full stop fits your risk cap.")
        items.append(f"First target near {_format_price(target)}; scale or trail per your playbook.")
    elif direction == "SHORT":
        items.append(f"Look for rejection near {_format_price(entry)} before leaning short.")
        items.append(f"Stop above {_format_price(stop)}; cover into {_format_price(target)}.")
    else:
        items.append("Review score breakdown and wait for direction to turn bullish.")
    if strategy:
        items.append(f"Playbook: {strategy} — trade only the rules you validated.")
    items.append("Log the setup after the session; skip if liquidity or spread is poor.")
    return items[:5]


def _caution_text(
    *,
    direction: str,
    score: int,
    market_state: dict[str, Any] | None,
    conditions_failed: list[str],
    levels: dict[str, float | None],
) -> str:
    if _is_market_closed(market_state, {}):
        return "Regular session is closed—levels and scores are for review only, not live entries."
    code = _market_state_code(market_state)
    if code == "HIGH_VOLATILITY":
        return "Volatility is elevated—use smaller size and wider noise tolerance on stops."
    if code == "RANGING":
        return "Market is choppy—breakout trades can fail quickly; demand extra confirmation."
    if score < 70 and direction == "LONG":
        return "Confidence is below the strong tier—treat this as watchlist quality, not a full-size entry."
    if not levels.get("stop") or not levels.get("target"):
        return "Stop or target is not set yet—wait for the next scan before committing risk."
    if conditions_failed:
        return "Some strategy checks failed—do not treat this as a complete setup."
    return "This is decision support, not a guaranteed outcome—stick to your risk limits."


def _has_trade_signal(ticker_row: dict[str, Any], score: int, strategy: str, direction: str) -> bool:
    if score >= 50 and direction == "LONG":
        return True
    if strategy and direction in ("LONG", "SHORT"):
        return True
    return False


def build_assistant_payload(
    ticker_row: dict[str, Any],
    market_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Build the `assistant` object for a scanner row.

    Uses score_data.explanation, conditions_met/failed, levels, and strategy.
    """
    ticker_row = ticker_row if isinstance(ticker_row, dict) else {}
    market_state = market_state if isinstance(market_state, dict) else {}
    score_data = ticker_row.get("score_data")
    if not isinstance(score_data, dict):
        score_data = {}

    ticker = str(ticker_row.get("ticker") or "Symbol").upper().strip() or "Symbol"
    direction = _normalize_direction(ticker_row.get("direction"))
    strategy = _clean_strategy(ticker_row.get("strategy"))
    score = _safe_int(ticker_row.get("score"), _safe_int(score_data.get("total"), 0))
    explanation = str(score_data.get("explanation") or "").strip()
    conditions_met = _as_str_list(ticker_row.get("conditions_met"))
    conditions_failed = _as_str_list(ticker_row.get("conditions_failed"))
    market_label = str(market_state.get("label") or "").strip()
    levels = _extract_levels(ticker_row, score_data)
    has_levels = bool(levels.get("entry") and levels.get("stop") and levels.get("target"))
    has_signal = _has_trade_signal(ticker_row, score, strategy, direction)

    if _is_market_closed(market_state, score_data):
        why = _why_bullets(explanation, conditions_met, conditions_failed)
        if not why or why == ["Scan is still building context for this symbol."]:
            why = ["Regular session is closed; scans are in review mode."]
        return {
            "headline": f"{ticker}: market closed — plan, don't trade",
            "why_bullets": why[:4],
            "action_checklist": [
                "Use this time to review open risk and tomorrow's watchlist.",
                "Mark key levels on your chart for the next session open.",
                "Avoid new entries until the regular session is active.",
            ],
            "levels": _levels_block(levels),
            "caution": _caution_text(
                direction=direction,
                score=score,
                market_state=market_state,
                conditions_failed=conditions_failed,
                levels=levels,
            ),
            "skill_hints": {
                "beginner": "When the market is closed, practice journaling past trades instead of chasing prices.",
                "advanced": "Queue conditional orders only if your broker supports them; otherwise script entries at the open.",
            },
        }

    if direction == "SHORT":
        headline = f"{ticker}: bearish setup"
        if strategy:
            headline = f"{ticker}: {strategy} (bearish)"
    elif direction == "LONG" and has_signal:
        headline = f"{ticker}: bullish setup · score {score}"
        if strategy:
            headline = f"{ticker}: {strategy} · score {score}"
    else:
        headline = f"{ticker}: on watch — no active setup"
        if score > 0:
            headline = f"{ticker}: watching · score {score}"

    return {
        "headline": headline,
        "why_bullets": _why_bullets(explanation, conditions_met, conditions_failed),
        "action_checklist": _action_checklist(
            direction=direction,
            strategy=strategy,
            levels=levels,
            has_signal=has_signal,
        ),
        "levels": _levels_block(levels),
        "caution": _caution_text(
            direction=direction,
            score=score,
            market_state=market_state,
            conditions_failed=conditions_failed,
            levels=levels,
        ),
        "skill_hints": _skill_hints(
            direction=direction,
            strategy=strategy,
            score=score,
            market_label=market_label,
            has_levels=has_levels,
        ),
    }


def enrich_ticker_row_with_assistant(
    row: dict[str, Any],
    market_state: dict[str, Any] | None,
    *,
    include_when_watchlist: bool = True,
) -> dict[str, Any]:
    """Return a shallow copy of ``row`` with ``assistant`` attached when appropriate."""
    if not isinstance(row, dict):
        return row
    out = dict(row)
    score_data = out.get("score_data")
    if not isinstance(score_data, dict):
        score_data = {}
    score = _safe_int(out.get("score"), _safe_int(score_data.get("total"), 0))
    direction = _normalize_direction(out.get("direction"))
    strategy = _clean_strategy(out.get("strategy"))
    has_signal = _has_trade_signal(out, score, strategy, direction)
    if has_signal or include_when_watchlist:
        if score > 0 or has_signal or out.get("score_data") or strategy:
            out["assistant"] = build_assistant_payload(out, market_state)
    return out
