"""Subscription plans data — used by the Payday browser-agent flow.

Returns a list of plans for a category, with the order randomised on each
call so the browser agent has to read prices off the screenshot rather than
memorise positions. Prices and provider mixes are fixed (real-world plans
as of early 2026), but rotation prevents brittle pattern-matching.
"""

from __future__ import annotations

import random
from typing import Any


_PLANS_BY_CATEGORY: dict[str, list[dict[str, Any]]] = {
    "streaming": [
        {"id": "spotify-prem",  "provider": "Spotify",   "plan": "Premium Individual",     "monthly": 11.99, "features": "Ad-free music · offline · podcasts"},
        {"id": "netflix-std",   "provider": "Netflix",   "plan": "Standard with ads",      "monthly": 5.99,  "features": "Most movies & series · 1080p · 2 devices"},
        {"id": "netflix-prem",  "provider": "Netflix",   "plan": "Premium",                "monthly": 18.99, "features": "4K · 4 devices · downloads"},
        {"id": "disney-stand",  "provider": "Disney+",   "plan": "Standard",               "monthly": 8.99,  "features": "Disney/Marvel/Star Wars · 1080p"},
        {"id": "ytmusic",       "provider": "YouTube",   "plan": "Music Premium",          "monthly": 9.99,  "features": "Music + offline · ad-free YT"},
        {"id": "appletv-std",   "provider": "Apple TV+", "plan": "Standard",               "monthly": 9.99,  "features": "Apple originals · 4K HDR"},
    ],
    "gym": [
        {"id": "basicfit-std",  "provider": "Basic-Fit", "plan": "Comfort",                "monthly": 24.99, "features": "All clubs · classes · app"},
        {"id": "gymbox-pro",    "provider": "GymBox",    "plan": "All-Access",             "monthly": 39.00, "features": "Sauna · 24/7 access · trainer"},
        {"id": "anytime-rg",    "provider": "Anytime",   "plan": "Regular",                "monthly": 32.50, "features": "Worldwide · 24/7"},
        {"id": "f45-base",      "provider": "F45",       "plan": "Foundation",             "monthly": 49.00, "features": "HIIT classes · 8 weeks plan"},
    ],
    "internet": [
        {"id": "ziggo-100",     "provider": "Ziggo",     "plan": "Internet 100",           "monthly": 39.00, "features": "100/10 Mb · Wi-Fi 6 router"},
        {"id": "kpn-200",       "provider": "KPN",       "plan": "Glasvezel 200",          "monthly": 45.00, "features": "200/200 Mb · KPN-iTV included"},
        {"id": "odido-500",     "provider": "Odido",     "plan": "Klap 500",               "monthly": 52.00, "features": "500/200 Mb · 5G hotspot"},
        {"id": "tweak-glas",    "provider": "Tweak",     "plan": "Glasvezel 1G",           "monthly": 47.50, "features": "1Gb fiber · no contract"},
    ],
    "mobile": [
        {"id": "lebara-10",     "provider": "Lebara",    "plan": "10 GB Sim Only",         "monthly": 12.50, "features": "EU roaming · 5G"},
        {"id": "vodafone-20",   "provider": "Vodafone",  "plan": "Red Unlimited 20",       "monthly": 24.00, "features": "Unlimited data · Disney+ included"},
        {"id": "simyo-15",      "provider": "Simyo",     "plan": "15 GB Vrijheid",         "monthly": 15.00, "features": "EU roaming · monthly cancel"},
    ],
}


def list_plans(category: str, limit: int = 6) -> list[dict[str, Any]]:
    """Return shuffled plans for a category. Falls back to streaming if category unknown."""
    plans = list(_PLANS_BY_CATEGORY.get(category.lower(), _PLANS_BY_CATEGORY["streaming"]))
    random.shuffle(plans)
    return plans[:limit]
