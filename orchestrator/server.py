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

from .agent_loop import run_mission, run_trip_turn
from .bunq_tools import BunqToolbox
from .events import bus
from .missions import MISSIONS
from .places import search_hotels, search_restaurants
from .sessions import get_or_create as get_session, reset as reset_session
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
_genesis_thread: threading.Thread | None = None

# Bridge: agent-loop call to request_confirmation() blocks on this event.
# /missions/council/confirm fills the slot + sets the event.
_user_confirm_event: threading.Event = threading.Event()
_user_confirm_slot: dict[str, Any] | None = None


def _classify_council_decision(
    transcript: str,
    personas: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Classify the user's spoken confirmation during a council.

    Returns a dict: `{decision, picked_persona_id, picked_name, confidence}`.
    `decision` ∈ 'yes' | 'no' | 'unsure'.

    The user can answer:
      - "yes" / "do it" / "approve"        → decision = 'yes', no override
      - "no" / "skip" / "cancel"           → decision = 'no'
      - "agree with Tokyo" / "go with Sara" / just "Tokyo"
                                            → decision = 'yes' AND picked_persona_id
                                              is set, so the agent honours that
                                              persona's stance instead of its own
                                              majority verdict
      - anything else                       → decision = 'unsure' (Haiku fallback)
    Negation guards: "not Tokyo", "no Sara" → 'no' (negation wins, no pick).
    """
    import re as _re

    t = (transcript or "").lower().strip()
    if not t:
        return {"decision": "unsure", "picked_persona_id": None, "picked_name": None, "confidence": 0.0}

    # 1. Explicit negation FIRST so "not Tokyo" doesn't get mis-classified.
    no_kw = [
        "no", "nope", "nah", "cancel", "stop", "wait", "don't", "do not",
        "never mind", "nevermind", "abort", "scrap that", "skip",
        "not now", "rejected", "deny", "denied", "not really", "no thanks",
    ]
    for kw in no_kw:
        # Multi-word keywords need plain substring; single words use word boundary.
        if " " in kw:
            if kw in t:
                return {"decision": "no", "picked_persona_id": None, "picked_name": None, "confidence": 0.9}
        elif _re.search(rf"\b{_re.escape(kw)}\b", t):
            return {"decision": "no", "picked_persona_id": None, "picked_name": None, "confidence": 0.9}

    # 2. Persona-name detection. The user side-stepped yes/no by naming a persona;
    # treat that as 'yes' WITH an override pointing at the named persona.
    personas = personas or []
    for p in personas:
        # Persona name is "🌹 Sara Anniversary · MM" — strip emoji prefix and
        # the demo-tag suffix so we match "Sara Anniversary" and "Sara".
        raw = str(p.get("name", ""))
        plain = _re.sub(r"^\S+\s+", "", raw).replace(" · MM", "").strip().lower()
        first_word = plain.split()[0] if plain else ""
        if plain and _re.search(rf"\b{_re.escape(plain)}\b", t):
            return {
                "decision":          "yes",
                "picked_persona_id": int(p["account_id"]),
                "picked_name":       raw,
                "confidence":        0.92,
            }
        if first_word and len(first_word) >= 3 and _re.search(rf"\b{_re.escape(first_word)}\b", t):
            return {
                "decision":          "yes",
                "picked_persona_id": int(p["account_id"]),
                "picked_name":       raw,
                "confidence":        0.85,
            }

    # 3. Plain affirmative keywords.
    yes_kw = [
        "yes", "yeah", "yep", "yup", "do it", "go ahead", "execute", "approve",
        "approved", "confirm", "confirmed", "send it", "let's do it", "fine",
        "okay", "ok", "sure", "alright", "go for it", "proceed",
    ]
    for kw in yes_kw:
        if " " in kw:
            if kw in t:
                return {"decision": "yes", "picked_persona_id": None, "picked_name": None, "confidence": 0.85}
        elif _re.search(rf"\b{_re.escape(kw)}\b", t):
            return {"decision": "yes", "picked_persona_id": None, "picked_name": None, "confidence": 0.85}

    # 4. Haiku fallback for ambiguous phrasing.
    try:
        client = anthropic.Anthropic()
        model = os.getenv("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001").strip() or "claude-haiku-4-5-20251001"
        resp = client.messages.create(
            model=model,
            max_tokens=4,
            messages=[{
                "role": "user",
                "content": (
                    "The user is being asked to confirm a money operation. Reply with exactly "
                    "one word: yes, no, or unsure.\n\n"
                    f"User said: {transcript}"
                ),
            }],
        )
        text = ""
        for b in resp.content:
            if getattr(b, "type", None) == "text":
                text += b.text
        word = text.strip().lower().split()[0] if text.strip() else "unsure"
        if word in ("yes", "no"):
            return {"decision": word, "picked_persona_id": None, "picked_name": None, "confidence": 0.7}
    except Exception:  # noqa: BLE001
        pass
    return {"decision": "unsure", "picked_persona_id": None, "picked_name": None, "confidence": 0.4}


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

    # Surface missing env vars BEFORE we hit anything that needs them.
    required = {
        "BUNQ_API_KEY":        "bunq sandbox auth",
        "ANTHROPIC_API_KEY":   "Claude (mission planner)",
        "ELEVENLABS_API_KEY":  "TTS + STT (audio in/out)",
        "ELEVENLABS_VOICE_ID": "TTS narrator voice",
    }
    missing = [(k, v) for k, v in required.items() if not os.getenv(k, "").strip()]
    if missing:
        print("[server] WARNING — missing env vars:", flush=True)
        for k, why in missing:
            print(f"           {k:22s} ({why})", flush=True)
        print("           Copy .env.example → .env and fill these in.", flush=True)

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
    """Read-only snapshot of the Council. Returns whatever sub-accounts exist
    right now — does NOT auto-create. The Genesis flow is responsible for
    populating an empty room.
    """
    try:
        tb = _get_toolbox()
        personas = tb.personas.list_cached()
        return {"ok": True, "personas": personas, "count": len(personas)}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}


@app.post("/genesis/start")
async def genesis_start(request: Request) -> dict[str, Any]:
    """Kick off the visible Council bring-up: seed primary, create each
    persona one-by-one, fund with a randomised amount, emit per-step events
    so the dashboard can animate tiles materialising. Idempotent — already-
    existing demo personas are reported as `skipped` instead of recreated.
    """
    global _genesis_thread
    if _genesis_thread is not None and _genesis_thread.is_alive():
        return {"ok": False, "error": "Genesis already running."}
    if _mission_thread is not None and _mission_thread.is_alive():
        return {"ok": False, "error": "A mission is already running — finish or reset before genesis."}

    body: dict[str, Any] = {}
    try:
        body = await request.json()
    except Exception:
        body = {}
    seed_eur = float(body.get("seed_primary_eur", 600.0))

    bus.reset()

    def _run() -> None:
        tb = _get_toolbox()
        try:
            tb.personas.run_genesis(seed_primary_eur=seed_eur)
        except Exception as e:  # noqa: BLE001
            bus.publish("genesis_error", {"error": str(e)})

    _genesis_thread = threading.Thread(target=_run, daemon=True, name="mission-mode-genesis")
    _genesis_thread.start()
    return {"ok": True, "seed_primary_eur": seed_eur}


@app.post("/missions/council/confirm")
async def council_confirm(audio: UploadFile = File(...)) -> dict[str, Any]:
    """Receive the user's spoken yes/no during a council confirmation prompt,
    classify it, fill the bridge slot, and unblock the mission thread waiting
    inside `request_confirmation`.

    The classifier also detects when the user names a specific persona (e.g.
    "agree with Tokyo"). In that case the slot carries `picked_persona_id` so
    the agent loop can honour the user's pick instead of its own majority verdict.
    """
    global _user_confirm_slot
    audio_bytes = await audio.read()
    if not audio_bytes:
        return {"ok": False, "error": "Empty audio upload"}
    try:
        transcript = transcribe_bytes(audio_bytes, filename=audio.filename or "confirm.webm")
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"STT failed: {e}"}

    # Pull the current persona registry so the classifier can match names.
    personas: list[dict[str, Any]] = []
    try:
        personas = _get_toolbox().personas.list_cached()
    except Exception:  # noqa: BLE001
        pass

    classification = _classify_council_decision(transcript, personas=personas)
    _user_confirm_slot = {
        "transcript":        transcript,
        "decision":          classification["decision"],
        "picked_persona_id": classification["picked_persona_id"],
        "picked_name":       classification["picked_name"],
        "confidence":        classification["confidence"],
    }
    bus.publish("user_confirmation_received", {
        "transcript":        transcript,
        "decision":          classification["decision"],
        "picked_persona_id": classification["picked_persona_id"],
        "picked_name":       classification["picked_name"],
        "confidence":        classification["confidence"],
    })
    _user_confirm_event.set()
    return {"ok": True, **_user_confirm_slot}


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
    """Readiness probe — also surfaces missing env vars so cloners know what
    to set without grepping the source."""
    required = ["BUNQ_API_KEY", "ANTHROPIC_API_KEY", "ELEVENLABS_API_KEY", "ELEVENLABS_VOICE_ID"]
    missing = [k for k in required if not os.getenv(k, "").strip()]
    return {
        "ok": not missing,
        "user_id": _bunq_client.user_id if _bunq_client else None,
        "primary_id": _toolbox.primary_id if _toolbox else None,
        "public_url": _public_url,
        "mission_running": bool(_mission_thread and _mission_thread.is_alive()),
        "env_missing": missing,
        "audio_ready": "ELEVENLABS_API_KEY" not in missing and "ELEVENLABS_VOICE_ID" not in missing,
    }


@app.get("/state")
async def state() -> dict[str, Any]:
    return {
        "history": bus._history,
        "mission_running": bool(_mission_thread and _mission_thread.is_alive()),
    }


# ----------------------------------------------------------------------
# Trip mission — interactive chat (multi-turn)
# ----------------------------------------------------------------------

@app.post("/chat")
async def chat(request: Request) -> dict[str, Any]:
    """Interactive chat for the Trip mission.

    Body: {session_id: str, message: str}. Streams events via the existing /events SSE.
    """
    body: dict[str, Any] = {}
    try:
        body = await request.json()
    except Exception:
        body = {}

    session_id = str(body.get("session_id", "default"))
    message = str(body.get("message", "")).strip()
    if not message:
        return {"ok": False, "error": "message is required"}

    session = get_session(session_id)
    tb = _get_toolbox()
    try:
        await run_trip_turn(tb, session, message)
    except Exception as e:  # noqa: BLE001
        bus.publish("mission_error", {"error": str(e)})
        return {"ok": False, "error": str(e)}
    return {"ok": True, "phase": session.phase}


@app.post("/chat/reset")
async def chat_reset(request: Request) -> dict[str, Any]:
    body: dict[str, Any] = {}
    try:
        body = await request.json()
    except Exception:
        body = {}
    session_id = str(body.get("session_id", "default"))
    reset_session(session_id)
    bus.reset()
    return {"ok": True}


# ----------------------------------------------------------------------
# TripLens mock search page (Trip mission's visible research beat)
# ----------------------------------------------------------------------

_TRIPLENS_HTML: str | None = None


def _triplens_html() -> str:
    global _TRIPLENS_HTML
    if _TRIPLENS_HTML is None:
        _TRIPLENS_HTML = (MOCK_SITES_DIR / "search" / "index.html").read_text()
    return _TRIPLENS_HTML


@app.get("/mock-search/")
@app.get("/mock-search")
async def mock_search_index() -> Any:
    """Serve the TripLens HTML page. Query params (`q`, `data`) are read by JS."""
    from fastapi.responses import HTMLResponse
    return HTMLResponse(_triplens_html())


# ----------------------------------------------------------------------
# Trip-mission debug endpoints (rehearsal + smoke testing)
# ----------------------------------------------------------------------

@app.post("/debug/generate-image")
async def debug_generate_image(request: Request) -> dict[str, Any]:
    """Smoke-test the OpenRouter Seedream pipeline.

    Body: either {prompt: "..."} or a PackageOption-shaped dict.
    """
    from . import image_gen
    body: dict[str, Any] = {}
    try:
        body = await request.json()
    except Exception:
        body = {}
    if "prompt" in body:
        url = await image_gen.generate_image(body["prompt"])
    else:
        url = await image_gen.generate_for_option(body)
    if not url:
        return {"ok": False, "error": "image generation failed; see server log"}
    return {"ok": True, "image_url": url, "length": len(url)}


@app.post("/debug/search-trip-options")
async def debug_search_trip_options(request: Request) -> dict[str, Any]:
    """Fire the live TripLens search without the LLM. Frames + links go on /events."""
    body: dict[str, Any] = {}
    try:
        body = await request.json()
    except Exception:
        body = {}
    query = str(body.get("query") or "boutique hotel Amsterdam canal")
    from .browser_agent import search_trip_options
    out = await search_trip_options(query=query, max_results=int(body.get("max_results", 6)))
    return {"ok": True, **out}


@app.post("/debug/present-options")
async def debug_present_options(request: Request) -> dict[str, Any]:
    """Publish a fake `options` event + kick off image gen for each option.

    Lets the team rehearse the cards UI without burning Claude tokens.
    """
    body: dict[str, Any] = {}
    try:
        body = await request.json()
    except Exception:
        body = {}
    options = body.get("options") or [
        {"id": "opt-a", "hotel": "Hotel V Fizeaustraat", "restaurant": "De Kas",
         "extra": "Canal sunset cruise", "total_eur": 445,
         "notes": "Calm, southeast Amsterdam, great for couples",
         "sources": [{"label": "tripadvisor.com", "url": "https://www.tripadvisor.com"}]},
        {"id": "opt-b", "hotel": "Casa Cook Amsterdam", "restaurant": "La Perla",
         "extra": "Vondelpark picnic", "total_eur": 480,
         "notes": "Bohemian, central, plant-filled lobby",
         "sources": [{"label": "casacook.com", "url": "https://casacook.com"}]},
        {"id": "opt-c", "hotel": "The Hoxton Lloyd", "restaurant": "Restaurant Floris",
         "extra": "Anne Frank House visit", "total_eur": 510,
         "notes": "Boutique, lively, walking distance to canals",
         "sources": [{"label": "thehoxton.com", "url": "https://thehoxton.com"}]},
    ]
    intro = body.get("intro_text") or "Three weekend picks for Amsterdam:"
    bus.publish("options", {"intro": intro, "options": options})
    from .agent_loop import _trip_generate_option_image
    for opt in options:
        asyncio.create_task(_trip_generate_option_image(opt))
    return {"ok": True, "options_count": len(options)}


# ----------------------------------------------------------------------
# Entry point
# ----------------------------------------------------------------------

def main() -> None:
    # AWS App Runner / Cloud Run / Heroku set $PORT; fall back to 8000 locally.
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("orchestrator.server:app", host="0.0.0.0", port=port, reload=False)


if __name__ == "__main__":
    main()
