# Deploying Mission Mode

Two paths. Pick one.

|  | **Image-based (recommended)** | **Source-based** |
|---|---|---|
| | You build & push a Docker image to **ECR**, App Runner pulls it. | App Runner clones GitHub, builds Python via `apprunner.yaml`. |
| | ✅ Multi-stage, fast cold start, full control over Chromium install | ❌ Slower deploys (no caching) |
| | ✅ Works in any region | ⚠️ Source mode + Playwright is fiddly |
| | ❌ One extra command (`./deploy/build-and-push.sh`) | ✅ Push to git → auto-deploy |

The rest of this doc covers the **image path**. `apprunner.yaml` at the repo root is provided as a fallback for source mode.

---

## Prerequisites (one-time)

1. **AWS account** with billing enabled
2. **AWS CLI v2** + `aws configure` (or `AWS_PROFILE` exported)
3. **Docker** running locally
4. **Region** picked — `eu-central-1` (Frankfurt) is closest to bunq sandbox; `us-east-1` if you have other AWS resources there

```bash
aws sts get-caller-identity   # should print your account info
docker info                   # should not error
```

---

## Step 1 · Build & push the image to ECR

```bash
AWS_REGION=eu-central-1 ./deploy/build-and-push.sh
```

What it does:
- Creates the `mission-mode` ECR repo if missing
- Logs Docker in via short-lived ECR token
- Builds for `linux/amd64` (App Runner architecture)
- Pushes two tags: `<git-sha>` and `latest`

Output ends with the full image URI:

```
123456789012.dkr.ecr.eu-central-1.amazonaws.com/mission-mode:latest
```

---

## Step 2 · Create the App Runner service

### A — From the AWS console (easiest first time)

1. **App Runner** → **Create service**
2. **Source**: Container registry → Amazon ECR → browse to the image you just pushed
3. **Deployment trigger**: *Manual*
4. **Service settings**:
   - Name: `mission-mode`
   - Virtual CPU & memory: **1 vCPU, 2 GB** (minimum for Chromium + python + node)
   - Port: **8000**
   - Health-check path: `/health`
5. **Environment variables** — see Step 3
6. **Auto-scaling**: 1 min, 1 max — single-instance is fine for a hackathon
7. **Create**

App Runner provisions in ~3-5 minutes. You'll get a public HTTPS URL like:
`https://abcd1234.eu-central-1.awsapprunner.com`

### B — From the CLI (once you've done it once)

```bash
aws apprunner create-service \
  --region eu-central-1 \
  --service-name mission-mode \
  --source-configuration "ImageRepository={ImageIdentifier=<account>.dkr.ecr.eu-central-1.amazonaws.com/mission-mode:latest,ImageConfiguration={Port=8000},ImageRepositoryType=ECR}" \
  --instance-configuration "Cpu=1024,Memory=2048" \
  --health-check-configuration "Protocol=HTTP,Path=/health,Interval=30,Timeout=5,HealthyThreshold=1,UnhealthyThreshold=3"
```

---

## Step 3 · Set environment variables

App Runner → your service → **Configuration** → **Edit configuration** → **Environment variables**.

Required (the same ones in your local `.env` — see `.env.example` for the canonical list):

| Name | Where it comes from |
|---|---|
| `BUNQ_API_KEY` | bunq sandbox key |
| `ANTHROPIC_API_KEY` | console.anthropic.com |
| `ANTHROPIC_MODEL` | e.g. `claude-haiku-4-5-20251001` |
| `ELEVENLABS_API_KEY` | elevenlabs.io account |
| `ELEVENLABS_VOICE_ID` | e.g. Rachel = `21m00Tcm4TlvDq8ikWAM` |

Optional:

| Name | Notes |
|---|---|
| `SLACK_WEBHOOK_URL` | api.slack.com → your app → Incoming Webhooks |
| `GOOGLE_PLACES_API_KEY` | live restaurant data; falls back to fixture if absent |
| `PUBLIC_BASE_URL` | **set this AFTER Step 4** — the App Runner URL (no trailing slash) |
| `PYTHONUNBUFFERED` | `1` (logs stream live to CloudWatch) |

For Google Calendar OAuth: skip in the cloud demo, or run the OAuth dance locally and copy `~/.bunq-hackathon/google_token.json` into AWS Secrets Manager.

---

## Step 4 · Tell bunq about the public URL

Once App Runner has a URL:

1. Set `PUBLIC_BASE_URL` to that exact value in App Runner config
2. App Runner redeploys; on boot the server registers `<URL>/bunq-webhook` as the bunq callback target
3. Verify: `curl https://<url>/health` → JSON with `"public_url": "https://…"` and `"audio_ready": true`

ngrok is no longer needed in production.

---

## Iterating

```bash
# Build + push the new image (tagged with git SHA)
./deploy/build-and-push.sh

# Tell App Runner to pull the latest tag
aws apprunner start-deployment \
  --region eu-central-1 \
  --service-arn arn:aws:apprunner:eu-central-1:<account>:service/mission-mode/<id>
```

Or in the console: your service → **Deploy**.

---

## Cost

| Resource | Approx |
|---|---|
| App Runner (1 vCPU / 2 GB / always-on) | ~$25/mo |
| ECR storage | ~$0.10/GB-mo (image is ~600 MB) |
| Data transfer | negligible at hackathon scale |
| **Pause** when not demoing: `aws apprunner pause-service --service-arn …` — drops to ~$0/mo |

For zero-cost demos, run from your laptop with `./start.sh` and ngrok.

---

## Troubleshooting

**Service fails health check on first deploy** — open CloudWatch Logs (auto-created by App Runner). Common causes:
- `[server] WARNING — missing env vars: …` line in the log → fix Step 3.
- `BUNQ_API_KEY` invalid → bunq returns 401 on the auth flow.

**`/health` returns `audio_ready: false`** — `ELEVENLABS_API_KEY` or `ELEVENLABS_VOICE_ID` is unset. The dashboard works but plays no audio.

**bunq webhook never arrives** — confirm `PUBLIC_BASE_URL` is set and re-deploy. `curl https://<url>/health` and look at `public_url` in the response. If `null`, the env var isn't reaching the container.

**Mic doesn't work in browser** — browsers require HTTPS for `getUserMedia`. App Runner is HTTPS-only, so this should always work. If you embed the dashboard somewhere else, ensure HTTPS.

**Playwright crashes** — increase memory to 4 GB in App Runner's Instance Configuration.
