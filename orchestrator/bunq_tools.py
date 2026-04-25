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
        # Lazy: don't touch ElevenLabs / Anthropic at construction time.
        self._personas = None  # type: ignore[assignment]

    # ------------------------------------------------------------------
    # Persona manager (Money has feelings)
    # ------------------------------------------------------------------

    @property
    def personas(self):  # noqa: ANN201
        from .personas import PersonaManager

        if self._personas is None:
            self._personas = PersonaManager(self)
        return self._personas

    def list_personas(self, *, ensure_min: int = 5) -> list[dict[str, Any]]:
        """Return the persona registry — fresh from bunq each call."""
        bus.publish("step_started", {"tool": "list_personas"})
        result = self.personas.discover_or_create(ensure_min=ensure_min)
        bus.publish("step_finished", {"tool": "list_personas", "result": {"count": len(result)}})
        return result

    def persona_speak(self, persona_id: int, text: str, stance: str = "neutral") -> dict[str, Any]:
        return self.personas.speak(int(persona_id), text, stance=stance)

    def council_payout(
        self,
        distributions: list[dict[str, Any]],
        description: str = "Council payout",
    ) -> dict[str, Any]:
        bus.publish("step_started", {
            "tool": "council_payout",
            "count": len(distributions or []),
            "description": description,
        })
        results = self.personas.payout_to_personas(distributions or [], description=description)
        ok_count = sum(1 for r in results if r.get("ok"))
        total = sum(float(r.get("amount_eur", 0.0)) for r in results if r.get("ok"))
        result = {"transferred": ok_count, "total_eur": round(total, 2), "details": results}
        bus.publish("step_finished", {"tool": "council_payout", "result": result})
        return result

    def await_user_confirmation(
        self,
        question: str,
        action_summary: str = "",
        winning_persona_id: int | None = None,
        timeout_s: float = 25.0,
    ) -> dict[str, Any]:
        """Block the mission thread until the user replies via /missions/council/confirm.

        Emits `awaiting_confirmation` so the dashboard auto-opens the mic dialog
        with the question prefilled. The `winning_persona_id`, when set, lets
        the dashboard label which persona is asking — useful for the demo voice.
        """
        from . import server as _srv  # noqa: PLC0415  — avoid circular import at module load

        timeout_s = max(3.0, min(60.0, float(timeout_s)))
        question = (question or "").strip()[:240] or "Should I execute this?"
        bus.publish("awaiting_confirmation", {
            "question":           question,
            "action_summary":     (action_summary or "")[:240],
            "winning_persona_id": winning_persona_id,
            "timeout_s":          timeout_s,
        })
        bus.publish("step_started", {"tool": "request_confirmation", "question": question})

        _srv._user_confirm_event.clear()
        _srv._user_confirm_slot = None

        got = _srv._user_confirm_event.wait(timeout=timeout_s)
        slot = _srv._user_confirm_slot
        if got and slot:
            result = {
                "ok":                True,
                "decision":          slot.get("decision", "unsure"),
                "transcript":        slot.get("transcript", ""),
                "confidence":        slot.get("confidence", 0.0),
                "picked_persona_id": slot.get("picked_persona_id"),
                "picked_name":       slot.get("picked_name"),
            }
        else:
            result = {
                "ok":                True,
                "decision":          "timeout",
                "transcript":        "",
                "confidence":        0.0,
                "picked_persona_id": None,
                "picked_name":       None,
            }
            bus.publish("user_confirmation_timeout", {"question": question})

        bus.publish("step_finished", {"tool": "request_confirmation", "result": result})
        bus.publish("user_confirmation", {
            "decision":   result["decision"],
            "transcript": result["transcript"],
        })
        return result

    def cleanup_demo_subs(self, dry: bool = False) -> dict[str, Any]:
        bus.publish("step_started", {"tool": "cleanup_demo_subs", "dry": dry})
        result = self.personas.cleanup_demo_accounts(dry=dry)
        bus.publish("step_finished", {"tool": "cleanup_demo_subs", "result": {
            "drained": result["drained"],
            "cancelled": result["cancelled"],
            "skipped": result["skipped"],
        }})
        return result

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
    # 7. create_bunqme_link (shareable payment link / QR)
    # ------------------------------------------------------------------

    def create_bunqme_link(self, amount_eur: float, description: str) -> dict[str, Any]:
        aid = self.primary_id
        bus.publish("step_started", {
            "tool": "create_bunqme_link",
            "amount_eur": amount_eur,
            "description": description,
            "from_account_id": aid,
        })
        resp = self.client.post(
            f"user/{self.uid}/monetary-account/{aid}/bunqme-tab",
            {
                "bunqme_tab_entry": {
                    "amount_inquired": {"value": f"{amount_eur:.2f}", "currency": "EUR"},
                    "description": description,
                },
            },
        )
        tab_id = resp[0]["Id"]["id"]
        tab = self.client.get(
            f"user/{self.uid}/monetary-account/{aid}/bunqme-tab/{tab_id}"
        )
        share_url = tab[0]["BunqMeTab"].get("bunqme_tab_share_url", "")
        result = {
            "bunqme_tab_id": tab_id,
            "amount_eur": amount_eur,
            "share_url": share_url,
            "description": description,
        }
        bus.publish("step_finished", {"tool": "create_bunqme_link", "result": result})
        return result

    # ------------------------------------------------------------------
    # 8. set_card_status  (freeze / unfreeze)
    # ------------------------------------------------------------------

    def set_card_status(self, card_id: int, status: str) -> dict[str, Any]:
        if status not in ("ACTIVE", "DEACTIVATED"):
            raise ValueError(f"Invalid card status: {status}")
        bus.publish("step_started", {"tool": "set_card_status", "card_id": card_id, "status": status})
        self.client.put(f"user/{self.uid}/card/{card_id}", {"status": status})
        result = {"card_id": card_id, "status": status}
        bus.publish("step_finished", {"tool": "set_card_status", "result": result})
        return result

    def list_cards(self) -> list[dict[str, Any]]:
        resp = self.client.get(f"user/{self.uid}/card")
        return [item.get("CardDebit") or item.get("CardCredit") or {} for item in resp]

    def _first_active_card_id(self) -> int | None:
        for c in self.list_cards():
            if not c:
                continue
            if c.get("status") == "ACTIVE":
                return int(c["id"])
        # Fall back to any card with an id at all (sandbox sometimes has DEACTIVATED).
        for c in self.list_cards():
            if c and c.get("id"):
                return int(c["id"])
        return None

    def freeze_home_card(self) -> dict[str, Any]:
        """Convenience: find the first usable card and freeze it. For Travel Mode."""
        cid = self._first_active_card_id()
        if cid is None:
            bus.publish("step_error", {"tool": "freeze_home_card", "error": "no card found"})
            return {"ok": False, "error": "No card found on this sandbox user."}
        return self.set_card_status(cid, "DEACTIVATED")

    def unfreeze_home_card(self) -> dict[str, Any]:
        cid = self._first_active_card_id()
        if cid is None:
            bus.publish("step_error", {"tool": "unfreeze_home_card", "error": "no card found"})
            return {"ok": False, "error": "No card found on this sandbox user."}
        return self.set_card_status(cid, "ACTIVE")

    # ------------------------------------------------------------------
    # 10. register_webhook (Phase 2+, not exposed to Claude)
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

