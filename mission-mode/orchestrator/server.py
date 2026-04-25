"""FastAPI entrypoint for the Trip Agent.

Run with: uvicorn orchestrator.server:app --reload --port 8000
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Request, UploadFile, File
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from sse_starlette.sse import EventSourceResponse

from . import agent_loop, browser_agent, bunq_tools, voice, webhooks
from .events import bus
from .sessions import get_or_create

load_dotenv()

ROOT = Path(__file__).resolve().parent.parent
MOCK_SITES = ROOT / "mock_sites"
DASHBOARD_DIST = ROOT / "dashboard" / "dist"

app = FastAPI(title="Trip Agent for bunq")


@app.get("/")
async def root() -> FileResponse:
    index = DASHBOARD_DIST / "index.html"
    if index.exists():
        return FileResponse(index)
    return FileResponse(MOCK_SITES / "placeholder.html")


@app.get("/signin")
async def signin() -> FileResponse:
    return FileResponse(MOCK_SITES / "signin.html")


@app.get("/events")
async def events(request: Request) -> EventSourceResponse:
    q = bus.subscribe()

    async def stream():
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    msg = await asyncio.wait_for(q.get(), timeout=15.0)
                    yield {"data": msg}
                except asyncio.TimeoutError:
                    yield {"event": "ping", "data": ""}
        finally:
            bus.unsubscribe(q)

    return EventSourceResponse(stream())


@app.post("/chat")
async def chat(payload: dict) -> JSONResponse:
    """Kick off one agent turn; events stream via /events SSE."""
    session = get_or_create(payload.get("session_id"))
    user_msg = payload.get("message", "")
    # Run the turn in the background — return immediately so the client reconnects to SSE
    asyncio.create_task(agent_loop.run_turn(session, user_msg))
    return JSONResponse({"session_id": session.session_id, "phase": session.phase.value})


@app.post("/bunq-webhook")
async def bunq_webhook(payload: dict) -> JSONResponse:
    """Bunq calls this when PAYMENT / MUTATION / DRAFT_PAYMENT / etc. events fire."""
    await webhooks.handle(payload)
    return JSONResponse({"ok": True})


@app.post("/simulate-approve")
async def simulate_approve(payload: dict) -> JSONResponse:
    """Stand-in for tapping 'approve' on the bunq sandbox app.

    Finds the most recent pending draft in the session and PUTs status=ACCEPTED,
    which triggers the real bunq webhook chain. Same visual effect as a real tap.
    """
    session = get_or_create(payload.get("session_id"))
    if not session.pending_draft_ids or not session.sub_account_id:
        return JSONResponse({"ok": False, "error": "no pending draft"}, status_code=400)
    draft_id = session.pending_draft_ids[-1]
    try:
        out = await asyncio.to_thread(bunq_tools.accept_draft_payment, draft_id, session.sub_account_id)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)
    # Emit an immediate tile flash; the webhook will confirm asynchronously
    await bus.publish("draft_payment_event", draft_id=draft_id, status="ACCEPTED")
    return JSONResponse({"ok": True, "draft_id": draft_id, "response": out})


@app.post("/debug/search-trip-options")
async def debug_search(payload: dict) -> JSONResponse:
    """Fire the live Playwright DuckDuckGo search directly (bypasses the LLM).

    Handy for rehearsing the browser panel + research feed. Frames + links go out
    on the /events SSE stream exactly like during a real chat turn.
    """
    query = payload.get("query", "boutique hotel Amsterdam canal weekend")
    try:
        out = await browser_agent.search_trip_options(query=query)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)
    return JSONResponse({"ok": True, **out})


@app.post("/debug/book-hotel")
async def debug_book_hotel(payload: dict) -> JSONResponse:
    """Fire the Playwright booking flow directly, bypassing the LLM.

    Useful for rehearsing the visible browser beat or demoing the dashboard panel
    without burning LLM tokens. Stays alongside /simulate-approve as a demo utility.
    """
    hotel = payload.get("hotel", "Hotel V Fizeaustraat")
    amount = float(payload.get("amount_eur", 445))
    try:
        out = await browser_agent.book_hotel(hotel=hotel, amount_eur=amount)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)
    return JSONResponse({"ok": True, **out})


@app.post("/debug/generate-image")
async def debug_generate_image(payload: dict) -> JSONResponse:
    """Smoke-test the OpenRouter Seedream image-gen pipeline.

    Either pass a full PackageOption-shaped object or a raw {prompt: "..."} body.
    Returns the data URL so callers can paste it in the browser to verify
    quality without going through the full agent loop.
    """
    from . import image_gen

    if "prompt" in payload:
        url = await image_gen.generate_image(payload["prompt"])
    else:
        url = await image_gen.generate_for_option(payload)
    if not url:
        return JSONResponse({"ok": False, "error": "image generation failed; see server log"}, status_code=502)
    return JSONResponse({"ok": True, "image_url": url, "length": len(url)})


@app.post("/debug/present-options")
async def debug_present_options(payload: dict | None = None) -> JSONResponse:
    """Publish a fake `options` SSE event + kick off image gen for each option.

    Mirrors what the present_options tool does in agent_loop, minus the phase
    flip. Lets the team rehearse the card grid + image fade-in pipeline
    without spending Claude tokens on a chat turn.
    """
    import asyncio
    from .agent_loop import _generate_and_publish_image

    payload = payload or {}
    options = payload.get("options") or [
        {
            "id": "opt-a",
            "hotel": "Hotel V Fizeaustraat",
            "restaurant": "De Kas",
            "extra": "Canal sunset cruise",
            "total_eur": 445,
            "notes": "Calm, southeast Amsterdam, great for couples",
            "sources": [{"label": "tripadvisor.com", "url": "https://www.tripadvisor.com"}],
        },
        {
            "id": "opt-b",
            "hotel": "Casa Cook Amsterdam",
            "restaurant": "La Perla",
            "extra": "Vondelpark picnic",
            "total_eur": 480,
            "notes": "Bohemian, central, plant-filled lobby",
            "sources": [{"label": "casacook.com", "url": "https://casacook.com"}],
        },
        {
            "id": "opt-c",
            "hotel": "The Hoxton Lloyd",
            "restaurant": "Restaurant Floris",
            "extra": "Anne Frank House visit",
            "total_eur": 510,
            "notes": "Boutique, lively, walking distance to canals",
            "sources": [{"label": "thehoxton.com", "url": "https://thehoxton.com"}],
        },
    ]
    intro = payload.get("intro_text") or "Three weekend picks for Amsterdam:"
    await bus.publish("options", intro=intro, options=options)
    for opt in options:
        asyncio.create_task(_generate_and_publish_image(opt))
    return JSONResponse({"ok": True, "options_count": len(options)})


@app.post("/stt")
async def stt(audio: UploadFile = File(...)) -> JSONResponse:
    """ElevenLabs Scribe proxy. Accepts an audio blob, returns {transcript}."""
    data = await audio.read()
    content_type = audio.content_type or "audio/webm"
    try:
        transcript = await voice.transcribe(data, content_type)
    except Exception as e:
        return JSONResponse({"transcript": "", "error": str(e)}, status_code=500)
    return JSONResponse({"transcript": transcript})


@app.get("/tts")
async def tts(text: str) -> StreamingResponse:
    """ElevenLabs streaming TTS proxy. Returns MP3 bytes."""
    return StreamingResponse(
        voice.stream_tts(text),
        media_type="audio/mpeg",
        headers={"Cache-Control": "no-store"},
    )


# Mock vendor site (Playwright clicks through this in EXECUTING phase)
app.mount("/mock-vendor", StaticFiles(directory=MOCK_SITES / "vendor", html=True), name="vendor")
# Mock search engine (Playwright renders this during UNDERSTANDING phase; data fetched via httpx+DDG)
app.mount("/mock-search", StaticFiles(directory=MOCK_SITES / "search", html=True), name="search")

# Built dashboard assets
if (DASHBOARD_DIST / "assets").exists():
    app.mount("/assets", StaticFiles(directory=DASHBOARD_DIST / "assets"), name="assets")


@app.on_event("startup")
async def startup() -> None:
    print(f"[startup] BUNQ_API_KEY present: {bool(os.getenv('BUNQ_API_KEY'))}")
    print(f"[startup] ANTHROPIC_API_KEY present: {bool(os.getenv('ANTHROPIC_API_KEY'))}")
    print(f"[startup] ELEVENLABS_API_KEY present: {bool(os.getenv('ELEVENLABS_API_KEY'))}")
    public_url = os.getenv("PUBLIC_BASE_URL", "")
    print(f"[startup] PUBLIC_BASE_URL: {public_url or '(unset — webhook registration skipped)'}")

    # Top up primary balance so the demo always has funds
    try:
        topup = await asyncio.to_thread(bunq_tools.ensure_primary_balance)
        print(f"[startup] balance check: {topup}")
    except Exception as e:
        print(f"[startup] ensure_primary_balance failed: {e}")

    # Register webhook subscriptions when tunnel is set
    if public_url:
        try:
            out = await asyncio.to_thread(bunq_tools.register_webhooks, public_url)
            print(f"[startup] webhooks registered: {out['registered']} → {out['target']}")
        except Exception as e:
            print(f"[startup] webhook registration failed: {e}")
