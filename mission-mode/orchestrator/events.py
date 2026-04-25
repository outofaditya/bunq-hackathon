"""SSE event bus for dashboard state updates.

Single global queue for the hackathon demo (one session at a time). Each connected
dashboard client gets its own asyncio.Queue and receives every event fanned out
from publish().

History replay: every published event (except a few ultra-high-volume ones) is
also kept in a bounded deque. New subscribers receive the buffered history first
so a late-mounted dashboard sees the events that already fired.
"""
from __future__ import annotations

import asyncio
import json
from collections import deque
from typing import Any


# These event types are emitted at very high frequency (per-token, per-frame)
# and would dominate the replay buffer for new subscribers without adding value.
_HISTORY_SKIP = {"agent_text_delta", "browser_frame"}
_HISTORY_MAX = 500


class EventBus:
    def __init__(self) -> None:
        self._subscribers: list[asyncio.Queue[str]] = []
        self._history: deque[str] = deque(maxlen=_HISTORY_MAX)

    def subscribe(self) -> asyncio.Queue[str]:
        q: asyncio.Queue[str] = asyncio.Queue(maxsize=512)
        # Replay buffered history so a late-mounted client sees prior state.
        for msg in self._history:
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:
                break
        self._subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[str]) -> None:
        if q in self._subscribers:
            self._subscribers.remove(q)

    def history(self) -> list[dict[str, Any]]:
        """Snapshot of the buffered events (for /state)."""
        out: list[dict[str, Any]] = []
        for raw in self._history:
            try:
                out.append(json.loads(raw))
            except Exception:  # noqa: BLE001
                pass
        return out

    def reset_history(self) -> None:
        self._history.clear()

    async def publish(self, event_type: str, **payload: Any) -> None:
        msg = json.dumps({"type": event_type, **payload})
        if event_type not in _HISTORY_SKIP:
            self._history.append(msg)
        for q in list(self._subscribers):
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:
                pass


bus = EventBus()
