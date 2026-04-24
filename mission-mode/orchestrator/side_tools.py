"""Non-bunq tools the agent can call: Slack DM + TTS narration."""
from __future__ import annotations

import os
from typing import Any

import requests

SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL", "")


def send_slack(message: str, channel: str | None = None) -> dict[str, Any]:
    """POST to an incoming webhook. If unset, no-op with ok=False so the agent sees it."""
    if not SLACK_WEBHOOK_URL:
        return {"ok": False, "reason": "SLACK_WEBHOOK_URL not set", "would_have_sent": message}
    payload: dict[str, Any] = {"text": message}
    if channel:
        payload["channel"] = channel
    try:
        r = requests.post(SLACK_WEBHOOK_URL, json=payload, timeout=5)
        return {"ok": r.status_code == 200, "status": r.status_code, "message": message}
    except Exception as e:
        return {"ok": False, "error": str(e), "message": message}
