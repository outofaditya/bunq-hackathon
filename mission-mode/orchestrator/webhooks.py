"""Parse bunq webhook POSTs and emit typed SSE events.

Bunq wraps every notification in a NotificationUrl envelope:
{
  "NotificationUrl": {
    "target_url": "...",
    "category": "PAYMENT" | "MUTATION" | "DRAFT_PAYMENT" | "SCHEDULE_RESULT" | "REQUEST",
    "event_type": "CREATED" | "UPDATED" | ...
    "object": {
      "Payment": {...} | "Mutation": {...} | "DraftPayment": {...} | ...
    }
  }
}
"""
from __future__ import annotations

from typing import Any

from .events import bus


async def handle(payload: dict[str, Any]) -> None:
    """Dispatch a webhook payload to typed SSE events."""
    nu = payload.get("NotificationUrl") or payload.get("notification_url") or {}
    category = nu.get("category")
    event_type = nu.get("event_type")
    obj = nu.get("object", {})

    # Raw passthrough for debugging
    await bus.publish("bunq_webhook", category=category, bunq_event=event_type)

    if not category or not obj:
        return

    # PAYMENT / MUTATION — outgoing or incoming balance movement
    if category in ("PAYMENT", "MUTATION"):
        body = obj.get("Payment") or obj.get("Mutation", {}).get("Payment") or obj.get("Mutation", {})
        if isinstance(body, dict):
            account_id = body.get("monetary_account_id")
            amount = body.get("amount", {})
            try:
                value = float(amount.get("value", "0"))
            except (TypeError, ValueError):
                value = 0.0
            description = body.get("description", "")
            sub_type = body.get("sub_type")
            await bus.publish(
                "payment_event",
                account_id=account_id,
                amount_eur=value,
                description=description,
                sub_type=sub_type,
                category=category,
            )

    # DRAFT_PAYMENT — user approved or rejected in the bunq app
    elif category == "DRAFT_PAYMENT":
        body = obj.get("DraftPayment", {})
        status = body.get("status")
        draft_id = body.get("id")
        await bus.publish("draft_payment_event", draft_id=draft_id, status=status)

    # SCHEDULE_RESULT — a scheduled payment fired
    elif category == "SCHEDULE_RESULT":
        body = obj.get("ScheduleInstance") or obj.get("SchedulePayment", {})
        await bus.publish("schedule_event", raw=body)

    # REQUEST — request-inquiry status change (accepted, paid, rejected)
    elif category == "REQUEST":
        body = obj.get("RequestInquiry") or obj.get("RequestResponse", {})
        status = body.get("status")
        amount = body.get("amount_inquired") or body.get("amount_responded") or {}
        try:
            value = float(amount.get("value", "0"))
        except (TypeError, ValueError):
            value = 0.0
        await bus.publish(
            "request_event",
            status=status,
            amount_eur=value,
        )
