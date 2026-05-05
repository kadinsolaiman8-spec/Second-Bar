"""Background scan jobs using APScheduler (no Discord)."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import pytz
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from backend import state
from scanner_core.config import MARKET_TIMEZONE, SCAN_INTERVAL_SECONDS
from scanner_core.scanner import scan_all_tickers
from scanner_core.watchlist import ALL_STOCKS

logger = logging.getLogger(__name__)


def build_scheduler() -> AsyncIOScheduler:
    tz = pytz.timezone(MARKET_TIMEZONE)
    return AsyncIOScheduler(timezone=tz)


async def run_scheduled_scan() -> None:
    tickers = list(ALL_STOCKS)
    ts = datetime.now(timezone.utc).isoformat()
    async with state.scan_lock:
        try:
            signals = await scan_all_tickers(tickers, alert_channel=None)
            state.set_scan_result(signals, ts_iso=ts, error=None)
            logger.info("Scheduled scan finished: %s signals", len(signals))
        except Exception as exc:
            logger.exception("Scheduled scan failed: %s", exc)
            state.set_scan_result([], ts_iso=ts, error=str(exc))
