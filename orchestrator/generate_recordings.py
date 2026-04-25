"""One-off: synthesize the pre-recorded mission commands using ElevenLabs.

Run once after Phase 0 setup:
    python -m orchestrator.generate_recordings

Generates assets/recorded_voice_<mission>.mp3 with a different voice from
the agent (so the user voice + agent voice are visibly distinct on stage).
"""

from __future__ import annotations

import sys
from pathlib import Path

from dotenv import load_dotenv

from .tts import synthesize_to_path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
ASSETS_DIR = PROJECT_ROOT / "assets"

# Roger — Laid-Back, Casual, Resonant. Distinct from the agent (Sarah).
USER_VOICE_ID = "CwhRBWXzGAHq8TQ4Fs17"

RECORDINGS = {
    "weekend": (
        "Five hundred euros. Best weekend for me and Sara. She's been stressed all month."
    ),
    # Future-proofed — Phase 7 missions can add more here.
    "payday": "Payday, distribute. Two thousand euros, the usual split.",
    "travel": "I'm flying to Tokyo Friday. Three hundred euros budget. Freeze my home card.",
}


def main() -> int:
    load_dotenv(override=True)
    ASSETS_DIR.mkdir(exist_ok=True)
    for name, text in RECORDINGS.items():
        out = ASSETS_DIR / f"recorded_voice_{name}.mp3"
        if out.exists() and out.stat().st_size > 1024:
            print(f"  skip {out.name} (already exists)")
            continue
        print(f"  synth {out.name} …", end="", flush=True)
        synthesize_to_path(text, out, voice_id=USER_VOICE_ID)
        print(f" {out.stat().st_size // 1024} KB")
    print("\nDone. Pre-recorded mission commands ready.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
