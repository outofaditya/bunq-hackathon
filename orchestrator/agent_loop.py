"""Claude tool-use loop with prompt caching.

Runs a mission end-to-end. Each tool call is dispatched to the toolbox.
Narration and mission-complete events are published to the SSE bus.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Callable

import anthropic

import asyncio

from .browser_agent import (
    book_hotel_via_browser,
    book_restaurant_via_browser,
    subscribe_to_service_via_browser,
)
from .bunq_tools import BunqToolbox
from .events import bus
from .side_tools import create_calendar_event, send_slack_message
from .tool_catalog import BUNQ_TOOLS
from .tts import synthesize_narration


DEFAULT_MODEL = "claude-haiku-4-5-20251001"


def _dispatch_bunq(toolbox: BunqToolbox, name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Route a tool_use block to the corresponding BunqToolbox method.

    Returns a JSON-safe dict (tool_result payload for Anthropic).
    """
    method_map: dict[str, Callable[..., dict[str, Any]]] = {
        "pay_vendor": toolbox.pay_vendor,
        "create_draft_payment": toolbox.create_draft_payment,
        "schedule_recurring_payment": toolbox.schedule_recurring_payment,
        "request_money": toolbox.request_money,
        "create_bunqme_link": toolbox.create_bunqme_link,
        "set_card_status": toolbox.set_card_status,
        "freeze_home_card": toolbox.freeze_home_card,
        "unfreeze_home_card": toolbox.unfreeze_home_card,
        "list_personas": toolbox.list_personas,
        "council_payout": toolbox.council_payout,
        "cleanup_demo_subs": toolbox.cleanup_demo_subs,
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


def _generate_closing_line(
    client: anthropic.Anthropic,
    model: str,
    draft_status: str,
    prior_narrations: list[str],
    final_summary: str | None,
) -> str:
    """Have Claude write a fresh closing line that fits the mission outcome.

    Avoids the canned 'Mission complete.' sound — every run says it slightly
    differently. Falls back to a safe default on API error.
    """
    status_hint = {
        "ACCEPTED": "the user just approved the pending payment on their phone — release energy, warm follow-through",
        "REJECTED": "the user rejected the pending payment — accept that gracefully, no judgement",
        "TIMEOUT":  "the user didn't approve in time — gentle reminder, no pressure",
    }.get(draft_status, "the cascade is wrapping up")

    bullets = "\n".join(f"- {n}" for n in prior_narrations[-4:])
    summary = final_summary or "(none)"

    prompt = (
        "You just helped a friend with a small errand and you're saying goodbye. Speak naturally, "
        "like a real person — warm, simple, with a contraction. End with a small well-wish that "
        "fits the moment ('enjoy', 'have fun', 'go relax', 'sleep easy', etc).\n\n"
        "Plain words only. NEVER say 'executing', 'processing', 'transaction', 'kindly', 'as per', "
        "or anything a corporate IVR would say. No quote marks, no emoji.\n\n"
        f"How it landed: {status_hint}.\n"
        f"Earlier things you said this run:\n{bullets or '(none)'}\n"
        f"Your pre-approval line was: {summary}\n\n"
        "Reply with EXACTLY one short sentence (8 to 14 words). End with a period. "
        "Don't reuse phrasing from earlier lines."
    )

    try:
        resp = client.messages.create(
            model=model,
            max_tokens=80,
            messages=[{"role": "user", "content": prompt}],
        )
        text = ""
        for block in resp.content:
            if getattr(block, "type", None) == "text":
                text += block.text
        text = text.strip().strip('"').strip("'")
        if not text:
            raise ValueError("empty closing")
        return text
    except Exception:  # noqa: BLE001
        return "Mission wrapped."


def _dispatch_browser(tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Run the async browser agent from inside the synchronous agent loop.

    Routes to the right task wrapper based on the tool name.
    """
    server_base = os.getenv("PUBLIC_BASE_URL", "").strip() or "http://localhost:8000"
    bus.publish("step_started", {"tool": tool_name, **{k: v for k, v in args.items() if not isinstance(v, (dict, list))}})

    try:
        if tool_name == "book_restaurant":
            base_url = os.getenv("MOCK_RESTAURANT_URL", f"{server_base}/mock-restaurant/").strip()
            result = asyncio.run(book_restaurant_via_browser(
                restaurant_hint=str(args.get("restaurant_hint", "")),
                max_budget=float(args.get("max_budget_eur", 100)),
                when=str(args.get("when", "Friday 19:30")),
                base_url=base_url,
            ))
        elif tool_name == "book_hotel":
            city = str(args.get("city", "Tokyo"))
            nights = int(args.get("nights", 3))
            base_url = f"{server_base}/mock-hotel/?city={city}&nights={nights}"
            result = asyncio.run(book_hotel_via_browser(
                city=city,
                nights=nights,
                max_budget=float(args.get("max_budget_eur", 600)),
                base_url=base_url,
            ))
        elif tool_name == "subscribe_to_service":
            category = str(args.get("category", "streaming"))
            base_url = f"{server_base}/mock-subscriptions/?category={category}"
            result = asyncio.run(subscribe_to_service_via_browser(
                category=category,
                max_monthly_eur=float(args.get("max_monthly_eur", 20)),
                base_url=base_url,
            ))
        else:
            return {"error": f"Unknown browser tool: {tool_name}"}

        bus.publish("step_finished", {"tool": tool_name, "result": result})
        return result
    except Exception as e:  # noqa: BLE001
        bus.publish("step_error", {"tool": tool_name, "error": str(e)})
        return {"error": str(e)}


def run_mission(
    toolbox: BunqToolbox,
    system_prompt: str,
    user_prompt: str,
    model: str | None = None,
    max_iterations: int = 20,
    wait_for_draft: bool = True,
    wait_timeout_s: float = 60.0,
) -> dict[str, Any]:
    """Run one mission end-to-end.

    Returns a summary dict with the final mission state.
    """
    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from env
    model = model or os.getenv("ANTHROPIC_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL

    bus.publish("mission_started", {"model": model, "user_prompt": user_prompt})

    # Prompt caching: the system prompt + tool catalog are stable across the
    # many tool-result iterations, so mark them ephemeral-cacheable. Dramatic
    # latency / cost win on Haiku.
    system_blocks = [
        {
            "type": "text",
            "text": system_prompt,
            "cache_control": {"type": "ephemeral"},
        }
    ]

    messages: list[dict[str, Any]] = [
        {"role": "user", "content": user_prompt}
    ]

    last_draft_id: int | None = None
    final_summary: str | None = None
    narrations: list[str] = []

    for _ in range(max_iterations):
        resp = client.messages.create(
            model=model,
            max_tokens=2048,
            system=system_blocks,
            tools=BUNQ_TOOLS,
            messages=messages,
        )

        # Add the assistant's response to the transcript.
        messages.append({"role": "assistant", "content": resp.content})

        tool_uses = [b for b in resp.content if b.type == "tool_use"]
        if not tool_uses:
            # No more tools — Claude's done.
            break

        tool_results = []
        stop_now = False

        for tu in tool_uses:
            name = tu.name
            args = tu.input or {}

            if name == "narrate":
                text = str(args.get("text", ""))[:240]
                narrations.append(text)
                bus.publish("narrate", {"text": text})
                # Synthesize speech and emit a follow-up event with the audio URL.
                try:
                    audio_filename = synthesize_narration(text)
                    bus.publish("narrate_audio", {"text": text, "url": f"/tts/{audio_filename}"})
                except Exception as e:  # noqa: BLE001
                    bus.publish("narrate_audio_error", {"text": text, "error": str(e)})
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tu.id,
                    "content": json.dumps({"ok": True}),
                })
                continue

            if name == "finish_mission":
                final_summary = str(args.get("summary", ""))[:400]
                bus.publish("mission_complete", {"summary": final_summary})
                # Synthesize the closing line too.
                try:
                    audio_filename = synthesize_narration(final_summary)
                    bus.publish("narrate_audio", {"text": final_summary, "url": f"/tts/{audio_filename}"})
                except Exception as e:  # noqa: BLE001
                    bus.publish("narrate_audio_error", {"text": final_summary, "error": str(e)})
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tu.id,
                    "content": json.dumps({"ok": True}),
                })
                stop_now = True
                continue

            if name in ("book_restaurant", "book_hotel", "subscribe_to_service"):
                result = _dispatch_browser(name, args)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tu.id,
                    "content": json.dumps(result, default=str),
                })
                continue

            if name == "send_slack_message":
                result = send_slack_message(
                    message=str(args.get("message", "")),
                    header=args.get("header"),
                )
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tu.id,
                    "content": json.dumps(result, default=str),
                })
                continue

            if name == "create_calendar_event":
                result = create_calendar_event(
                    title=str(args.get("title", "Mission event")),
                    description=args.get("description"),
                    when=args.get("when"),
                    duration_minutes=int(args.get("duration_minutes", 120)),
                    invitees=args.get("invitees") or [],
                )
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tu.id,
                    "content": json.dumps(result, default=str),
                })
                continue

            if name == "persona_speak":
                # Voice synth + bus event — no bunq mutation, no balance snapshot.
                result = toolbox.persona_speak(
                    persona_id=int(args.get("persona_id", 0)),
                    text=str(args.get("text", "")),
                    stance=str(args.get("stance", "neutral")),
                )
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tu.id,
                    "content": json.dumps(result, default=str),
                })
                continue

            if name == "request_confirmation":
                # Blocks the worker thread up to timeout_s waiting for the
                # user to record an answer via /missions/council/confirm.
                wpid = args.get("winning_persona_id")
                result = toolbox.await_user_confirmation(
                    question=str(args.get("question", "Should I execute this?")),
                    action_summary=str(args.get("action_summary", "")),
                    winning_persona_id=int(wpid) if wpid is not None else None,
                    timeout_s=float(args.get("timeout_s", 25.0)),
                )
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tu.id,
                    "content": json.dumps(result, default=str),
                })
                continue

            if name == "council_verdict":
                payload = {
                    "verdict":    str(args.get("verdict", "APPROVE")).upper(),
                    "amount_eur": float(args.get("amount_eur", 0.0)),
                    "reasoning":  str(args.get("reasoning", ""))[:240],
                }
                bus.publish("council_verdict", payload)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tu.id,
                    "content": json.dumps({"ok": True, **payload}, default=str),
                })
                continue

            if name == "list_personas":
                # Returns a list (not a dict) — special-case so we don't crash _dispatch_bunq.
                try:
                    personas = toolbox.list_personas(ensure_min=int(args.get("ensure_min", 5)))
                    result = {"personas": personas, "count": len(personas)}
                except Exception as e:  # noqa: BLE001
                    bus.publish("step_error", {"tool": name, "error": str(e)})
                    result = {"error": str(e)}
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tu.id,
                    "content": json.dumps(result, default=str),
                })
                continue

            result = _dispatch_bunq(toolbox, name, args)
            if name == "create_draft_payment" and "draft_id" in result:
                last_draft_id = result["draft_id"]
            # Emit balance snapshot after every bunq-mutating tool, except
            # for persona-registry ops which don't move money on the primary.
            if name not in ("cleanup_demo_subs",):
                try:
                    toolbox.snapshot_balance(step_label=name)
                except Exception as e:  # noqa: BLE001
                    bus.publish("balance_snapshot_error", {"step": name, "error": str(e)})
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": tu.id,
                "content": json.dumps(result, default=str),
            })

        messages.append({"role": "user", "content": tool_results})
        if stop_now:
            break

    # After Claude's cascade finishes, give the presenter a window to tap
    # approve on the bunq app. Polls for up to 60 seconds.
    if wait_for_draft and last_draft_id is not None:
        bus.publish("awaiting_draft_approval", {"draft_id": last_draft_id, "timeout_s": wait_timeout_s})
        status = toolbox.wait_for_draft_approval(last_draft_id, timeout_s=wait_timeout_s)
        bus.publish("draft_final", {"draft_id": last_draft_id, "status": status})
        # One more balance snapshot after the draft is resolved.
        try:
            toolbox.snapshot_balance(step_label=f"draft_{status.lower()}")
        except Exception as e:  # noqa: BLE001
            bus.publish("balance_snapshot_error", {"step": "draft_resolved", "error": str(e)})

        # Closing narrative — generated fresh by Claude each run for natural variation.
        closing = _generate_closing_line(client, model, status, narrations, final_summary)

        bus.publish("narrate", {"text": closing})
        narrations.append(closing)
        try:
            audio_filename = synthesize_narration(closing)
            bus.publish("narrate_audio", {"text": closing, "url": f"/tts/{audio_filename}"})
        except Exception as e:  # noqa: BLE001
            bus.publish("narrate_audio_error", {"text": closing, "error": str(e)})
        bus.publish("mission_finalized", {"status": status, "summary": closing})

    return {
        "final_summary": final_summary,
        "narrations": narrations,
        "draft_id": last_draft_id,
        "primary_id": toolbox.primary_id,
        "primary_iban": toolbox.primary_iban,
    }


# ============================================================================
# Trip mission — multi-turn interactive runner.
#
# Unlike `run_mission` (single-shot cascade), the trip mission alternates
# between agent and user. Each call to `run_trip_turn` runs ONE conversational
# turn, possibly including many tool calls, then returns control so the user
# can reply via the chat panel. State is stored in TripSession.
# ============================================================================

from . import image_gen
from .browser_agent import search_trip_options
from .missions import MISSIONS
from .sessions import (
    PHASE_AWAITING_CONFIRMATION,
    PHASE_DONE,
    PHASE_EXECUTING,
    PHASE_UNDERSTANDING,
    TripSession,
    get_or_create as get_session,
)
from .tool_catalog import trip_tools_for_phase


_TRIP_YES_WORDS = {
    "yes", "y", "go", "confirm", "do it", "ok", "okay", "sure", "yep",
    "yeah", "proceed", "let's go", "approve", "lets go", "send it",
}


def _is_trip_yes(text: str) -> bool:
    t = (text or "").strip().lower().rstrip(".!?,")
    if not t:
        return False
    return any(t == w or t.startswith(w + " ") or t.endswith(" " + w) for w in _TRIP_YES_WORDS)


async def _trip_dispatch(session: TripSession, toolbox: BunqToolbox, name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Run one tool call for the trip mission. Publishes step events itself
    where needed (BunqToolbox methods publish their own; UI tools publish
    their own custom events here)."""

    # ---- UNDERSTAND-phase tools (UI publishers) ----
    if name == "search_trip_options":
        result = await search_trip_options(
            query=str(args.get("query", "")),
            max_results=int(args.get("max_results", 6)),
        )
        return result

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

    # ---- EXECUTE-phase tools — mostly delegate to BunqToolbox or shared dispatch ----
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
        result = await asyncio.to_thread(
            toolbox.fund_sub_account,
            float(args.get("amount_eur", 0)),
            session.sub_account_iban,
        )
        return result

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

    if name in ("book_hotel", "book_restaurant", "subscribe_to_service"):
        # Browser-vision dispatch reuses the existing helper.
        return await asyncio.to_thread(_dispatch_browser, name, args)

    if name == "send_slack_message":
        return await asyncio.to_thread(
            send_slack_message,
            message=str(args.get("message", "")),
            header=args.get("header"),
        )

    if name == "create_calendar_event":
        return await asyncio.to_thread(
            create_calendar_event,
            title=str(args.get("title", "Trip event")),
            description=args.get("description"),
            when=args.get("when"),
            duration_minutes=int(args.get("duration_minutes", 120)),
            invitees=args.get("invitees") or [],
        )

    # All remaining bunq mutations: pay_vendor, create_draft_payment,
    # schedule_recurring_payment, request_money, etc.
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


async def run_trip_turn(toolbox: BunqToolbox, session: TripSession, user_message: str, model: str | None = None) -> None:
    """Run one user turn of the trip mission. Many tool calls may fire inside it.

    Publishes:
      - user_message {text}        — echo
      - agent_message {text}       — final assistant text per turn
      - trip_phase {value}         — when phase flips
      - tool/step events as tools fire

    Caller is responsible for catching exceptions; this function lets them bubble.
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
