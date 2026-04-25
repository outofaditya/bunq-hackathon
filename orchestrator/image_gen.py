"""OpenRouter image generation for Trip Agent option cards.

Generates a cartoonish illustration per package option via the
`bytedance-seed/seedream-4.5` model.

API contract used:
- POST https://openrouter.ai/api/v1/chat/completions
- modalities=["image"]  (Seedream is image-only output)
- Response: choices[0].message.images[0].image_url.url  (base64 data URL,
  shape `data:image/png;base64,…`).

Pricing: $0.04 per generated image. Three options ⇒ ~$0.12 per
present_options call. Costs are silently swallowed on failure — the demo
should never break because an image didn't render.
"""
from __future__ import annotations

import os
from typing import Any

import httpx

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
MODEL = "bytedance-seed/seedream-4.5"

# Wide aspect for postcard feel on landscape cards. 16:9 felt too letterbox in
# the dashboard, 4:3 is closer to a polaroid.
DEFAULT_ASPECT = "4:3"
DEFAULT_SIZE = "1K"
TIMEOUT_S = 60.0


def _api_key() -> str:
    key = os.getenv("OPENROUTER_API_KEY", "").strip()
    if not key:
        raise RuntimeError("OPENROUTER_API_KEY missing in environment")
    return key


# Style suffix appended to every prompt. Word order matters in Seedream — keep
# the subject leading and the style/composition trailing. Phrasing tuned from
# the fal.ai + ByteDance prompting guides: short style words, friendly tone,
# explicit "no realism" + "no text" to prevent watermark-y captions.
CARTOON_STYLE = (
    "Cartoon illustration style, flat vibrant colors, bold clean outlines, "
    "soft pastel shading, friendly inviting mood, gentle warm lighting, "
    "stylized characters and architecture, postcard aesthetic, slightly "
    "rounded shapes, no photorealism, no text, no logos, no watermarks."
)


def build_prompt(option: dict[str, Any]) -> str:
    """Compose a Seedream prompt from one PackageOption.

    Subject-first (hotel + restaurant + activity in one short scene), then
    style suffix. The model handles compound subjects well as long as they're
    in one sentence with concrete nouns.
    """
    hotel = option.get("hotel", "a cozy hotel")
    restaurant = option.get("restaurant", "a romantic restaurant")
    extra = option.get("extra", "a local experience")
    notes = option.get("notes", "")

    subject = (
        f"A weekend getaway scene featuring {hotel}, dinner at {restaurant}, "
        f"and {extra}"
    )
    if notes:
        subject = f"{subject}. {notes}"
    subject += "."

    return f"{subject} {CARTOON_STYLE}"


async def generate_image(
    prompt: str,
    *,
    aspect_ratio: str = DEFAULT_ASPECT,
    image_size: str = DEFAULT_SIZE,
    timeout_s: float = TIMEOUT_S,
) -> str | None:
    """Generate one image, return a base64 data URL or None on failure.

    Returns the FIRST image in the response. Errors are caught and logged —
    the caller renders the option card without an image rather than failing
    the whole demo.
    """
    payload = {
        "model": MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "modalities": ["image"],
        "image_config": {
            "aspect_ratio": aspect_ratio,
            "image_size": image_size,
        },
    }
    headers = {
        "Authorization": f"Bearer {_api_key()}",
        "Content-Type": "application/json",
        # Optional but recommended by OpenRouter to attribute the app on
        # leaderboards. Both are safe to expose.
        "HTTP-Referer": "https://github.com/outofaditya/bunq-hackathon",
        "X-Title": "Trip Agent - bunq Hackathon 7.0",
    }

    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            r = await client.post(OPENROUTER_URL, json=payload, headers=headers)
            if r.status_code != 200:
                print(
                    f"[image_gen] {r.status_code} {r.text[:240]}",
                    flush=True,
                )
                return None
            data = r.json()
    except httpx.HTTPError as e:
        print(f"[image_gen] http error: {e}", flush=True)
        return None
    except Exception as e:  # noqa: BLE001 — never break the demo
        print(f"[image_gen] unexpected error: {e}", flush=True)
        return None

    try:
        msg = data["choices"][0]["message"]
        images = msg.get("images") or []
        if not images:
            print(
                f"[image_gen] no images in response: {str(data)[:240]}",
                flush=True,
            )
            return None
        url = images[0]["image_url"]["url"]
        if not isinstance(url, str) or not url:
            return None
        return url
    except (KeyError, IndexError, TypeError) as e:
        print(f"[image_gen] parse error: {e}; payload: {str(data)[:240]}", flush=True)
        return None


async def generate_for_option(option: dict[str, Any]) -> str | None:
    """Convenience wrapper that builds the prompt + calls generate_image."""
    return await generate_image(build_prompt(option))
