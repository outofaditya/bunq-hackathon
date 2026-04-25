<div align="center">

# Trip Agent — for **bunq**

### One sentence in. Real money out.

A multimodal AI agent that lives inside bunq, plans your weekend, watches itself
book it on a live browser, and moves the money — across **seven real bunq
sandbox actions** — in under three minutes.

![bunq](https://img.shields.io/badge/bunq-FF7819?style=for-the-badge&labelColor=000000&color=FF7819)
![Hackathon](https://img.shields.io/badge/bunq%20Hackathon%207.0-Multimodal%20AI-FF7819?style=for-the-badge&labelColor=000000)
![Claude](https://img.shields.io/badge/Claude-Haiku%204.5-FF7819?style=for-the-badge&labelColor=000000)
![Python](https://img.shields.io/badge/python-3.11+-FF7819?style=for-the-badge&labelColor=000000)
![React](https://img.shields.io/badge/react-19-FF7819?style=for-the-badge&labelColor=000000)

</div>

---

## What it is

Trip Agent is a **conversational, multimodal AI** that sits inside bunq. You
type or speak a trip idea — *"romantic weekend in Amsterdam, around €600"* —
and the agent:

1. **Asks one focused clarifier**, not an interrogation
2. **Runs real web searches**, visibly, on a stylized search engine in the dashboard's browser panel
3. **Proposes three concrete packages** with real hotels, real restaurants, real prices, **cited sources**, and AI-generated cartoon illustrations
4. **Asks for confirmation** — no money moves until you say yes
5. **Fires seven bunq sandbox actions** in a row, narrated aloud, while a real bunq draft-payment push lands on your phone

> **`AI is the core`** · The whole flow is driven by a phased Claude Haiku 4.5 tool-use loop. Without the model, nothing happens.
> **`Three modalities`** · Voice in/out (ElevenLabs), live video of the agent's browser (Playwright JPEG stream), and AI-generated images (OpenRouter Seedream 4.5).
> **`Real banking`** · Every payment, sub-account, draft, schedule, and request hits the real bunq sandbox API.

---

## The 3-minute demo flow

| Beat | What happens |
|---|---|
| **Sign in** | Mock bunq OAuth landing → dashboard loads with `Primary €17,457` ticker |
| **Understand** | User describes the trip in chat (or via mic). Agent asks one clarifier. |
| **Research** | `search_trip_options` fires 2–3× — TripLens search engine animates live in the right panel; Research feed populates with real Tripadvisor / Condé Nast / Wanderlog links |
| **Present** | Three option cards render in chat with hotel + restaurant + activity + total + cartoon illustration + sources strip |
| **Confirm** | User picks one → `request_confirmation` summarizes the seven actions → user types `yes` |
| **Execute** | Cascade fires across ~90 seconds: sub-account → fund → live hotel-booking on StayHub → pay vendor → draft payment → schedule recurring → split request → Slack DM |
| **Signature** | A **real bunq sandbox push** lands on the user's phone. Tap approve. Webhook fires back. Tile flashes green. |
| **Wrap** | Agent's closing line is freshly Claude-generated each run; balance counter ticks down; rainbow strip glows along the bottom. |

---

## Architecture

```
┌─ chat (text or voice) ─────────────────────────────────┐
│                                                         │
│   user ──► /chat ──► AsyncAnthropic (streaming) ──► SSE bus
│                              │
│                              ▼ (phased tool-use loop)
│   UNDERSTANDING → AWAITING_CONFIRMATION → EXECUTING → DONE
│                              │
│             ┌────────────────┼────────────────┐
│             ▼                ▼                ▼
│        bunq sandbox    Playwright +       ElevenLabs
│        (7 real tools)  cursor overlay     (TTS + STT)
│             │           (live JPEG       
│             │            stream)          OpenRouter
│             ▼                              Seedream 4.5
│        webhooks ──► SSE ──► dashboard      (cartoon images)
│                                                         │
└─────────────────────────────────────────────────────────┘
```

### Phased tool catalog

| Phase | Tools the model can see |
|---|---|
| `UNDERSTANDING` | `search_trip_options`, `web_search`, `present_options`, `request_confirmation` |
| `AWAITING_CONFIRMATION` | `request_confirmation` |
| `EXECUTING` | `create_sub_account`, `fund_sub_account`, `book_hotel`, `pay_vendor`, `create_draft_payment`, `schedule_recurring`, `request_from_partner`, `send_slack`, `narrate` |
| `DONE` | *(none — the model writes a wrap line and stops)* |

The model literally **cannot** call payment tools during `UNDERSTANDING` because they're not in the tool list passed for that phase. Safer than prompt-policing.

---

## How it's built

| Layer | Stack |
|---|---|
| **Agent** | Anthropic Claude **Haiku 4.5** with `AsyncAnthropic` streaming + phased tool catalog + prompt caching |
| **Banking** | Real **bunq sandbox API** v1 — `monetary-account-savings`, `payment`, `draft-payment`, `schedule-payment`, `request-inquiry`, `notification-filter-url` |
| **Voice** | ElevenLabs — Scribe (STT) + streaming Turbo v2.5 (TTS), played through a sequential AudioQueue so clips never overlap |
| **Browser** | Playwright headless Chromium, JPEG frame streaming at 900×560 / q=65, animated agent-cursor overlay in bunq orange |
| **Images** | OpenRouter `bytedance-seed/seedream-4.5` — three illustrations generated in parallel via `asyncio.create_task`, fade in via `option_image` SSE events |
| **Server** | FastAPI + `sse-starlette` + custom event bus with bounded history replay |
| **Dashboard** | Vite + React 19 + TypeScript, brand-aligned to the bunq press-kit |
| **Realtime** | Server-sent events with auto-reconnect indicator (`Wifi/WifiOff`) and live token streaming |

---

## Setup

```bash
git clone https://github.com/outofaditya/bunq-hackathon.git
cd bunq-hackathon/mission-mode

python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium

cd dashboard && npm install && npm run build && cd ..

cp .env.example .env
# Required: BUNQ_API_KEY, ANTHROPIC_API_KEY, ELEVENLABS_API_KEY, ELEVENLABS_VOICE_ID
# Optional: OPENROUTER_API_KEY (cartoon illustrations), SLACK_WEBHOOK_URL,
#           BRAVE_SEARCH_API_KEY (search reliability upgrade)
```

### Run

```bash
# Terminal A — public tunnel for bunq webhooks
ngrok http 8000

# Terminal B — server (auto-discovers ngrok via 127.0.0.1:4040 if PUBLIC_BASE_URL unset)
.venv/bin/uvicorn orchestrator.server:app --port 8000
```

Open `http://localhost:8000/`. The header shows your live primary balance and SSE connection state. Type a trip in the chat, or tap the mic and speak.

### Health check

```bash
curl http://localhost:8000/health
```

```json
{
  "ok": true,
  "user_id": 3628754,
  "primary_id": 3620547,
  "public_url": "https://...ngrok-free.dev",
  "webhooks_registered": true,
  "env_missing": [],
  "audio_ready": true
}
```

---

## Brand notes

This UI ships brand-correct to the bunq press kit:

- **Color** — primary orange `#FF7819`, deep black `#000000`, fresh white `#FFFFFF`. Status colors mapped to the brand palette: green `#34CC8D` (ok), yellow `#FAC800` (pending), red `#E63223` (error).
- **Type** — **Montserrat**, ExtraBold + Medium pairings.
- **Wordmark** — lowercase `bunq`. Always.
- **Tagline** — *bank of The Free* (lowercase `b` and `T`).
- **Rainbow** — used as a standalone device only — a 6 px full-bleed strip across the bottom of the viewport. Never recolored or recombined.
- **Highlight blocks** — section labels render as white-on-orange blocks, the same device the press-kit uses for headlines.

---

## What's behind each tool

<details>
<summary><strong><code>search_trip_options</code></strong> — visible web search beat</summary>

httpx fetches real DuckDuckGo HTML results (with **Ecosia fallback** when DDG rate-limits with HTTP 202). Playwright renders a stylized "TripLens" mock search page with the query typed character-by-character and the real results scrolling in. JPEG frames stream into the dashboard's Agent Browser panel; the Research feed below it populates with clickable links grouped by query.
</details>

<details>
<summary><strong><code>book_hotel</code></strong> — live booking with animated cursor</summary>

Playwright drives a 4-stage StayHub mock (review → details → payment → confirmation). An injected agent-cursor overlay (orange dot + pulsing ring) animates toward each click target before pressing it, so screenshots show *where* the agent is acting. Returns a real booking reference.
</details>

<details>
<summary><strong><code>create_draft_payment</code></strong> — real bunq push on a real phone</summary>

POSTs `draft-payment` with the right `entries[]` shape. The bunq sandbox app pushes a real notification. The user taps approve. bunq fires a webhook back at our `/bunq-webhook`; the dashboard tile flashes green.
</details>

<details>
<summary><strong><code>narrate</code></strong> — closing line generation</summary>

When the cascade reaches `DONE`, the agent calls a small Haiku invocation referencing the last few narrations to write a fresh, conversational wrap-up sentence. Stops the demo from sounding canned across rehearsals.
</details>

<details>
<summary><strong>SSE event bus</strong> — late-mounted clients get state replay</summary>

Allow-listed events (`balance_snapshot`, `phase`, `balance`) buffer in a bounded deque and replay to new subscribers on connect — so a freshly-loaded dashboard sees the current balance and phase, but **chat history is live-only** so reload starts fresh.
</details>

---

## Engineering surprises

We learned three things the hard way:

1. **The bunq sandbox sugardaddy is capped at €500 per request.** Top-up requests for larger amounts sit pending forever. We wrote a chunked `ensure_primary_balance(min_eur, target_eur)` that loops €500 calls until the target is hit.
2. **DuckDuckGo HTML rate-limits aggressively** in 2026 — every major LLM agent framework has open tickets for the exact `202 Ratelimit` we hit. Fixed with an Ecosia fallback chain (different rate-limit pool) plus capturing Anthropic's server-side `web_search` results into the same Research feed.
3. **The sync `Anthropic` SDK blocks the asyncio event loop** during streaming, so SSE token deltas were buffering until the full reply finished. Switched to `AsyncAnthropic` with `async for event in stream:` so the loop interleaves the upstream read with the downstream SSE writer. Genuine token-by-token streaming now arrives in the browser.

---

## Repo layout

```
mission-mode/
├── orchestrator/                # FastAPI server + agent loop + bunq tools
│   ├── server.py                # /chat, /events, /health, /state, /tts, /signin, /debug/*
│   ├── agent_loop.py            # AsyncAnthropic streaming + tool dispatch
│   ├── system_prompt.py         # SYSTEM_PROMPT + tool catalog + tools_for_phase
│   ├── bunq_tools.py            # 7 sandbox wrappers + ensure_primary_balance + snapshot_primary_balance
│   ├── browser_agent.py         # Playwright TripLens search + StayHub booking + cursor overlay
│   ├── events.py                # SSE event bus with bounded history replay
│   ├── image_gen.py             # OpenRouter Seedream 4.5 cartoon illustrations
│   ├── voice.py                 # ElevenLabs STT + streaming TTS proxies
│   ├── webhooks.py              # bunq webhook → typed SSE events
│   ├── sessions.py              # in-memory session store
│   └── phases.py                # UNDERSTANDING / AWAITING_CONFIRMATION / EXECUTING / DONE
├── dashboard/                   # Vite + React 19 + TS
│   └── src/
│       ├── App.tsx              # SSE subscription, brand header, balance pill, FX hooks
│       ├── Chat.tsx             # streaming bubbles, options rack, confirmation gate, mic
│       ├── Dashboard.tsx        # tile strip, balance card, browser panel, research feed
│       ├── markdown.tsx         # streaming-safe markdown (heals partial **bold**, [links]() etc.)
│       ├── audio-fx.ts          # synth ticks/done/buzz/chime + sequential AudioQueue
│       ├── AnimatedNumber.tsx   # cubic-eased rAF tween
│       └── styles.css           # bunq-brand redesign (Montserrat, orange, rainbow strip)
├── mock_sites/
│   ├── signin.html              # mock bunq OAuth
│   ├── search/index.html        # TripLens
│   └── vendor/index.html        # StayHub
├── tests/                       # pytest smoke tests against the real bunq sandbox
├── CLAUDE.md                    # full project reference
└── OVERVIEW.md                  # high-level architecture doc
```

---

## License & credits

Built for **bunq Hackathon 7.0 — Multimodal AI** (April 2026).

- bunq sandbox API · the bank that actually moves money for the demo
- Anthropic Claude · the agent's brain
- ElevenLabs · voice in and out
- OpenRouter + ByteDance Seedream · cartoon illustrations
- Playwright · the visible browser beat
- The bunq press-kit · for the visual language

> *"Built for the bank of The Free."*

<div align="center">

[![bunq](https://img.shields.io/badge/-bunq-FF7819?style=flat-square&labelColor=000000)](https://www.bunq.com/)
[![GitHub](https://img.shields.io/badge/-github-FF7819?style=flat-square&labelColor=000000)](https://github.com/outofaditya/bunq-hackathon)

</div>
