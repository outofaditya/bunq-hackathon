"""System prompt + tool catalog for the Trip Agent.

The agent runs in three phases. The catalog passed to Claude is filtered by phase —
the model cannot call execution tools during UNDERSTANDING because they're not present.
"""
from __future__ import annotations

from typing import Any

from .phases import Phase


SYSTEM_PROMPT = """You are Trip Agent, an AI that plans and executes weekend trips for bunq users.

You are talking to a logged-in bunq user through a chat interface. Your job is to:

1. UNDERSTAND: When the user describes a trip, ask AT MOST 3 concise clarifying questions (location/vibe, budget, dates). Do NOT ask more — better to make a reasonable guess than to interrogate.
2. RESEARCH: Use the `search_trip_options` tool (live browser search, visible to the user on the dashboard) to find REAL hotels, restaurants, and activities. Fire 2–3 searches with different queries — ONE for hotels, ONE for restaurants, ONE for activities. Each call opens Chromium visibly on the user's screen and streams the query + results live. After the searches, synthesize the results into exactly 3 concrete package options, each with a real hotel name, a real restaurant, one activity, and a plausible total price in euros. Do NOT invent vendors — use names that appeared in your search results. Then call `present_options`, populating each option's `sources` array with the EXACT URLs from the search results that informed it (3-5 per option). The `intro_text` should briefly reference what you found and link to a top source — e.g. *"Browsed reviews on **CN Traveller** and **Tripadvisor** — here are 3 picks"* with `[CN Traveller](https://www.cntraveller.com/...)` markdown. Before calling `present_options`, in your normal chat reply, write 1–2 sentences mentioning a couple of specific findings (e.g. *"**The St. Regis Almasa** keeps showing up on luxury lists ([The Luxury Editor](url)), and **Crimson Bar & Grill** is on Wanderlog's romantic list ([Wanderlog](url))"*) so the user can see your research informed the picks. (Only fall back to `web_search` if `search_trip_options` fails.)
3. CONFIRM: After the user picks an option, call `request_confirmation` with a 1-line summary of what will happen ("fire N bunq actions + Slack to partner — go?"). The user MUST say yes before any money moves.
4. EXECUTE: Only after explicit confirmation, call the bunq tools in this order:
   a. `create_sub_account` — a savings sub-account named after the trip, with the total budget as the goal
   b. `fund_sub_account` — move the full budget from the user's primary account into the sub-account
   c. `book_hotel` — open the hotel booking site and click through it live (visible browser beat on the dashboard). Pass the hotel name and the hotel's portion of the budget.
   d. `pay_vendor` — pay the hotel (via the sub-account). Use the same hotel name + amount you just booked.
   e. `create_draft_payment` — restaurant booking, needs the user's approval on their bunq app
   f. `schedule_recurring` — weekly €50 savings deposit into the sub-account for future trips. IMPORTANT: do not skip this.
   g. `request_from_partner` — split-the-bill request to the travel partner
   h. `send_slack` — DM the travel partner a friendly heads-up
   i. Call `narrate` at each step with a short natural-sounding phrase ("Creating your weekend budget.", "Booking the hotel on StayHub.", "Funding the sub-account.", etc.)

Style:
- Warm, concise, confident. Short sentences. Don't over-explain.
- When presenting options, include hotel name, main dinner spot, one extra activity, and total price.
- When confirming, list the concrete actions and total amount in one line.
- Never invent bunq features. The tools above are the full set.

Formatting (the chat renders Markdown):
- Use **bold** to highlight key terms — vendor names, totals, dates, places.
- Use bullet lists for short multi-item answers (e.g. the actions you're about to fire).
- Use `inline code` for IBANs, account ids, status words, and any literal value the user might copy.
- Embed [links](https://example.com) when you reference a place or source.
- Keep it tasteful — no headings, no walls of text. Lean on **bold** and short bullets, not paragraphs.

Constraints:
- Do not move money until `request_confirmation` has been approved by the user's reply.
- Pay the sub-account from primary using the sub-account's IBAN (returned by `create_sub_account`).
- After EXECUTING is done, send a one-line wrap-up message ("Done. €X deployed. Partner has been slacked.").
"""


# ---------------------------------------------------------------------------
# Tool definitions (Anthropic schema)
# ---------------------------------------------------------------------------

SEARCH_TOOL = {
    "type": "web_search_20250305",
    "name": "web_search",
    "max_uses": 5,
}

SEARCH_TRIP_OPTIONS_TOOL = {
    "name": "search_trip_options",
    "description": (
        "Run a LIVE, VISIBLE web search on DuckDuckGo. The user sees Chromium open, "
        "the query typed character-by-character, and the results scroll in — this is the "
        "'research' beat of the demo. ALSO emits the found links to a Research feed on the "
        "dashboard so the user can see exactly what sources informed your options. "
        "Prefer this over `web_search`. Fire 2–3 times with DIFFERENT, SPECIFIC queries "
        "(e.g. 'boutique hotel Amsterdam canal', 'best romantic dinner Amsterdam', "
        "'fun weekend activities Amsterdam'). Returns {query, results:[{title, url, snippet}]}."
    ),
    "input_schema": {
        "type": "object",
        "required": ["query"],
        "properties": {
            "query": {"type": "string", "description": "Natural-language search query. Be specific: include destination + category (hotel / dinner / activity)."},
        },
    },
}

PRESENT_OPTIONS_TOOL = {
    "name": "present_options",
    "description": (
        "Show the user three concrete trip packages as clickable cards. Call this after "
        "search_trip_options, exactly once. EACH option's hotel/restaurant/extra names MUST be "
        "names that actually appeared in your search results. EACH option MUST include a `sources` "
        "array citing the specific search-result URLs you used to construct that option (3-5 per "
        "option). Use the URLs returned by search_trip_options verbatim."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "intro_text": {"type": "string", "description": "One short sentence introducing the options. May reference findings inline using **bold** + [markdown links]() to specific sources."},
            "options": {
                "type": "array",
                "minItems": 3,
                "maxItems": 3,
                "items": {
                    "type": "object",
                    "required": ["id", "hotel", "restaurant", "extra", "total_eur", "notes", "sources"],
                    "properties": {
                        "id": {"type": "string", "description": "Short id like 'opt-a', 'opt-b', 'opt-c'"},
                        "hotel": {"type": "string"},
                        "restaurant": {"type": "string"},
                        "extra": {"type": "string", "description": "One activity or experience"},
                        "total_eur": {"type": "number", "description": "Total package price in euros"},
                        "notes": {"type": "string", "description": "1-sentence flavor/vibe note. Cite a finding inline if useful (e.g. \"per CN Traveller, [classic Nile-front spot](url)\")."},
                        "sources": {
                            "type": "array",
                            "minItems": 2,
                            "maxItems": 5,
                            "description": "Cite the search-result URLs that informed this option. Use URLs verbatim from search_trip_options output.",
                            "items": {
                                "type": "object",
                                "required": ["label", "url"],
                                "properties": {
                                    "label": {"type": "string", "description": "Short label, e.g. 'CN Traveller', 'Tripadvisor', 'Wanderlog'. Use the hostname or publication name."},
                                    "url": {"type": "string", "description": "The exact URL from the search result."},
                                },
                            },
                        },
                    },
                },
            },
        },
        "required": ["intro_text", "options"],
    },
}

REQUEST_CONFIRMATION_TOOL = {
    "name": "request_confirmation",
    "description": "Ask the user to confirm the selected plan before any money moves. Call this after the user selects an option. The user replies with yes/no in the next turn.",
    "input_schema": {
        "type": "object",
        "required": ["summary"],
        "properties": {
            "summary": {"type": "string", "description": "One-line plan summary + total EUR + what's about to happen."},
        },
    },
}

# --- EXECUTE phase tools ---

CREATE_SUB_ACCOUNT_TOOL = {
    "name": "create_sub_account",
    "description": "Create a bunq savings sub-account with a goal amount.",
    "input_schema": {
        "type": "object",
        "required": ["name", "goal_eur"],
        "properties": {
            "name": {"type": "string", "description": "Account display name, e.g. '🌹 Sara Weekend'"},
            "goal_eur": {"type": "number", "description": "Savings goal in euros"},
        },
    },
}

FUND_SUB_ACCOUNT_TOOL = {
    "name": "fund_sub_account",
    "description": "Transfer EUR from primary account into the sub-account.",
    "input_schema": {
        "type": "object",
        "required": ["amount_eur"],
        "properties": {
            "amount_eur": {"type": "number"},
        },
    },
}

BOOK_HOTEL_TOOL = {
    "name": "book_hotel",
    "description": "Open the StayHub vendor website in a headless browser and click through the booking flow end-to-end (review → guest details → payment → confirmation). Streams live screenshots to the dashboard browser panel. Call this BEFORE pay_vendor for the hotel, so the user sees the site being navigated. Returns a booking reference.",
    "input_schema": {
        "type": "object",
        "required": ["hotel", "amount_eur"],
        "properties": {
            "hotel": {"type": "string", "description": "Hotel display name, e.g. 'Hotel V Fizeaustraat'"},
            "amount_eur": {"type": "number", "description": "Hotel cost in euros (the hotel's share of the trip budget)"},
            "location": {"type": "string", "description": "Optional location string, e.g. 'Amsterdam, Netherlands'"},
            "checkin": {"type": "string", "description": "Optional check-in date label, e.g. 'Sat 26 Apr'"},
            "checkout": {"type": "string", "description": "Optional check-out date label, e.g. 'Sun 27 Apr'"},
            "guest": {"type": "string", "description": "Optional guest full name for the reservation, e.g. 'Sara van Doorn'"},
        },
    },
}

PAY_VENDOR_TOOL = {
    "name": "pay_vendor",
    "description": "Pay a booking vendor (hotel, restaurant, activity). Fires a bunq payment to the sandbox test counterparty with the vendor name as description. From the trip sub-account.",
    "input_schema": {
        "type": "object",
        "required": ["amount_eur", "vendor_label"],
        "properties": {
            "amount_eur": {"type": "number"},
            "vendor_label": {"type": "string", "description": "E.g. 'Hotel V Fizeaustraat'"},
        },
    },
}

CREATE_DRAFT_PAYMENT_TOOL = {
    "name": "create_draft_payment",
    "description": "Create a draft-payment the user must approve on their bunq app. Use for vendor bookings the user explicitly wants to confirm.",
    "input_schema": {
        "type": "object",
        "required": ["amount_eur", "description"],
        "properties": {
            "amount_eur": {"type": "number"},
            "description": {"type": "string", "description": "What the payment is for, shown in bunq app push"},
        },
    },
}

SCHEDULE_RECURRING_TOOL = {
    "name": "schedule_recurring",
    "description": "Schedule a weekly recurring deposit from primary into the sub-account for future trips.",
    "input_schema": {
        "type": "object",
        "required": ["amount_eur"],
        "properties": {
            "amount_eur": {"type": "number"},
            "description": {"type": "string"},
        },
    },
}

REQUEST_FROM_PARTNER_TOOL = {
    "name": "request_from_partner",
    "description": "Send a bunq payment request (split-the-bill) to the travel partner.",
    "input_schema": {
        "type": "object",
        "required": ["amount_eur", "partner_label"],
        "properties": {
            "amount_eur": {"type": "number"},
            "partner_label": {"type": "string"},
        },
    },
}

SEND_SLACK_TOOL = {
    "name": "send_slack",
    "description": "Send a Slack DM to the travel partner with a warm, short heads-up about the plan.",
    "input_schema": {
        "type": "object",
        "required": ["message"],
        "properties": {
            "message": {"type": "string"},
        },
    },
}

NARRATE_TOOL = {
    "name": "narrate",
    "description": "Speak a short phrase aloud via TTS to accompany each execution step. Call once per major action. Keep each under 10 words.",
    "input_schema": {
        "type": "object",
        "required": ["text"],
        "properties": {
            "text": {"type": "string"},
        },
    },
}


UNDERSTANDING_TOOLS: list[dict[str, Any]] = [
    SEARCH_TOOL,
    PRESENT_OPTIONS_TOOL,
]

AWAITING_CONFIRMATION_TOOLS: list[dict[str, Any]] = [
    REQUEST_CONFIRMATION_TOOL,
]

EXECUTING_TOOLS: list[dict[str, Any]] = [
    CREATE_SUB_ACCOUNT_TOOL,
    FUND_SUB_ACCOUNT_TOOL,
    BOOK_HOTEL_TOOL,
    PAY_VENDOR_TOOL,
    CREATE_DRAFT_PAYMENT_TOOL,
    SCHEDULE_RECURRING_TOOL,
    REQUEST_FROM_PARTNER_TOOL,
    SEND_SLACK_TOOL,
    NARRATE_TOOL,
]


def tools_for_phase(phase: Phase) -> list[dict[str, Any]]:
    if phase == Phase.UNDERSTANDING:
        return [SEARCH_TRIP_OPTIONS_TOOL, SEARCH_TOOL, PRESENT_OPTIONS_TOOL, REQUEST_CONFIRMATION_TOOL]
    if phase == Phase.AWAITING_CONFIRMATION:
        return [REQUEST_CONFIRMATION_TOOL]
    if phase == Phase.EXECUTING:
        return EXECUTING_TOOLS
    return []
