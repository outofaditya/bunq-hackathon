"""ElevenLabs TTS — synthesize narration on demand, *no caching*.

Every call hits ElevenLabs fresh and writes a uniquely-named MP3 to
`assets/tts_cache/`. The directory acts as a short-lived runtime store
served by /tts/<filename>. Files older than TTL_SECONDS are pruned
opportunistically on each synthesis so the disk doesn't bloat.

The voice settings are jittered slightly per call so identical text reads
with slightly different prosody — keeps the demo from sounding canned.
"""

from __future__ import annotations

import os
import random
import secrets
import time
from pathlib import Path

import httpx


PROJECT_ROOT = Path(__file__).resolve().parent.parent
RUNTIME_DIR = PROJECT_ROOT / "assets" / "tts_cache"  # path retained for server compat
RUNTIME_DIR.mkdir(parents=True, exist_ok=True)

# `eleven_multilingual_v2` is ElevenLabs' most natural-sounding production model
# — slightly higher latency than turbo, much warmer prosody for narration.
DEFAULT_MODEL = "eleven_multilingual_v2"
TTL_SECONDS = 5 * 60  # prune audio files older than 5 minutes


def _prune_old() -> None:
    cutoff = time.time() - TTL_SECONDS
    try:
        for f in RUNTIME_DIR.iterdir():
            if not f.is_file():
                continue
            if f.suffix != ".mp3":
                continue
            if f.stat().st_mtime < cutoff:
                try:
                    f.unlink()
                except OSError:
                    pass
    except Exception:  # noqa: BLE001
        pass


def synthesize_narration(
    text: str,
    voice_id: str | None = None,
    model_id: str = DEFAULT_MODEL,
) -> str:
    """Synthesize `text` to a fresh MP3 file. Returns the filename only."""
    voice_id = voice_id or os.getenv("ELEVENLABS_VOICE_ID", "").strip()
    if not voice_id:
        raise RuntimeError("ELEVENLABS_VOICE_ID missing")
    api_key = os.getenv("ELEVENLABS_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("ELEVENLABS_API_KEY missing")

    _prune_old()

    out = RUNTIME_DIR / f"{int(time.time() * 1000):x}_{secrets.token_hex(3)}.mp3"

    # Tuned for the most natural Rachel-style narration:
    # - low-ish stability gives organic pitch variation (not robotic-flat).
    # - high similarity_boost keeps it recognisably the chosen voice.
    # - style ~0.45 introduces expressiveness without sounding theatrical.
    # Per-call jitter so identical phrases never sound karaoke-perfect identical.
    rng = random.Random(out.name)
    settings = {
        "stability":        round(rng.uniform(0.30, 0.42), 2),
        "similarity_boost": round(rng.uniform(0.82, 0.92), 2),
        "style":            round(rng.uniform(0.35, 0.55), 2),
        "use_speaker_boost": True,
    }

    r = httpx.post(
        f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}",
        headers={
            "xi-api-key": api_key,
            "Content-Type": "application/json",
            "Accept": "audio/mpeg",
        },
        json={
            "text": text,
            "model_id": model_id,
            "voice_settings": settings,
            "output_format": "mp3_44100_128",
        },
        timeout=20.0,
    )
    r.raise_for_status()
    out.write_bytes(r.content)
    return out.name


def synthesize_to_path(
    text: str,
    out_path: Path,
    voice_id: str,
    model_id: str = DEFAULT_MODEL,
) -> Path:
    """One-off helper: synthesize to a specific path (used by generate_recordings)."""
    api_key = os.getenv("ELEVENLABS_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("ELEVENLABS_API_KEY missing")
    r = httpx.post(
        f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}",
        headers={
            "xi-api-key": api_key,
            "Content-Type": "application/json",
            "Accept": "audio/mpeg",
        },
        json={
            "text": text,
            "model_id": model_id,
            "voice_settings": {
                "stability": 0.5,
                "similarity_boost": 0.7,
                "style": 0.15,
                "use_speaker_boost": True,
            },
            "output_format": "mp3_44100_128",
        },
        timeout=30.0,
    )
    r.raise_for_status()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(r.content)
    return out_path
