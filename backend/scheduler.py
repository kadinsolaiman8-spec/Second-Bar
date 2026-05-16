"""Background scan jobs using APScheduler (no Discord)."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import pytz
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from backend import state
from backend.persistence import DAILY_RESET_KEY, read_kv, save_journal_snapshot, write_kv
from backend.realtime import broadcast_scan_event
from backend.scan_publish import finalize_scan_publish_async
from scanner_core import journal as journal_mod
from scanner_core.config import MARKET_TIMEZONE, SCAN_INTERVAL_SECONDS
from scanner_core.scanner import scan_all_tickers
from scanner_core.watchlist import ALL_STOCKS

logger = logging.getLogger(__name__)


def _today_et_iso() -> str:
    et_tz = pytz.timezone(MARKET_TIMEZONE)
    return datetime.now(et_tz).date().isoformat()


async def run_daily_reset() -> None:
    """Cron-triggered daily journal reset; idempotent via kv dedup."""
    today_iso = _today_et_iso()
    if read_kv(DAILY_RESET_KEY) == today_iso:
        logger.debug("Daily reset already done for %s — skipping", today_iso)
        return
    journal_mod.reset_daily_stats()
    save_journal_snapshot()
    write_kv(DAILY_RESET_KEY, today_iso)
    logger.info("Daily journal reset completed for %s", today_iso)


def build_scheduler() -> AsyncIOScheduler:
    tz = pytz.timezone(MARKET_TIMEZONE)
    return AsyncIOScheduler(timezone=tz)


async def run_scheduled_scan() -> None:
    today_iso = _today_et_iso()
    if read_kv(DAILY_RESET_KEY) != today_iso:
        journal_mod.reset_daily_stats()
        save_journal_snapshot()
        write_kv(DAILY_RESET_KEY, today_iso)
        logger.info("Lazy daily journal reset triggered for %s", today_iso)

    # Server jobs use the shared symbol list only. Browser watchlist order lives in
    # localStorage and is sent on manual POST /api/scan/run; prepending a client
    # watchlist here would duplicate or skew order without that payload.
    tickers = list(ALL_STOCKS)
    ts = datetime.now(timezone.utc).isoformat()

    async def _on_scan_progress(progress: dict[str, object]) -> None:
        await broadcast_scan_event({"type": "scan_progress", "data": progress})

    async with state.scan_lock:
        try:
            signals = await scan_all_tickers(
                tickers,
                alert_channel=None,
                on_scan_progress=_on_scan_progress,
            )
            state.set_scan_result(signals, ts_iso=ts, error=None)
            logger.info("Scheduled scan finished: %s signals", len(signals))
        except Exception as exc:
            logger.exception("Scheduled scan failed: %s", exc)
            state.set_scan_error(ts_iso=ts, error=str(exc))
        finally:
            await finalize_scan_publish_async()
