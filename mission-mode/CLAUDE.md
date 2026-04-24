# Trip Agent for bunq — CLAUDE.md

Project-local context for future Claude sessions (and human teammates) working in `mission-mode/`.

---

## 1. One-line pitch

A conversational trip-planning agent that lives inside bunq's ecosystem. User types or speaks a trip idea, Claude runs a phased tool-use loop (clarify → web-search → present → confirm → execute), and the bunq sandbox API + a Playwright browser beat carry it out while a live dashboard narrates every step.

Built for **bunq Hackathon 7.0**, April 2026.

---

## 2. Demo story (the 3-minute flow judges see)

| Beat | ~Time | What happens |
|---|---|---|
| Sign-in | 5s | Mock bunq OAuth page (`/signin`) → lands in chat |
| Understanding | 30s | 2–3 clarifying Q&A exchanges |
| **Live search** | 30s | `search_trip_options` fires 2–3×. Right panel shows a stylized "TripLens" search engine animating the query being typed and real results scrolling in. Below the browser panel, a "Research · sources found" feed lists every link with title + host + snippet (clickable). |
| Present | 10s | 3 option cards render in chat with hotel + dinner + activity + total €. User picks one. |
| Confirm | 10s | `request_confirmation` → user replies "yes" → phase flips to `EXECUTING` |
| **Execute** | ~90s | 7 beats fire, ~700ms apart: `create_sub_account` → `fund_sub_account` → `book_hotel` (visible Playwright booking flow on StayHub mock) → `pay_vendor` → `create_draft_payment` → `schedule_recurring` → `request_from_partner` → `send_slack` |
| **Signature beat** | during execute | Real bunq draft-payment push lands on presenter's phone; tap approve; tile flashes green via webhook. Path C: `/simulate-approve` button on dashboard (no phone needed). |
| Wrap | 5s | Agent narrates summary; dashboard frozen for ~20s for judges to inspect |

---

## 3. Architecture

### File tree (what's actually here)

```
mission-mode/
├── .env.example               # template; real .env is gitignored
├── .gitignore                 # blocks .env, .venv, bunq_context.json, dist/, node_modules, etc.
├── requirements.txt
├── bunq_client.py             # copied from hackathon_toolkit: 3-step auth + RSA signing + context caching
├── orchestrator/
│   ├── server.py              # FastAPI: /, /signin, /events (SSE), /chat, /bunq-webhook,
│   │                          #          /simulate-approve, /stt, /tts, /debug/search-trip-options,
│   │                          #          /debug/book-hotel, /mock-vendor, /mock-search, /assets
│   ├── agent_loop.py          # Claude tool-use streaming loop + dispatch table
│   ├── phases.py              # Phase enum: UNDERSTANDING | AWAITING_CONFIRMATION | EXECUTING | DONE
│   ├── sessions.py            # In-memory session store keyed by session_id
│   ├── events.py              # SSE EventBus (asyncio.Queue fan-out)
│   ├── system_prompt.py       # SYSTEM_PROMPT + 9 tool schemas + tools_for_phase()
│   ├── bunq_tools.py          # 6 sandbox wrappers + ensure_primary_balance + register_webhooks
│   ├── browser_agent.py       # Playwright: search_trip_options + book_hotel, JPEG frame streaming
│   ├── webhooks.py            # Parses bunq NotificationUrl envelopes → typed SSE events
│   ├── voice.py               # ElevenLabs Scribe STT + streaming TTS httpx proxies
│   └── side_tools.py          # send_slack stub
├── mock_sites/
│   ├── signin.html            # bunq-branded mock OAuth landing
│   ├── vendor/index.html      # "StayHub" 4-stage booking flow (Playwright clicks through)
│   └── search/index.html      # "TripLens" search engine (Playwright renders, results pre-fetched)
├── dashboard/                 # Vite + React 19 + TypeScript
│   ├── index.html, vite.config.ts, tsconfig.json, package.json
│   └── src/
│       ├── App.tsx            # SSE subscription, state, sessionId, TTS queue
│       ├── Chat.tsx           # Streaming bubbles, option cards, confirmation gate, mic
│       ├── Dashboard.tsx      # Balance card, tile strip, browser panel, research feed, narration
│       ├── types.ts           # ServerEvent / ChatEntry / TileState / PackageOption
│       ├── styles.css
│       └── main.tsx
└── tests/
    ├── test_bunq_tools.py     # 9 smoke tests against sandbox, all green
    └── test_agent_loop.py
```

### Data flow

```
user → Chat.tsx → POST /chat → agent_loop.run_turn()
                                   │
                                   ├─ emits SSE: user_message, agent_text_delta, agent_message,
                                   │             tool_call, options, confirmation_request, phase,
                                   │             narration, browser_status, browser_frame,
                                   │             search_results
                                   │
                                   └─ dispatches tools via _execute_tool():
                                        bunq_tools.*  → real bunq sandbox POSTs
                                        browser_agent.search_trip_options → httpx+DDG + Playwright
                                        browser_agent.book_hotel          → Playwright on mock vendor

bunq → POST /bunq-webhook → webhooks.handle() → bus.publish() → Dashboard tiles update
```

### SSE event bus

Global `EventBus` in `events.py`. Every dashboard client gets its own `asyncio.Queue` (maxsize=256). Never use `event_type=` as a kwarg — it collides with `publish`'s positional arg. Use e.g. `bunq_event=` instead.

---

## 4. Quick start

### One-time setup

```bash
cd mission-mode
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium

cd dashboard && npm install && npm run build && cd ..

cp .env.example .env
# Fill in: BUNQ_API_KEY, ANTHROPIC_API_KEY, ELEVENLABS_API_KEY, ELEVENLABS_VOICE_ID,
#         SLACK_WEBHOOK_URL (optional), PUBLIC_BASE_URL (set after ngrok starts)
```

### Every-run

```bash
# Terminal A: tunnel for bunq webhooks
ngrok http 8000   # copy the HTTPS URL into .env as PUBLIC_BASE_URL

# Terminal B: server (auto-registers webhooks at startup)
.venv/bin/uvicorn orchestrator.server:app --port 8000

# Browser → http://localhost:8000/  (or /signin for the themed landing)
```

Startup log expectations:
```
[startup] BUNQ_API_KEY present: True
[startup] ANTHROPIC_API_KEY present: True
[startup] ELEVENLABS_API_KEY present: True
[startup] PUBLIC_BASE_URL: https://<your-ngrok>.ngrok-free.dev
[startup] balance check: {'topped_up': True|False, ...}
[startup] webhooks registered: [...] → https://.../bunq-webhook
```

### Generating a fresh sandbox user (when the current one gets weird)

```python
from bunq_client import BunqClient
key = BunqClient.create_sandbox_user()  # prints new sandbox_xxx key
# copy into .env, delete bunq_context.json, restart server
```

Then fetch the user's alias for signing into the sandbox app:
```python
c = BunqClient(api_key=key, sandbox=True); c.authenticate()
info = c.get(f'user/{c.user_id}')
# alias[].PHONE_NUMBER and alias[].EMAIL → use these to log into the sandbox app
```

---

## 5. Phase state machine

```
UNDERSTANDING ──(options presented + user picks)──► AWAITING_CONFIRMATION
AWAITING_CONFIRMATION ──(user says "yes"/"go"/etc.)──► EXECUTING
EXECUTING ──(request_from_partner + send_slack both fired)──► DONE
```

- Phase is stored server-side per `Session`. The model CANNOT call execution tools during `UNDERSTANDING` because `tools_for_phase()` strips them from the tools array passed to `anthropic.messages.stream`.
- The confirmation gate is a text match: `_is_yes()` in agent_loop.py accepts "yes", "y", "go", "confirm", "ok", "yep", "proceed", "let's go", "approve", etc.
- `present_options` auto-flips UNDERSTANDING → AWAITING_CONFIRMATION.
- DONE detection checks the full message history for `request_from_partner` + `send_slack` having both been called.

---

## 6. Tool catalog (9 tools, 3 phases)

| Phase | Tool | Type | What it does |
|---|---|---|---|
| UNDERSTANDING | `search_trip_options` | local | httpx fetches DDG HTML results → Playwright renders them on TripLens mock page → streams JPEG frames + emits `search_results` event |
| UNDERSTANDING | `web_search` | Anthropic server-side | Fallback real web search (type: `web_search_20250305`, max_uses: 5) |
| UNDERSTANDING | `present_options` | local | Renders 3 package cards in chat; auto-flips phase to AWAITING_CONFIRMATION |
| ALL | `request_confirmation` | local | Renders approval bubble in chat; user's next text turn decides |
| EXECUTING | `create_sub_account` | bunq | POST `monetary-account-savings` with `savings_goal` — the shape below works |
| EXECUTING | `fund_sub_account` | bunq | POST `payment` from primary → sub-account IBAN |
| EXECUTING | `book_hotel` | local | Playwright drives a 4-stage booking on StayHub mock (`/mock-vendor/`) |
| EXECUTING | `pay_vendor` | bunq | POST `payment` from sub-account to sugardaddy, with vendor label in description |
| EXECUTING | `create_draft_payment` | bunq | POST `draft-payment` with `entries[]` shape — triggers a real push on the bunq sandbox app |
| EXECUTING | `schedule_recurring` | bunq | POST `schedule-payment` with `schedule.recurrence_unit: WEEKLY` |
| EXECUTING | `request_from_partner` | bunq | POST `request-inquiry` to sugardaddy (auto-accepted in sandbox) |
| EXECUTING | `send_slack` | local | POST to `SLACK_WEBHOOK_URL` if set (stub if not) |
| EXECUTING | `narrate` | local | Emits `narration` SSE event + kicks off ElevenLabs TTS stream |

Full tool schemas live in `orchestrator/system_prompt.py`. The system prompt enforces ordering and reminds the model not to skip `schedule_recurring` (Haiku 4.5 dropped it on one run).

---

## 7. bunq API gotchas — what we learned

### Sugardaddy top-ups are capped at €500 per request
`ensure_primary_balance(target_eur=2000)` loops requesting chunks of €500 until the target is hit. One chunk = one `request-inquiry` to `sugardaddy@bunq.com`, which auto-accepts in sandbox. If you blast a single €5000 request it'll sit pending forever.

### `savings_goal` shape — works but wasn't documented with a full example
```python
client.post(f"user/{uid}/monetary-account-savings", {
    "currency": "EUR",
    "description": name,
    "savings_goal": {"currency": "EUR", "value": f"{goal_eur:.2f}"}
})
```
Parse response: `id` + `alias[]` where `alias[].type == "IBAN"`.

### `draft-payment` requires `entries[]`, not a flat payment body
```python
client.post(f"user/{uid}/monetary-account/{aid}/draft-payment", {
    "number_of_required_accepts": 1,
    "entries": [{
        "amount": {"currency": "EUR", "value": f"{amount:.2f}"},
        "counterparty_alias": {"type": "EMAIL", "value": "sugardaddy@bunq.com"},
        "description": description,
    }]
})
```
User approves in the bunq sandbox app; bunq fires a `PAYMENT` webhook (there isn't a dedicated "draft_approved" webhook). Approval status flip = PUT `status: ACCEPTED` on the draft (that's what `/simulate-approve` does).

### `schedule-payment` wraps `payment` + `schedule`
```python
client.post(f"user/{uid}/monetary-account/{aid}/schedule-payment", {
    "payment": {
        "amount": {"currency": "EUR", "value": f"{amount:.2f}"},
        "counterparty_alias": {"type": "IBAN", "value": to_iban},
        "description": description,
    },
    "schedule": {
        "recurrence_unit": "WEEKLY",
        "recurrence_size": 1,
        "time_start": (datetime.utcnow() + timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S"),
    }
})
```

### Webhook categories that actually work
`PAYMENT, MUTATION, DRAFT_PAYMENT, SCHEDULE_RESULT, REQUEST` — register all 5 at startup via POST `user/{uid}/notification-filter-url`. bunq sends from IP `18.196.44.64` (AWS eu-central-1). **bunq does NOT sign outbound webhooks** — no body verification.

### Device-server registration is per-API-key
If you swap API keys but forget to delete `bunq_context.json`, auth silently reuses the stale installation token from the old key → step-2 device-server returns `400: "User credentials are incorrect"`. Always `rm bunq_context.json` when changing keys.

### `EventBus.publish()` kwarg collision
`publish(self, event_type: str, **payload)` — don't pass `event_type=` inside payload. Webhooks.py previously did this and crashed on every incoming webhook. Renamed it to `bunq_event=` in the payload.

### The monetary-account-savings endpoint requires a distinct create call
Cannot re-use `monetary-account-bank` and expect goal tracking. The savings variant returns accounts with `alias[]` containing the IBAN (type=IBAN); that IBAN is what you transfer to from primary.

---

## 8. Playwright design decisions

### Why we don't hit Google / Bing / DuckDuckGo in Playwright headless
All three block or captcha headless Chromium (Playwright headless-shell specifically).
- `duckduckgo.com` — returns the landing page without results
- `bing.com` — returns empty body (125 bytes)
- `search.brave.com` — renders but `networkidle` never fires (websockets kept alive)

### What works: httpx POST to DDG's HTML endpoint
```python
httpx.post("https://html.duckduckgo.com/html/",
  data={"q": query, "b": "", "kl": "us-en"},
  headers={"User-Agent": "...", "Referer": "https://html.duckduckgo.com/"})
```
Returns stable markup with `<a class="result__a" href="...">` containing the real URL (sometimes wrapped in `duckduckgo.com/l/?uddg=<encoded>` — unwrap via query-string parsing).

### Why we built TripLens mock search page
Playwright needs something visible to render. So: httpx does the data fetch, then we pass the results as base64-encoded JSON in the mock page's URL (`/mock-search/?q=...&data=...`). The page's JS auto-animates: types the query char-by-char, shows "Searching the web…" spinner, then renders results one-by-one. Playwright just navigates + sits for ~5s streaming frames.

### JPEG frame streaming
- Viewport 900×560, JPEG quality 65, ~20–30KB per frame
- `FRAME_INTERVAL_S = 0.35` (~30 frames per 10s beat, well under the 256-item queue cap)
- Published as `browser_frame` SSE events with base64 payload; dashboard renders them via `<img src="data:image/jpeg;base64,..."/>`
- Panel aspect-ratio 900/560 in CSS keeps the frame crisp in the right column (420px wide)

### User-agent used
```
Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36
```
Full Chrome, not headless-chrome — less likely to be flagged by simple bot detectors.

---

## 9. SSE event types

See `dashboard/src/types.ts` for the authoritative list. Highlights:

| Event | Payload | Purpose |
|---|---|---|
| `user_message` | `{text}` | User's input echoed |
| `agent_text_delta` | `{text}` | Streaming chunk during `messages.stream` |
| `agent_message` | `{text}` | Finalized assistant message |
| `tool_call` | `{name, status, input, result, error}` | Status: firing/ok/failed. Drives tile strip. |
| `options` | `{intro, options:[{id, hotel, restaurant, extra, total_eur, notes}]}` | Triggers card rack in chat |
| `confirmation_request` | `{summary}` | Approval gate in chat |
| `phase` | `{value}` | Pill in header + toggles tool catalog |
| `narration` | `{text}` | Triggers TTS playback queue |
| `browser_frame` | `{jpeg_b64}` | One frame from Playwright |
| `browser_status` | `{status, step, hotel, booking_ref, query}` | Status badge on browser panel |
| `search_results` | `{query, results:[{title, url, snippet}]}` | Appends one group to Research feed |
| `payment_event` / `draft_payment_event` / `schedule_event` / `request_event` | from bunq webhook parse | Tile flip + balance updates |
| `balance` | `{account_id, value_eur}` | Sub-account balance animation |
| `bunq_webhook` | `{category, bunq_event}` | Raw passthrough for debugging |

---

## 10. Debug endpoints (demo utilities, not removed)

| Endpoint | Purpose |
|---|---|
| `POST /simulate-approve` | Fake the bunq-app tap: PUTs the latest pending draft to ACCEPTED. Same webhook chain as a real tap. |
| `POST /debug/search-trip-options` `{query}` | Fires the search beat standalone. No LLM tokens. |
| `POST /debug/book-hotel` `{hotel, amount_eur}` | Fires the booking beat standalone. |
| `GET /mock-vendor/?hotel=X&price=Y&checkin=...&guest=...` | The StayHub page Playwright drives. Can be opened by hand. |
| `GET /mock-search/?q=X&data=<base64 json>` | The TripLens page Playwright renders. |
| `GET /signin` | Mock bunq OAuth landing (visual only, any input is accepted). |

---

## 11. Known risks & fallbacks

| Risk | Mitigation |
|---|---|
| Playwright chromium not installed | `playwright install chromium` — ~77MB download |
| Ngrok tunnel URL changes between sessions | Update `PUBLIC_BASE_URL` in `.env` before each demo, restart server |
| bunq sandbox app missing / judge hasn't tapped draft | `/simulate-approve` button on the Dashboard draft tile |
| Primary balance drained by prior test runs | `ensure_primary_balance(min_eur=2000, target_eur=3000)` at startup tops up in €500 chunks |
| Haiku 4.5 occasionally skips `schedule_recurring` | Prompt explicitly emphasizes "IMPORTANT: do not skip this" |
| DDG HTML endpoint returns 0 results | `search_trip_options` still renders the mock page with an "no results" state and the agent continues |
| Anthropic API outage | Pre-record a successful-run MP4 as fallback video before hour-20 feature freeze |
| ElevenLabs rate-limit mid-demo | TTS queue in App.tsx gracefully fails per-phrase; demo continues without voice |

---

## 12. What's done

- ✅ 6 bunq tool wrappers, all smoke-tested against sandbox (9 tests green)
- ✅ Phased Claude tool-use loop with streaming
- ✅ FastAPI server + SSE + webhook receiver + STT/TTS proxies
- ✅ ngrok tunnel + auto-register webhooks at startup
- ✅ Ensure-balance loop (chunked top-ups respecting sugardaddy's €500 cap)
- ✅ Mock bunq sign-in landing
- ✅ React dashboard: chat, tile strip, balance counter, browser panel, research feed, narration badge
- ✅ `search_trip_options` — visible DDG search via TripLens mock
- ✅ `book_hotel` — visible 4-stage booking via StayHub mock
- ✅ `/simulate-approve` path-C fallback
- ✅ `/debug/*` rehearsal endpoints
- ✅ Code pushed to `outofaditya/bunq-hackathon` on branch `trip-agent-mission-mode`

---

## 13. What's left (in priority order)

1. **Slack integration** — once `SLACK_WEBHOOK_URL` is provided; `side_tools.send_slack` is stubbed
2. **End-to-end live dry runs** — verify full conversation → search → options → confirm → execute → draft-approve → done on projector, with phone
3. **Polish pass** — tile animations, narration timing, CSS fades
4. **Fallback MP4 recording** — capture one successful run as demo insurance, hour 20
5. **DevPost submission** — slide deck (3 slides), README, GitHub push

---

## 14. Demo rehearsal checklist

- [ ] ngrok tunnel up, `PUBLIC_BASE_URL` in `.env` matches
- [ ] Server starts clean: balance check ok, webhooks registered
- [ ] `/signin` → `/` flow renders
- [ ] Trip prompt → agent asks ≤3 clarifiers
- [ ] `search_trip_options` fires 2–3× with different queries (hotel / dinner / activity)
- [ ] TripLens page animates in browser panel, frames stream smoothly
- [ ] Research feed populates with real clickable links
- [ ] `present_options` renders 3 cards with vendor names that appeared in search results
- [ ] User picks one → `request_confirmation` → user says "yes" → phase flips EXECUTING
- [ ] `create_sub_account` + `fund_sub_account` → balance counter animates, tile flips ok
- [ ] `book_hotel` → StayHub panel shows: review → details → payment → confirmation
- [ ] `pay_vendor` → Hotel paid tile flips ok
- [ ] `create_draft_payment` → Dinner approval tile flips to pending with "Simulate approve tap" button
- [ ] Presenter taps bunq sandbox app push (or hits `/simulate-approve`) → tile flashes green via webhook
- [ ] `schedule_recurring` → Weekly savings tile flips ok (verify it didn't get skipped!)
- [ ] `request_from_partner` → Split requested tile flips ok
- [ ] `send_slack` → Slack DM lands on second device (if `SLACK_WEBHOOK_URL` set)
- [ ] Agent writes wrap-up message, phase flips DONE
- [ ] Full run ≤ 3 min, no operator intervention except intentional phone tap

---

## 15. Session history so far (for context)

- **Fresh sandbox user switches**: Ada → K. Glover → P. Alderson (IP-locked, discarded) → A. Gould (currently active, id=3628692, phone +31618616868, email `test+75cf1404-367a-458e-a245-b1379d58c29f@bunq.com`)
- **User preferences observed**:
  - Wants auto-mode throughout — execute, minimize interruptions
  - Prefers reliability over fanciness (validated the httpx+DDG + local mock approach over flaky real-search in headless Chromium)
  - Wanted Playwright to BE the visible research step, not just the booking step — led to the TripLens mock page design

---

*When in doubt, read `orchestrator/system_prompt.py` for the current tool shapes and prompt text — it's the authoritative contract between the agent and the rest of the system.*
