"""FastAPI orchestrator server — Trip Agent.

Boot:
  python -m orchestrator.server     (or `./start.sh`)

Endpoints:
  GET  /                          → React dashboard (static)
  GET  /events                    → SSE stream of bus events
  POST /chat                      → multi-turn Trip mission turn
  POST /chat/reset                → drop session + clear bus history
  POST /bunq-webhook              → bunq's real-time mutation/payment notifications
  GET  /health                    → liveness probe
  GET  /state                     → bus history snapshot

  POST /stt                       → ElevenLabs Scribe transcription
  GET  /tts/{filename}            → cached narration audio

  GET  /mock-hotel/               → StayHub mock booking page (driven by browser-vision)
  GET  /mock-search/              → TripLens mock search page (animated by Playwright)

  POST /debug/generate-image      → smoke-test Seedream image gen
  POST /debug/search-trip-options → fire the TripLens search beat without LLM
  POST /debug/present-options     → publish a synthetic option set + image gen
  POST /debug/book-hotel          → drive the hotel booking flow standalone

ngrok:
  If the local ngrok dashboard is reachable at http://127.0.0.1:4040, the server
  auto-discovers the public HTTPS URL and registers it with bunq as the webhook
  callback target. Otherwise webhooks fall back to polling.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

import httpx
import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, File, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from sse_starlette.sse import EventSourceResponse

from bunq_client import BunqClient

from .agent_loop import _trip_generate_option_image, run_trip_turn
from .bunq_tools import BunqToolbox
from .events import bus
from .sessions import get_or_create as get_session, reset as reset_session
from .stt import transcribe_bytes


load_dotenv(override=True)


PROJECT_ROOT = Path(__file__).resolve().parent.parent
REACT_DIST = PROJECT_ROOT / "dashboard-react" / "dist"
DASHBOARD_HTML = REACT_DIST / "index.html"
ASSETS_DIR = PROJECT_ROOT / "assets"
TTS_CACHE_DIR = ASSETS_DIR / "tts_cache"
MOCK_SITES_DIR = PROJECT_ROOT / "mock_sites"


# ----------------------------------------------------------------------
# Singletons populated at startup
# ----------------------------------------------------------------------

_bunq_client: BunqClient | None = None
_toolbox: BunqToolbox | None = None
_public_url: str | None = None


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

app = FastAPI(title="Trip Agent")


@app.on_event("startup")
async def _startup() -> None:
    global _public_url

    tb = _get_toolbox()
    print(f"[server] authenticated as user {tb.client.user_id}, primary={tb.primary_id} ({tb.primary_iban})")

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
            "[server] no PUBLIC_BASE_URL and no local ngrok detected — webhook disabled. "
            "Run `ngrok http 8000` in another terminal to enable webhooks."
        )

    print("[server] ready at http://localhost:8000/")


# ----- Static dashboard ------------------------------------------------

@app.get("/")
async def root() -> FileResponse:
    return FileResponse(str(DASHBOARD_HTML))


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


# ----- SSE event stream ------------------------------------------------

@app.get("/events")
async def events(request: Request) -> EventSourceResponse:
    async def stream():
        async for msg in bus.subscribe():
            if await request.is_disconnected():
                break
            yield {"data": msg}
    return EventSourceResponse(stream())


# ----- Trip mission — interactive chat --------------------------------

@app.post("/chat")
async def chat(request: Request) -> dict[str, Any]:
    """One Trip mission turn. Body: {session_id: str, message: str}.
    Streams events via /events SSE."""
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


# ----- ElevenLabs Scribe STT (used for optional voice input) -----------

@app.post("/stt")
async def stt(audio: UploadFile = File(...)) -> JSONResponse:
    """Transcribe a recorded audio blob via ElevenLabs Scribe."""
    data = await audio.read()
    if not data:
        return JSONResponse({"ok": False, "error": "empty audio"}, status_code=400)
    try:
        transcript = transcribe_bytes(data, filename=audio.filename or "recording.webm")
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)
    return JSONResponse({"ok": True, "transcript": transcript})


@app.get("/tts/{filename}")
async def serve_tts(filename: str) -> FileResponse:
    p = TTS_CACHE_DIR / filename
    if not p.exists() or ".." in filename or "/" in filename:
        return FileResponse(str(TTS_CACHE_DIR / "missing"))  # 404-ish
    return FileResponse(str(p), media_type="audio/mpeg")


# ----- Mock booking site (StayHub) — driven by browser-vision ----------

_HOTEL_HTML_TEMPLATE: str | None = None


def _hotel_html() -> str:
    global _HOTEL_HTML_TEMPLATE
    if _HOTEL_HTML_TEMPLATE is None:
        _HOTEL_HTML_TEMPLATE = (MOCK_SITES_DIR / "hotel" / "index.html").read_text()
    return _HOTEL_HTML_TEMPLATE


# Tiny built-in fixture — replaces the dropped `places.search_hotels` call.
def _hotel_fixture(city: str) -> list[dict[str, Any]]:
    base = [
        {"name": "Casa Cook " + city,         "area": "Centre",      "price": 180, "rating": 4.5, "url": "https://example.com/casa-cook"},
        {"name": "The Hoxton " + city,        "area": "Lloyd",       "price": 210, "rating": 4.6, "url": "https://example.com/hoxton"},
        {"name": "Hilton " + city,            "area": "Riverside",   "price": 240, "rating": 4.4, "url": "https://example.com/hilton"},
        {"name": "Boutique Stay " + city,     "area": "Old Town",    "price": 155, "rating": 4.3, "url": "https://example.com/boutique"},
    ]
    return base


def _inject_hotel_data(city: str, nights: int) -> str:
    import json as _json

    html = _hotel_html()
    return (
        html
        .replace("__HOTELS__", _json.dumps(_hotel_fixture(city), ensure_ascii=False))
        .replace("__CITY__", city)
        .replace("__NIGHTS__", str(int(nights)))
    )


@app.get("/mock-hotel/")
@app.get("/mock-hotel")
async def mock_hotel_index(city: str = "Amsterdam", nights: int = 2) -> Any:
    return HTMLResponse(_inject_hotel_data(city=city, nights=nights))


# ----- TripLens mock search page ---------------------------------------

_TRIPLENS_HTML: str | None = None


def _triplens_html() -> str:
    global _TRIPLENS_HTML
    if _TRIPLENS_HTML is None:
        _TRIPLENS_HTML = (MOCK_SITES_DIR / "search" / "index.html").read_text()
    return _TRIPLENS_HTML


@app.get("/mock-search/")
@app.get("/mock-search")
async def mock_search_index() -> Any:
    """The Trip mission's TripLens page. Query params (`q`, `data`) are read by JS."""
    return HTMLResponse(_triplens_html())


# ----- bunq webhook receiver -------------------------------------------

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
    }


@app.get("/state")
async def state() -> dict[str, Any]:
    return {"history": bus._history}


# ----- Debug / rehearsal endpoints ------------------------------------

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
    """Fire the live TripLens search without the LLM."""
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
    Lets the team rehearse the cards UI without burning Claude tokens."""
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
    for opt in options:
        asyncio.create_task(_trip_generate_option_image(opt))
    return {"ok": True, "options_count": len(options)}


@app.post("/debug/book-hotel")
async def debug_book_hotel(request: Request) -> dict[str, Any]:
    """Drive the hotel booking flow standalone — useful for rehearsals."""
    body: dict[str, Any] = {}
    try:
        body = await request.json()
    except Exception:
        body = {}
    from .agent_loop import _dispatch_book_hotel
    return await asyncio.to_thread(_dispatch_book_hotel, body or {"city": "Amsterdam", "nights": 2, "max_budget_eur": 600})


# ----------------------------------------------------------------------
# Entry point
# ----------------------------------------------------------------------

def main() -> None:
    uvicorn.run("orchestrator.server:app", host="0.0.0.0", port=8000, reload=False)


if __name__ == "__main__":
    main()
