"""Phase machine for the trip-agent conversation.

UNDERSTANDING ─(options presented + selection made)─► AWAITING_CONFIRMATION
AWAITING_CONFIRMATION ─(user confirms)─► EXECUTING
EXECUTING ─(all execution tools called)─► DONE

The phase controls which tools Claude sees on each turn. Execution tools are
not even in the catalog during UNDERSTANDING, so the model literally cannot
call them.
"""
from __future__ import annotations

from enum import Enum


class Phase(str, Enum):
    UNDERSTANDING = "UNDERSTANDING"
    AWAITING_CONFIRMATION = "AWAITING_CONFIRMATION"
    EXECUTING = "EXECUTING"
    DONE = "DONE"
