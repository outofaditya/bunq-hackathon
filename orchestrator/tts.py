"""ElevenLabs TTS — synthesize narration on demand, cache to disk.

Uses HTTPS directly (no SDK) for tighter control over latency and caching.
Cached files live at `assets/tts_cache/<sha1>.mp3` so repeat phrases
(e.g. "Mission complete") play instantly with zero API spend.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import httpx


PROJECT_ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = PROJECT_ROOT / "assets" / "tts_cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_MODEL = "eleven_turbo_v2_5"


def _cache_path(text: str, voice_id: str) -> Path:
    digest = hashlib.sha1(f"{voice_id}::{text}".encode()).hexdigest()[:16]
    return CACHE_DIR / f"{digest}.mp3"


def synthesize_narration(
    text: str,
    voice_id: str | None = None,
    model_id: str = DEFAULT_MODEL,
) -> str:
    """Synthesize `text` to MP3, cache to disk, return the cache filename (no path).

    The server's /tts/<filename> endpoint serves it back to the browser.
    """
    voice_id = voice_id or os.getenv("ELEVENLABS_VOICE_ID", "").strip()
    if not voice_id:
        raise RuntimeError("ELEVENLABS_VOICE_ID missing")
    api_key = os.getenv("ELEVENLABS_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("ELEVENLABS_API_KEY missing")

    out = _cache_path(text, voice_id)
    if out.exists() and out.stat().st_size > 256:
        return out.name

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
                "stability": 0.45,
                "similarity_boost": 0.75,
                "style": 0.2,
                "use_speaker_boost": True,
            },
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
