"""ElevenLabs voice proxies — Scribe STT + streaming TTS.

Kept as thin proxies so the browser never sees the ElevenLabs API key.
"""
from __future__ import annotations

import os
from typing import AsyncIterator

import httpx

ELEVENLABS_BASE = "https://api.elevenlabs.io/v1"
TTS_MODEL = "eleven_turbo_v2_5"
STT_MODEL = "scribe_v1"


def _key() -> str:
    return os.environ["ELEVENLABS_API_KEY"]


def _voice() -> str:
    return os.environ.get("ELEVENLABS_VOICE_ID", "cgSgspJ2msm6clMCkdW9")


async def stream_tts(text: str) -> AsyncIterator[bytes]:
    """Stream MP3 audio chunks from ElevenLabs for a given phrase."""
    url = f"{ELEVENLABS_BASE}/text-to-speech/{_voice()}/stream"
    headers = {
        "xi-api-key": _key(),
        "Content-Type": "application/json",
        "Accept": "audio/mpeg",
    }
    body = {
        "text": text,
        "model_id": TTS_MODEL,
        "voice_settings": {"stability": 0.45, "similarity_boost": 0.75},
    }
    async with httpx.AsyncClient(timeout=30) as c:
        async with c.stream("POST", url, headers=headers, json=body) as r:
            r.raise_for_status()
            async for chunk in r.aiter_bytes():
                yield chunk


async def transcribe(audio_bytes: bytes, content_type: str) -> str:
    """Return the text transcript for a single audio blob (webm/wav/mp3)."""
    url = f"{ELEVENLABS_BASE}/speech-to-text"
    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.post(
            url,
            headers={"xi-api-key": _key()},
            data={"model_id": STT_MODEL},
            files={"file": ("audio", audio_bytes, content_type)},
        )
        r.raise_for_status()
        j = r.json()
        return j.get("text", "").strip()
