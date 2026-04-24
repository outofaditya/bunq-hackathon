"""Shared SSE event bus — orchestrator publishes, dashboard subscribes.

Safe to use without a running asyncio loop (drops into a no-op). When the
FastAPI server is up in Phase 2, SSE subscribers consume the queued events.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any, AsyncIterator


class EventBus:
    def __init__(self) -> None:
        self._subscribers: list[asyncio.Queue[str]] = []
        self._history: list[dict[str, Any]] = []

    def publish(self, event_type: str, data: dict[str, Any]) -> None:
        payload = {"type": event_type, "t": time.time(), **data}
        self._history.append(payload)
        msg = json.dumps(payload, default=str)
        for q in list(self._subscribers):
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:
                pass
        # Always echo to stdout so CLI runs have visibility.
        print(f"[event] {payload['type']}: {json.dumps({k: v for k, v in payload.items() if k not in ('type', 't')}, default=str)[:160]}")

    async def subscribe(self) -> AsyncIterator[str]:
        q: asyncio.Queue[str] = asyncio.Queue(maxsize=512)
        for item in self._history:
            await q.put(json.dumps(item, default=str))
        self._subscribers.append(q)
        try:
            while True:
                yield await q.get()
        finally:
            self._subscribers.remove(q)

    def reset(self) -> None:
        self._history = []
        self._subscribers = []


bus = EventBus()
