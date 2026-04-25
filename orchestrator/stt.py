"""ElevenLabs Scribe — speech-to-text wrapper.

Used to transcribe the pre-recorded mission command before kicking off the
agent loop. The transcript becomes the user_prompt for Claude.
"""

from __future__ import annotations

import os
from pathlib import Path

import httpx


def transcribe_file(file_path: Path, language: str = "eng", model_id: str = "scribe_v1") -> str:
    """Transcribe an audio file via ElevenLabs Scribe and return the text."""
    api_key = os.getenv("ELEVENLABS_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("ELEVENLABS_API_KEY missing")

    file_path = Path(file_path)
    if not file_path.exists():
        raise FileNotFoundError(file_path)

    with file_path.open("rb") as f:
        files = {"file": (file_path.name, f, "audio/mpeg")}
        data = {"model_id": model_id, "language_code": language}
        r = httpx.post(
            "https://api.elevenlabs.io/v1/speech-to-text",
            headers={"xi-api-key": api_key},
            files=files,
            data=data,
            timeout=30.0,
        )
    r.raise_for_status()
    return r.json().get("text", "").strip()


def transcribe_bytes(audio_bytes: bytes, filename: str = "audio.webm", language: str = "eng",
                    model_id: str = "scribe_v1") -> str:
    """Transcribe in-memory audio bytes (for browser-uploaded recordings)."""
    api_key = os.getenv("ELEVENLABS_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("ELEVENLABS_API_KEY missing")
    files = {"file": (filename, audio_bytes, "audio/webm")}
    data = {"model_id": model_id, "language_code": language}
    r = httpx.post(
        "https://api.elevenlabs.io/v1/speech-to-text",
        headers={"xi-api-key": api_key},
        files=files,
        data=data,
        timeout=30.0,
    )
    r.raise_for_status()
    return r.json().get("text", "").strip()
