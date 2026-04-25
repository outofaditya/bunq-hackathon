# Mission Mode

Voice-driven multi-modal AI agent that orchestrates real bunq sandbox API calls.
Built for the bunq Hackathon 7.0.

You speak. Claude plans. The dashboard renders the cascade in real time:
bunq payments, scheduled transfers, drafts that need your phone tap,
restaurant/hotel browser bookings, Slack pings, calendar events — all
narrated by an ElevenLabs voice. Optionally invokes **the Council**:
your bunq sub-accounts as voiced personas that argue about purchases.

---

## Quick start (clone → run, ~3 minutes)

```bash
git clone <repo-url> && cd bunq-hackathon
./start.sh
```

On first run, `start.sh` will:
1. Bootstrap a Python venv + install `requirements.txt`
2. Copy `.env.example` → `.env` and ask you to fill in the keys
3. `npm ci && npm run build` the React dashboard
4. Start the FastAPI server (and ngrok if installed)

Then it tells you what to do next: open the keys, paste them in, re-run.

After `.env` is filled, every subsequent `./start.sh` is one command — no rebuild,
no venv reactivation, no manual port wrangling.

Open http://localhost:8000 — tap the mic, speak a mission. That's it.

---

## What you need

| | Required for | Where |
|---|---|---|
| `BUNQ_API_KEY` | bunq sandbox auth | run `python 01_authentication.py` once, OR developer.bunq.com |
| `ANTHROPIC_API_KEY` | Claude (mission planner + persona archetype assignment) | console.anthropic.com → API Keys |
| `ELEVENLABS_API_KEY` | **TTS + STT — without this, audio is silent** | elevenlabs.io → Settings → API Keys |
| `ELEVENLABS_VOICE_ID` | Narrator voice. Rachel = `21m00Tcm4TlvDq8ikWAM` | leave the default unless you cloned a custom voice |
| `SLACK_WEBHOOK_URL` *(optional)* | Mission Slack pings | api.slack.com → Incoming Webhooks |
| `GOOGLE_PLACES_API_KEY` *(optional)* | Live restaurant data (falls back to fixture) | console.cloud.google.com → APIs |

The full list with comments is in [.env.example](.env.example). Copy → fill → re-run.

---

## Audio not working on a fresh clone?

Almost always one of these:

1. **`ELEVENLABS_API_KEY` is unset.** `curl localhost:8000/health` — if `audio_ready: false`, that's why. The dashboard works but plays no agent voice.
2. **Browser autoplay blocked.** Most browsers won't autoplay audio until the user has interacted with the page. The mic-tap counts as interaction; if you load the dashboard and never tap, sounds queue silently. Tap the mic once to unlock.
3. **HTTPS required for the mic.** `getUserMedia` only works on `localhost` or HTTPS origins. If you're hitting the server via a LAN IP (e.g. `http://192.168.1.5:8000`), the mic button will silently fail. Use `localhost` directly, or run through ngrok / a real HTTPS deploy.
4. **`dashboard-react/dist/` is stale or missing.** `start.sh` rebuilds it on first run; if you cloned and skipped the script, run `cd dashboard-react && npm ci && npm run build` manually.

The server prints a banner at startup listing every missing env var:

```
[server] WARNING — missing env vars:
           ELEVENLABS_API_KEY     (TTS + STT (audio in/out))
           ELEVENLABS_VOICE_ID    (TTS narrator voice)
           Copy .env.example → .env and fill these in.
```

---

## Repository layout

```
bunq-hackathon/
├── README.md                ← you are here
├── DEPLOY.md                ← AWS App Runner deploy guide
├── .env.example             ← canonical env var list
├── start.sh                 ← one-command launcher
├── stop.sh                  ← kills server + ngrok
│
├── orchestrator/            ← FastAPI server + agent loop
│   ├── server.py            ← HTTP routes, SSE bus, mission kickoff
│   ├── agent_loop.py        ← Claude tool-use cascade
│   ├── bunq_tools.py        ← bunq API wrappers exposed as tools
│   ├── browser_agent.py     ← Playwright + Claude Vision booking flows
│   ├── personas.py          ← The Council — sub-accounts as voiced personas
│   ├── tts.py / stt.py      ← ElevenLabs voice in/out
│   ├── tool_catalog.py      ← Anthropic tool schemas
│   └── missions/            ← weekend, payday, travel, council
│
├── dashboard-react/         ← React + Vite + Tailwind v4 + ShadCN
│   └── src/                 ← App.tsx, components/, hooks/
│
├── mock_sites/              ← fake restaurant / hotel / subs sites the browser-agent navigates
├── assets/                  ← pre-recorded mission voice clips (mp3, tracked)
│   └── tts_cache/           ← runtime TTS output (gitignored)
│
├── deploy/                  ← AWS deployment scripts
│   └── build-and-push.sh    ← ECR build + push
├── Dockerfile               ← multi-stage: node frontend → playwright/python runtime
├── .dockerignore
├── apprunner.yaml           ← App Runner source-mode fallback
│
├── bunq_client.py           ← shared bunq auth client (RSA + signed requests)
├── 01_authentication.py …   ← bunq tutorial scripts (also work standalone)
└── tests/                   ← pytest smoke tests against the sandbox
```

---

## Common operations

```bash
# Run dev (default — inline output, ngrok if available, auto-open browser)
./start.sh

# No ngrok (polling-only; bunq webhooks won't reach you)
./start.sh --no-ngrok

# Stop everything
./stop.sh

# Smoke tests against the sandbox (no LLM cost)
pytest tests/ -q

# Reset demo personas (drains + cancels any 🚨/🛵/🌹 sub-accounts tagged · MM)
curl -X POST http://localhost:8000/admin/cleanup-demo-subs

# Deploy to AWS (see DEPLOY.md for the full walkthrough)
AWS_REGION=eu-central-1 ./deploy/build-and-push.sh
```

---

## bunq tutorial scripts (standalone)

The `0X_*.py` scripts work without the full Mission Mode stack — they're a
clean walk through the bunq API. Run them in order if you're new to bunq:

```bash
python 01_authentication.py             # creates a sandbox user + API key, runs the auth flow
python 02_create_monetary_account.py    # create + list bank accounts
python 03_make_payment.py               # request test money + send a payment
python 04_request_money.py              # create a RequestInquiry
python 05_create_bunqme_link.py         # generate a shareable payment link
python 06_list_transactions.py          # paginate payment history
python 07_setup_callbacks.py            # webhook setup
```

Session is cached in `bunq_context.json` so you don't re-auth each time.

---

## bunq sandbox rate limits

| Method | Limit |
|--------|-------|
| GET | 3 / 3 s |
| POST | 5 / 3 s |
| PUT | 2 / 3 s |
| `/session-server` | 1 / 30 s |

Mission Mode paces itself, but if you fire many missions back-to-back you'll hit 429.
`./stop.sh` between heavy demo runs.

---

## Resources

- [bunq API documentation](https://doc.bunq.com)
- [bunq Developer portal](https://developer.bunq.com)
- [DEPLOY.md](DEPLOY.md) — production deployment to AWS
- [docs/API_REFERENCE.md](docs/API_REFERENCE.md) — bunq endpoint cheat sheet
- [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) — common errors and fixes
