"""NYSE regular-session snapshot for the market clock API."""

from __future__ import annotations

import datetime
from typing import Any

import pytz

from scanner_core.session_calendar import (
    get_session_close,
    is_early_close,
    is_nyse_session,
)

_MARKET_TZ = pytz.timezone("US/Eastern")
_DEFAULT_OPEN = datetime.time(9, 30)
_MAX_LOOKAHEAD_DAYS = 14

try:
    import exchange_calendars as _xcals

    _nyse = _xcals.get_calendar("XNYS")
    _EC_AVAILABLE = True
except ImportError:
    _nyse = None  # type: ignore[assignment]
    _EC_AVAILABLE = False


def _ensure_et(now: datetime.datetime) -> datetime.datetime:
    if now.tzinfo is None:
        return _MARKET_TZ.localize(now)
    return now.astimezone(_MARKET_TZ)


def _iso_et(dt: datetime.datetime | None) -> str | None:
    if dt is None:
        return None
    return _ensure_et(dt).isoformat()


def _session_open_for_date(day: datetime.date) -> datetime.datetime:
    default = _MARKET_TZ.localize(datetime.datetime.combine(day, _DEFAULT_OPEN))
    if not _EC_AVAILABLE:
        return default
    try:
        open_utc = _nyse.session_open(str(day))
        return open_utc.tz_convert(_MARKET_TZ)
    except Exception:
        return default


def _next_trading_date(after: datetime.date) -> datetime.date | None:
    if _EC_AVAILABLE:
        try:
            next_session = _nyse.next_session(str(after))
            return next_session.date()
        except Exception:
            pass
    probe = after + datetime.timedelta(days=1)
    for _ in range(_MAX_LOOKAHEAD_DAYS):
        if probe.weekday() < 5 and is_nyse_session(probe):
            return probe
        probe += datetime.timedelta(days=1)
    return None


def _format_et_time(dt: datetime.datetime) -> str:
    hour = dt.hour % 12 or 12
    minute = dt.minute
    suffix = "AM" if dt.hour < 12 else "PM"
    if minute:
        return f"{hour}:{minute:02d} {suffix} ET"
    return f"{hour} {suffix} ET"


def _build_label(
    *,
    in_session: bool,
    trading_day: bool,
    early: bool,
    now: datetime.datetime,
    session_open: datetime.datetime | None,
    session_close: datetime.datetime | None,
) -> str:
    if in_session:
        if early and session_close is not None:
            return (
                f"Regular session is open. Early close today at "
                f"{_format_et_time(session_close)}."
            )
        return "Regular session is open."

    if trading_day and session_open is not None and now < session_open:
        return f"Regular session opens at {_format_et_time(session_open)}."

    if trading_day and session_close is not None and now >= session_close:
        if early:
            return "Regular session ended early today."
        return "Regular session has ended for today."

    if not trading_day:
        return "Market closed today."

    return "Market closed."


def build_market_session(*, now: datetime.datetime | None = None) -> dict[str, Any]:
    """Return a market-session snapshot for API consumers."""
    current = _ensure_et(now or datetime.datetime.now(_MARKET_TZ))
    today = current.date()
    trading_day = is_nyse_session(today)
    early = is_early_close(today) if trading_day else False

    session_open = _session_open_for_date(today) if trading_day else None
    session_close = get_session_close(today) if trading_day else None

    in_session = bool(
        trading_day
        and session_open is not None
        and session_close is not None
        and session_open <= current < session_close
    )

    if in_session:
        next_close = session_close
        next_day = _next_trading_date(today)
        next_open = _session_open_for_date(next_day) if next_day else None
    elif trading_day and session_open is not None and current < session_open:
        next_open = session_open
        next_close = session_close
    else:
        next_day = _next_trading_date(today)
        if next_day is None:
            next_open = None
            next_close = None
        else:
            next_open = _session_open_for_date(next_day)
            next_close = get_session_close(next_day)

    label = _build_label(
        in_session=in_session,
        trading_day=trading_day,
        early=early,
        now=current,
        session_open=session_open,
        session_close=session_close,
    )

    return {
        "is_session": in_session,
        "is_early_close": early,
        "session_open_et": _iso_et(session_open),
        "session_close_et": _iso_et(session_close),
        "next_open_et": _iso_et(next_open),
        "next_close_et": _iso_et(next_close),
        "label": label,
    }
