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

from .bunq_tools import BunqToolbox
from .events import bus
from .tool_catalog import BUNQ_TOOLS


DEFAULT_MODEL = "claude-haiku-4-5-20251001"


def _dispatch_bunq(toolbox: BunqToolbox, name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Route a tool_use block to the corresponding BunqToolbox method.

    Returns a JSON-safe dict (tool_result payload for Anthropic).
    """
    method_map: dict[str, Callable[..., dict[str, Any]]] = {
        "create_sub_account": toolbox.create_sub_account,
        "fund_sub_account": toolbox.fund_sub_account,
        "pay_vendor": toolbox.pay_vendor,
        "create_draft_payment": toolbox.create_draft_payment,
        "schedule_recurring_payment": toolbox.schedule_recurring_payment,
        "request_money": toolbox.request_money,
        "create_bunqme_link": toolbox.create_bunqme_link,
        "update_sub_account": toolbox.update_sub_account,
        "set_card_status": toolbox.set_card_status,
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
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tu.id,
                    "content": json.dumps({"ok": True}),
                })
                continue

            if name == "finish_mission":
                final_summary = str(args.get("summary", ""))[:400]
                bus.publish("mission_complete", {"summary": final_summary})
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tu.id,
                    "content": json.dumps({"ok": True}),
                })
                stop_now = True
                continue

            result = _dispatch_bunq(toolbox, name, args)
            if name == "create_draft_payment" and "draft_id" in result:
                last_draft_id = result["draft_id"]
            # Emit balance snapshot after every bunq-mutating tool.
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

    return {
        "final_summary": final_summary,
        "narrations": narrations,
        "draft_id": last_draft_id,
        "mission_sub_id": toolbox.mission_sub_id,
        "mission_sub_iban": toolbox.mission_sub_iban,
    }
