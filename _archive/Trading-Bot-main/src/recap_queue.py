"""
Queue for recap requests. Processes recap jobs sequentially to avoid rate limits
and ensure every signal is posted.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from src.stop import StopRequested

if TYPE_CHECKING:
    import discord

logger = logging.getLogger(__name__)

_recap_queue: asyncio.Queue[RecapJob] | None = None


@dataclass
class RecapJob:
    """A single recap job to be processed by the queue worker."""

    job_type: str  # "recap" | "market" | "watchlist_recap"
    params: dict
    deliver: Callable[
        [discord.Embed | None, str | None], Awaitable[None]
    ]  # async (embed, error) -> None


def init_recap_queue(max_size: int = 3) -> None:
    """Initialize the recap queue with the given max size. Call at startup."""
    global _recap_queue
    _recap_queue = asyncio.Queue(maxsize=max_size if max_size > 0 else 0)


def enqueue_recap(job: RecapJob) -> int | None:
    """
    Enqueue a recap job. Returns 1-based position if enqueued, or None if queue is full.
    """
    if _recap_queue is None:
        raise RuntimeError("Recap queue not initialized. Call init_recap_queue first.")
    if _recap_queue.full():
        return None
    _recap_queue.put_nowait(job)
    return _recap_queue.qsize()


def get_queue_position() -> int:
    """Return current number of jobs in queue (0 if empty)."""
    if _recap_queue is None:
        return 0
    return _recap_queue.qsize()


async def recap_queue_worker(
    executor: object,
    run_recap: Callable[..., object],
    run_market: Callable[..., object],
    run_watchlist_recap: Callable[..., object],
) -> None:
    """
    Background worker that processes recap jobs sequentially.
    executor: ThreadPoolExecutor for running blocking recap logic
    run_recap, run_market, run_watchlist_recap: functions that return discord.Embed
    """
    if _recap_queue is None:
        logger.error("Recap queue worker started but queue not initialized")
        return

    loop = asyncio.get_event_loop()

    while True:
        try:
            job = await _recap_queue.get()
        except asyncio.CancelledError:
            logger.info("Recap queue worker cancelled")
            raise

        embed: object | None = None
        error: str | None = None

        try:
            if job.job_type == "recap":
                embed = await loop.run_in_executor(
                    executor,
                    lambda: run_recap(
                        ignore_volatility=job.params.get("ignore_volatility", False),
                        timeframe=job.params.get("timeframe", "Daily"),
                        show_breakdown=job.params.get("show_breakdown", False),
                    ),
                )
            elif job.job_type == "market":
                embed = await loop.run_in_executor(
                    executor,
                    lambda: run_market(
                        index_id=job.params["index_id"],
                        index_name=job.params["index_name"],
                        ignore_volatility=job.params.get("ignore_volatility", False),
                        timeframe=job.params.get("timeframe", "Daily"),
                        show_breakdown=job.params.get("show_breakdown", False),
                    ),
                )
            elif job.job_type == "watchlist_recap":
                embed = await loop.run_in_executor(
                    executor,
                    lambda: run_watchlist_recap(
                        user_id=job.params["user_id"],
                        guild_id=job.params.get("guild_id"),
                        ignore_volatility=job.params.get("ignore_volatility", False),
                        timeframe=job.params.get("timeframe", "Daily"),
                        show_breakdown=job.params.get("show_breakdown", False),
                    ),
                )
            else:
                error = f"Unknown job type: {job.job_type}"
        except StopRequested:
            error = "Operation stopped."
        except Exception as e:
            logger.exception("Recap job failed: %s", e)
            error = str(e) or "An error occurred while generating the recap."

        try:
            await job.deliver(embed, error)
        except Exception as e:
            logger.exception("Failed to deliver recap result: %s", e)
