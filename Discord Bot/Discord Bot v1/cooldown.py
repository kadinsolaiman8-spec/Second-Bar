# cooldown.py — Cooldown tracker, mute system, VWAP trap suppression, reminders

import logging
from datetime import datetime, timedelta
from typing import Optional
import pytz

from config import (
    COOLDOWN_MINUTES, COOLDOWN_REVERSAL_ATR_MULTIPLIER,
    VWAP_TRAP_SUPPRESS_MINUTES, REMINDER_MAX_MINUTES, MARKET_TIMEZONE,
)

logger = logging.getLogger(__name__)
ET = pytz.timezone(MARKET_TIMEZONE)


def _now() -> datetime:
    return datetime.now(ET)


def _fmt_remaining(expiry: datetime) -> str:
    """Format time remaining as 'Xm Ys'."""
    remaining = expiry - _now()
    total = max(0, int(remaining.total_seconds()))
    mins = total // 60
    secs = total % 60
    return f"{mins}m {secs}s"


# ══════════════════════════════════════════════════════════════════════════
# DATA STRUCTURES
# ══════════════════════════════════════════════════════════════════════════

# { "TICKER": {direction, expiry, entry_price, atr, alert_time} }
cooldown_tracker: dict = {}

# { "TICKER": {expiry, reason} }
mute_tracker: dict = {}

# { "TICKER": suppress_until (datetime) }
vwap_trap_tracker: dict = {}

# [ {ticker, user_id, fire_at, channel_id} ]
reminder_list: list = []


# ══════════════════════════════════════════════════════════════════════════
# COOLDOWN SYSTEM
# ══════════════════════════════════════════════════════════════════════════

def is_in_cooldown(ticker: str, direction: str) -> bool:
    """True if ticker is locked in the given direction and hasn't expired."""
    ticker = ticker.upper()
    if ticker not in cooldown_tracker:
        return False
    data = cooldown_tracker[ticker]
    if _now() >= data["expiry"]:
        del cooldown_tracker[ticker]
        return False
    return data["direction"] == direction


def set_cooldown(ticker: str, direction: str, entry_price: float, atr: float):
    """Lock ticker for COOLDOWN_MINUTES after alert fires."""
    ticker = ticker.upper()
    now = _now()
    expiry = now + timedelta(minutes=COOLDOWN_MINUTES)
    cooldown_tracker[ticker] = {
        "direction": direction,
        "expiry": expiry,
        "entry_price": entry_price,
        "atr": atr,
        "alert_time": now,
    }
    logger.info(f"Cooldown set: {ticker} {direction} until {expiry.strftime('%H:%M:%S ET')}")


def check_reversal_reset(ticker: str, current_price: float) -> bool:
    """If price reverses > 1.5x ATR against signal, reset cooldown. Returns True if reset."""
    ticker = ticker.upper()
    if ticker not in cooldown_tracker:
        return False
    data = cooldown_tracker[ticker]
    entry = data["entry_price"]
    atr = data["atr"]
    threshold = COOLDOWN_REVERSAL_ATR_MULTIPLIER * atr

    reversed_ = False
    if data["direction"] == "BUY" and current_price < entry - threshold:
        reversed_ = True
    # SELL reversal removed — BUY only

    if reversed_:
        logger.info(f"Reversal detected for {ticker}. Resetting cooldown.")
        del cooldown_tracker[ticker]
        return True
    return False


def get_all_cooldowns() -> dict:
    """Return all active cooldowns with time remaining."""
    now = _now()
    active = {}
    expired = []
    for ticker, data in cooldown_tracker.items():
        if now >= data["expiry"]:
            expired.append(ticker)
            continue
        active[ticker] = {
            "direction": data["direction"],
            "entry_price": data["entry_price"],
            "expiry": data["expiry"],
            "atr": data["atr"],
            "alert_time": data["alert_time"],
            "time_remaining": _fmt_remaining(data["expiry"]),
        }
    for t in expired:
        del cooldown_tracker[t]
    return active


def clear_cooldown(ticker: str, direction: str = None) -> bool:
    """Manually clear a cooldown. Returns True if was in cooldown."""
    ticker = ticker.upper()
    if ticker in cooldown_tracker:
        if direction and cooldown_tracker[ticker].get("direction") != direction:
            return False
        del cooldown_tracker[ticker]
        logger.info(f"Cooldown cleared: {ticker}")
        return True
    return False


def clear_all_cooldowns() -> int:
    """Clear all cooldowns. Returns count cleared."""
    count = len(cooldown_tracker)
    cooldown_tracker.clear()
    logger.info(f"Cleared {count} cooldowns")
    return count


def cleanup_expired_cooldowns():
    """Remove expired cooldown entries."""
    now = _now()
    expired = [t for t, d in cooldown_tracker.items() if now >= d["expiry"]]
    for t in expired:
        del cooldown_tracker[t]


def get_cooldown_status(ticker: str) -> str:
    """Human-readable cooldown status."""
    ticker = ticker.upper()
    if ticker not in cooldown_tracker:
        return "Not in cooldown"
    data = cooldown_tracker[ticker]
    if _now() >= data["expiry"]:
        del cooldown_tracker[ticker]
        return "Not in cooldown"
    return f"In {data['direction']} cooldown — {_fmt_remaining(data['expiry'])} remaining"


# ══════════════════════════════════════════════════════════════════════════
# MUTE SYSTEM
# ══════════════════════════════════════════════════════════════════════════

def add_mute(ticker: str, minutes: int, reason: str = "Manual mute") -> datetime:
    """Mute a ticker for N minutes. Returns expiry datetime."""
    ticker = ticker.upper()
    expiry = _now() + timedelta(minutes=minutes)
    mute_tracker[ticker] = {"expiry": expiry, "reason": reason}
    logger.info(f"Muted {ticker} for {minutes}m. Reason: {reason}")
    return expiry


def remove_mute(ticker: str) -> bool:
    """Remove mute. Returns True if was muted."""
    ticker = ticker.upper()
    if ticker in mute_tracker:
        del mute_tracker[ticker]
        logger.info(f"Unmuted {ticker}")
        return True
    return False


def is_muted(ticker: str) -> bool:
    """Check if ticker is muted (auto-expires)."""
    ticker = ticker.upper()
    if ticker not in mute_tracker:
        return False
    if _now() >= mute_tracker[ticker]["expiry"]:
        del mute_tracker[ticker]
        return False
    return True


def get_all_mutes() -> dict:
    """Return all active mutes with time remaining."""
    now = _now()
    active = {}
    expired = []
    for ticker, data in mute_tracker.items():
        if now >= data["expiry"]:
            expired.append(ticker)
            continue
        active[ticker] = {
            "expiry": data["expiry"],
            "reason": data["reason"],
            "remaining": _fmt_remaining(data["expiry"]),
        }
    for t in expired:
        del mute_tracker[t]
    return active


def cleanup_expired_mutes():
    """Remove expired mutes."""
    now = _now()
    expired = [t for t, d in mute_tracker.items() if now >= d["expiry"]]
    for t in expired:
        del mute_tracker[t]


# ══════════════════════════════════════════════════════════════════════════
# VWAP TRAP SUPPRESSION
# ══════════════════════════════════════════════════════════════════════════

def set_vwap_trap(ticker: str):
    """Suppress buy alerts for VWAP_TRAP_SUPPRESS_MINUTES."""
    ticker = ticker.upper()
    vwap_trap_tracker[ticker] = _now() + timedelta(minutes=VWAP_TRAP_SUPPRESS_MINUTES)
    logger.debug(f"VWAP trap set for {ticker}")


def is_vwap_trapped(ticker: str) -> bool:
    """Check if ticker is suppressed by VWAP trap."""
    ticker = ticker.upper()
    if ticker not in vwap_trap_tracker:
        return False
    if _now() >= vwap_trap_tracker[ticker]:
        del vwap_trap_tracker[ticker]
        return False
    return True


def clear_vwap_trap(ticker: str):
    """Manually clear VWAP trap."""
    vwap_trap_tracker.pop(ticker.upper(), None)


# ══════════════════════════════════════════════════════════════════════════
# REMINDER SYSTEM
# ══════════════════════════════════════════════════════════════════════════

def add_reminder(ticker: str, user_id: int, minutes: int, channel_id: int) -> bool:
    """Add a reminder. Returns False if minutes > max allowed."""
    if minutes > REMINDER_MAX_MINUTES or minutes < 1:
        return False
    fire_at = _now() + timedelta(minutes=minutes)
    reminder_list.append({
        "ticker": ticker.upper(),
        "user_id": user_id,
        "fire_at": fire_at,
        "channel_id": channel_id,
    })
    logger.debug(f"Reminder set: {ticker} for user {user_id} in {minutes}m")
    return True


def get_due_reminders() -> list:
    """Return and remove all reminders that should fire now."""
    now = _now()
    due = [r for r in reminder_list if now >= r["fire_at"]]
    for r in due:
        reminder_list.remove(r)
    return due


def remove_reminder(ticker: str, user_id: int) -> bool:
    """Remove a specific reminder. Returns True if found."""
    ticker = ticker.upper()
    for i, r in enumerate(reminder_list):
        if r["ticker"] == ticker and r["user_id"] == user_id:
            reminder_list.pop(i)
            return True
    return False


def get_all_reminders() -> list:
    """Return all pending reminders with time remaining."""
    now = _now()
    result = []
    for r in reminder_list:
        if now < r["fire_at"]:
            result.append({
                **r,
                "remaining": _fmt_remaining(r["fire_at"]),
            })
    return result


# ══════════════════════════════════════════════════════════════════════════
# GLOBAL OPERATIONS
# ══════════════════════════════════════════════════════════════════════════

def reset_all() -> dict:
    """Clear everything. Returns summary of what was cleared."""
    n_cd = len(cooldown_tracker)
    n_mu = len(mute_tracker)
    n_vt = len(vwap_trap_tracker)
    n_re = len(reminder_list)

    cooldown_tracker.clear()
    mute_tracker.clear()
    vwap_trap_tracker.clear()
    reminder_list.clear()

    logger.info(f"Reset all: {n_cd} cooldowns, {n_mu} mutes, {n_vt} traps, {n_re} reminders")
    return {
        "cooldowns_cleared": n_cd,
        "mutes_cleared": n_mu,
        "vwap_traps_cleared": n_vt,
        "reminders_cleared": n_re,
        "total_cleared": n_cd + n_mu + n_vt + n_re,
    }


def get_system_status() -> dict:
    """Full status of all subsystems."""
    return {
        "cooldowns_active": len(get_all_cooldowns()),
        "mutes_active": len(get_all_mutes()),
        "vwap_traps_active": sum(1 for t in vwap_trap_tracker if _now() < vwap_trap_tracker[t]),
        "reminders_pending": len([r for r in reminder_list if _now() < r["fire_at"]]),
    }
