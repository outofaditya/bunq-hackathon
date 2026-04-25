"""Claude tool-use schemas. These are the tools the agent can call.

Only bunq tools are defined in Phase 1. Side-action tools (Slack, Calendar,
browser booking) are added in later phases.
"""

from __future__ import annotations

from typing import Any


BUNQ_TOOLS: list[dict[str, Any]] = [
    {
        "name": "pay_vendor",
        "description": (
            "Send an immediate payment from the current mission sub-account to a vendor. Use for "
            "confirmed small-to-medium transactions (restaurant, uber, flowers). For amounts over "
            "100 EUR prefer create_draft_payment so the user approves on their phone."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "amount_eur": {"type": "number"},
                "vendor_name": {"type": "string"},
                "description": {"type": "string"},
                "vendor_email": {"type": "string", "description": "Defaults to sandbox sugardaddy@bunq.com if omitted."},
            },
            "required": ["amount_eur", "vendor_name", "description"],
        },
    },
    {
        "name": "create_draft_payment",
        "description": (
            "Create a PENDING payment that requires the user to tap 'approve' in their bunq app. "
            "Use this for larger amounts (concert tickets, expensive bookings) so the human gets a "
            "confirmation step. After calling, the cascade continues; the webhook / polling layer "
            "handles the approval asynchronously."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "amount_eur": {"type": "number"},
                "vendor_name": {"type": "string"},
                "description": {"type": "string"},
                "vendor_email": {"type": "string"},
            },
            "required": ["amount_eur", "vendor_name", "description"],
        },
    },
    {
        "name": "schedule_recurring_payment",
        "description": (
            "Create a recurring scheduled outgoing payment from primary to a counterparty (vendor "
            "by email). Use for standing orders like 'pay €1200 rent to landlord every month' or "
            "'pay €15 streaming every month'. counterparty_name is the human-readable label "
            "(e.g. 'Landlord'); counterparty_email defaults to sugardaddy@bunq.com in sandbox."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "amount_eur": {"type": "number"},
                "description": {"type": "string"},
                "recurrence_unit": {"type": "string", "enum": ["DAILY", "WEEKLY", "MONTHLY"]},
                "recurrence_size": {"type": "integer", "minimum": 1},
                "days_from_now": {"type": "integer", "minimum": 0},
                "counterparty_email": {"type": "string"},
                "counterparty_name": {"type": "string"},
            },
            "required": ["amount_eur", "description", "recurrence_unit", "counterparty_name"],
        },
    },
    {
        "name": "request_money",
        "description": (
            "Ask someone to pay you via bunq request-inquiry. Use to split bills or collect a "
            "friend's share. Counterparty email in sandbox should usually be sugardaddy@bunq.com "
            "(auto-accepts)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "counterparty_email": {"type": "string"},
                "amount_eur": {"type": "number"},
                "description": {"type": "string"},
                "counterparty_name": {"type": "string"},
            },
            "required": ["counterparty_email", "amount_eur", "description"],
        },
    },
    {
        "name": "create_bunqme_link",
        "description": (
            "Generate a shareable bunq.me payment link for the current mission sub-account. The "
            "link can be posted publicly (QR code) for anyone to contribute. Use for gift pools, "
            "emergency chip-ins, group funds."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "amount_eur": {"type": "number"},
                "description": {"type": "string"},
            },
            "required": ["amount_eur", "description"],
        },
    },
    {
        "name": "set_card_status",
        "description": (
            "Freeze (DEACTIVATED) or unfreeze (ACTIVE) a bunq card by id. Use only when you "
            "already know a specific card_id. Otherwise call `freeze_home_card` / `unfreeze_home_card`."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "card_id": {"type": "integer"},
                "status": {"type": "string", "enum": ["ACTIVE", "DEACTIVATED"]},
            },
            "required": ["card_id", "status"],
        },
    },
    {
        "name": "freeze_home_card",
        "description": (
            "Freeze the user's primary card (auto-finds the first active one). Use for Travel Mode "
            "to lock the home card before a trip — protects against fraud abroad."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "unfreeze_home_card",
        "description": "Re-activate the user's primary card after a trip.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "book_restaurant",
        "description": (
            "Have a real browser-agent navigate a restaurant-booking site (Playwright + Claude Vision) "
            "and complete a real reservation. Returns the actual confirmed price, restaurant name, "
            "time slot, and reference. Use this for the dinner step BEFORE calling pay_vendor — pay "
            "the returned price to the returned restaurant_name."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "restaurant_hint": {"type": "string", "description": "Cuisine or vibe hint, e.g. 'italian', 'cozy dutch', 'rooftop'."},
                "max_budget_eur": {"type": "number"},
                "when": {"type": "string", "description": "When to book, e.g. 'Friday 19:30'."},
            },
            "required": ["restaurant_hint", "max_budget_eur", "when"],
        },
    },
    {
        "name": "send_slack_message",
        "description": (
            "Send a Slack DM/channel message via the configured incoming webhook. "
            "Use this to notify a friend or partner during a mission (e.g. 'Friday. Don't plan. Trust me.'). "
            "Keep messages short — they read like a real text. Use the optional `header` for a bold title."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "message": {"type": "string"},
                "header": {"type": "string", "description": "Short bold title rendered above the message."},
            },
            "required": ["message"],
        },
    },
    {
        "name": "create_calendar_event",
        "description": (
            "Create a Google Calendar event on the user's primary calendar. Optional invitees get "
            "an email invitation. Use `when` for free-text times like 'Friday 19:30'; if omitted, "
            "defaults to the upcoming Friday at 19:30."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "description": {"type": "string"},
                "when": {"type": "string"},
                "duration_minutes": {"type": "integer", "minimum": 15, "maximum": 480},
                "invitees": {"type": "array", "items": {"type": "string", "format": "email"}},
            },
            "required": ["title"],
        },
    },
    {
        "name": "book_hotel",
        "description": (
            "Have a real browser agent navigate a hotel-booking site (Playwright + Claude Vision) "
            "and complete a real reservation. Returns the actual confirmed hotel name, total EUR, "
            "nights, and reference. Use this for the lodging step BEFORE calling pay_vendor."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "city": {"type": "string"},
                "nights": {"type": "integer", "minimum": 1, "maximum": 14},
                "max_budget_eur": {"type": "number"},
            },
            "required": ["city", "nights", "max_budget_eur"],
        },
    },
    {
        "name": "subscribe_to_service",
        "description": (
            "Have a real browser agent navigate a subscription-comparison site (Playwright + Claude "
            "Vision) and confirm a recurring plan in the given category. Returns the chosen "
            "service_name, plan, monthly_eur, and reference. Use this BEFORE schedule_recurring_payment "
            "so the recurring is set up for the actually-chosen plan."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "category": {"type": "string", "enum": ["streaming", "gym", "internet", "mobile"]},
                "max_monthly_eur": {"type": "number"},
            },
            "required": ["category", "max_monthly_eur"],
        },
    },
    {
        "name": "narrate",
        "description": (
            "Speak a one-line summary to the user via TTS. Use AT MOST once per step so the demo "
            "feels narrated, not chatty. Keep each narration under 18 words, conversational, "
            "present tense."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {"type": "string"},
            },
            "required": ["text"],
        },
    },
    {
        "name": "finish_mission",
        "description": (
            "Signal the mission is complete. Include a final summary line for the user. After this, "
            "no more tool calls will be made."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "summary": {"type": "string"},
            },
            "required": ["summary"],
        },
    },
]
