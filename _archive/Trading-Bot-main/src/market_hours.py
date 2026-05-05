"""
US stock market hours check (Eastern Time).
Market open: 9:30 AM - 4:00 PM ET, Monday - Friday.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
MARKET_OPEN = (9, 30)  # 9:30 AM
MARKET_CLOSE = (16, 0)  # 4:00 PM


def is_market_open(dt: datetime | None = None) -> bool:
    """
    Check if the US stock market is open at the given time.
    Uses Eastern Time. Market: Mon-Fri, 9:30 AM - 4:00 PM ET.

    Args:
        dt: Datetime to check. If None, uses current time in ET.

    Returns:
        True if market is open, False otherwise.
    """
    if dt is None:
        dt = datetime.now(ET)
    elif dt.tzinfo is None:
        dt = dt.replace(tzinfo=ET)
    else:
        dt = dt.astimezone(ET)

    # Weekend: 0 = Monday, 6 = Sunday
    if dt.weekday() >= 5:
        return False

    open_time = dt.replace(hour=MARKET_OPEN[0], minute=MARKET_OPEN[1], second=0, microsecond=0)
    close_time = dt.replace(hour=MARKET_CLOSE[0], minute=MARKET_CLOSE[1], second=0, microsecond=0)

    return open_time <= dt < close_time


def get_current_et() -> datetime:
    """Return current datetime in Eastern Time."""
    return datetime.now(ET)
