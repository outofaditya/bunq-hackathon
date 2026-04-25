"""bunq tool wrappers used by the Claude agent.

Each method is a thin orchestration over `bunq_client.BunqClient`. Every
action emits SSE events so the dashboard can render tiles in real time.

All 10 endpoints are verified against the bunq sandbox.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Any

from bunq_client import BunqClient

from .events import bus


class BunqToolbox:
    """Thin facade over BunqClient; every method drives the user's primary account."""

    def __init__(self, client: BunqClient) -> None:
        self.client = client
        self.uid = client.user_id
        self.primary_id = client.get_primary_account_id()
        self.primary_iban = self._iban_of_bank_account(self.primary_id)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _iban_of_bank_account(self, account_id: int) -> str:
        data = self.client.get(f"user/{self.uid}/monetary-account-bank/{account_id}")
        acc = data[0]["MonetaryAccountBank"]
        return next(a["value"] for a in acc["alias"] if a["type"] == "IBAN")

    def _balance_of(self, account_id: int) -> float:
        """Current EUR balance of a bank account (works for sub-accounts too)."""
        data = self.client.get(f"user/{self.uid}/monetary-account-bank/{account_id}")
        acc = data[0]["MonetaryAccountBank"]
        return float(acc.get("balance", {}).get("value", "0.00"))

    def snapshot_balance(self, step_label: str) -> dict[str, Any]:
        """Fetch + publish + print the primary balance after a mission step."""
        primary_bal = self._balance_of(self.primary_id)
        snapshot: dict[str, Any] = {
            "step": step_label,
            "primary_id": self.primary_id,
            "primary_balance_eur": primary_bal,
        }
        bus.publish("balance_snapshot", snapshot)
        return snapshot

    # ------------------------------------------------------------------
    # Sandbox helper (not exposed to Claude)
    # ------------------------------------------------------------------

    def seed_primary(self, amount: float = 500.0) -> None:
        """Request EUR from sugardaddy@bunq.com so primary has money to move."""
        self.client.post(
            f"user/{self.uid}/monetary-account/{self.primary_id}/request-inquiry",
            {
                "amount_inquired": {"value": f"{amount:.2f}", "currency": "EUR"},
                "counterparty_alias": {
                    "type": "EMAIL",
                    "value": "sugardaddy@bunq.com",
                    "name": "Sugar Daddy",
                },
                "description": "Mission seed",
                "allow_bunqme": False,
            },
        )
        time.sleep(1.5)

    # ------------------------------------------------------------------
    # Trip mission helpers — sub-accounts + self-healing top-up
    # ------------------------------------------------------------------

    # Sandbox sugardaddy caps each request at €500 — chunked top-up below.
    _SUGARDADDY_CHUNK_EUR = 500.0

    def ensure_primary_balance(
        self,
        min_eur: float,
        target_eur: float | None = None,
        timeout_s: float = 20.0,
    ) -> dict[str, Any]:
        """Self-healing top-up: loop sugardaddy €500 chunks until balance ≥ min_eur.

        Useful for the trip mission's funding step — we don't know in advance
        how much primary the user has. If already enough, no-op.
        """
        target = target_eur if target_eur is not None else min_eur + 500.0
        cur = self._balance_of(self.primary_id)
        if cur >= min_eur:
            return {"topped_up": False, "balance_eur": cur}

        prior = cur
        chunks_needed = max(1, int((target - prior) // self._SUGARDADDY_CHUNK_EUR) + 1)
        chunks_needed = min(chunks_needed, 20)
        for _ in range(chunks_needed):
            self.seed_primary(self._SUGARDADDY_CHUNK_EUR)
        # Settle window
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            cur = self._balance_of(self.primary_id)
            if cur >= min_eur:
                return {"topped_up": True, "chunks": chunks_needed, "prior_balance_eur": prior, "balance_eur": cur}
            time.sleep(1.0)
        return {"topped_up": True, "chunks": chunks_needed, "prior_balance_eur": prior, "balance_eur": self._balance_of(self.primary_id), "note": "settle window exceeded"}

    def create_sub_account(self, name: str, goal_eur: float) -> dict[str, Any]:
        """Create a MonetaryAccountSavings with a goal — used by the trip mission.

        Falls back to MonetaryAccountBank if the savings endpoint rejects the
        body (rare in sandbox, but the demo should never break).
        """
        bus.publish("step_started", {"tool": "create_sub_account", "name": name, "goal_eur": goal_eur})
        try:
            resp = self.client.post(
                f"user/{self.uid}/monetary-account-savings",
                {
                    "currency": "EUR",
                    "description": name,
                    "savings_goal": {"value": f"{goal_eur:.2f}", "currency": "EUR"},
                },
            )
            account_type = "MonetaryAccountSavings"
        except Exception as e:  # noqa: BLE001
            print(f"[bunq_tools] savings create failed ({e}); falling back to monetary-account-bank")
            resp = self.client.post(
                f"user/{self.uid}/monetary-account-bank",
                {"currency": "EUR", "description": name},
            )
            account_type = "MonetaryAccountBank"

        account_id = next((item["Id"]["id"] for item in resp if "Id" in item), None)
        if account_id is None:
            raise RuntimeError(f"could not parse sub-account id from response: {resp}")

        # Fetch IBAN
        path = (
            f"user/{self.uid}/monetary-account-savings/{account_id}"
            if account_type == "MonetaryAccountSavings"
            else f"user/{self.uid}/monetary-account-bank/{account_id}"
        )
        detail = self.client.get(path)
        acc = detail[0][account_type]
        iban = next((a["value"] for a in acc.get("alias", []) if a.get("type") == "IBAN"), None)

        result = {"account_id": account_id, "iban": iban, "name": name, "goal_eur": goal_eur, "type": account_type}
        bus.publish("step_finished", {"tool": "create_sub_account", "result": result})
        return result

    def fund_sub_account(self, amount_eur: float, sub_iban: str) -> dict[str, Any]:
        """Move EUR from primary into a sub-account (by IBAN). Self-heals primary
        balance via sugardaddy chunks if short."""
        # Self-heal primary if needed
        cur = self._balance_of(self.primary_id)
        if cur < amount_eur + 50:
            self.ensure_primary_balance(min_eur=amount_eur + 50, target_eur=cur + max(500.0, amount_eur + 100 - cur))

        bus.publish("step_started", {
            "tool": "fund_sub_account",
            "amount_eur": amount_eur,
            "to_iban": sub_iban,
            "from_account_id": self.primary_id,
        })
        resp = self.client.post(
            f"user/{self.uid}/monetary-account/{self.primary_id}/payment",
            {
                "amount": {"value": f"{amount_eur:.2f}", "currency": "EUR"},
                "counterparty_alias": {"type": "IBAN", "value": sub_iban, "name": "Trip Agent"},
                "description": "Trip Agent — fund sub-account",
            },
        )
        payment_id = resp[0]["Id"]["id"]
        result = {"payment_id": payment_id, "amount_eur": amount_eur, "to_iban": sub_iban}
        bus.publish("step_finished", {"tool": "fund_sub_account", "result": result})
        return result

    # ------------------------------------------------------------------
    # 1. pay_vendor  (outbound from the user's primary account)
    # ------------------------------------------------------------------

    def pay_vendor(
        self,
        amount_eur: float,
        vendor_name: str,
        description: str,
        vendor_email: str = "sugardaddy@bunq.com",
    ) -> dict[str, Any]:
        aid = self.primary_id
        bus.publish("step_started", {
            "tool": "pay_vendor",
            "vendor": vendor_name,
            "amount_eur": amount_eur,
            "description": description,
            "from_account_id": aid,
        })
        resp = self.client.post(
            f"user/{self.uid}/monetary-account/{aid}/payment",
            {
                "amount": {"value": f"{amount_eur:.2f}", "currency": "EUR"},
                "counterparty_alias": {
                    "type": "EMAIL",
                    "value": vendor_email,
                    "name": vendor_name,
                },
                "description": description,
            },
        )
        payment_id = resp[0]["Id"]["id"]
        result = {"payment_id": payment_id, "vendor": vendor_name, "amount_eur": amount_eur}
        bus.publish("step_finished", {"tool": "pay_vendor", "result": result})
        return result

    # ------------------------------------------------------------------
    # 4. create_draft_payment
    # ------------------------------------------------------------------

    def create_draft_payment(
        self,
        amount_eur: float,
        vendor_name: str,
        description: str,
        vendor_email: str = "sugardaddy@bunq.com",
    ) -> dict[str, Any]:
        aid = self.primary_id
        bus.publish("step_started", {
            "tool": "create_draft_payment",
            "vendor": vendor_name,
            "amount_eur": amount_eur,
            "description": description,
            "from_account_id": aid,
        })
        resp = self.client.post(
            f"user/{self.uid}/monetary-account/{aid}/draft-payment",
            {
                "entries": [
                    {
                        "amount": {"value": f"{amount_eur:.2f}", "currency": "EUR"},
                        "counterparty_alias": {
                            "type": "EMAIL",
                            "value": vendor_email,
                            "name": vendor_name,
                        },
                        "description": description,
                    }
                ],
                "number_of_required_accepts": 1,
            },
        )
        draft_id = resp[0]["Id"]["id"]
        result = {
            "draft_id": draft_id,
            "vendor": vendor_name,
            "amount_eur": amount_eur,
            "status": "PENDING",
        }
        bus.publish("step_finished", {"tool": "create_draft_payment", "result": result})
        return result

    def wait_for_draft_approval(self, draft_id: int, timeout_s: float = 60.0) -> str:
        """Polling fallback in case webhook is missed. Returns final status."""
        aid = self.primary_id
        start = time.monotonic()
        print(f"[bunq] polling draft {draft_id} on account {aid} (timeout {timeout_s:.0f}s)… tap Approve on the sandbox app.", flush=True)
        while time.monotonic() - start < timeout_s:
            data = self.client.get(
                f"user/{self.uid}/monetary-account/{aid}/draft-payment/{draft_id}"
            )
            status = data[0]["DraftPayment"]["status"]
            if status in ("ACCEPTED", "REJECTED"):
                bus.publish("draft_resolved", {"draft_id": draft_id, "status": status})
                return status
            time.sleep(1.5)
        bus.publish("draft_resolved", {"draft_id": draft_id, "status": "TIMEOUT"})
        return "TIMEOUT"

    # ------------------------------------------------------------------
    # 5. schedule_recurring_payment
    # ------------------------------------------------------------------

    def schedule_recurring_payment(
        self,
        amount_eur: float,
        description: str,
        recurrence_unit: str = "WEEKLY",
        recurrence_size: int = 1,
        days_from_now: int = 7,
        counterparty_email: str = "sugardaddy@bunq.com",
        counterparty_name: str = "Vendor",
    ) -> dict[str, Any]:
        bus.publish("step_started", {
            "tool": "schedule_recurring_payment",
            "amount_eur": amount_eur,
            "unit": recurrence_unit,
            "size": recurrence_size,
            "counterparty": counterparty_name,
        })
        start = (datetime.now(timezone.utc) + timedelta(days=days_from_now)).strftime(
            "%Y-%m-%d %H:%M:%S.000000"
        )
        resp = self.client.post(
            f"user/{self.uid}/monetary-account/{self.primary_id}/schedule-payment",
            {
                "payment": {
                    "amount": {"value": f"{amount_eur:.2f}", "currency": "EUR"},
                    "counterparty_alias": {
                        "type": "EMAIL",
                        "value": counterparty_email,
                        "name": counterparty_name,
                    },
                    "description": description,
                },
                "schedule": {
                    "time_start": start,
                    "recurrence_unit": recurrence_unit,
                    "recurrence_size": recurrence_size,
                },
            },
        )
        schedule_id = resp[0]["Id"]["id"]
        result = {
            "schedule_id": schedule_id,
            "amount_eur": amount_eur,
            "unit": recurrence_unit,
            "size": recurrence_size,
            "starts": start,
            "counterparty": counterparty_name,
        }
        bus.publish("step_finished", {"tool": "schedule_recurring_payment", "result": result})
        return result

    # ------------------------------------------------------------------
    # 6. request_money
    # ------------------------------------------------------------------

    def request_money(
        self,
        counterparty_email: str,
        amount_eur: float,
        description: str,
        counterparty_name: str = "Friend",
    ) -> dict[str, Any]:
        aid = self.primary_id
        bus.publish("step_started", {
            "tool": "request_money",
            "to": counterparty_email,
            "amount_eur": amount_eur,
            "description": description,
            "from_account_id": aid,
        })
        resp = self.client.post(
            f"user/{self.uid}/monetary-account/{aid}/request-inquiry",
            {
                "amount_inquired": {"value": f"{amount_eur:.2f}", "currency": "EUR"},
                "counterparty_alias": {
                    "type": "EMAIL",
                    "value": counterparty_email,
                    "name": counterparty_name,
                },
                "description": description,
                "allow_bunqme": True,
            },
        )
        inquiry_id = resp[0]["Id"]["id"]
        result = {"request_id": inquiry_id, "amount_eur": amount_eur, "to": counterparty_email}
        bus.publish("step_finished", {"tool": "request_money", "result": result})
        return result

    # ------------------------------------------------------------------
    # register_webhook (not exposed to Claude)
    # ------------------------------------------------------------------

    def register_webhook(self, public_url: str) -> None:
        callback = f"{public_url.rstrip('/')}/bunq-webhook"
        self.client.post(
            f"user/{self.uid}/notification-filter-url",
            {
                "notification_filters": [
                    {"category": "PAYMENT", "notification_target": callback},
                    {"category": "MUTATION", "notification_target": callback},
                    {"category": "DRAFT_PAYMENT", "notification_target": callback},
                ],
            },
        )

