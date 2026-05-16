"""Server-Sent Events: subscriber registry and broadcast for scan updates."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

SSE_QUEUE_MAX = 32

_subscribers: list[asyncio.Queue[str]] = []
_sub_lock = asyncio.Lock()


async def register_sse_subscriber(max_queue_size: int = SSE_QUEUE_MAX) -> asyncio.Queue[str]:
    queue_instance: asyncio.Queue[str] = asyncio.Queue(maxsize=max_queue_size)
    async with _sub_lock:
        _subscribers.append(queue_instance)
    return queue_instance


async def unregister_sse_subscriber(queue_instance: asyncio.Queue[str]) -> None:
    async with _sub_lock:
        if queue_instance in _subscribers:
            _subscribers.remove(queue_instance)


async def broadcast_scan_event(envelope: dict[str, Any]) -> None:
    """Push JSON envelope to connected SSE clients (trim queue if backed up)."""
    try:
        line = json.dumps(envelope, default=str)
    except (TypeError, ValueError):
        logger.debug("SSE broadcast serialization failed")
        return
    async with _sub_lock:
        targets = list(_subscribers)
    for queue_instance in targets:
        try:
            queue_instance.put_nowait(line)
        except asyncio.QueueFull:
            try:
                queue_instance.get_nowait()
            except asyncio.QueueEmpty:
                pass
            try:
                queue_instance.put_nowait(line)
            except asyncio.QueueFull:
                async with _sub_lock:
                    if queue_instance in _subscribers:
                        _subscribers.remove(queue_instance)
        except Exception:
            async with _sub_lock:
                if queue_instance in _subscribers:
                    _subscribers.remove(queue_instance)
