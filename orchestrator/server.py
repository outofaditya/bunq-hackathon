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
from fastapi import BackgroundTasks, FastAPI, File, Form, Request, UploadFile
from fastapi.responses import FileResponse
from sse_starlette.sse import EventSourceResponse

from bunq_client import BunqClient

import anthropic

from .agent_loop import run_mission
from .bunq_tools import BunqToolbox
from .events import bus
from .missions import MISSIONS
from .places import search_hotels, search_restaurants
from .stt import transcribe_bytes, transcribe_file
from .subscriptions import list_plans


load_dotenv(override=True)


PROJECT_ROOT = Path(__file__).resolve().parent.parent
REACT_DIST = PROJECT_ROOT / "dashboard-react" / "dist"
LEGACY_HTML = PROJECT_ROOT / "dashboard" / "index.html"
DASHBOARD_HTML = REACT_DIST / "index.html" if REACT_DIST.exists() else LEGACY_HTML
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


# Vite emits JS/CSS chunks into dist/assets/. The /assets/* path is also
# used by mission audio (recorded_voice_*.mp3) so we have a precedence:
# check the React build first, fall back to the project assets dir.
@app.get("/assets/{filename:path}")
async def serve_react_or_audio(filename: str) -> FileResponse:
    react_path = REACT_DIST / "assets" / filename
    if react_path.exists() and ".." not in filename:
        media = "application/javascript" if filename.endswith(".js") else "text/css" if filename.endswith(".css") else None
        return FileResponse(str(react_path), media_type=media) if media else FileResponse(str(react_path))
    legacy = ASSETS_DIR / filename
    if legacy.exists() and ".." not in filename:
        media = "audio/mpeg" if filename.endswith(".mp3") else None
        return FileResponse(str(legacy), media_type=media) if media else FileResponse(str(legacy))
    return FileResponse(str(ASSETS_DIR / "missing"))


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


def _classify_mission(transcript: str) -> str:
    """Map a free-text spoken command to one of weekend/payday/travel/council."""
    transcript = (transcript or "").strip()
    if not transcript:
        return "weekend"
    # Cheap keyword fast-path before we burn an LLM token.
    t = transcript.lower()
    if any(k in t for k in [
        "council", "should i buy", "should i get", "tempted", "talk me out",
        "talk me into", "convince me", "do i need", "argue", "feelings",
        "voices", "opinions", "vote", "verdict", "is it worth",
    ]):
        return "council"
    if any(k in t for k in ["payday", "salary", "rent", "bills", "monthly", "duwo"]):
        return "payday"
    if any(k in t for k in ["fly", "flight", "trip", "travel", "tokyo", "abroad", "vacation", "holiday"]):
        return "travel"
    if any(k in t for k in ["weekend", "dinner", "restaurant", "concert", "sara", "surprise"]):
        return "weekend"

    # Fallback: ask Claude. Cheap Haiku call.
    try:
        client = anthropic.Anthropic()
        model = os.getenv("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001").strip() or "claude-haiku-4-5-20251001"
        resp = client.messages.create(
            model=model,
            max_tokens=8,
            messages=[{
                "role": "user",
                "content": (
                    "Classify the user's spoken command into ONE mission name and reply with "
                    "exactly that word and nothing else: weekend, payday, travel, council.\n"
                    "council = the user is wavering on a purchase and wants opinions.\n\n"
                    f"Command: {transcript}"
                ),
            }],
        )
        text = ""
        for b in resp.content:
            if getattr(b, "type", None) == "text":
                text += b.text
        text = text.strip().lower().split()[0] if text.strip() else "weekend"
        return text if text in ("weekend", "payday", "travel", "council") else "weekend"
    except Exception:
        return "weekend"


@app.post("/missions/auto/start-from-mic")
async def start_mission_from_mic_auto(
    audio: UploadFile = File(...),
    seed_eur: float = Form(500.0),
    wait_seconds: float = Form(60.0),
) -> dict[str, Any]:
    """Auto-route flow: upload a voice clip, we classify the mission from the
    transcript, then run it. Returns the chosen mission + transcript."""
    audio_bytes = await audio.read()
    if not audio_bytes:
        return {"ok": False, "error": "Empty audio upload"}

    bus.reset()
    bus.publish("voice_capture_started", {"mission": "auto", "live": True, "size_bytes": len(audio_bytes)})
    try:
        transcript = transcribe_bytes(audio_bytes, filename=audio.filename or "recording.webm")
    except Exception as e:  # noqa: BLE001
        bus.publish("voice_capture_error", {"error": str(e)})
        return {"ok": False, "error": f"STT failed: {e}"}
    if not transcript:
        bus.publish("voice_capture_error", {"error": "empty transcript"})
        return {"ok": False, "error": "Empty transcript — was the mic muted?"}

    name = _classify_mission(transcript)
    bus.publish("transcript_ready", {"text": transcript, "live": True, "mission": name})
    bus.publish("mission_routed", {"mission": name, "display": MISSIONS[name]["display_name"]})

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

    _mission_thread = threading.Thread(target=_run, daemon=True, name=f"mission-{name}-auto")
    _mission_thread.start()
    return {"ok": True, "transcript": transcript, "mission": name}


@app.post("/missions/{name}/start-from-mic")
async def start_mission_from_mic(
    name: str,
    audio: UploadFile = File(...),
    seed_eur: float = Form(500.0),
    wait_seconds: float = Form(60.0),
) -> dict[str, Any]:
    """Live-mic flow. Browser uploads recorded audio (typically webm/opus);
    we transcribe via ElevenLabs Scribe and kick the cascade.
    """
    if name not in MISSIONS:
        return {"ok": False, "error": f"Unknown mission: {name}"}

    audio_bytes = await audio.read()
    if not audio_bytes:
        return {"ok": False, "error": "Empty audio upload"}

    bus.reset()
    bus.publish("voice_capture_started", {"mission": name, "live": True, "size_bytes": len(audio_bytes)})
    try:
        transcript = transcribe_bytes(
            audio_bytes,
            filename=audio.filename or "recording.webm",
        )
    except Exception as e:  # noqa: BLE001
        bus.publish("voice_capture_error", {"error": str(e)})
        return {"ok": False, "error": f"STT failed: {e}"}

    if not transcript:
        bus.publish("voice_capture_error", {"error": "empty transcript"})
        return {"ok": False, "error": "Empty transcript — was the mic muted?"}

    bus.publish("transcript_ready", {"text": transcript, "live": True})

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

    _mission_thread = threading.Thread(target=_run, daemon=True, name=f"mission-{name}-mic")
    _mission_thread.start()
    return {"ok": True, "transcript": transcript, "mission": name}


_OPENING_LINES: list[str] = [
    "Alright, what's the mission?",
    "I'm listening — go ahead.",
    "Hit me with it.",
    "Tell me what you need.",
    "Ready when you are.",
    "Okay, what are we doing?",
]


@app.post("/tts/opening")
async def tts_opening() -> dict[str, Any]:
    """Pre-synthesize a short greeting line — dashboard plays it the moment
    the user taps the mic button so the agent feels alive, not silent."""
    import random as _random

    from .tts import synthesize_narration

    line = _random.choice(_OPENING_LINES)
    try:
        fname = synthesize_narration(line)
        return {"ok": True, "text": line, "url": f"/tts/{fname}"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e), "text": line}


@app.get("/tts/{filename}")
async def serve_tts(filename: str) -> FileResponse:
    p = TTS_CACHE_DIR / filename
    if not p.exists() or ".." in filename or "/" in filename:
        return FileResponse(str(TTS_CACHE_DIR / "missing"))  # 404-ish
    return FileResponse(str(p), media_type="audio/mpeg")


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
    """Render the booking page with live Google Places data injected."""
    import json as _json

    html = _restaurant_html()
    restaurants = search_restaurants(query=query, max_results=4)
    return html.replace("__RESTAURANTS__", _json.dumps(restaurants, ensure_ascii=False))


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

@app.get("/personas")
async def get_personas() -> dict[str, Any]:
    """Read-only snapshot of the Council — useful for the dashboard pre-mission."""
    try:
        tb = _get_toolbox()
        personas = tb.list_personas(ensure_min=5)
        return {"ok": True, "personas": personas}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}


@app.post("/admin/cleanup-demo-subs")
async def admin_cleanup_demo_subs(request: Request) -> dict[str, Any]:
    """Drain + cancel any demo-tagged sub-account.

    Body (optional): {"dry": true}  ← preview only, no mutations.
    Untagged accounts the user pre-created are NEVER touched.
    """
    body: dict[str, Any] = {}
    try:
        body = await request.json()
    except Exception:
        body = {}
    dry = bool(body.get("dry", False))
    try:
        tb = _get_toolbox()
        return {"ok": True, **tb.cleanup_demo_subs(dry=dry)}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}


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
