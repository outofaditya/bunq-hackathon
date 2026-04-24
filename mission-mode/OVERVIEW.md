# Trip Agent — Project Overview

A conversational trip-planning agent built for **bunq Hackathon 7.0** (3-min demo flow). Lives in `mission-mode/`. Pushed to `outofaditya/bunq-hackathon` on branch `trip-agent-mission-mode`.

---

## High-level architecture

```
Browser (React 19 dashboard)
   │  ├─ EventSource ──► GET /events ──── SSE EventBus (asyncio.Queue per client)
   │  └─ POST /chat ───► agent_loop.run_turn()
   │                        │
   │                        ├─ Anthropic messages.stream (Claude Haiku 4.5, prompt-cached)
   │                        │      ↑ tools filtered by Phase
   │                        │
   │                        └─ tool dispatch:
   │                             ├─ bunq_tools.*     → bunq sandbox REST (RSA-signed)
   │                             ├─ browser_agent.*  → Playwright (TripLens / StayHub mocks)
   │                             ├─ voice / TTS / STT → ElevenLabs proxies
   │                             └─ side_tools.send_slack
   │
bunq sandbox ──► POST /bunq-webhook ──► webhooks.handle() ──► EventBus ──► dashboard tiles
phone tap (or /simulate-approve) ──► PUT draft ACCEPTED ──► same webhook chain
```

Everything runs in a single process: **FastAPI + Uvicorn on :8000**, ngrok tunnel for inbound webhooks, dashboard built statically and served from `/`.

---

## The 3-minute demo flow

| Beat | Mechanism |
|---|---|
| Sign-in | Mock `/signin` page — visual only, any input redirects to `/` |
| Understanding | Phased Claude loop with tools `search_trip_options`, `web_search`, `present_options` only |
| Live search | `httpx` POSTs DDG's HTML endpoint → results passed base64 into TripLens mock page → Playwright renders + streams JPEG frames at 0.35s interval (~30 fps over 10s); `search_results` SSE feed populates clickable links sidebar |
| Present | `present_options` auto-flips phase → `AWAITING_CONFIRMATION`, renders 3 cards in chat |
| Confirm | Text match against "yes/go/confirm/…" flips phase → `EXECUTING` |
| Execute | 7 tool beats fire ~700ms apart (validation gates enforce dependency on `create_sub_account`) |
| Signature | `create_draft_payment` → real bunq push lands on phone → tap → bunq webhook → tile flashes green. Path-C: `/simulate-approve` button on the draft tile |
| Wrap | `DONE` detected once both `request_from_partner` AND `send_slack` have fired |

---

## Backend (`orchestrator/`)

**Routes (`server.py`)** — `/`, `/signin`, `/events` (SSE, 15s ping), `/chat`, `/bunq-webhook`, `/simulate-approve`, `/stt`, `/tts`, `/debug/search-trip-options`, `/debug/book-hotel`, `/mock-vendor/*`, `/mock-search/*`, `/assets/*`.

**Agent loop (`agent_loop.py`)** — model `claude-haiku-4-5-20251001`; system prompt has `cache_control: ephemeral`; streams text deltas to SSE; dispatch table at lines 50–166; phase gates throw `RuntimeError` if e.g. `pay_vendor` runs before `create_sub_account`; DONE detection at line 277.

**Tool catalog** (9 tools, `system_prompt.py`) — phase filter strips execution tools during UNDERSTANDING.

| Phase | Tool | Type |
|---|---|---|
| UNDERSTANDING | `search_trip_options` | local (httpx + Playwright) |
| UNDERSTANDING | `web_search` | Anthropic server-side fallback |
| UNDERSTANDING | `present_options` | local |
| ALL | `request_confirmation` | local |
| EXECUTING | `create_sub_account` | bunq |
| EXECUTING | `fund_sub_account` | bunq |
| EXECUTING | `book_hotel` | local (Playwright) |
| EXECUTING | `pay_vendor` | bunq |
| EXECUTING | `create_draft_payment` | bunq |
| EXECUTING | `schedule_recurring` | bunq |
| EXECUTING | `request_from_partner` | bunq |
| EXECUTING | `send_slack` | local |
| EXECUTING | `narrate` | local (SSE + client-side TTS) |

**bunq tools (`bunq_tools.py`)** — all 6 wrappers + `ensure_primary_balance` (chunked €500 sugardaddy top-ups, sandbox cap) + `register_webhooks` (5 categories: `PAYMENT`, `MUTATION`, `DRAFT_PAYMENT`, `SCHEDULE_RESULT`, `REQUEST`). Undocumented safety net: `create_sub_account` falls back to `monetary-account-bank` if `monetary-account-savings` fails.

**Browser agent (`browser_agent.py`)** — Playwright Chromium 900×560, full Chrome UA, JPEG quality 65, 0.35s frame interval. Two flows: TripLens search render + StayHub 4-stage booking click-through.

**Webhooks (`webhooks.py`)** — parses bunq `NotificationUrl` envelopes, emits typed SSE (`payment_event`, `draft_payment_event`, `schedule_event`, `request_event`); kwarg renamed to `bunq_event=` to avoid `EventBus.publish()` collision.

**Voice (`voice.py`)** — ElevenLabs Scribe STT (`scribe_v1`) + streaming TTS, both as httpx proxies.

**Auth (`bunq_client.py`)** — 3-step bunq install / device-server / session with RSA-PSS signing; context cached in `bunq_context.json` (`user_id=3628692` = A. Gould, current sandbox user).

---

## Phase state machine

```
UNDERSTANDING ──(options presented + user picks)──► AWAITING_CONFIRMATION
AWAITING_CONFIRMATION ──(user says "yes"/"go"/etc.)──► EXECUTING
EXECUTING ──(request_from_partner + send_slack both fired)──► DONE
```

- Phase is stored server-side per `Session`. The model **cannot** call execution tools during `UNDERSTANDING` because `tools_for_phase()` strips them from the tools array passed to `anthropic.messages.stream`.
- The confirmation gate is a text match: `_is_yes()` accepts "yes", "y", "go", "confirm", "ok", "yep", "proceed", "let's go", "approve", etc.
- `present_options` auto-flips UNDERSTANDING → AWAITING_CONFIRMATION.

---

## Frontend (`dashboard/`)

Vite + React 19 + TypeScript. Built statically, served from `/`.

**Layout** — 2-column grid:

- **Left (chat, `Chat.tsx`)** — streaming bubbles, tool chips, option-card rack, confirmation gate, mic button (records WebM → POST `/stt` → transcript → POST `/chat`).
- **Right (420px, `Dashboard.tsx`)** — balance card with eased progress bar (800ms cubic-out), 7-tile strip with "Simulate approve tap" button on the draft tile, browser panel showing live JPEG frames (auto-clears 3s after `browser_status.status === "done"`), research feed grouped by query, narration badge.

**Event handling (`App.tsx:51–177`)** — 16+ SSE event types handled. TTS queue (`pumpQueue`, lines 275–306) plays `narration` events sequentially via `<audio>` against `/tts?text=…`, fails gracefully.

**Session** — `session_id` is null on first POST; server generates and returns; held only in `sessionIdRef` (no localStorage). Lost on refresh — acceptable for a 3-min demo.

---

## Mock sites (`mock_sites/`)

- **`signin.html`** — bunq-branded landing, accepts anything, redirects to `/`.
- **`vendor/index.html`** — StayHub 4-stage state machine (landing → details → payment → done), generates `STH-<random>` booking ref; query params populate hotel/price/guest.
- **`search/index.html`** — TripLens animation: types query at 45ms/char, 1.1s spinner, then results slide in at 120ms intervals; results pre-fetched and passed as base64 via `?data=`.

---

## SSE event types (selected)

| Event | Purpose |
|---|---|
| `user_message` / `agent_text_delta` / `agent_message` | Chat stream |
| `tool_call` | Tile + balance state; status `firing/ok/failed` |
| `options` | Triggers card rack in chat |
| `confirmation_request` | Approval gate in chat |
| `phase` | Header pill + toggles tool catalog |
| `narration` | Triggers client-side TTS playback queue |
| `browser_frame` | One Playwright JPEG (base64) |
| `browser_status` | Status badge on browser panel |
| `search_results` | Appends one group to Research feed |
| `payment_event` / `draft_payment_event` / `schedule_event` / `request_event` | From bunq webhook parse → tile flip + balance update |
| `balance` | Sub-account balance animation |
| `bunq_webhook` | Raw passthrough for debugging |

---

## Tests (`tests/`)

- **`test_bunq_tools.py`** — 9 ordered smoke tests against live sandbox (state threaded via `_state` dict). Covers all 6 bunq tools, asserts response shape. All green.
- **`test_agent_loop.py`** — end-to-end 4-turn conversation against live Anthropic + bunq, observes SSE stream (no assertions, just structure-by-event-type).

---

## Key design decisions

### Why we don't hit Google / Bing / DuckDuckGo in Playwright headless
All three block or captcha headless Chromium. Solution: `httpx` POSTs to DDG's HTML endpoint (`https://html.duckduckgo.com/html/`), then results are rendered in the **TripLens** mock page (the visible search engine the judges see). Playwright just navigates and streams frames.

### Why TripLens exists
Playwright needs something visible to render. The mock page accepts pre-fetched results via base64 URL param, then animates: types the query char-by-char, shows "Searching the web…" spinner, then renders results one-by-one. Looks like a real search engine; reliability of httpx fetching.

### bunq API gotchas baked in
- **Sugardaddy €500 cap** — `ensure_primary_balance` loops chunked requests.
- **`savings_goal` shape** — required for `monetary-account-savings`; parse `alias[].type == "IBAN"` from response.
- **`draft-payment` requires `entries[]`**, not a flat payment body. Approval = PUT `status: ACCEPTED` on the draft.
- **`schedule-payment` wraps `payment` + `schedule`**, with `recurrence_unit: WEEKLY`.
- **Webhook categories that work**: `PAYMENT`, `MUTATION`, `DRAFT_PAYMENT`, `SCHEDULE_RESULT`, `REQUEST`. bunq does **not** sign outbound webhooks.
- **Device-server registration is per-API-key** — always `rm bunq_context.json` when changing keys, or step-2 returns "User credentials are incorrect".

---

## Risks & fallbacks

| Risk | Mitigation |
|---|---|
| Playwright chromium not installed | `playwright install chromium` |
| Ngrok URL changes between sessions | Update `PUBLIC_BASE_URL` in `.env`, restart server |
| Judge hasn't tapped the draft on the bunq sandbox app | `/simulate-approve` button on the dashboard draft tile |
| Primary balance drained from prior tests | `ensure_primary_balance(min_eur=2000, target_eur=3000)` at startup |
| Haiku 4.5 occasionally skips `schedule_recurring` | System prompt explicitly emphasizes "do not skip this" |
| DDG HTML returns 0 results | Search beat still renders mock with empty state; agent continues |
| Anthropic API outage | Fallback MP4 recording (planned, hour-20 freeze) |
| ElevenLabs rate-limit mid-demo | TTS queue fails per-phrase; demo continues silent |

---

## What's done

- All 6 bunq tool wrappers, smoke-tested green against sandbox (9 tests)
- Phased Claude tool-use loop with streaming + prompt caching
- FastAPI server + SSE + webhook receiver + STT/TTS proxies
- ngrok tunnel + auto-register webhooks at startup
- Ensure-balance loop (chunked top-ups respecting sugardaddy's €500 cap)
- Mock bunq sign-in landing
- React dashboard: chat, tile strip, balance counter, browser panel, research feed, narration badge
- `search_trip_options` — visible DDG search via TripLens mock
- `book_hotel` — visible 4-stage booking via StayHub mock
- `/simulate-approve` path-C fallback
- `/debug/*` rehearsal endpoints

## What's left

1. **Slack integration** — once `SLACK_WEBHOOK_URL` is provided; `side_tools.send_slack` is stubbed
2. **End-to-end live dry runs** — full conversation → search → options → confirm → execute → draft-approve → done on projector, with phone
3. **Polish pass** — tile animations, narration timing, CSS fades
4. **Fallback MP4 recording** — capture one successful run as demo insurance, hour 20
5. **DevPost submission** — slide deck (3 slides), README, GitHub push
