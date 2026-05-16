"""NYSE session calendar helpers.

Uses ``exchange_calendars`` (XNYS calendar) when available.  Degrades gracefully
if the package is not installed: ``is_nyse_session`` returns True (assume open),
``get_session_close`` returns 16:00 ET, and ``is_early_close`` returns False.
This ensures the rest of the codebase continues to function even when the optional
dependency is absent.
"""

from __future__ import annotations

import datetime
import logging

import pytz

logger = logging.getLogger(__name__)

_MARKET_TZ = pytz.timezone("US/Eastern")
_DEFAULT_CLOSE_TIME = datetime.time(16, 0)

try:
    import exchange_calendars as _xcals

    _nyse = _xcals.get_calendar("XNYS")
    _EC_AVAILABLE = True
except ImportError:
    _nyse = None  # type: ignore[assignment]
    _EC_AVAILABLE = False
    logger.debug(
        "exchange_calendars not installed; session_calendar helpers will use conservative defaults. "
        "Install with: pip install exchange-calendars>=4.2"
    )


def is_nyse_session(date: datetime.date) -> bool:
    """Return True if *date* is a regular NYSE trading session (market open day).

    Falls back to True (assume open) when exchange_calendars is unavailable or
    the lookup raises an unexpected exception.
    """
    if not _EC_AVAILABLE:
        return True
    try:
        return bool(_nyse.is_session(str(date)))
    except Exception:
        logger.debug("is_nyse_session: lookup failed for %s, assuming open", date)
        return True


def get_session_close(date: datetime.date) -> datetime.datetime:
    """Return the regular-session close time for *date* as a timezone-aware ET datetime.

    Returns 16:00 US/Eastern if exchange_calendars is unavailable, if *date* is
    not a trading session, or if the lookup fails.
    """
    default = _MARKET_TZ.localize(datetime.datetime.combine(date, _DEFAULT_CLOSE_TIME))
    if not _EC_AVAILABLE:
        return default
    try:
        close_utc = _nyse.session_close(str(date))
        return close_utc.tz_convert(_MARKET_TZ)
    except Exception:
        logger.debug("get_session_close: lookup failed for %s, using 16:00 ET", date)
        return default


def is_early_close(date: datetime.date) -> bool:
    """Return True if *date* is a NYSE early-close day (e.g. day before Thanksgiving).

    An early close is defined as a session whose regular close is before 16:00 ET.
    Returns False when exchange_calendars is unavailable (conservative default).
    """
    if not _EC_AVAILABLE:
        return False
    try:
        close_et = get_session_close(date)
        return close_et.hour < 16
    except Exception:
        logger.debug("is_early_close: lookup failed for %s, assuming regular close", date)
        return False
