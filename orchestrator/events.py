"""Shared event bus — thread-safe so worker threads can publish to SSE consumers.

The mission cascade runs in a daemon thread (`threading.Thread`); the SSE
endpoint runs in the asyncio event loop. We bridge them with a stdlib
`queue.Queue` per subscriber, drained via `asyncio.to_thread` so the loop
never blocks. Live updates are guaranteed.
"""

from __future__ import annotations

import asyncio
import json
import queue
import threading
import time
from typing import Any, AsyncIterator


class EventBus:
    def __init__(self) -> None:
        self._subscribers: list[queue.Queue[str]] = []
        self._history: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    def publish(self, event_type: str, data: dict[str, Any]) -> None:
        payload = {"type": event_type, "t": time.time(), **data}
        msg = json.dumps(payload, default=str)
        with self._lock:
            self._history.append(payload)
            subs = list(self._subscribers)
        for q in subs:
            try:
                q.put_nowait(msg)
            except queue.Full:
                pass
        # CLI echo so terminal runs have visibility.
        try:
            preview = json.dumps(
                {k: v for k, v in payload.items() if k not in ("type", "t")},
                default=str,
            )[:160]
        except Exception:  # noqa: BLE001
            preview = ""
        print(f"[event] {payload['type']}: {preview}", flush=True)

    async def subscribe(self) -> AsyncIterator[str]:
        q: queue.Queue[str] = queue.Queue(maxsize=512)
        with self._lock:
            for item in self._history:
                try:
                    q.put_nowait(json.dumps(item, default=str))
                except queue.Full:
                    pass
            self._subscribers.append(q)
        try:
            while True:
                # Block in a worker thread so the event loop stays free.
                msg = await asyncio.to_thread(q.get)
                yield msg
        finally:
            with self._lock:
                if q in self._subscribers:
                    self._subscribers.remove(q)

    def reset(self) -> None:
        with self._lock:
            self._history = []
            # Keep subscribers connected — they'll just see new events from now on.


bus = EventBus()
