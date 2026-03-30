from __future__ import annotations

import asyncio
from typing import List

from .models import SignalEvent


class SignalBus:
    """Small in-memory async bus.

    This keeps the system event-driven without introducing external infra.
    It is intentionally lightweight so it can run on Railway/Render style
    deployments and can be swapped later for Redis/Kafka/NATS.
    """

    def __init__(self, maxsize: int = 2000):
        self._queue: asyncio.Queue[SignalEvent] = asyncio.Queue(maxsize=maxsize)

    async def publish(self, signal: SignalEvent) -> None:
        await self._queue.put(signal)

    async def drain(self) -> List[SignalEvent]:
        items: List[SignalEvent] = []
        while not self._queue.empty():
            items.append(self._queue.get_nowait())
        return items

    def size(self) -> int:
        return self._queue.qsize()
