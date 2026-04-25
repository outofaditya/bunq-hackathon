"""Multi-turn Claude tool-use loop for the Trip mission.

The trip mission is interactive: the user types into a chat panel, the agent
responds, fires tools, and (after `present_options` + a confirmation gate)
runs the bunq + browser cascade. Each `/chat` POST runs ONE turn via
`run_trip_turn`, possibly with many tool calls inside it.

Phase enforcement: `tool_catalog.trip_tools_for_phase(phase)` returns the tool
list passed to Claude on each turn — the model literally can't see execution
tools during UNDERSTAND.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Any, Callable

import anthropic

from . import image_gen
from .browser_agent import book_hotel_via_browser, search_trip_options
from .bunq_tools import BunqToolbox
from .events import bus
from .missions import MISSIONS
from .sessions import (
    PHASE_AWAITING_CONFIRMATION,
    PHASE_DONE,
    PHASE_EXECUTING,
    PHASE_UNDERSTANDING,
    TripSession,
)
from .side_tools import send_slack_message
from .tool_catalog import trip_tools_for_phase
from .tts import synthesize_narration


DEFAULT_MODEL = "claude-haiku-4-5-20251001"


# ---------------------------------------------------------------------------
# Synchronous-toolbox dispatch — wrapped in asyncio.to_thread by callers.
# ---------------------------------------------------------------------------

def _dispatch_bunq(toolbox: BunqToolbox, name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Route a tool_use block to the corresponding BunqToolbox method."""
    method_map: dict[str, Callable[..., dict[str, Any]]] = {
        "pay_vendor":                  toolbox.pay_vendor,
        "create_draft_payment":        toolbox.create_draft_payment,
        "schedule_recurring_payment":  toolbox.schedule_recurring_payment,
        "request_money":               toolbox.request_money,
    }
    fn = method_map.get(name)
    if fn is None:
        return {"error": f"Unknown bunq tool: {name}"}
    try:
        # Light rate-limit guard: sandbox POST is 5/3s.
        time.sleep(0.5)
        return fn(**args)
    except Exception as e:  # noqa: BLE001
        bus.publish("step_error", {"tool": name, "error": str(e)})
        return {"error": str(e)}


def _dispatch_book_hotel(args: dict[str, Any]) -> dict[str, Any]:
    """Sync wrapper around the async browser-agent hotel booker."""
    server_base = os.getenv("PUBLIC_BASE_URL", "").strip() or "http://localhost:8000"
    city = str(args.get("city", "Amsterdam"))
    nights = int(args.get("nights", 2))
    base_url = f"{server_base}/mock-hotel/?city={city}&nights={nights}"
    bus.publish("step_started", {"tool": "book_hotel", "city": city, "nights": nights})
    try:
        result = asyncio.run(book_hotel_via_browser(
            city=city,
            nights=nights,
            max_budget=float(args.get("max_budget_eur", 600)),
            base_url=base_url,
        ))
        bus.publish("step_finished", {"tool": "book_hotel", "result": result})
        return result
    except Exception as e:  # noqa: BLE001
        bus.publish("step_error", {"tool": "book_hotel", "error": str(e)})
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# Trip-mission tool dispatcher
# ---------------------------------------------------------------------------

async def _trip_dispatch(session: TripSession, toolbox: BunqToolbox, name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Run one tool call for the trip mission. Publishes its own events
    where needed (BunqToolbox methods publish theirs)."""

    # ---- UNDERSTAND-phase tools (UI publishers) ----
    if name == "search_trip_options":
        return await search_trip_options(
            query=str(args.get("query", "")),
            max_results=int(args.get("max_results", 6)),
        )

    if name == "present_options":
        intro = str(args.get("intro_text", ""))[:240]
        options = list(args.get("options") or [])
        bus.publish("options", {"intro": intro, "options": options})
        # Kick off cartoon-image generation in the background; cards render
        # immediately with a skeleton, images stream in via option_image events.
        for opt in options:
            asyncio.create_task(_trip_generate_option_image(opt))
        # Auto-flip phase: present_options happens once, after which we're
        # waiting for the user to pick + confirm.
        session.phase = PHASE_AWAITING_CONFIRMATION
        bus.publish("trip_phase", {"value": session.phase})
        return {"presented": len(options)}

    if name == "request_confirmation":
        summary = str(args.get("summary", ""))[:400]
        bus.publish("confirmation_request", {"summary": summary})
        return {"awaiting_user_yes": True}

    # ---- EXECUTE-phase tools ----
    if name == "create_sub_account":
        result = await asyncio.to_thread(
            toolbox.create_sub_account,
            str(args.get("name", "Trip")),
            float(args.get("goal_eur", 500)),
        )
        session.sub_account_id = result.get("account_id")
        session.sub_account_iban = result.get("iban")
        return result

    if name == "fund_sub_account":
        if not session.sub_account_iban:
            return {"error": "fund_sub_account called before create_sub_account"}
        return await asyncio.to_thread(
            toolbox.fund_sub_account,
            float(args.get("amount_eur", 0)),
            session.sub_account_iban,
        )

    if name == "narrate":
        text = str(args.get("text", ""))[:240]
        bus.publish("narrate", {"text": text})
        try:
            audio_filename = synthesize_narration(text)
            bus.publish("narrate_audio", {"text": text, "url": f"/tts/{audio_filename}"})
        except Exception as e:  # noqa: BLE001
            bus.publish("narrate_audio_error", {"text": text, "error": str(e)})
        return {"ok": True}

    if name == "finish_mission":
        summary = str(args.get("summary", ""))[:400]
        bus.publish("mission_complete", {"summary": summary})
        session.phase = PHASE_DONE
        bus.publish("trip_phase", {"value": session.phase})
        return {"ok": True}

    if name == "book_hotel":
        return await asyncio.to_thread(_dispatch_book_hotel, args)

    if name == "send_slack_message":
        return await asyncio.to_thread(
            send_slack_message,
            message=str(args.get("message", "")),
            header=args.get("header"),
        )

    # All remaining bunq mutations: pay_vendor, create_draft_payment,
    # schedule_recurring_payment, request_money.
    result = await asyncio.to_thread(_dispatch_bunq, toolbox, name, args)
    if name == "create_draft_payment" and isinstance(result, dict) and "draft_id" in result:
        session.pending_draft_ids.append(result["draft_id"])
    return result


async def _trip_generate_option_image(option: dict[str, Any]) -> None:
    """Background task: generate one option's cartoon postcard, publish on the bus."""
    option_id = option.get("id", "")
    try:
        url = await image_gen.generate_for_option(option)
    except Exception as e:  # noqa: BLE001
        print(f"[agent_loop] image gen crash for {option_id}: {e}", flush=True)
        url = None
    bus.publish("option_image", {
        "option_id": option_id,
        "image_url": url,
        "status": "ok" if url else "failed",
    })


# ---------------------------------------------------------------------------
# Multi-turn runner
# ---------------------------------------------------------------------------

_TRIP_YES_WORDS = {
    "yes", "y", "go", "confirm", "do it", "ok", "okay", "sure", "yep",
    "yeah", "proceed", "let's go", "approve", "lets go", "send it",
}


def _is_trip_yes(text: str) -> bool:
    t = (text or "").strip().lower().rstrip(".!?,")
    if not t:
        return False
    return any(t == w or t.startswith(w + " ") or t.endswith(" " + w) for w in _TRIP_YES_WORDS)


async def run_trip_turn(toolbox: BunqToolbox, session: TripSession, user_message: str, model: str | None = None) -> None:
    """Run one user turn of the trip mission. Many tool calls may fire inside it.

    Publishes:
      - user_message {text}        — echo
      - agent_message {text}       — final assistant text per turn
      - trip_phase {value}         — when phase flips
      - tool / step events as tools fire
    """
    client = anthropic.Anthropic()
    model = model or os.getenv("ANTHROPIC_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL

    bus.publish("user_message", {"text": user_message})

    # Confirmation gate — when the agent is awaiting a yes/no from the user.
    if session.phase == PHASE_AWAITING_CONFIRMATION and _is_trip_yes(user_message):
        session.phase = PHASE_EXECUTING
        bus.publish("trip_phase", {"value": session.phase})

    session.messages.append({"role": "user", "content": user_message})

    system_prompt = MISSIONS["trip"]["system_prompt"]
    system_blocks = [{"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}]

    for _ in range(20):  # iteration cap per turn
        tools = trip_tools_for_phase(session.phase)

        def _call() -> Any:
            return client.messages.create(
                model=model,
                max_tokens=2048,
                system=system_blocks,
                tools=tools,
                messages=session.messages,
            )

        resp = await asyncio.to_thread(_call)
        session.messages.append({"role": "assistant", "content": resp.content})

        # Emit final text blocks as agent messages.
        for block in resp.content:
            if getattr(block, "type", None) == "text":
                txt = block.text or ""
                if txt.strip():
                    bus.publish("agent_message", {"text": txt})

        tool_uses = [b for b in resp.content if b.type == "tool_use"]
        if not tool_uses:
            break  # Claude is done for this turn; wait for user reply.

        tool_results = []
        stop_after = False
        for tu in tool_uses:
            name = tu.name
            args = tu.input or {}

            # Some tools end the turn (request_confirmation; finish_mission).
            ends_turn = name in ("request_confirmation", "finish_mission")

            try:
                result = await _trip_dispatch(session, toolbox, name, args)
            except Exception as e:  # noqa: BLE001
                bus.publish("step_error", {"tool": name, "error": str(e)})
                result = {"error": str(e)}

            tool_results.append({
                "type": "tool_result",
                "tool_use_id": tu.id,
                "content": json.dumps(result, default=str),
            })
            if ends_turn:
                stop_after = True

        session.messages.append({"role": "user", "content": tool_results})
        if stop_after:
            break
