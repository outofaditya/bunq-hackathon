# Mission Mode — MVP Design Spec

**Date:** 2026-04-24
**Project:** bunq Hackathon 7.0 submission
**Status:** Draft for user review
**Execution model:** Claude implements solo, user monitors, phase-by-phase with user testing gate between phases.

---

## 1. Context

The bunq hackathon 7.0 brief is to build a multi-modal AI that *sees, hears, understands, and acts* on top of the bunq sandbox API. Rubric: Impact 30%, Creativity 25%, Technical Execution 20%, bunq Integration 15%. Prize: €5,000 + career track at bunq.

The approved product concept is **Mission Mode**: a single spoken life-goal ("€500, best weekend for me and Sara") triggers an autonomous cascade of 8–10 real bunq API calls plus side-actions (Slack message, Google Calendar event, browser-booked reservation) in under two minutes, all visibly executed on a live dashboard while the agent narrates in a stock ElevenLabs voice.

The demo is **on stage at bunq HQ** with physical phones for the signature beats: (a) a real bunq draft-payment approval notification buzzes a phone and the presenter taps "approve" live, and (b) a Slack DM lands on a teammate's phone mid-cascade.

The build model is **phase-by-phase iterative**. Each phase delivers a testable artifact with clear exit criteria. The user runs each phase's test plan and approves before we move to the next phase. There is no hard wall-clock deadline — the quality bar per phase is the constraint.

## 2. Goals

- Win on **Impact & Usefulness (30%)** with a product people would actually pay for: a financial concierge that plans and executes complete life-missions from one spoken command.
- Win on **Creativity (25%)** with a demo the audience has never seen: multi-modal intake → live agent cascade → real money moves → real phones buzzing.
- Hit **Technical Execution (20%)** with a clean Claude tool-use loop, prompt caching, real webhook-driven UI, real browser agent with vision-in-loop, and graceful fallbacks.
- Max out **bunq Integration (15%)** by using **10 distinct bunq API endpoints** across three missions — more than any other team will touch.

## 3. Locked decisions (from brainstorming)

| Decision | Value |
|---|---|
| Primary mission | Surprise Weekend (hero demo) |
| Secondary missions | Payday Autopilot (medium), Travel Mode (full trip prep) |
| Demo context | On stage at bunq HQ, in-person, projector, physical phones |
| Voice input | Pre-recorded `.wav` on button tap (deterministic on stage) |
| Voice output | Stock ElevenLabs voice (proposed: `Rachel` — warm, professional; swappable) |
| Vision modalities | (1) Claude Vision on one uploaded screenshot at intake, (2) Claude Vision in browser-agent decision loop |
| Browser agent | Playwright + Claude Vision against a **local mock restaurant site** we build (real third-party vendors are out — anti-bot walls) |
| Google Calendar | Real event creation via OAuth (user completes consent once) |
| Slack | Real incoming-webhook DM to a teammate channel |
| Voice cloning | **OUT** — stock voice only |
| SQLite persistence | **OUT** — in-memory mission state |
| Multi-user | **OUT** — single sandbox user, hardcoded |
| PSD2 OAuth | **OUT** — not rubric-relevant, 5–6h we don't need |
| Live mic | **OPTIONAL Tier 3** — only if Phase 8 finishes early (~3–4h to add) |
| Sound FX | IN — subtle ticks on API calls + completion chime |

## 4. The three missions

### 4.1 Surprise Weekend — hero demo (8 bunq calls + 3 auxiliary actions: browser-agent booking, Slack DM, Calendar event)

**Trigger:** presenter plays pre-recorded wav → *"Five hundred euros, best weekend for me and Sara."*
**Intake vision beat:** user drags a WhatsApp screenshot of a chat with Sara onto the dashboard; Claude Vision extracts preferences (e.g. "Italian, rooftop, not Thai").

| # | Action | bunq endpoint / side-action |
|---|---|---|
| 1 | Create 🌹 Sara Weekend sub-account with €500 `savings_goal` | `POST /user/{uid}/monetary-account-savings` |
| 2 | Transfer €500 primary → sub (IBAN→IBAN) | `POST /user/{uid}/monetary-account/{primary}/payment` |
| 3 | Browser-agent books dinner on local mock site (Claude vision reads screenshots, clicks). Payment fires on confirmation. | Playwright + `POST /payment` (~€85) |
| 4 | **⭐ Draft-payment for €120 concert tickets — presenter's phone buzzes, they tap approve live.** Webhook fires, dashboard flashes green. | `POST /draft-payment` → `PUT /draft-payment/{id}` {ACCEPTED} via real bunq app |
| 5 | Pay €40 to Uber (pre-book ride) | `POST /payment` |
| 6 | Create €50 `bunqme-tab` (shareable gift link; QR appears on dashboard for "Grandma's contribution") | `POST /bunqme-tab` |
| 7 | Schedule €50/week recurring "next weekend fund" | `POST /schedule-payment` |
| 8 | Request €40 split from Sara (sandbox sugardaddy auto-accepts) | `POST /request-inquiry` |
| 9 | **⭐ Slack DM to Sara's channel: "Friday. Don't plan. Trust me."** — teammate phone buzzes | Slack incoming-webhook POST |
| 10 | Google Calendar event: "🌹 Weekend" Fri 7pm, invitee Sara | Google Calendar API `events.insert` |

Spoken wrap-up: *"€455 committed, €45 spontaneity buffer. Sara notified. Enjoy your weekend."*

### 4.2 Payday Autopilot — medium (~7 bunq calls)

**Trigger:** voice → *"Payday, distribute."*

| # | Action | bunq endpoint |
|---|---|---|
| 1 | Create 🏠 Rent sub-account | `POST /monetary-account-savings` |
| 2 | Create 💼 Savings sub-account | `POST /monetary-account-savings` |
| 3 | Create 🍻 Fun sub-account | `POST /monetary-account-savings` |
| 4 | Fund Rent with €1,200 (60% of €2,000) | `POST /payment` IBAN→IBAN |
| 5 | Fund Savings with €600 (30%) | `POST /payment` IBAN→IBAN |
| 6 | Fund Fun with €200 (10%) | `POST /payment` IBAN→IBAN |
| 7 | Schedule monthly recurring €1,200 Rent standing order | `POST /schedule-payment` MONTHLY |

Spoken wrap-up narrates the split percentages and next paydate.

### 4.3 Travel Mode — full trip prep (~6 bunq calls + 2 side-actions)

**Trigger:** voice → *"I'm flying to Tokyo Friday."*

| # | Action | bunq endpoint / side-action |
|---|---|---|
| 1 | Create 🇯🇵 Tokyo sub-account with €300 `savings_goal` | `POST /monetary-account-savings` |
| 2 | Fund €300 primary → Tokyo | `POST /payment` IBAN→IBAN |
| 3 | **⭐ Freeze home card** (PUT status DEACTIVATED) | `PUT /user/{uid}/card/{cid}` |
| 4 | Schedule daily €50 budget transfer from Tokyo-sub to a secondary "spending" sub | `POST /schedule-payment` DAILY |
| 5 | Request €150 from travel buddy (sandbox sugardaddy) | `POST /request-inquiry` |
| 6 | Create bunqme-tab €100 "anyone can chip in" emergency fund | `POST /bunqme-tab` |
| 7 | Calendar events: flight, hotel check-in, dinner reservation | Google Calendar `events.insert` ×3 |
| 8 | **⭐ Slack DM to travel buddy channel:** "🇯🇵 Tokyo Friday. Let me know if you need anything." | Slack incoming-webhook POST |

Spoken wrap-up notes card is frozen and daily budget is set.

## 5. The 10 bunq endpoints used (integration rubric)

1. `POST /user/{uid}/monetary-account-savings` — create emoji-tagged sub-accounts with `savings_goal`
2. `PUT /user/{uid}/monetary-account-savings/{id}` — update goal/color on mission completion (e.g., turn main Weekend sub green at end)
3. `POST /user/{uid}/monetary-account/{aid}/payment` — both IBAN→IBAN internal transfers and email→vendor outbound payments
4. `POST /user/{uid}/monetary-account/{aid}/draft-payment` — pending payments requiring real user approval
5. `PUT /user/{uid}/monetary-account/{aid}/draft-payment/{id}` — status transitions (observed via polling fallback if webhook misses)
6. `POST /user/{uid}/monetary-account/{aid}/schedule-payment` — WEEKLY/MONTHLY/DAILY recurrence
7. `POST /user/{uid}/monetary-account/{aid}/request-inquiry` — ask counterparty to pay
8. `POST /user/{uid}/monetary-account/{aid}/bunqme-tab` — shareable payment links (QR rendered on dashboard)
9. `PUT /user/{uid}/card/{cid}` — freeze/unfreeze card (Travel Mode)
10. `POST /user/{uid}/notification-filter-url` — webhook registration; incoming webhooks drive the live balance counter and the draft-approval flash

Plus `GET /user/{uid}/monetary-account/{aid}/payment` for the optional "memory" tool (history-aware preferences) — Tier 2.

## 6. Architecture

```
┌──────────────────────────────────────────────────────────────────────┐
│             USER DEVICES (laptop + two phones)                        │
│  ┌────────────┐   ┌───────────┐   ┌────────────┐   ┌───────────┐    │
│  │ Dashboard  │   │ Presenter │   │ Teammate   │   │ Another   │    │
│  │ (laptop)   │   │ phone     │   │ phone      │   │ phone     │    │
│  │  Vite+     │   │ (bunq     │   │ (Slack)    │   │ (Calendar)│    │
│  │  React+SSE │   │  sandbox  │   │            │   │           │    │
│  └─────┬──────┘   │  app)     │   └─────▲──────┘   └─────▲─────┘    │
│        │          └─────▲─────┘         │                │           │
└────────│────────────────│───────────────│────────────────│───────────┘
         │ EventSource    │ bunq app      │ Slack          │ Gmail/Cal
         │ (SSE)          │ approval      │ mobile         │ mobile
         ▼                ▼               │                │
┌──────────────────────────────────────────│────────────────│───────────┐
│                    FastAPI orchestrator (Python)          │           │
│  ┌─────────────────────────────────────┐                  │           │
│  │ Claude tool-use loop                 │                  │           │
│  │ claude-opus-4-7, prompt-caching on   │                  │           │
│  │ system + tool catalog                │                  │           │
│  └────┬─────────────────────────────────┘                  │           │
│       │                                                    │           │
│   ┌───┴───┬───────┬─────────┬───────────┬────────┐         │           │
│   ▼       ▼       ▼         ▼           ▼        ▼         │           │
│  bunq   browser  STT/TTS   Slack    Calendar   Vision      │           │
│  tools  agent    Whisper+  webhook  OAuth       (Claude    │           │
│  (x10)  (Playw.) ElevenLabs          + events   images)    │           │
│         +Claude                                             │           │
│         Vision                                              │           │
│  │       │                                                  │           │
│  ▼       ▼                                                  │           │
│  bunq    local mock restaurant site (served by same FastAPI │           │
│  sandbox at /mock-restaurant/*)                              │           │
└──────│───────────────────────────────────────────────────────│──────────┘
       │                                                       │
       └─── webhooks (ngrok tunnel) ───────────────────────────┘
```

### 6.1 File layout

```
bunq-hackathon/
├── bunq_client.py                    # starter kit (unchanged)
├── orchestrator/
│   ├── __init__.py
│   ├── server.py                     # FastAPI: routes, SSE, webhook, static, mock-site
│   ├── agent_loop.py                 # Claude tool-use loop + prompt caching
│   ├── bunq_tools.py                 # 10 bunq tools (already 7 of 10 done in prev iteration; refactor)
│   ├── side_tools.py                 # Slack, Google Calendar, narrate
│   ├── browser_agent.py              # Playwright + Claude Vision decision loop
│   ├── tts.py                        # ElevenLabs streaming TTS
│   ├── stt.py                        # Whisper transcription of uploaded wav
│   ├── vision.py                     # Claude Vision helper for intake screenshot
│   ├── missions/
│   │   ├── weekend.py                # system prompt template + skeleton
│   │   ├── payday.py
│   │   └── travel.py
│   ├── events.py                     # SSE event bus
│   └── config.py                     # env loading, voice ids, model name
├── dashboard/                        # Vite + React
│   ├── index.html
│   ├── package.json
│   ├── src/
│   │   ├── main.tsx
│   │   ├── App.tsx
│   │   ├── components/
│   │   │   ├── MissionSelector.tsx
│   │   │   ├── ToolCallTile.tsx
│   │   │   ├── BalanceCounter.tsx
│   │   │   ├── BrowserPanel.tsx
│   │   │   ├── NarrationCaption.tsx
│   │   │   └── BunqMeQR.tsx
│   │   └── styles.css
├── mock_sites/
│   └── restaurant/
│       └── index.html                # "TheFork"-styled booking page
├── assets/
│   ├── recorded_voice_weekend.wav
│   ├── recorded_voice_payday.wav
│   ├── recorded_voice_travel.wav
│   └── sample_whatsapp_sara.png      # intake-vision demo screenshot
├── docs/superpowers/specs/
│   └── 2026-04-24-mission-mode-mvp-design.md   # THIS DOC
├── .env / .env.example
├── requirements.txt
└── README.md
```

## 7. Phases (build plan)

Each phase is a testable artifact. User tests, we iterate, we move on. No phase starts before the previous phase passes.

### Phase 0 — Keys & setup (user action) [blocker gate, ~30min user time]

**User provides / sets up:**
- `ANTHROPIC_API_KEY` (Opus 4.7 access)
- `OPENAI_API_KEY` (Whisper STT)
- `ELEVENLABS_API_KEY` + chosen voice_id (I'll propose `Rachel`)
- `SLACK_WEBHOOK_URL` — user creates a Slack app with Incoming Webhook pointed at a test channel
- Google Cloud project + OAuth consent screen + downloaded `google_oauth_client.json` (desktop credentials)
- `ngrok` installed + `authtoken` set
- Two phones charged: one with bunq sandbox app logged in as the test user, one logged into the Slack workspace

**Test:** I run a 10-line `env check` script that hits each service with a no-op call and confirms all keys work.
**Exit:** all six env checks green.

### Phase 1 — Core bunq cascade (headless CLI)

**I build:**
- `orchestrator/bunq_tools.py` — refactor / extend the 7 tools already verified into the 10-tool catalog (add `update_sub_account`, `freeze_card`, `create_bunqme_link`).
- `orchestrator/agent_loop.py` — Claude tool-use loop with prompt caching.
- `orchestrator/missions/weekend.py` — system prompt for the hero mission.
- A CLI entry point: `python -m orchestrator.run_mission weekend "€500 best weekend for me and Sara"`.

**Artifact:** CLI runs the **bunq-only subset** of the Weekend cascade (all 8 bunq calls — browser-agent booking at step 3 is stubbed to a simple payment, Slack + Calendar side-actions are stubs that only log). Prints each tool call + result.

**User test plan:**
1. `python -m orchestrator.run_mission weekend "..."` runs without error.
2. Open bunq sandbox app on phone — confirm: 🌹 Sara Weekend sub-account exists with €500/€500 goal; 3 payments visible; 1 draft-payment pending; 1 scheduled payment pending; 1 bunqme-tab link generated; 1 request-inquiry pending.
3. Tap "approve" on the draft-payment in the bunq app — verify the CLI detects it via polling fallback and prints "approved."

**Exit:** 8 bunq calls fire cleanly, draft-approval detected.

### Phase 2 — Web server + live dashboard (no voice yet)

**I build:**
- `orchestrator/server.py` — FastAPI with SSE `/events`, mission trigger `POST /missions/{name}/start`, `POST /bunq-webhook`, static dashboard at `/`.
- `orchestrator/events.py` — event bus publishing to all SSE subscribers.
- `dashboard/` — Vite+React with: tile grid, live balance counter, draft-approval badge, bunqme QR renderer, BrowserPanel placeholder.
- ngrok tunnel startup + auto-registration of bunq webhook on server boot.

**Artifact:** open `http://localhost:8000/`, click "Start Weekend Mission" button, cascade runs with live visual updates.

**User test plan:**
1. `ngrok http 8000` in one terminal.
2. `python -m orchestrator.server` in another.
3. Open browser → see "Ready" state.
4. Click Start → 8 tiles animate in sequence; balance counter ticks up from webhook.
5. Tap "approve" in bunq app → dashboard's Draft tile flashes green within 2s.

**Exit:** full cascade runs visually end-to-end from a button tap.

### Phase 3 — Voice in (Whisper) + voice out (ElevenLabs TTS)

**I build:**
- `orchestrator/stt.py` — button plays pre-recorded wav → Whisper transcribes → becomes mission prompt.
- `orchestrator/tts.py` — per-step narration synthesized and streamed to dashboard for playback.
- Dashboard mic-button UI + TTS audio player + caption display.

**Artifact:** clicking mic-button plays recorded voice; agent runs; TTS narrates each step in `Rachel` voice; captions appear.

**User test plan:**
1. Click mic → hear pre-recorded command + Whisper transcript visible.
2. Cascade runs, TTS narrates each step with minimal latency.
3. Captions render in sync.

**Exit:** voice layer feels synchronous and natural.

### Phase 4 — Intake vision beat

**I build:**
- `orchestrator/vision.py` — accepts a PNG upload, calls Claude Vision with `type: "image"` content block, extracts a preferences JSON.
- Dashboard drop-zone for the WhatsApp screenshot before mission start.
- Mission system prompt updated to use extracted preferences.

**Artifact:** drag `sample_whatsapp_sara.png` onto dashboard → preferences parsed (cuisine, venue) → cascade uses them.

**User test plan:** swap in a different WhatsApp screenshot (e.g. "let's do sushi this weekend") → verify the agent adapts the dinner vendor name / description.

**Exit:** uploaded image measurably steers the cascade.

### Phase 5 — Browser agent + mock restaurant site

**I build:**
- `mock_sites/restaurant/index.html` — TheFork-styled booking page, 3 screens: search → choose time → confirm. Styling done with minimal CSS.
- `orchestrator/browser_agent.py` — Playwright launches headless Chromium, navigates the mock site; at each screen, takes a screenshot, Claude Vision decides "click this button" / "type this into this field"; loop until confirmation page detected; screen-capture streamed to dashboard BrowserPanel.
- Hook into Weekend cascade at step 3: replaces the static "Café de Klos" payment with "browser-agent books, then payment fires after confirmation screen detected."

**Artifact:** during Weekend cascade, BrowserPanel in dashboard shows live Playwright navigation; booking completes; payment fires.

**User test plan:**
1. Run Weekend cascade → browser panel shows actual navigation on the mock site.
2. Verify final payment amount matches what the mock site displayed on confirmation.
3. Try swapping the mock site's prices → verify agent adjusts the payment amount.

**Exit:** browser agent completes booking in < 25s deterministically across 3 dry runs.

### Phase 6 — Side actions (Slack + Google Calendar)

**I build:**
- `orchestrator/side_tools.py`:
  - `send_slack_message(channel_topic, message)` → POST to `SLACK_WEBHOOK_URL`.
  - `create_calendar_event(title, start, end, invitees)` → Google Calendar API; one-time OAuth flow on first run; refresh tokens cached in `~/.bunq-hackathon/google_token.json`.
- Agent tool catalog extended; Weekend cascade fires both at the right beats.

**User test plan:**
1. Run Weekend cascade.
2. Teammate phone should buzz with real Slack DM ("Friday. Don't plan. Trust me.") within the cascade's 90s window.
3. Teammate's Google Calendar gets an invite for "🌹 Weekend Friday 19:00".

**Exit:** both side-actions land on physical phones during every dry run.

### Phase 7 — Second & third missions

**I build:**
- `orchestrator/missions/payday.py` — system prompt + skeleton for the 7-call Payday cascade.
- `orchestrator/missions/travel.py` — system prompt + skeleton for the 8-call Travel cascade including `freeze_card` tool.
- Dashboard MissionSelector component — 3 cards: Weekend / Payday / Travel. User picks → corresponding pre-recorded wav + system prompt load.
- Assets: `recorded_voice_payday.wav`, `recorded_voice_travel.wav` — 5s clean voice samples (recorded by user or synthesized with ElevenLabs as a fallback).

**User test plan:**
1. Pick Payday → cascade creates 3 sub-accounts, fires 3 transfers, schedules 1 recurring. Verify in bunq app.
2. Pick Travel → cascade creates Tokyo sub-account, funds it, freezes card (verify card goes DEACTIVATED in bunq app), schedules daily transfer, creates Calendar events, sends Slack DM.
3. Run all 3 missions back-to-back → no cross-mission state corruption.

**Exit:** all three missions run end-to-end with their advertised cascades.

### Phase 8 — Polish (sound FX + animations + stage timing)

**I build:**
- Subtle tick audio on each `step_started` event + warmer chime on `mission_complete`.
- Balance-counter money-flow animation (particles flowing tile-to-tile on transfers).
- TTS narration pacing tuned to tile animation timing.
- Typewriter effect on Claude "thinking" transcript (subtle; kept readable).
- Dashboard aesthetic polish — **proposed default:** bunq brand palette (cyan #2DCCA7 + dark #0B1413) with rounded tile cards. Overrule anytime.

**User test plan:** dry run 5 times, time the full Weekend run to < 110s, verify audio + visual pacing.

**Exit:** demo feels stage-ready; pacing consistent across runs.

### Phase 9 — Stage prep (fallback + rehearsal + submission)

**I build / record:**
- **Fallback video** — record a perfect 90s run as `.mp4` stored locally. Dashboard has a "Play fallback" button that triggers the video from the same UI (so if live wifi dies on stage, presenter taps that and nothing looks different).
- Stage-run checklist printed as speaker-notes card.
- DevPost submission: title, 3-sentence pitch, 500-word description, tech-stack list, 90s demo video, 3 images.
- Final GitHub README with setup steps.

**User test plan:**
1. Record fallback video.
2. Do 5 full stage-simulation dry runs with phones charged, projector, simulated wifi drop at each run to confirm fallback path works.
3. Submit DevPost (user presses final submit).

**Exit:** project is submitted, fallback validated.

### Tier 3 — Live mic (only if Phase 8 lands early)

**Optional.** Browser `getUserMedia` → WebSocket PCM stream → Whisper streaming endpoint → replaces the pre-recorded wav flow. Estimated 3–4h with iteration. Skip if anything in Phases 1–9 needs rework.

## 8. Risks & mitigations

| Risk | Mitigation |
|---|---|
| bunq sandbox rate limits (POST 5/3s, PUT 2/3s) | Async queue enforces ≥700ms inter-call spacing — fits comfortably within rubric-expected pacing. |
| Stage wifi drops | Fallback video button; everything required to *render* the demo is local (dashboard, mock site, browser agent) — only the bunq webhook + Slack + Calendar need network. |
| Webhook doesn't arrive (ngrok / bunq delay) | Polling fallback on draft-payment status; indistinguishable on stage from webhook-driven version. |
| ElevenLabs latency spike | Pre-generate and cache every fixed narration phrase at Phase 8; fall back to macOS `say` if API 500s. |
| Anthropic API outage during demo | Fallback video plays the exact same UI with a mock event stream. |
| Browser agent flakes | Mock site is local, static, deterministic; 3+ dry runs guarantee stability. |
| Google OAuth consent not completed before demo | Phase 6 test blocks until user completes consent on first run. |
| Presenter forgets to tap approve on bunq app | Polling fallback times out after 45s and the cascade continues with a "[pending approval]" state on the tile — still acceptable stage beat. |

## 9. Stage demo choreography (110s target)

**Setup on stage (before demo starts):**
- Laptop connected to projector running dashboard.
- Presenter phone on camera stand, bunq sandbox app open.
- Teammate phone on camera stand 2, Slack open in the demo channel.
- A third phone (or the presenter's) showing Google Calendar.

**Beats:**
- 0:00 — "Mission Mode" slide. Presenter taps MissionSelector → picks "Weekend." A sample WhatsApp screenshot auto-uploads (pre-loaded).
- 0:08 — Presenter taps mic button. Recorded voice plays. Waveform animates.
- 0:15 — Claude's plan streams as a typed list. TTS: *"Planning eight actions across bunq."*
- 0:22 — 🌹 sub-account tile animates in. Balance 0 / 500.
- 0:30 — Fund €500 fires. Webhook arrives ~2s later. Counter animates 0 → 500.
- 0:40 — Browser panel opens live. Playwright navigates mock restaurant. Claude Vision narrated on side.
- 1:00 — Restaurant booking confirmed. Payment fires. Balance 500 → 420.
- 1:08 — **⭐ Presenter phone buzzes** with bunq draft-payment. Presenter holds phone up, taps approve. Dashboard flashes green.
- 1:25 — Uber payment, bunqme-tab QR renders.
- 1:35 — Schedule + request-inquiry fire in quick succession.
- 1:42 — **⭐ Teammate phone buzzes** with Slack DM. Teammate holds phone up.
- 1:50 — Calendar invite pings. Teammate's Calendar app visible.
- 2:00 — TTS wrap-up: *"Mission complete. €455 deployed. Sara notified. Enjoy."*

Two phone-buzz beats + one browser-panel beat + one live balance animation = four "is this real?" moments.

## 10. Rubric scorecard (planned)

- **Impact 30%** — 3 real missions, real money moves. "Our product books your life and moves your money from one voice command."
- **Creativity 25%** — On-stage cascade of real API calls + real phones buzzing. Not a chatbot. Not a demo app.
- **Technical Execution 20%** — Clean tool-use loop + prompt caching + Playwright/Vision browser agent + webhook-driven live UI + rate-limit-aware queue + graceful fallbacks.
- **Integration 15%** — 10 distinct bunq endpoints touched across three missions (see §5).

## 11. Submission checklist

- [ ] DevPost project page with title "Mission Mode", 3-sentence tagline, 500-word description
- [ ] 60–90s demo video uploaded
- [ ] Public GitHub repo with working README + architecture diagram
- [ ] Slide deck: problem / demo / stack (3 slides)
- [ ] Submit form with: list of bunq endpoints used

## 12. Open defaults (change anytime)

- **ElevenLabs voice:** `Rachel` (warm female, English). Alternatives ready if you dislike it.
- **Dashboard palette:** bunq-brand cyan on dark. Alternatives: dark-terminal green-on-black; clean white minimal.
- **Claude model:** `claude-opus-4-7` for demo runs; `claude-sonnet-4-6` during dev to save cost.
- **Waveform colors / motion:** default subtle; can go wild if you prefer.

All of these are cheap to change — just tell me in-flight.
