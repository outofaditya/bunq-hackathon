"""In-memory session store for the hackathon demo.

One user, one active session at a time. Session state tracks the phase machine
and full message history for the Claude tool-use loop.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from .phases import Phase


@dataclass
class Session:
    session_id: str
    phase: Phase = Phase.UNDERSTANDING
    messages: list[dict[str, Any]] = field(default_factory=list)
    selected_package: dict[str, Any] | None = None
    sub_account_id: int | None = None
    sub_account_iban: str | None = None
    pending_draft_ids: list[int] = field(default_factory=list)
    narrations: list[str] = field(default_factory=list)
    closing_line_emitted: bool = False


_sessions: dict[str, Session] = {}


def create_session() -> Session:
    sid = uuid.uuid4().hex[:8]
    s = Session(session_id=sid)
    _sessions[sid] = s
    return s


def get_session(session_id: str) -> Session | None:
    return _sessions.get(session_id)


def get_or_create(session_id: str | None) -> Session:
    if session_id and session_id in _sessions:
        return _sessions[session_id]
    return create_session()
