"""Phase 0 env check — verifies every third-party credential works.

Exit green on all six checks before Phase 1. Each probe uses the cheapest
non-destructive call available for the respective service.

Run: python -m orchestrator.phase0_env_check
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


RED = "\033[31m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RESET = "\033[0m"


def ok(msg: str) -> None:
    print(f"  {GREEN}✓{RESET} {msg}")


def fail(msg: str) -> None:
    print(f"  {RED}✗{RESET} {msg}")


def warn(msg: str) -> None:
    print(f"  {YELLOW}!{RESET} {msg}")


def check_bunq() -> bool:
    print("1. bunq sandbox")
    key = os.getenv("BUNQ_API_KEY", "").strip()
    if not key:
        fail("BUNQ_API_KEY missing")
        return False
    try:
        # Use the already-verified BunqClient; exercises the full 3-step auth.
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from bunq_client import BunqClient

        client = BunqClient(api_key=key, sandbox=True)
        client.authenticate()
        ok(f"BUNQ_API_KEY valid — user_id={client.user_id}")
        return True
    except Exception as e:  # noqa: BLE001
        fail(f"bunq auth failed: {e}")
        return False


def check_anthropic() -> bool:
    model = os.getenv("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001").strip()
    print(f"2. Anthropic ({model})")
    key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not key:
        fail("ANTHROPIC_API_KEY missing")
        return False
    try:
        import anthropic

        client = anthropic.Anthropic(api_key=key)
        resp = client.messages.create(
            model=model,
            max_tokens=16,
            messages=[{"role": "user", "content": "Say OK."}],
        )
        ok(f"Claude responded: {resp.content[0].text.strip()[:40]}")
        return True
    except Exception as e:  # noqa: BLE001
        fail(f"Anthropic call failed: {e}")
        return False


def check_elevenlabs() -> bool:
    print("3. ElevenLabs (TTS + STT via Scribe)")
    key = os.getenv("ELEVENLABS_API_KEY", "").strip()
    voice_id = os.getenv("ELEVENLABS_VOICE_ID", "").strip()
    if not key:
        fail("ELEVENLABS_API_KEY missing")
        return False
    if not voice_id:
        warn("ELEVENLABS_VOICE_ID missing — run with `--list-voices` to pick one")
    try:
        import httpx

        r = httpx.get(
            "https://api.elevenlabs.io/v1/voices",
            headers={"xi-api-key": key},
            timeout=10,
        )
        r.raise_for_status()
        voices = r.json().get("voices", [])
        ok(f"ElevenLabs key valid — {len(voices)} voices available")
        if voice_id:
            if any(v.get("voice_id") == voice_id for v in voices):
                ok(f"voice_id {voice_id} found")
            else:
                warn(f"voice_id {voice_id} NOT found in your account voices")
        return True
    except Exception as e:  # noqa: BLE001
        fail(f"ElevenLabs call failed: {e}")
        return False


def check_slack() -> bool:
    print("4. Slack (incoming webhook)")
    url = os.getenv("SLACK_WEBHOOK_URL", "").strip()
    if not url:
        fail("SLACK_WEBHOOK_URL missing")
        return False
    try:
        import httpx

        r = httpx.post(
            url,
            json={"text": "phase0 env-check ping (ignore)"},
            timeout=10,
        )
        if r.status_code == 200 and r.text == "ok":
            ok("Slack webhook delivered")
            return True
        fail(f"Slack webhook returned {r.status_code} {r.text[:80]}")
        return False
    except Exception as e:  # noqa: BLE001
        fail(f"Slack call failed: {e}")
        return False


def check_google() -> bool:
    print("5. Google Calendar OAuth")
    client_json = os.getenv("GOOGLE_OAUTH_CLIENT_JSON", "./google_oauth_client.json").strip()
    if not Path(client_json).exists():
        fail(f"{client_json} missing — download desktop-app credentials from GCP")
        return False
    try:
        data = json.loads(Path(client_json).read_text())
        # Accept either 'installed' (desktop) or 'web' shapes.
        if "installed" in data:
            ok(f"client_json looks valid (desktop app): {data['installed']['client_id'][:20]}...")
        elif "web" in data:
            ok(f"client_json looks valid (web app): {data['web']['client_id'][:20]}...")
        else:
            warn("client_json present but unfamiliar shape")
        warn("OAuth consent not yet exchanged — that happens in Phase 6 on first run")
        return True
    except Exception as e:  # noqa: BLE001
        fail(f"client_json parse failed: {e}")
        return False


def check_ngrok() -> bool:
    print("6. ngrok (public tunnel)")
    import shutil

    if shutil.which("ngrok") is None:
        fail("ngrok not in PATH — install via `brew install ngrok/ngrok/ngrok`")
        return False
    ok("ngrok binary present")
    warn("tunnel itself is started in Phase 2 — PUBLIC_BASE_URL will be set then")
    return True


def main() -> int:
    print("\n=== Mission Mode — Phase 0 env check ===\n")
    results = {
        "bunq": check_bunq(),
        "anthropic": check_anthropic(),
        "elevenlabs": check_elevenlabs(),
        "slack": check_slack(),
        "google": check_google(),
        "ngrok": check_ngrok(),
    }
    print()
    green = sum(1 for v in results.values() if v)
    total = len(results)
    print(f"  {green}/{total} checks passed.")
    if green == total:
        print(f"\n{GREEN}Phase 0 complete — ready for Phase 1.{RESET}")
        return 0
    print(f"\n{RED}Phase 0 incomplete — fix the red rows above.{RESET}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
