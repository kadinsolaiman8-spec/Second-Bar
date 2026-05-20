"""Tests for GET /api/market/session and build_market_session()."""

from __future__ import annotations

import datetime

import pytz
from fastapi.testclient import TestClient

from backend.app import app
from backend.market_session import build_market_session

_ET = pytz.timezone("US/Eastern")

_REQUIRED_KEYS = (
    "is_session",
    "is_early_close",
    "session_open_et",
    "session_close_et",
    "next_open_et",
    "next_close_et",
    "label",
)


def _et(y: int, m: int, d: int, hh: int, mm: int = 0) -> datetime.datetime:
    return _ET.localize(datetime.datetime(y, m, d, hh, mm))


def test_market_session_api_shape() -> None:
    with TestClient(app) as client:
        response = client.get("/api/market/session")
        assert response.status_code == 200
        body = response.json()
        for key in _REQUIRED_KEYS:
            assert key in body
        assert isinstance(body["is_session"], bool)
        assert isinstance(body["is_early_close"], bool)
        assert isinstance(body["label"], str) and body["label"]


def test_regular_session_midday_is_open() -> None:
    snapshot = build_market_session(now=_et(2025, 5, 19, 11, 0))
    assert snapshot["is_session"] is True
    assert snapshot["is_early_close"] is False
    assert snapshot["session_open_et"] is not None
    assert snapshot["session_close_et"] is not None
    assert snapshot["next_close_et"] == snapshot["session_close_et"]
    assert "open" in snapshot["label"].lower()


def test_christmas_holiday_closed() -> None:
    snapshot = build_market_session(now=_et(2025, 12, 25, 12, 0))
    assert snapshot["is_session"] is False
    assert snapshot["session_open_et"] is None
    assert snapshot["session_close_et"] is None
    assert snapshot["next_open_et"] is not None
    assert snapshot["next_close_et"] is not None
    assert "closed" in snapshot["label"].lower()


def test_early_close_day_flags_and_close_time() -> None:
    snapshot = build_market_session(now=_et(2025, 11, 28, 10, 0))
    assert snapshot["is_session"] is True
    assert snapshot["is_early_close"] is True
    assert snapshot["session_close_et"] is not None
    close_dt = datetime.datetime.fromisoformat(snapshot["session_close_et"])
    assert close_dt.hour == 13
    assert "early close" in snapshot["label"].lower()


def test_premarket_before_open() -> None:
    snapshot = build_market_session(now=_et(2025, 5, 19, 8, 0))
    assert snapshot["is_session"] is False
    assert snapshot["next_open_et"] == snapshot["session_open_et"]
    assert "opens at" in snapshot["label"].lower()


def test_after_regular_close() -> None:
    snapshot = build_market_session(now=_et(2025, 5, 19, 17, 0))
    assert snapshot["is_session"] is False
    assert snapshot["next_open_et"] is not None
    assert snapshot["next_open_et"] != snapshot["session_open_et"]
    assert "ended" in snapshot["label"].lower()
