"""Money has feelings — sub-accounts as voiced personas.

This is the differentiator for Mission Mode. The user's bunq sub-accounts
already carry meaning (🌹 Sara Anniversary, 🇯🇵 Tokyo Fund, 🚨 Emergency).
We map each one to:

  - a personality archetype (romantic, defender, dreamer, …)
  - a distinct ElevenLabs voice
  - jittered prosody settings so each persona sounds like itself
  - a one-line catchphrase used as a fallback when Claude can't write a fresh one

Discovery is *dynamic*: we read the user's existing sub-accounts at boot
time. If the user has none, we create a starter cast on first use so the
demo always has voices in the room.

Persistence: archetype + voice mapping is cached in `bunq_personas.json`
(gitignored) so the LLM call doesn't run every server boot.
"""

from __future__ import annotations

import json
import os
import random
import time
from pathlib import Path
from typing import Any

import anthropic

from .events import bus
from .tts import synthesize_narration

PROJECT_ROOT = Path(__file__).resolve().parent.parent
REGISTRY_FILE = PROJECT_ROOT / "bunq_personas.json"

# Mark we add to descriptions of demo-created sub-accounts so we know they're
# safe to drain + cancel during cleanup. Real user accounts are left alone.
DEMO_TAG = " · MM"

# ---------------------------------------------------------------------------
# Voice + archetype catalogue (ElevenLabs free pre-made voices)
# ---------------------------------------------------------------------------
# Each archetype has its own voice_id and prosody profile. The ranges below
# are stability / similarity_boost / style. Per-call randomness gives natural
# variation without losing the persona's signature.
ARCHETYPES: dict[str, dict[str, Any]] = {
    "ROMANTIC": {
        "voice_id": "EXAVITQu4vr4xnSDxMaL",  # Bella — soft, warm female
        "stability": (0.18, 0.30),
        "similarity": (0.80, 0.92),
        "style":      (0.55, 0.75),
        "blurb": "Excitable, sentimental, defends grand gestures.",
    },
    "DREAMER": {
        "voice_id": "MF3mGyEYCl7XYWbV9V6O",  # Elli — youthful, hopeful female
        "stability": (0.22, 0.34),
        "similarity": (0.78, 0.90),
        "style":      (0.50, 0.70),
        "blurb": "Wistful, looks at the long horizon, hates short-termism.",
    },
    "PROTECTOR": {
        "voice_id": "AZnzlk1XvdvUeBnXmlld",  # Domi — confident, strong female
        "stability": (0.35, 0.50),
        "similarity": (0.82, 0.92),
        "style":      (0.30, 0.50),
        "blurb": "Gruff, holds the line on safety nets, takes no prisoners.",
    },
    "IMPATIENT": {
        "voice_id": "yoZ06aMxZJJ28mfd3POQ",  # Sam — raspy, hurried male
        "stability": (0.20, 0.32),
        "similarity": (0.78, 0.90),
        "style":      (0.50, 0.72),
        "blurb": "Wants everything yesterday, hates anything that delays the goal.",
    },
    "GRUMPY_TAX": {
        "voice_id": "VR6AewLTigWG4xSOukaG",  # Arnold — crisp, news-anchor male
        "stability": (0.45, 0.58),
        "similarity": (0.80, 0.90),
        "style":      (0.18, 0.32),
        "blurb": "Resentful pragmatist, bookkeeper energy, cuts to the cost.",
    },
    "PLEASER": {
        "voice_id": "TxGEqnHWrfWFTfGW9XjX",  # Josh — deep, warm narrator
        "stability": (0.30, 0.42),
        "similarity": (0.82, 0.92),
        "style":      (0.40, 0.62),
        "blurb": "Eager, optimistic, leans yes, hypes the dream.",
    },
    "CARETAKER": {
        "voice_id": "21m00Tcm4TlvDq8ikWAM",  # Rachel — calm, warm female
        "stability": (0.34, 0.46),
        "similarity": (0.85, 0.93),
        "style":      (0.30, 0.48),
        "blurb": "Steady, motherly, weighs trade-offs without drama.",
    },
    "ADVENTURER": {
        "voice_id": "pNInz6obpgDQGcFmaJgB",  # Adam — deep, bold male
        "stability": (0.24, 0.36),
        "similarity": (0.80, 0.90),
        "style":      (0.50, 0.70),
        "blurb": "Restless, pushes for the bold move, romanticises the unknown.",
    },
    "WISE": {
        "voice_id": "ErXwobaYiN019PkySvjV",  # Antoni — well-rounded, warm male
        "stability": (0.40, 0.52),
        "similarity": (0.85, 0.92),
        "style":      (0.30, 0.48),
        "blurb": "Steady elder, asks the question everyone's avoiding.",
    },
}

DEFAULT_ARCHETYPE = "CARETAKER"

# ---------------------------------------------------------------------------
# Starter cast — created if the user has no sub-accounts yet
# ---------------------------------------------------------------------------
STARTER_CAST: list[dict[str, str]] = [
    {"label": "🌹 Sara Anniversary", "archetype": "ROMANTIC",   "catchphrase": "She remembers everything you forget."},
    {"label": "🇯🇵 Tokyo Fund",      "archetype": "DREAMER",    "catchphrase": "Cherry blossoms don't wait."},
    {"label": "🚨 Emergency",        "archetype": "PROTECTOR",  "catchphrase": "I am the reason you sleep at night."},
    {"label": "🛵 Vespa",            "archetype": "IMPATIENT",  "catchphrase": "Every euro you spend elsewhere is a corner I don't take."},
    {"label": "🧂 Tax",              "archetype": "GRUMPY_TAX", "catchphrase": "Don't even think about it. The state already did."},
]


# ---------------------------------------------------------------------------
# Registry on disk
# ---------------------------------------------------------------------------
def _load_registry() -> dict[str, Any]:
    if not REGISTRY_FILE.exists():
        return {"personas": {}}
    try:
        return json.loads(REGISTRY_FILE.read_text())
    except Exception:  # noqa: BLE001
        return {"personas": {}}


def _save_registry(reg: dict[str, Any]) -> None:
    REGISTRY_FILE.write_text(json.dumps(reg, indent=2, ensure_ascii=False))


# ---------------------------------------------------------------------------
# Persona class — dataclass-ish dict so it serialises trivially
# ---------------------------------------------------------------------------
def _make_persona(
    *,
    account_id: int,
    iban: str,
    name: str,
    archetype: str,
    catchphrase: str,
    balance_eur: float = 0.0,
    is_demo: bool = False,
) -> dict[str, Any]:
    arch = ARCHETYPES.get(archetype, ARCHETYPES[DEFAULT_ARCHETYPE])
    return {
        "account_id":   account_id,
        "iban":         iban,
        "name":         name,
        "archetype":    archetype,
        "voice_id":     arch["voice_id"],
        "blurb":        arch["blurb"],
        "catchphrase":  catchphrase,
        "balance_eur":  float(balance_eur),
        "is_demo":      is_demo,
    }


def _voice_settings(archetype: str, seed: str) -> dict[str, float]:
    """Per-call jittered ElevenLabs settings derived from archetype range."""
    arch = ARCHETYPES.get(archetype, ARCHETYPES[DEFAULT_ARCHETYPE])
    rng = random.Random(seed)
    return {
        "stability":        round(rng.uniform(*arch["stability"]), 2),
        "similarity_boost": round(rng.uniform(*arch["similarity"]), 2),
        "style":            round(rng.uniform(*arch["style"]), 2),
        "use_speaker_boost": True,
    }


# ---------------------------------------------------------------------------
# Personality assignment — Claude Haiku reads name + emoji and picks archetype
# ---------------------------------------------------------------------------
def _claude_assign_archetypes(labels: list[str]) -> list[dict[str, str]]:
    """Ask Haiku to assign an archetype + 1-line catchphrase to each label.

    Returns a list of {archetype, catchphrase} aligned to `labels`. Falls back
    to CARETAKER + a generic line if anything breaks.
    """
    if not labels:
        return []
    options = ", ".join(ARCHETYPES.keys())
    prompt = (
        "You are giving voice to someone's bunq sub-accounts. Each one is a "
        "savings goal with a personality. For each account label below, pick "
        f"the archetype that fits BEST from this list: {options}. Then write a "
        "one-line catchphrase the account would say in its own voice — present "
        "tense, no quotes, under 14 words, with personality.\n\n"
        "Reply with strict JSON: a list of {\"label\", \"archetype\", \"catchphrase\"} "
        "objects, in the same order as the input. NO commentary.\n\n"
        "Labels:\n" + "\n".join(f"- {l}" for l in labels)
    )
    try:
        client = anthropic.Anthropic()
        model = os.getenv("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001").strip() or "claude-haiku-4-5-20251001"
        resp = client.messages.create(
            model=model,
            max_tokens=600,
            messages=[{"role": "user", "content": prompt}],
        )
        text = ""
        for b in resp.content:
            if getattr(b, "type", None) == "text":
                text += b.text
        # Strip markdown fencing if present
        text = text.strip()
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        data = json.loads(text)
        out: list[dict[str, str]] = []
        for i, label in enumerate(labels):
            entry = data[i] if i < len(data) else {}
            arch = str(entry.get("archetype", DEFAULT_ARCHETYPE)).upper()
            if arch not in ARCHETYPES:
                arch = DEFAULT_ARCHETYPE
            out.append({
                "archetype":   arch,
                "catchphrase": str(entry.get("catchphrase", ""))[:120] or "I have opinions about your money.",
            })
        return out
    except Exception as e:  # noqa: BLE001
        bus.publish("persona_assign_warning", {"error": str(e)})
        return [
            {"archetype": DEFAULT_ARCHETYPE, "catchphrase": "I have opinions about your money."}
            for _ in labels
        ]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
class PersonaManager:
    """Knows how to find, create, drain, and voice the user's persona accounts.

    Held by BunqToolbox so missions can reach into it.
    """

    def __init__(self, toolbox: "BunqToolboxLike") -> None:  # noqa: F821
        self.tb = toolbox
        self.client = toolbox.client
        self.uid = toolbox.uid
        self._registry = _load_registry()
        self._self_name = self._discover_self_name()

    # -- bunq i/o -------------------------------------------------------

    def _discover_self_name(self) -> str:
        """Account-holder name from the primary IBAN alias — needed for own-IBAN transfers."""
        try:
            data = self.client.get(f"user/{self.uid}/monetary-account-bank/{self.tb.primary_id}")
            for alias in data[0]["MonetaryAccountBank"]["alias"]:
                if alias["type"] == "IBAN":
                    return alias.get("name") or "Mission Mode"
        except Exception:  # noqa: BLE001
            pass
        return "Mission Mode"

    def _list_active_subs(self) -> list[dict[str, Any]]:
        """Every ACTIVE bunq sub-account except the primary."""
        data = self.client.get(f"user/{self.uid}/monetary-account-bank")
        out: list[dict[str, Any]] = []
        for item in data:
            acc = item.get("MonetaryAccountBank") or {}
            if acc.get("status") != "ACTIVE":
                continue
            if int(acc.get("id", 0)) == int(self.tb.primary_id):
                continue
            iban = next((a["value"] for a in acc.get("alias", []) if a["type"] == "IBAN"), "")
            out.append({
                "id":          int(acc["id"]),
                "iban":        iban,
                "description": acc.get("description") or "(no name)",
                "balance_eur": float(acc.get("balance", {}).get("value", "0.00")),
            })
        return out

    def _create_sub(self, description: str) -> dict[str, Any]:
        """POST a new EUR sub-account — used to seed the starter cast."""
        resp = self.client.post(
            f"user/{self.uid}/monetary-account-bank",
            {"currency": "EUR", "description": description},
        )
        new_id = int(resp[0]["Id"]["id"])
        # Refetch to grab the IBAN
        data = self.client.get(f"user/{self.uid}/monetary-account-bank/{new_id}")
        iban = next(
            a["value"] for a in data[0]["MonetaryAccountBank"]["alias"] if a["type"] == "IBAN"
        )
        bus.publish("persona_account_created", {
            "account_id": new_id,
            "iban":       iban,
            "description": description,
        })
        return {"id": new_id, "iban": iban}

    def _cancel_sub(self, account_id: int) -> None:
        self.client.put(
            f"user/{self.uid}/monetary-account-bank/{account_id}",
            {
                "status":             "CANCELLED",
                "sub_status":         "REDEMPTION_VOLUNTARY",
                "reason":             "OTHER",
                "reason_description": "Mission Mode demo cleanup",
            },
        )

    def _transfer(
        self,
        from_id: int,
        to_iban: str,
        to_name: str,
        amount_eur: float,
        description: str,
    ) -> int:
        """Move EUR between two accounts the same user owns (own-IBAN payment)."""
        if amount_eur <= 0:
            return 0
        resp = self.client.post(
            f"user/{self.uid}/monetary-account/{from_id}/payment",
            {
                "amount":             {"value": f"{amount_eur:.2f}", "currency": "EUR"},
                "counterparty_alias": {"type": "IBAN", "value": to_iban, "name": to_name},
                "description":        description,
            },
        )
        return int(resp[0]["Id"]["id"])

    # -- persona discovery / creation ----------------------------------

    def discover_or_create(self, *, ensure_min: int = 5) -> list[dict[str, Any]]:
        """Return the persona registry. If the user has fewer sub-accounts than
        `ensure_min`, top up with the starter cast so the demo always has voices.
        """
        subs = self._list_active_subs()

        # Decide if we need to create starters
        if len(subs) < ensure_min:
            existing_descs = {s["description"] for s in subs}
            for tpl in STARTER_CAST:
                label = tpl["label"]
                tagged = label + DEMO_TAG
                # Only create if neither the plain nor tagged label already exists.
                if label in existing_descs or tagged in existing_descs:
                    continue
                if len(subs) >= ensure_min:
                    break
                try:
                    info = self._create_sub(tagged)
                    subs.append({
                        "id":          info["id"],
                        "iban":        info["iban"],
                        "description": tagged,
                        "balance_eur": 0.0,
                    })
                    # Seed it with €40 from primary so balances render meaningfully.
                    try:
                        self._transfer(
                            from_id=self.tb.primary_id,
                            to_iban=info["iban"],
                            to_name=self._self_name,
                            amount_eur=40.0,
                            description=f"Seed → {label}",
                        )
                    except Exception as e:  # noqa: BLE001
                        bus.publish("persona_seed_warning", {
                            "label": label, "error": str(e),
                        })
                    time.sleep(0.6)  # bunq sandbox rate-limit cushion
                except Exception as e:  # noqa: BLE001
                    bus.publish("persona_create_warning", {
                        "label": label, "error": str(e),
                    })

        # Now build personas. Use cached archetype if we've seen this label
        # before; otherwise ask Claude in one batched call.
        cache: dict[str, Any] = self._registry.get("personas", {})
        unknown_labels: list[str] = [s["description"] for s in subs if s["description"] not in cache]
        new_assignments: list[dict[str, str]] = []
        if unknown_labels:
            new_assignments = _claude_assign_archetypes(unknown_labels)

        # Allow STARTER_CAST entries to bypass Claude with their canonical archetype.
        starter_lookup: dict[str, dict[str, str]] = {}
        for tpl in STARTER_CAST:
            starter_lookup[tpl["label"]] = tpl
            starter_lookup[tpl["label"] + DEMO_TAG] = tpl

        personas: list[dict[str, Any]] = []
        new_idx = 0
        for s in subs:
            label = s["description"]
            if label in cache:
                arch = cache[label]["archetype"]
                catch = cache[label]["catchphrase"]
            elif label in starter_lookup:
                arch = starter_lookup[label]["archetype"]
                catch = starter_lookup[label]["catchphrase"]
            else:
                if new_idx < len(new_assignments):
                    arch = new_assignments[new_idx]["archetype"]
                    catch = new_assignments[new_idx]["catchphrase"]
                    new_idx += 1
                else:
                    arch = DEFAULT_ARCHETYPE
                    catch = "I have opinions about your money."

            cache[label] = {"archetype": arch, "catchphrase": catch}
            personas.append(_make_persona(
                account_id=s["id"],
                iban=s["iban"],
                name=label,
                archetype=arch,
                catchphrase=catch,
                balance_eur=s["balance_eur"],
                is_demo=label.endswith(DEMO_TAG),
            ))

        # Save registry
        self._registry["personas"] = cache
        _save_registry(self._registry)

        bus.publish("personas_loaded", {"personas": personas})
        return personas

    # -- genesis ------------------------------------------------------
    #
    # Public visible boot of the council: seed primary, then create each
    # starter-cast persona one at a time, fund with a randomised amount,
    # emit a per-step event so the dashboard can animate tile-by-tile.
    # Idempotent — skips any persona whose account is already created.

    def run_genesis(
        self,
        *,
        seed_primary_eur: float = 600.0,
        random_seed: int | None = None,
    ) -> dict[str, Any]:
        """Run the visible Council bring-up. Returns a summary dict; emits:
          - genesis_started
          - genesis_step_started   (per persona, before bunq calls)
          - persona_account_created (when bunq POST returns)
          - persona_funded          (when seed transfer lands)
          - genesis_step_finished   (per persona, after fund + sleep)
          - personas_loaded         (final registry, full cast)
          - genesis_complete        (after the last persona is in)
        """
        rng = random.Random(random_seed)
        bus.publish("genesis_started", {
            "primary_id":  self.tb.primary_id,
            "cast_size":   len(STARTER_CAST),
            "seed_eur":    seed_primary_eur,
        })

        # Top up primary so we have euros to scatter.
        try:
            self.tb.seed_primary(seed_primary_eur)
            self.tb.snapshot_balance("genesis_seed")
        except Exception as e:  # noqa: BLE001
            bus.publish("genesis_warning", {"step": "seed_primary", "error": str(e)})

        # Snapshot what already exists so we know what to skip.
        existing_subs = {s["description"]: s for s in self._list_active_subs()}
        cache: dict[str, Any] = self._registry.get("personas", {})
        created: list[dict[str, Any]] = []
        skipped: list[str] = []

        for tpl in STARTER_CAST:
            label = tpl["label"]
            tagged = label + DEMO_TAG
            archetype = tpl["archetype"]
            catchphrase = tpl.get("catchphrase", "")
            seed_amt = float(rng.choice([20, 30, 40, 50, 60, 70, 80]))

            bus.publish("genesis_step_started", {
                "label":      tagged,
                "emoji":      label.split(" ", 1)[0] if " " in label else "💰",
                "archetype":  archetype,
                "seed_eur":   seed_amt,
                "skipped":    tagged in existing_subs or label in existing_subs,
            })
            print(f"[genesis] {tagged} (archetype={archetype}, seed=€{seed_amt:.0f})", flush=True)

            # Existence check — skip if we already created it
            existing = existing_subs.get(tagged) or existing_subs.get(label)
            if existing:
                skipped.append(tagged)
                cache[existing["description"]] = {
                    "archetype":   archetype,
                    "catchphrase": catchphrase or ARCHETYPES[archetype]["blurb"],
                }
                created.append(_make_persona(
                    account_id=existing["id"],
                    iban=existing["iban"],
                    name=existing["description"],
                    archetype=archetype,
                    catchphrase=catchphrase or ARCHETYPES[archetype]["blurb"],
                    balance_eur=existing["balance_eur"],
                    is_demo=existing["description"].endswith(DEMO_TAG),
                ))
                bus.publish("genesis_step_finished", {
                    "label":         existing["description"],
                    "account_id":    existing["id"],
                    "balance_eur":   existing["balance_eur"],
                    "archetype":     archetype,
                    "skipped":       True,
                })
                time.sleep(0.4)
                continue

            try:
                info = self._create_sub(tagged)
                time.sleep(0.7)  # rate-limit cushion (bunq sandbox: 5 POST / 3s)
                self._transfer(
                    from_id=self.tb.primary_id,
                    to_iban=info["iban"],
                    to_name=self._self_name,
                    amount_eur=seed_amt,
                    description=f"Genesis → {label}",
                )
                bus.publish("persona_funded", {
                    "account_id": info["id"],
                    "label":      tagged,
                    "amount_eur": seed_amt,
                })
                cache[tagged] = {
                    "archetype":   archetype,
                    "catchphrase": catchphrase or ARCHETYPES[archetype]["blurb"],
                }
                persona = _make_persona(
                    account_id=info["id"],
                    iban=info["iban"],
                    name=tagged,
                    archetype=archetype,
                    catchphrase=cache[tagged]["catchphrase"],
                    balance_eur=seed_amt,
                    is_demo=True,
                )
                created.append(persona)
                # Incremental reveal: emit the partial cast so the dashboard
                # animates each tile in as it lands.
                bus.publish("personas_loaded", {"personas": list(created)})
                bus.publish("genesis_step_finished", {
                    "label":       tagged,
                    "account_id":  info["id"],
                    "balance_eur": seed_amt,
                    "archetype":   archetype,
                    "skipped":     False,
                })
                time.sleep(0.5)
            except Exception as e:  # noqa: BLE001
                bus.publish("genesis_warning", {"step": "create_or_fund", "label": tagged, "error": str(e)})
                time.sleep(0.4)

        self._registry["personas"] = cache
        _save_registry(self._registry)

        bus.publish("personas_loaded", {"personas": created})
        bus.publish("genesis_complete", {
            "count":   len(created),
            "created": [p["name"] for p in created if p["name"] not in skipped],
            "skipped": skipped,
        })
        try:
            self.tb.snapshot_balance("genesis_complete")
        except Exception:  # noqa: BLE001
            pass
        return {"ok": True, "personas": created, "skipped": skipped}

    # -- council voice synth -------------------------------------------

    def speak(
        self,
        persona_id: int,
        text: str,
        *,
        stance: str = "neutral",
    ) -> dict[str, Any]:
        """Synthesize one persona line and publish a `persona_speaks` event.

        Falls back gracefully if voice synth fails — still publishes the event
        so the dashboard renders the bubble even with no audio.
        """
        personas = self.list_cached()
        match = next((p for p in personas if p["account_id"] == persona_id), None)
        if not match:
            # Try refreshing registry once before giving up
            self.discover_or_create()
            personas = self.list_cached()
            match = next((p for p in personas if p["account_id"] == persona_id), None)
        if not match:
            return {"ok": False, "error": f"Unknown persona id {persona_id}"}

        text = (text or "").strip()
        if not text:
            text = match["catchphrase"]

        audio_url: str | None = None
        try:
            settings = _voice_settings(match["archetype"], seed=f"{persona_id}-{text[:24]}")
            fname = synthesize_narration(
                text=text,
                voice_id=match["voice_id"],
            )
            audio_url = f"/tts/{fname}"
            # Tag the file with our chosen settings post-hoc — the underlying
            # synth helper already jitters; we accept its randomness for now.
            _ = settings  # documented for future use
        except Exception as e:  # noqa: BLE001
            bus.publish("persona_voice_error", {"persona_id": persona_id, "error": str(e)})

        payload = {
            "persona_id": persona_id,
            "name":       match["name"],
            "archetype":  match["archetype"],
            "voice_id":   match["voice_id"],
            "stance":     stance,
            "text":       text,
            "audio_url":  audio_url,
        }
        bus.publish("persona_speaks", payload)
        return {"ok": True, **payload}

    def list_cached(self) -> list[dict[str, Any]]:
        """Cheap re-read of the personas — re-fetches balances from bunq."""
        # Fast path: registry has labels, just join with current sub balances.
        cache = self._registry.get("personas", {})
        if not cache:
            return self.discover_or_create()
        try:
            subs = self._list_active_subs()
        except Exception:  # noqa: BLE001
            subs = []
        out: list[dict[str, Any]] = []
        for s in subs:
            label = s["description"]
            entry = cache.get(label)
            if not entry:
                continue  # account not yet known to registry; refresh would catch it
            out.append(_make_persona(
                account_id=s["id"],
                iban=s["iban"],
                name=label,
                archetype=entry["archetype"],
                catchphrase=entry["catchphrase"],
                balance_eur=s["balance_eur"],
                is_demo=label.endswith(DEMO_TAG),
            ))
        return out

    # -- council verdict actions ---------------------------------------

    def payout_to_personas(
        self,
        distributions: list[dict[str, Any]],
        description: str = "Council payout",
    ) -> list[dict[str, Any]]:
        """Move money from primary to a list of personas.

        `distributions` = [{persona_id, amount_eur}, …]
        Emits `persona_payout` events; safe-no-op if amount <= 0.
        """
        personas = self.list_cached()
        by_id = {p["account_id"]: p for p in personas}
        results: list[dict[str, Any]] = []
        for d in distributions or []:
            try:
                pid = int(d["persona_id"])
                amt = float(d["amount_eur"])
            except (KeyError, TypeError, ValueError):
                continue
            persona = by_id.get(pid)
            if not persona or amt <= 0:
                continue
            try:
                payment_id = self._transfer(
                    from_id=self.tb.primary_id,
                    to_iban=persona["iban"],
                    to_name=self._self_name,
                    amount_eur=amt,
                    description=description,
                )
                bus.publish("persona_payout", {
                    "persona_id":   pid,
                    "name":         persona["name"],
                    "amount_eur":   amt,
                    "payment_id":   payment_id,
                    "description":  description,
                })
                results.append({"persona_id": pid, "ok": True, "amount_eur": amt, "payment_id": payment_id})
                time.sleep(0.5)  # bunq rate-limit cushion
            except Exception as e:  # noqa: BLE001
                bus.publish("persona_payout_error", {"persona_id": pid, "error": str(e)})
                results.append({"persona_id": pid, "ok": False, "error": str(e)})
        return results

    # -- cleanup --------------------------------------------------------

    def cleanup_demo_accounts(self, *, dry: bool = False) -> dict[str, Any]:
        """Drain + cancel any sub-account whose description carries DEMO_TAG.

        Real (untagged) accounts the user pre-created are NEVER touched.
        Returns a summary {drained, cancelled, skipped}.
        """
        subs = self._list_active_subs()
        drained = 0
        cancelled = 0
        skipped = 0
        details: list[dict[str, Any]] = []
        for s in subs:
            if not s["description"].endswith(DEMO_TAG):
                skipped += 1
                continue
            entry: dict[str, Any] = {
                "account_id":  s["id"],
                "description": s["description"],
                "balance_eur": s["balance_eur"],
            }
            if dry:
                entry["action"] = "would-cancel"
                details.append(entry)
                continue
            try:
                if s["balance_eur"] > 0.005:
                    primary_iban = self.tb.primary_iban
                    self._transfer(
                        from_id=s["id"],
                        to_iban=primary_iban,
                        to_name=self._self_name,
                        amount_eur=round(s["balance_eur"], 2),
                        description=f"Drain {s['description']}",
                    )
                    drained += 1
                    time.sleep(0.5)
                self._cancel_sub(s["id"])
                cancelled += 1
                entry["action"] = "cancelled"
                time.sleep(0.4)
            except Exception as e:  # noqa: BLE001
                entry["action"] = "error"
                entry["error"] = str(e)
            details.append(entry)

        # Wipe registry entries that pointed at demo-tagged labels
        if not dry:
            cache = self._registry.get("personas", {})
            for label in list(cache.keys()):
                if label.endswith(DEMO_TAG):
                    del cache[label]
            self._registry["personas"] = cache
            _save_registry(self._registry)

        summary = {
            "dry":       dry,
            "drained":   drained,
            "cancelled": cancelled,
            "skipped":   skipped,
            "details":   details,
        }
        bus.publish("persona_cleanup", summary)
        return summary
