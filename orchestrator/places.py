"""Google Places API (New) — Text Search.

Pulls real Amsterdam restaurants on demand. The browser-agent then navigates
a server-rendered booking page containing this real data. Falls back to a
hardcoded fixture if GOOGLE_PLACES_API_KEY is missing.

Requires:
  - GCP project with `places.googleapis.com` enabled
  - Billing enabled (free tier covers ~5k searches/month)
  - An API key (different from the OAuth client) restricted to Places API

Add to .env:
  GOOGLE_PLACES_API_KEY=AIzaSy...
"""

from __future__ import annotations

import os
from typing import Any

import httpx


PRICE_LEVEL_TO_EUR: dict[str, int] = {
    "PRICE_LEVEL_FREE": 0,
    "PRICE_LEVEL_INEXPENSIVE": 25,
    "PRICE_LEVEL_MODERATE": 55,
    "PRICE_LEVEL_EXPENSIVE": 85,
    "PRICE_LEVEL_VERY_EXPENSIVE": 130,
}

CUISINE_EMOJI: dict[str, str] = {
    "italian_restaurant": "🍕",
    "pizza_restaurant": "🍕",
    "japanese_restaurant": "🍣",
    "sushi_restaurant": "🍣",
    "ramen_restaurant": "🍜",
    "chinese_restaurant": "🥡",
    "indian_restaurant": "🍛",
    "thai_restaurant": "🌶️",
    "mexican_restaurant": "🌮",
    "french_restaurant": "🥖",
    "mediterranean_restaurant": "🥗",
    "steak_house": "🥩",
    "seafood_restaurant": "🦐",
    "vegan_restaurant": "🥗",
    "vegetarian_restaurant": "🥗",
    "cafe": "☕",
    "bakery": "🥐",
    "wine_bar": "🍷",
    "brewery": "🍺",
    "bar": "🍸",
}


HARDCODED_FIXTURE: list[dict[str, Any]] = [
    {"id": "klos",  "name": "Café de Klos",  "emoji": "🥩", "cuisine": "Dutch grill · cozy",                  "price": 85,  "meta": "Reguliersdwarsstraat · 4.6★"},
    {"id": "perla", "name": "La Perla",      "emoji": "🍕", "cuisine": "Italian · wood-fired pizza & rooftop", "price": 92,  "meta": "Jordaan · 4.7★"},
    {"id": "sora",  "name": "Sushi Sora",    "emoji": "🍣", "cuisine": "Japanese omakase · counter seats",     "price": 78,  "meta": "De Pijp · 4.5★"},
    {"id": "zaza",  "name": "Zaza Rooftop",  "emoji": "🍷", "cuisine": "Mediterranean · rooftop views",        "price": 110, "meta": "Westerpark · 4.4★"},
]


def _emoji_for(types: list[str]) -> str:
    for t in types or []:
        if t in CUISINE_EMOJI:
            return CUISINE_EMOJI[t]
    return "🍽️"


def _short_cuisine(types: list[str], summary: str | None) -> str:
    """Best-effort one-liner: prefer editorial summary; fallback to friendly type."""
    if summary:
        return summary[:80]
    for t in types or []:
        if t.endswith("_restaurant") and t != "restaurant":
            label = t.replace("_restaurant", "").replace("_", " ").title()
            return f"{label} · Amsterdam"
    return "Restaurant · Amsterdam"


def search_restaurants(
    query: str = "popular dinner restaurants Amsterdam",
    max_results: int = 4,
    timeout_s: float = 10.0,
) -> list[dict[str, Any]]:
    """Query Google Places (New). Returns 4 dicts in our internal shape, or
    the hardcoded fixture if the API key is missing or the call fails."""
    api_key = os.getenv("GOOGLE_PLACES_API_KEY", "").strip()
    if not api_key:
        return HARDCODED_FIXTURE[:max_results]

    field_mask = ",".join([
        "places.id",
        "places.displayName",
        "places.formattedAddress",
        "places.priceLevel",
        "places.rating",
        "places.userRatingCount",
        "places.types",
        "places.editorialSummary",
    ])
    try:
        r = httpx.post(
            "https://places.googleapis.com/v1/places:searchText",
            headers={
                "Content-Type": "application/json",
                "X-Goog-Api-Key": api_key,
                "X-Goog-FieldMask": field_mask,
            },
            json={
                "textQuery": query,
                "maxResultCount": max_results,
                "languageCode": "en",
            },
            timeout=timeout_s,
        )
        r.raise_for_status()
        data = r.json()
    except Exception as e:  # noqa: BLE001
        print(f"[places] error: {e!r} — falling back to hardcoded fixture")
        return HARDCODED_FIXTURE[:max_results]

    results: list[dict[str, Any]] = []
    for place in (data.get("places") or [])[:max_results]:
        name = (place.get("displayName") or {}).get("text", "Unknown")
        types = place.get("types") or []
        addr = place.get("formattedAddress", "Amsterdam")
        rating = place.get("rating")
        count = place.get("userRatingCount")
        price_level = place.get("priceLevel", "PRICE_LEVEL_MODERATE")
        editorial = (place.get("editorialSummary") or {}).get("text")

        meta_parts: list[str] = []
        # Keep the first line of the address (street/area).
        meta_parts.append(addr.split(",")[0])
        if rating is not None:
            stars = f"{rating:.1f}★"
            if count:
                stars += f" · {count} reviews"
            meta_parts.append(stars)

        results.append({
            "id": place.get("id", name.lower().replace(" ", "-")),
            "name": name,
            "emoji": _emoji_for(types),
            "cuisine": _short_cuisine(types, editorial),
            "price": PRICE_LEVEL_TO_EUR.get(price_level, 55),
            "meta": " · ".join(meta_parts),
        })

    if not results:
        return HARDCODED_FIXTURE[:max_results]
    return results
