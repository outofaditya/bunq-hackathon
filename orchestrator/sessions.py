"""Per-session state for the interactive Trip mission.

Sessions are keyed by session_id sent from the dashboard's chat panel. Each
session owns its full message history, current phase, and any state derived
from earlier tool calls (sub-account id/iban, pending draft ids).

In-memory store; one process. No persistence — refreshing the dashboard
starts a fresh session.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from threading import Lock
from typing import Any


PHASE_UNDERSTANDING = "UNDERSTANDING"
PHASE_AWAITING_CONFIRMATION = "AWAITING_CONFIRMATION"
PHASE_EXECUTING = "EXECUTING"
PHASE_DONE = "DONE"


@dataclass
class TripSession:
    session_id: str
    phase: str = PHASE_UNDERSTANDING
    messages: list[dict[str, Any]] = field(default_factory=list)
    sub_account_id: int | None = None
    sub_account_iban: str | None = None
    pending_draft_ids: list[int] = field(default_factory=list)


_sessions: dict[str, TripSession] = {}
_lock = Lock()


def get_or_create(session_id: str) -> TripSession:
    with _lock:
        if session_id not in _sessions:
            _sessions[session_id] = TripSession(session_id=session_id)
        return _sessions[session_id]


def reset(session_id: str) -> None:
    with _lock:
        _sessions.pop(session_id, None)
