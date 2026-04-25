"""Claude tool-use schemas. These are the tools the agent can call.

Only bunq tools are defined in Phase 1. Side-action tools (Slack, Calendar,
browser booking) are added in later phases.
"""

from __future__ import annotations

from typing import Any


BUNQ_TOOLS: list[dict[str, Any]] = [
    {
        "name": "create_sub_account",
        "description": (
            "Create a new bunq current (bank) sub-account with an emoji-tagged name. The sub-account "
            "becomes the 'current mission account' for subsequent payments, drafts, bunqme links "
            "and requests. For multi-account missions, pass a short `alias` (e.g. 'rent', 'tokyo') "
            "to address it later via fund_sub_account."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "User-facing description, emoji welcome. e.g. '🌹 Sara Weekend'.",
                },
                "alias": {
                    "type": "string",
                    "description": "Optional short key for multi-sub missions (e.g. 'rent').",
                },
            },
            "required": ["name"],
        },
    },
    {
        "name": "fund_sub_account",
        "description": (
            "Transfer EUR from the user's primary account to a sub-account. If `target_alias` is "
            "omitted, funds the most-recently-created sub-account. Use this for the initial funding "
            "of a mission, or after create_sub_account with an alias for a multi-sub flow."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "amount_eur": {"type": "number"},
                "target_alias": {"type": "string", "description": "Alias from create_sub_account; omit to target the current mission sub-account."},
                "description": {"type": "string"},
            },
            "required": ["amount_eur"],
        },
    },
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
            "Create a recurring scheduled transfer from primary to a sub-account. Use for standing "
            "orders like 'save 50 EUR every Friday' or 'pay 1200 EUR rent every month'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "amount_eur": {"type": "number"},
                "description": {"type": "string"},
                "recurrence_unit": {
                    "type": "string",
                    "enum": ["DAILY", "WEEKLY", "MONTHLY"],
                },
                "recurrence_size": {"type": "integer", "minimum": 1},
                "days_from_now": {"type": "integer", "minimum": 0},
                "target_alias": {"type": "string"},
            },
            "required": ["amount_eur", "description", "recurrence_unit"],
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
        "name": "update_sub_account",
        "description": (
            "Update the current mission sub-account's description. Use to mark a mission complete "
            "('🌹 Sara Weekend — done!')."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "new_description": {"type": "string"},
            },
            "required": ["new_description"],
        },
    },
    {
        "name": "set_card_status",
        "description": (
            "Freeze (DEACTIVATED) or unfreeze (ACTIVE) a bunq card by id. Use for Travel Mode to "
            "freeze the home card before a trip."
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
