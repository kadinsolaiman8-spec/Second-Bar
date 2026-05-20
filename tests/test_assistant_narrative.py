"""Unit tests for scanner assistant narrative payloads."""

from __future__ import annotations

from backend.assistant_narrative import build_assistant_payload


def _base_row(**overrides):
    row = {
        "ticker": "AAPL",
        "direction": "BUY",
        "strategy": "VWAP Momentum Breakout",
        "score": 78,
        "entry_price": 190.5,
        "stop": 188.0,
        "tp1": 195.0,
        "rr": 1.8,
        "score_data": {
            "total": 78,
            "explanation": "Strong trend alignment across timeframes; volume above average.",
            "rr_ratio": 1.8,
        },
        "conditions_met": ["above_vwap", "volume_surge"],
        "conditions_failed": ["rsi_healthy"],
    }
    row.update(overrides)
    return row


def _assert_contract(payload: dict) -> None:
    assert isinstance(payload.get("headline"), str) and payload["headline"]
    assert isinstance(payload.get("why_bullets"), list) and payload["why_bullets"]
    assert isinstance(payload.get("action_checklist"), list) and payload["action_checklist"]
    levels = payload.get("levels")
    assert isinstance(levels, dict)
    for key in ("entry", "stop", "target", "risk_reward"):
        assert key in levels
    assert isinstance(payload.get("caution"), str) and payload["caution"]
    hints = payload.get("skill_hints")
    assert isinstance(hints, dict)
    assert hints.get("beginner")
    assert hints.get("advanced")


def test_long_signal_payload() -> None:
    payload = build_assistant_payload(_base_row(), {"code": "STRONG_TREND", "label": "Strong trend"})
    _assert_contract(payload)
    assert "AAPL" in payload["headline"]
    assert "bullish" in payload["headline"].lower() or "VWAP" in payload["headline"]
    assert payload["levels"]["entry"] == 190.5
    assert payload["levels"]["stop"] == 188.0
    assert payload["levels"]["target"] == 195.0
    assert payload["levels"]["risk_reward"] == 1.8
    assert any("vwap" in b.lower() or "VWAP" in b for b in payload["why_bullets"])


def test_short_signal_payload() -> None:
    row = _base_row(direction="SELL", strategy="Mean Reversion Fade")
    payload = build_assistant_payload(row, {"code": "WEAK_TREND", "label": "Weak trend"})
    _assert_contract(payload)
    assert "bearish" in payload["headline"].lower()
    assert any("stop" in item.lower() for item in payload["action_checklist"])


def test_neutral_watch_payload() -> None:
    row = _base_row(
        direction="NEUTRAL",
        strategy="No Active Setup",
        score=42,
        score_data={"total": 42, "explanation": "Moderate trend alignment; weak momentum."},
    )
    payload = build_assistant_payload(row, {"code": "RANGING", "label": "Ranging"})
    _assert_contract(payload)
    assert "watch" in payload["headline"].lower() or "watching" in payload["headline"].lower()
    assert any("flat" in item.lower() or "wait" in item.lower() for item in payload["action_checklist"])


def test_missing_levels() -> None:
    row = _base_row(entry_price=0, stop=0, tp1=0, rr=0)
    row["entry_price"] = 200.0
    row["stop"] = None
    row["tp1"] = None
    row.pop("rr", None)
    row["score_data"] = {"total": 72, "explanation": "Strong trend alignment."}
    payload = build_assistant_payload(row, {"code": "STRONG_TREND", "label": "Strong trend"})
    _assert_contract(payload)
    assert payload["levels"]["entry"] == 200.0
    assert payload["levels"]["stop"] is None
    assert payload["levels"]["target"] is None
    assert "stop" in payload["caution"].lower() or "target" in payload["caution"].lower()
    assert "forming" in payload["skill_hints"]["beginner"].lower() or "wait" in payload["caution"].lower()


def test_market_closed() -> None:
    row = _base_row(
        score=0,
        score_data={
            "total": 0,
            "suppress": True,
            "headline": "Market closed - no trade score",
            "explanation": "AAPL: market closed; no trade score generated",
        },
    )
    payload = build_assistant_payload(row, {"code": "WEAK_TREND", "label": "Weak trend", "is_session": False})
    _assert_contract(payload)
    assert "closed" in payload["headline"].lower()
    assert "closed" in payload["caution"].lower()
    assert any("session" in item.lower() or "review" in item.lower() for item in payload["action_checklist"])
