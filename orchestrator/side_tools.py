"""Side-actions used by the agent: Slack DM + Google Calendar event.

Both publish step_started/step_finished events so the dashboard renders
them in the same cascade as the bunq tools.

Slack:
  POST a JSON payload to SLACK_WEBHOOK_URL. Configurable via .env.

Google Calendar:
  Lazy OAuth — on the first call we run InstalledAppFlow.run_local_server()
  which opens a browser for consent, then we cache a refresh token in
  ~/.bunq-hackathon/google_token.json so subsequent runs are silent.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx

from .events import bus


GOOGLE_TOKEN_PATH = Path.home() / ".bunq-hackathon" / "google_token.json"
GOOGLE_SCOPES = ["https://www.googleapis.com/auth/calendar.events"]


# ----------------------------------------------------------------------
# Slack
# ----------------------------------------------------------------------

def send_slack_message(message: str, header: str | None = None) -> dict[str, Any]:
    """POST a message to SLACK_WEBHOOK_URL. Optional `header` renders as a bold title."""
    bus.publish("step_started", {
        "tool": "send_slack_message",
        "header": header or "Mission Agent",
        "preview": message[:80],
    })
    url = os.getenv("SLACK_WEBHOOK_URL", "").strip()
    if not url:
        result = {"ok": False, "error": "SLACK_WEBHOOK_URL missing"}
        bus.publish("step_error", {"tool": "send_slack_message", "error": result["error"]})
        return result

    blocks: list[dict[str, Any]] = []
    if header:
        blocks.append({"type": "header", "text": {"type": "plain_text", "text": header}})
    blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": message}})
    blocks.append({
        "type": "context",
        "elements": [{"type": "mrkdwn", "text": "_via Mission Agent · bunq Hackathon 7.0_"}],
    })

    try:
        r = httpx.post(
            url,
            json={"text": message, "blocks": blocks},
            timeout=10.0,
        )
        ok = (r.status_code == 200 and r.text.strip().lower() == "ok")
        result = {
            "ok": ok,
            "status_code": r.status_code,
            "header": header,
            "message_preview": message[:140],
        }
        if not ok:
            result["error"] = f"Slack webhook returned {r.status_code}: {r.text[:120]}"
            bus.publish("step_error", {"tool": "send_slack_message", "error": result["error"]})
        else:
            bus.publish("step_finished", {"tool": "send_slack_message", "result": result})
        return result
    except Exception as e:  # noqa: BLE001
        bus.publish("step_error", {"tool": "send_slack_message", "error": str(e)})
        return {"ok": False, "error": str(e)}


# ----------------------------------------------------------------------
# Google Calendar
# ----------------------------------------------------------------------

def _load_or_refresh_credentials():
    """Return a google.oauth2.credentials.Credentials, prompting for OAuth on first call."""
    from google.auth.transport.requests import Request as GoogleRequest
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow

    creds = None
    if GOOGLE_TOKEN_PATH.exists():
        try:
            creds = Credentials.from_authorized_user_info(
                json.loads(GOOGLE_TOKEN_PATH.read_text()), GOOGLE_SCOPES
            )
        except Exception:
            creds = None

    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(GoogleRequest())
            GOOGLE_TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
            GOOGLE_TOKEN_PATH.write_text(creds.to_json())
            return creds
        except Exception as e:  # noqa: BLE001
            print(f"[google] refresh failed: {e!r} — re-running consent flow")

    client_json = os.getenv("GOOGLE_OAUTH_CLIENT_JSON", "./google_oauth_client.json").strip()
    if not Path(client_json).exists():
        raise RuntimeError(
            f"Missing OAuth client JSON at {client_json}. Download desktop credentials from GCP."
        )

    flow = InstalledAppFlow.from_client_secrets_file(client_json, GOOGLE_SCOPES)
    creds = flow.run_local_server(port=0, open_browser=True, authorization_prompt_message="")
    GOOGLE_TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    GOOGLE_TOKEN_PATH.write_text(creds.to_json())
    return creds


def _next_friday_at(hour: int, minute: int = 0) -> datetime:
    """Return the upcoming Friday at HH:MM in Europe/Amsterdam (UTC offset +02 in DST)."""
    now = datetime.now(timezone.utc)
    days_ahead = (4 - now.weekday()) % 7  # Friday = 4
    if days_ahead == 0 and now.hour >= hour:
        days_ahead = 7
    target = now + timedelta(days=days_ahead)
    return target.replace(hour=hour, minute=minute, second=0, microsecond=0)


def create_calendar_event(
    title: str,
    description: str | None = None,
    when: str | None = None,        # e.g. "Friday 19:30"
    duration_minutes: int = 120,
    invitees: list[str] | None = None,
) -> dict[str, Any]:
    """Insert an event into the user's primary Google Calendar.

    `when` is a free-text time — for the demo we resolve "Friday HH:MM" to the
    upcoming Friday, otherwise default to Friday 19:30.
    """
    bus.publish("step_started", {
        "tool": "create_calendar_event",
        "title": title,
        "when": when,
        "invitees": invitees or [],
    })
    try:
        # Resolve the time.
        hour, minute = 19, 30
        if when:
            for token in when.replace(",", " ").split():
                if ":" in token and token.replace(":", "").isdigit():
                    parts = token.split(":")
                    try:
                        hour = int(parts[0])
                        minute = int(parts[1]) if len(parts) > 1 else 0
                        break
                    except ValueError:
                        continue
        start_dt = _next_friday_at(hour, minute)
        end_dt = start_dt + timedelta(minutes=duration_minutes)

        creds = _load_or_refresh_credentials()
        from googleapiclient.discovery import build

        service = build("calendar", "v3", credentials=creds, cache_discovery=False)
        body = {
            "summary": title,
            "description": description or "Booked by your Mission Agent.",
            "start": {"dateTime": start_dt.astimezone().isoformat(), "timeZone": "Europe/Amsterdam"},
            "end": {"dateTime": end_dt.astimezone().isoformat(), "timeZone": "Europe/Amsterdam"},
            "reminders": {"useDefault": True},
        }
        if invitees:
            body["attendees"] = [{"email": e} for e in invitees]
        event = service.events().insert(
            calendarId="primary",
            body=body,
            sendUpdates="all" if invitees else "none",
        ).execute()

        result = {
            "ok": True,
            "event_id": event.get("id"),
            "html_link": event.get("htmlLink"),
            "title": title,
            "start": body["start"]["dateTime"],
            "end": body["end"]["dateTime"],
            "invitees": invitees or [],
        }
        bus.publish("step_finished", {"tool": "create_calendar_event", "result": result})
        return result
    except Exception as e:  # noqa: BLE001
        bus.publish("step_error", {"tool": "create_calendar_event", "error": str(e)})
        return {"ok": False, "error": str(e)}
