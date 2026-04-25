"""bunq API tool wrappers used by the Claude agent loop.

All six execution-phase tools live here. Each function:
- accepts plain Python args (euros as floats, labels as strings)
- returns a small dict the agent can feed back into the model
- fires through a shared BunqClient that handles auth + signing

Test each in isolation via tests/test_bunq_tools.py before wiring into the agent.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any

from dotenv import load_dotenv

from bunq_client import BunqClient

load_dotenv()

SUGARDADDY_EMAIL = os.getenv("SUGARDADDY_EMAIL", "sugardaddy@bunq.com")

_client: BunqClient | None = None


def client() -> BunqClient:
    global _client
    if _client is None:
        api_key = os.environ["BUNQ_API_KEY"]
        _client = BunqClient(api_key=api_key, sandbox=True)
        _client.authenticate()
    return _client


def _amount(value_eur: float) -> dict[str, str]:
    return {"currency": "EUR", "value": f"{value_eur:.2f}"}


def _iban_from_response(response_item: dict[str, Any], account_type: str) -> str | None:
    """Pull the first IBAN alias from a newly-created monetary-account response."""
    account = response_item.get(account_type, response_item)
    for alias in account.get("alias", []):
        if alias.get("type") == "IBAN":
            return alias.get("value")
    return None


def get_primary_account() -> dict[str, Any]:
    """Return the primary active MonetaryAccountBank — {id, iban}."""
    c = client()
    resp = c.get(f"user/{c.user_id}/monetary-account-bank")
    for item in resp:
        acc = item.get("MonetaryAccountBank", {})
        if acc.get("status") == "ACTIVE":
            iban = next((a["value"] for a in acc.get("alias", []) if a.get("type") == "IBAN"), None)
            return {"id": acc["id"], "iban": iban, "balance_eur": float(acc.get("balance", {}).get("value", "0"))}
    raise RuntimeError("No active primary account found")


def snapshot_primary_balance(step_label: str) -> dict[str, Any]:
    """Fetch the current primary balance and return a balance_snapshot payload.

    Synchronous — the async caller is expected to publish the result over SSE
    (we don't publish from here because we're typically called from a
    threadpool worker via asyncio.to_thread, where the asyncio.Queue used by
    the bus isn't thread-safe).
    """
    primary = get_primary_account()
    return {
        "step": step_label,
        "primary_id": primary["id"],
        "primary_balance_eur": primary["balance_eur"],
    }


# --------------------------------------------------------------------------
# Tool 1 — create a savings sub-account with a goal
# --------------------------------------------------------------------------

def create_sub_account(name: str, goal_eur: float) -> dict[str, Any]:
    """Create a MonetaryAccountSavings. Fallback to MonetaryAccountBank on failure."""
    c = client()
    try:
        resp = c.post(
            f"user/{c.user_id}/monetary-account-savings",
            {
                "currency": "EUR",
                "description": name,
                "savings_goal": _amount(goal_eur),
            },
        )
    except Exception as e:
        # Fallback: plain bank account, we'll track goal in our own state.
        print(f"[bunq_tools] savings-account create failed ({e}); falling back to monetary-account-bank")
        resp = c.post(
            f"user/{c.user_id}/monetary-account-bank",
            {"currency": "EUR", "description": name},
        )
        account_type = "MonetaryAccountBank"
    else:
        account_type = "MonetaryAccountSavings"

    # Response shape: Response[0].{Id: {id}, ...]
    # After creation we need the IBAN — do a GET to pull it.
    account_id = None
    for item in resp:
        if "Id" in item:
            account_id = item["Id"]["id"]
            break
    if account_id is None:
        raise RuntimeError(f"Could not parse account id from create response: {resp}")

    # Fetch the created account to get its IBAN
    path = (
        f"user/{c.user_id}/monetary-account-savings/{account_id}"
        if account_type == "MonetaryAccountSavings"
        else f"user/{c.user_id}/monetary-account-bank/{account_id}"
    )
    detail = c.get(path)
    iban = _iban_from_response(detail[0], account_type) if detail else None

    return {
        "account_id": account_id,
        "iban": iban,
        "name": name,
        "goal_eur": goal_eur,
        "type": account_type,
    }


# --------------------------------------------------------------------------
# Tool 2 — fund sub-account from primary via IBAN self-transfer
# --------------------------------------------------------------------------

def fund_sub_account(amount_eur: float, to_iban: str, from_account_id: int | None = None) -> dict[str, Any]:
    c = client()
    primary = get_primary_account()
    if from_account_id is None:
        from_account_id = primary["id"]

    # Self-heal: if primary is short, top up via sugardaddy in €500 chunks before paying.
    if primary["balance_eur"] < amount_eur + 50:
        needed = amount_eur + 100 - primary["balance_eur"]
        target = primary["balance_eur"] + max(needed, 500.0)
        print(f"[bunq_tools] fund_sub_account: primary €{primary['balance_eur']} < required €{amount_eur}; topping up to €{target}")
        ensure_primary_balance(min_eur=amount_eur + 50, target_eur=target)

    resp = c.post(
        f"user/{c.user_id}/monetary-account/{from_account_id}/payment",
        {
            "amount": _amount(amount_eur),
            "counterparty_alias": {"type": "IBAN", "value": to_iban, "name": "Trip Agent"},
            "description": "Trip Agent — weekend fund",
        },
    )
    payment_id = resp[0]["Id"]["id"] if resp and "Id" in resp[0] else None
    return {"payment_id": payment_id, "amount_eur": amount_eur, "to_iban": to_iban}


# --------------------------------------------------------------------------
# Tool 3 — pay a vendor (demo-visible payment to sugardaddy, vendor-named)
# --------------------------------------------------------------------------

def pay_vendor(amount_eur: float, vendor_label: str, from_account_id: int) -> dict[str, Any]:
    c = client()
    resp = c.post(
        f"user/{c.user_id}/monetary-account/{from_account_id}/payment",
        {
            "amount": _amount(amount_eur),
            "counterparty_alias": {"type": "EMAIL", "value": SUGARDADDY_EMAIL},
            "description": f"Booking: {vendor_label}",
        },
    )
    payment_id = resp[0]["Id"]["id"] if resp and "Id" in resp[0] else None
    return {"payment_id": payment_id, "vendor": vendor_label, "amount_eur": amount_eur}


# --------------------------------------------------------------------------
# Tool 4 — create a draft-payment awaiting user approval on bunq app
# --------------------------------------------------------------------------

def create_draft_payment(amount_eur: float, description: str, from_account_id: int) -> dict[str, Any]:
    c = client()
    resp = c.post(
        f"user/{c.user_id}/monetary-account/{from_account_id}/draft-payment",
        {
            "number_of_required_accepts": 1,
            "entries": [
                {
                    "amount": _amount(amount_eur),
                    "counterparty_alias": {"type": "EMAIL", "value": SUGARDADDY_EMAIL},
                    "description": description,
                }
            ],
        },
    )
    draft_id = resp[0]["Id"]["id"] if resp and "Id" in resp[0] else None
    return {"draft_id": draft_id, "amount_eur": amount_eur, "description": description, "status": "PENDING"}


def get_draft_payment(draft_id: int, from_account_id: int) -> dict[str, Any]:
    """Poll helper — check whether a draft has been accepted."""
    c = client()
    resp = c.get(f"user/{c.user_id}/monetary-account/{from_account_id}/draft-payment/{draft_id}")
    if resp and "DraftPayment" in resp[0]:
        d = resp[0]["DraftPayment"]
        return {"draft_id": draft_id, "status": d.get("status"), "object": d.get("object")}
    return {"draft_id": draft_id, "status": "UNKNOWN"}


def accept_draft_payment(draft_id: int, from_account_id: int) -> dict[str, Any]:
    """Simulate the user tapping 'approve' on their bunq app.

    Fires a PUT to the draft-payment endpoint with status=ACCEPTED. bunq then
    executes the underlying payment and (separately) pushes a PAYMENT webhook.
    """
    c = client()
    resp = c.put(
        f"user/{c.user_id}/monetary-account/{from_account_id}/draft-payment/{draft_id}",
        {"status": "ACCEPTED"},
    )
    return {"draft_id": draft_id, "status": "ACCEPTED", "response": resp}


# --------------------------------------------------------------------------
# Tool 5 — schedule a recurring weekly payment
# --------------------------------------------------------------------------

def schedule_recurring(amount_eur: float, to_iban: str, from_account_id: int, description: str = "Trip fund") -> dict[str, Any]:
    c = client()
    # bunq expects UTC formatted as "YYYY-MM-DD HH:MM:SS.ffffff"
    start = (datetime.now(timezone.utc) + timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S.%f")
    try:
        resp = c.post(
            f"user/{c.user_id}/monetary-account/{from_account_id}/schedule-payment",
            {
                "payment": {
                    "amount": _amount(amount_eur),
                    "counterparty_alias": {"type": "IBAN", "value": to_iban, "name": "Trip Agent"},
                    "description": description,
                },
                "schedule": {
                    "time_start": start,
                    "recurrence_unit": "WEEKLY",
                    "recurrence_size": 1,
                },
            },
        )
        schedule_id = resp[0]["Id"]["id"] if resp and "Id" in resp[0] else None
        return {"schedule_id": schedule_id, "amount_eur": amount_eur, "cadence": "WEEKLY", "fallback": False}
    except Exception as e:
        print(f"[bunq_tools] schedule_recurring failed ({e}); returning UI-only fallback")
        return {"schedule_id": None, "amount_eur": amount_eur, "cadence": "WEEKLY", "fallback": True, "error": str(e)}


# --------------------------------------------------------------------------
# Tool 6 — request money from the travel partner (labeled Sara, fires to sugardaddy)
# --------------------------------------------------------------------------

def request_from_partner(amount_eur: float, from_account_id: int, partner_label: str = "Sara") -> dict[str, Any]:
    c = client()
    resp = c.post(
        f"user/{c.user_id}/monetary-account/{from_account_id}/request-inquiry",
        {
            "amount_inquired": _amount(amount_eur),
            "counterparty_alias": {"type": "EMAIL", "value": SUGARDADDY_EMAIL},
            "description": f"Split with {partner_label} — weekend",
            "allow_bunqme": False,
        },
    )
    request_id = resp[0]["Id"]["id"] if resp and "Id" in resp[0] else None
    return {"request_id": request_id, "partner": partner_label, "amount_eur": amount_eur}


# --------------------------------------------------------------------------
# Webhook subscription (called once at server startup)
# --------------------------------------------------------------------------

WEBHOOK_CATEGORIES = ["PAYMENT", "MUTATION", "DRAFT_PAYMENT", "SCHEDULE_RESULT", "REQUEST"]


MAX_PER_SUGARDADDY_REQUEST_EUR = 500.0


def ensure_primary_balance(min_eur: float = 2000.0, target_eur: float = 3000.0) -> dict[str, Any]:
    """Request €500 chunks from sugardaddy until balance >= min_eur.

    Sandbox sugardaddy caps each request at €500 and auto-accepts within ~2s. This
    function is idempotent-ish: if balance already exceeds min_eur, it no-ops.
    """
    import time

    c = client()
    primary = get_primary_account()
    if primary["balance_eur"] >= min_eur:
        return {"topped_up": False, "balance_eur": primary["balance_eur"]}

    prior = primary["balance_eur"]
    chunks_requested = 0
    chunks_needed = max(1, int((target_eur - prior) // MAX_PER_SUGARDADDY_REQUEST_EUR) + 1)
    chunks_needed = min(chunks_needed, 20)  # safety cap
    for _ in range(chunks_needed):
        c.post(
            f"user/{c.user_id}/monetary-account/{primary['id']}/request-inquiry",
            {
                "amount_inquired": _amount(MAX_PER_SUGARDADDY_REQUEST_EUR),
                "counterparty_alias": {"type": "EMAIL", "value": SUGARDADDY_EMAIL},
                "description": "Demo top-up",
                "allow_bunqme": False,
            },
        )
        chunks_requested += 1
        time.sleep(0.6)  # pace to avoid rate-limit (POST 5/3s)

    # Wait for settle
    for _ in range(15):
        time.sleep(1)
        cur = get_primary_account()["balance_eur"]
        if cur >= min_eur:
            return {
                "topped_up": True,
                "chunks": chunks_requested,
                "prior_balance_eur": prior,
                "final_balance_eur": cur,
            }
    return {
        "topped_up": True,
        "chunks": chunks_requested,
        "prior_balance_eur": prior,
        "final_balance_eur": get_primary_account()["balance_eur"],
        "note": "Settle window exceeded; balance may still be propagating.",
    }


def register_webhooks(public_base_url: str) -> dict[str, Any]:
    c = client()
    target = f"{public_base_url.rstrip('/')}/bunq-webhook"
    resp = c.post(
        f"user/{c.user_id}/notification-filter-url",
        {
            "notification_filters": [
                {"category": cat, "notification_target": target}
                for cat in WEBHOOK_CATEGORIES
            ]
        },
    )
    return {"registered": WEBHOOK_CATEGORIES, "target": target, "response": resp}
