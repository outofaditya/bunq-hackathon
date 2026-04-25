"""FastAPI orchestrator server — Phase 2.

Boot:
  python -m orchestrator.server

Endpoints:
  GET  /                         → dashboard (static HTML)
  GET  /events                   → SSE stream of bus events
  POST /missions/{name}/start    → kick off a mission cascade in a worker thread
  POST /bunq-webhook             → bunq's real-time mutation/payment notifications
  GET  /health                   → liveness probe
  GET  /state                    → current mission snapshot (one-shot, no SSE)

ngrok:
  If the local ngrok dashboard is reachable at http://127.0.0.1:4040, the server
  auto-discovers the public HTTPS URL and registers it with bunq as the webhook
  callback target. Otherwise the dashboard still works (the cascade emits SSE
  events directly) but bunq webhooks are silently dropped.
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
from pathlib import Path
from typing import Any

import httpx
import uvicorn
from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, Request
from fastapi.responses import FileResponse
from sse_starlette.sse import EventSourceResponse

from bunq_client import BunqClient

from .agent_loop import run_mission
from .bunq_tools import BunqToolbox
from .events import bus
from .missions import MISSIONS
from .places import search_hotels, search_restaurants
from .stt import transcribe_bytes, transcribe_file
from .subscriptions import list_plans


load_dotenv(override=True)


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DASHBOARD_HTML = PROJECT_ROOT / "dashboard" / "index.html"
ASSETS_DIR = PROJECT_ROOT / "assets"
TTS_CACHE_DIR = ASSETS_DIR / "tts_cache"
MOCK_SITES_DIR = PROJECT_ROOT / "mock_sites"


# ----------------------------------------------------------------------
# Singletons populated at startup
# ----------------------------------------------------------------------

_bunq_client: BunqClient | None = None
_toolbox: BunqToolbox | None = None
_public_url: str | None = None
_mission_thread: threading.Thread | None = None


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------

def _discover_ngrok_url() -> str | None:
    """Query the local ngrok agent for an active https tunnel pointing at :8000."""
    try:
        r = httpx.get("http://127.0.0.1:4040/api/tunnels", timeout=2.0)
        r.raise_for_status()
        for tun in r.json().get("tunnels", []):
            if tun.get("proto") == "https":
                return tun.get("public_url")
    except Exception:  # noqa: BLE001
        pass
    return None


def _get_toolbox() -> BunqToolbox:
    global _bunq_client, _toolbox
    if _toolbox is not None:
        return _toolbox
    api_key = os.getenv("BUNQ_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("BUNQ_API_KEY missing")
    _bunq_client = BunqClient(api_key=api_key, sandbox=True)
    _bunq_client.authenticate()
    _toolbox = BunqToolbox(_bunq_client)
    return _toolbox


# ----------------------------------------------------------------------
# FastAPI app
# ----------------------------------------------------------------------

app = FastAPI(title="Mission Mode Orchestrator")


@app.on_event("startup")
async def _startup() -> None:
    global _public_url

    # Authenticate bunq + cache toolbox.
    tb = _get_toolbox()
    print(f"[server] authenticated as user {tb.client.user_id}, primary={tb.primary_id} ({tb.primary_iban})")

    # Discover ngrok and register webhook.
    env_url = os.getenv("PUBLIC_BASE_URL", "").strip()
    discovered = _discover_ngrok_url() if not env_url else None
    _public_url = env_url or discovered

    if _public_url:
        print(f"[server] public URL: {_public_url}")
        try:
            tb.register_webhook(_public_url)
            print(f"[server] bunq webhook registered → {_public_url}/bunq-webhook")
        except Exception as e:  # noqa: BLE001
            print(f"[server] webhook registration FAILED: {e!r}; falling back to polling")
    else:
        print(
            "[server] no PUBLIC_BASE_URL and no local ngrok detected — webhook disabled, "
            "polling-only mode. Run `ngrok http 8000` in another terminal to enable webhooks."
        )

    print("[server] ready at http://localhost:8000/")


# ----- Static dashboard ------------------------------------------------

@app.get("/")
async def root() -> FileResponse:
    return FileResponse(str(DASHBOARD_HTML))


# ----- SSE event stream -----------------------------------------------

@app.get("/events")
async def events(request: Request) -> EventSourceResponse:
    async def stream():
        async for msg in bus.subscribe():
            if await request.is_disconnected():
                break
            yield {"data": msg}
    return EventSourceResponse(stream())


# ----- Mission trigger -------------------------------------------------

def _kickoff_mission(name: str, user_prompt: str, seed_eur: float, wait_seconds: float) -> dict[str, Any]:
    """Common helper used by both text-trigger and voice-trigger endpoints."""
    global _mission_thread
    if _mission_thread is not None and _mission_thread.is_alive():
        return {"ok": False, "error": "A mission is already running."}

    bus.reset()

    def _run() -> None:
        tb = _get_toolbox()
        try:
            if seed_eur > 0:
                tb.seed_primary(seed_eur)
                tb.snapshot_balance("seed")
            run_mission(
                toolbox=tb,
                system_prompt=MISSIONS[name]["system_prompt"],
                user_prompt=user_prompt,
                wait_for_draft=True,
                wait_timeout_s=wait_seconds,
            )
        except Exception as e:  # noqa: BLE001
            bus.publish("mission_error", {"error": str(e)})

    _mission_thread = threading.Thread(target=_run, daemon=True, name=f"mission-{name}")
    _mission_thread.start()
    return {"ok": True, "mission": name, "user_prompt": user_prompt, "seed_eur": seed_eur, "wait_seconds": wait_seconds}


@app.post("/missions/{name}/start")
async def start_mission(name: str, request: Request) -> dict[str, Any]:
    if name not in MISSIONS:
        return {"ok": False, "error": f"Unknown mission: {name}"}

    body: dict[str, Any] = {}
    try:
        body = await request.json()
    except Exception:
        body = {}

    user_prompt = body.get("user_prompt") or MISSIONS[name]["default_user_prompt"]
    seed_eur = float(body.get("seed_eur", 500.0))
    wait_seconds = float(body.get("wait_seconds", 60.0))

    return _kickoff_mission(name, user_prompt, seed_eur, wait_seconds)


@app.post("/missions/{name}/start-from-voice")
async def start_mission_from_voice(name: str, request: Request) -> dict[str, Any]:
    """Use the pre-recorded mission audio. Transcribes via ElevenLabs Scribe,
    publishes a `transcript_ready` event, then runs the cascade with that text.
    """
    if name not in MISSIONS:
        return {"ok": False, "error": f"Unknown mission: {name}"}

    body: dict[str, Any] = {}
    try:
        body = await request.json()
    except Exception:
        body = {}
    seed_eur = float(body.get("seed_eur", 500.0))
    wait_seconds = float(body.get("wait_seconds", 60.0))

    audio = ASSETS_DIR / f"recorded_voice_{name}.mp3"
    if not audio.exists():
        return {"ok": False, "error": f"Pre-recorded audio missing: {audio.name}"}

    # Reset bus *before* transcribing so the user sees fresh state.
    bus.reset()
    bus.publish("voice_capture_started", {"mission": name, "audio_url": f"/assets/{audio.name}"})
    try:
        transcript = transcribe_file(audio)
    except Exception as e:  # noqa: BLE001
        bus.publish("voice_capture_error", {"error": str(e)})
        return {"ok": False, "error": f"STT failed: {e}"}
    bus.publish("transcript_ready", {"text": transcript})

    # Reuse kickoff but skip its bus.reset — we just published voice events.
    global _mission_thread
    if _mission_thread is not None and _mission_thread.is_alive():
        return {"ok": False, "error": "A mission is already running."}

    def _run() -> None:
        tb = _get_toolbox()
        try:
            if seed_eur > 0:
                tb.seed_primary(seed_eur)
                tb.snapshot_balance("seed")
            run_mission(
                toolbox=tb,
                system_prompt=MISSIONS[name]["system_prompt"],
                user_prompt=transcript,
                wait_for_draft=True,
                wait_timeout_s=wait_seconds,
            )
        except Exception as e:  # noqa: BLE001
            bus.publish("mission_error", {"error": str(e)})

    _mission_thread = threading.Thread(target=_run, daemon=True, name=f"mission-{name}-voice")
    _mission_thread.start()
    return {"ok": True, "transcript": transcript, "mission": name}


@app.get("/tts/{filename}")
async def serve_tts(filename: str) -> FileResponse:
    p = TTS_CACHE_DIR / filename
    if not p.exists() or ".." in filename or "/" in filename:
        return FileResponse(str(TTS_CACHE_DIR / "missing"))  # 404-ish
    return FileResponse(str(p), media_type="audio/mpeg")


@app.get("/assets/{filename}")
async def serve_asset(filename: str) -> FileResponse:
    p = ASSETS_DIR / filename
    if not p.exists() or ".." in filename or "/" in filename:
        return FileResponse(str(ASSETS_DIR / "missing"))
    media_type = "audio/mpeg" if filename.endswith(".mp3") else "application/octet-stream"
    return FileResponse(str(p), media_type=media_type)


# Real-data booking site — restaurants come from Google Places API (or a
# hardcoded fallback if no key is set). The browser-agent navigates this
# page; you can also open it directly in a browser at /mock-restaurant/.
_RESTAURANT_HTML_TEMPLATE: str | None = None


def _restaurant_html() -> str:
    global _RESTAURANT_HTML_TEMPLATE
    if _RESTAURANT_HTML_TEMPLATE is None:
        _RESTAURANT_HTML_TEMPLATE = (MOCK_SITES_DIR / "restaurant" / "index.html").read_text()
    return _RESTAURANT_HTML_TEMPLATE


def _inject_restaurant_data(query: str = "popular dinner restaurants Amsterdam") -> str:
    """Render the booking page with live Google Places data injected.

    Replaces the in-page `RESTAURANTS = [...]` literal with a fresh array.
    """
    import json as _json

    html = _restaurant_html()
    restaurants = search_restaurants(query=query, max_results=4)

    # Build the JS array literal that replaces the hardcoded one in the page.
    js_array = _json.dumps(restaurants, ensure_ascii=False)
    # The original line in index.html starts with "const RESTAURANTS = ["
    # and is multiline. Replace from `const RESTAURANTS = [` through the
    # closing `];` on a line by itself.
    import re as _re

    pattern = _re.compile(r"const RESTAURANTS = \[[\s\S]*?\];", _re.MULTILINE)
    replacement = f"const RESTAURANTS = {js_array};"
    if not pattern.search(html):
        return html  # nothing matched — fall back to the static page
    return pattern.sub(replacement, html, count=1)


@app.get("/mock-restaurant/")
@app.get("/mock-restaurant")
async def mock_restaurant_index(query: str | None = None) -> Any:
    from fastapi.responses import HTMLResponse

    rendered = _inject_restaurant_data(query=query or "popular dinner restaurants Amsterdam")
    return HTMLResponse(rendered)


# Hotel booking site — driven by browser-agent for the Travel mission.
_HOTEL_HTML_TEMPLATE: str | None = None


def _hotel_html() -> str:
    global _HOTEL_HTML_TEMPLATE
    if _HOTEL_HTML_TEMPLATE is None:
        _HOTEL_HTML_TEMPLATE = (MOCK_SITES_DIR / "hotel" / "index.html").read_text()
    return _HOTEL_HTML_TEMPLATE


def _inject_hotel_data(city: str, nights: int) -> str:
    import json as _json

    html = _hotel_html()
    hotels = search_hotels(city, max_results=4)
    return (
        html
        .replace("__HOTELS__", _json.dumps(hotels, ensure_ascii=False))
        .replace("__CITY__", city)
        .replace("__NIGHTS__", str(int(nights)))
    )


@app.get("/mock-hotel/")
@app.get("/mock-hotel")
async def mock_hotel_index(city: str = "Tokyo", nights: int = 3) -> Any:
    from fastapi.responses import HTMLResponse

    return HTMLResponse(_inject_hotel_data(city=city, nights=nights))


# Subscription comparison site — driven by browser-agent for the Payday mission.
_SUB_HTML_TEMPLATE: str | None = None


def _sub_html() -> str:
    global _SUB_HTML_TEMPLATE
    if _SUB_HTML_TEMPLATE is None:
        _SUB_HTML_TEMPLATE = (MOCK_SITES_DIR / "subscriptions" / "index.html").read_text()
    return _SUB_HTML_TEMPLATE


def _inject_subscription_data(category: str) -> str:
    import json as _json

    html = _sub_html()
    plans = list_plans(category, limit=6)
    return (
        html
        .replace("__PLANS__", _json.dumps(plans, ensure_ascii=False))
        .replace("__CATEGORY__", category)
    )


@app.get("/mock-subscriptions/")
@app.get("/mock-subscriptions")
async def mock_subscriptions_index(category: str = "streaming") -> Any:
    from fastapi.responses import HTMLResponse

    return HTMLResponse(_inject_subscription_data(category=category))


# ----- bunq webhook receiver ------------------------------------------

@app.post("/bunq-webhook")
async def bunq_webhook(request: Request) -> dict[str, Any]:
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    notif = payload.get("NotificationUrl", payload)
    category = notif.get("category", "?")
    obj = notif.get("object", {})
    inner_kind = next(iter(obj.keys()), "?") if isinstance(obj, dict) else "?"
    bus.publish("bunq_webhook", {
        "category": category,
        "kind": inner_kind,
        "raw": notif,
    })
    return {"ok": True}


# ----- Health / state probes ------------------------------------------

@app.get("/health")
async def health() -> dict[str, Any]:
    return {
        "ok": True,
        "user_id": _bunq_client.user_id if _bunq_client else None,
        "primary_id": _toolbox.primary_id if _toolbox else None,
        "public_url": _public_url,
        "mission_running": bool(_mission_thread and _mission_thread.is_alive()),
    }


@app.get("/state")
async def state() -> dict[str, Any]:
    return {
        "history": bus._history,
        "mission_running": bool(_mission_thread and _mission_thread.is_alive()),
    }


# ----------------------------------------------------------------------
# Entry point
# ----------------------------------------------------------------------

def main() -> None:
    uvicorn.run("orchestrator.server:app", host="0.0.0.0", port=8000, reload=False)


if __name__ == "__main__":
    main()
