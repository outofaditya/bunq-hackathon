"""Claude tool-use loop for the Trip Agent.

Flow:
  POST /chat  ──►  queue turn for session
                    │
                    ▼
  agent_loop.run_turn(session, user_msg)
    ├── emits SSE "user_message"
    ├── loops calling anthropic.messages.stream until stop_reason != "tool_use"
    │     for each tool_use block, executes the tool and appends tool_result
    │     after `present_options` completes, flip phase → AWAITING_CONFIRMATION
    │     after `request_confirmation` completes, next user turn's yes → EXECUTING
    ├── emits SSE "agent_message" for each text block
    ├── emits SSE "tool_call" { name, status=firing | ok | failed, result }
    └── emits SSE "phase" whenever it changes
"""
from __future__ import annotations

import asyncio
import json
import os
from typing import Any

from anthropic import Anthropic

from . import browser_agent, bunq_tools, image_gen, side_tools
from .events import bus
from .phases import Phase
from .sessions import Session
from .system_prompt import SYSTEM_PROMPT, tools_for_phase

def get_model() -> str:
    return os.getenv("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")


_client: Anthropic | None = None


def anthropic_client() -> Anthropic:
    global _client
    if _client is None:
        _client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    return _client


# ---------------------------------------------------------------------------
# Tool dispatch: runs the server-side function for each tool_use block
# ---------------------------------------------------------------------------

async def dispatch_tool(session: Session, name: str, tool_input: dict[str, Any]) -> dict[str, Any]:
    """Execute a tool call, return the tool_result content."""
    await bus.publish("tool_call", name=name, status="firing", input=tool_input)
    try:
        result = await _execute_tool(session, name, tool_input)
        await bus.publish("tool_call", name=name, status="ok", result=result)
        return result
    except Exception as e:
        err = {"error": str(e), "tool": name}
        await bus.publish("tool_call", name=name, status="failed", error=str(e))
        return err


async def _execute_tool(session: Session, name: str, args: dict[str, Any]) -> dict[str, Any]:
    # ---- UNDERSTANDING phase tools ----
    if name == "search_trip_options":
        out = await browser_agent.search_trip_options(
            query=args["query"],
            max_results=int(args.get("max_results", 6)),
        )
        return out

    if name == "present_options":
        await bus.publish("options", intro=args["intro_text"], options=args["options"])
        session.phase = Phase.AWAITING_CONFIRMATION
        await bus.publish("phase", value=session.phase.value)
        # Fire-and-forget: generate cartoon illustrations in parallel and stream
        # them in as `option_image` events so cards aren't blocked on image gen.
        for opt in args["options"]:
            asyncio.create_task(_generate_and_publish_image(opt))
        return {"presented": len(args["options"])}

    if name == "request_confirmation":
        await bus.publish("confirmation_request", summary=args["summary"])
        return {"awaiting_user_yes": True}

    # ---- EXECUTING phase tools ----
    if name == "create_sub_account":
        out = await asyncio.to_thread(bunq_tools.create_sub_account, args["name"], args["goal_eur"])
        session.sub_account_id = out["account_id"]
        session.sub_account_iban = out["iban"]
        return out

    if name == "fund_sub_account":
        if not session.sub_account_iban:
            raise RuntimeError("fund_sub_account called before create_sub_account")
        out = await asyncio.to_thread(
            bunq_tools.fund_sub_account,
            args["amount_eur"],
            session.sub_account_iban,
        )
        return out

    if name == "book_hotel":
        out = await browser_agent.book_hotel(
            hotel=args["hotel"],
            amount_eur=args["amount_eur"],
            location=args.get("location", "Amsterdam, Netherlands"),
            checkin=args.get("checkin", "Sat 26 Apr"),
            checkout=args.get("checkout", "Sun 27 Apr"),
            guest=args.get("guest", "Sara van Doorn"),
        )
        return out

    if name == "pay_vendor":
        if not session.sub_account_id:
            raise RuntimeError("pay_vendor called before create_sub_account")
        out = await asyncio.to_thread(
            bunq_tools.pay_vendor,
            args["amount_eur"],
            args["vendor_label"],
            session.sub_account_id,
        )
        return out

    if name == "create_draft_payment":
        if not session.sub_account_id:
            raise RuntimeError("create_draft_payment called before create_sub_account")
        out = await asyncio.to_thread(
            bunq_tools.create_draft_payment,
            args["amount_eur"],
            args["description"],
            session.sub_account_id,
        )
        if out.get("draft_id"):
            session.pending_draft_ids.append(out["draft_id"])
        return out

    if name == "schedule_recurring":
        if not session.sub_account_iban:
            raise RuntimeError("schedule_recurring called before create_sub_account")
        primary = await asyncio.to_thread(bunq_tools.get_primary_account)
        out = await asyncio.to_thread(
            bunq_tools.schedule_recurring,
            args["amount_eur"],
            session.sub_account_iban,
            primary["id"],
            args.get("description", "Trip fund"),
        )
        return out

    if name == "request_from_partner":
        if not session.sub_account_id:
            raise RuntimeError("request_from_partner called before create_sub_account")
        out = await asyncio.to_thread(
            bunq_tools.request_from_partner,
            args["amount_eur"],
            session.sub_account_id,
            args["partner_label"],
        )
        return out

    if name == "send_slack":
        out = await asyncio.to_thread(side_tools.send_slack, args["message"])
        return out

    if name == "narrate":
        await bus.publish("narration", text=args["text"])
        return {"narrated": args["text"]}

    raise RuntimeError(f"Unknown tool: {name}")


# ---------------------------------------------------------------------------
# Turn runner
# ---------------------------------------------------------------------------

MAX_ITERATIONS = 20


async def run_turn(session: Session, user_message: str) -> None:
    """Execute one conversational turn, possibly with many tool calls.

    After user sends the confirmation "yes", we flip phase → EXECUTING here.
    """
    await bus.publish("user_message", text=user_message)
    session.messages.append({"role": "user", "content": user_message})

    # Confirmation gate
    if session.phase == Phase.AWAITING_CONFIRMATION:
        if _is_yes(user_message):
            session.phase = Phase.EXECUTING
            await bus.publish("phase", value=session.phase.value)
        else:
            # No gate: don't flip, let the agent have a normal conversation
            pass

    for i in range(MAX_ITERATIONS):
        tools = tools_for_phase(session.phase)
        stream_kwargs: dict[str, Any] = {
            "model": get_model(),
            "max_tokens": 4096,
            "system": [
                {"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}},
            ],
            "messages": session.messages,
            "tools": tools,
        }

        text_chunks: list[str] = []
        tool_uses: list[dict[str, Any]] = []
        stop_reason: str | None = None
        assistant_content: list[dict[str, Any]] = []

        try:
            client = anthropic_client()
            with client.messages.stream(**stream_kwargs) as stream:
                for event in stream:
                    etype = getattr(event, "type", None)
                    if etype == "content_block_start":
                        block = event.content_block
                        if block.type == "text":
                            assistant_content.append({"type": "text", "text": ""})
                        elif block.type == "tool_use":
                            tool_uses.append({"id": block.id, "name": block.name, "input": {}})
                            assistant_content.append(
                                {"type": "tool_use", "id": block.id, "name": block.name, "input": {}}
                            )
                    elif etype == "content_block_delta":
                        delta = event.delta
                        if delta.type == "text_delta":
                            text_chunks.append(delta.text)
                            await bus.publish("agent_text_delta", text=delta.text)
                            if assistant_content and assistant_content[-1].get("type") == "text":
                                assistant_content[-1]["text"] += delta.text
                    elif etype == "message_stop":
                        msg = stream.get_final_message()
                        stop_reason = msg.stop_reason
                        # Rebuild assistant_content from the final message, stripping fields
                        # that the API rejects on reentry (e.g. `text` on server_tool_use).
                        assistant_content = [_clean_block(b.model_dump()) for b in msg.content]
                        tool_uses = [
                            {"id": b.id, "name": b.name, "input": b.input}
                            for b in msg.content if b.type == "tool_use"
                        ]
        except Exception as e:
            await bus.publish("agent_error", error=str(e))
            raise

        # Append assistant message to history
        session.messages.append({"role": "assistant", "content": assistant_content})

        # If agent produced text, emit the finalized message event
        if text_chunks:
            await bus.publish("agent_message", text="".join(text_chunks))

        if stop_reason != "tool_use":
            break

        # Execute tools and build tool_result user message
        tool_results_content: list[dict[str, Any]] = []
        for tu in tool_uses:
            # web_search is executed by Anthropic server-side; we don't dispatch it
            if tu["name"] == "web_search":
                continue
            result = await dispatch_tool(session, tu["name"], tu["input"])
            tool_results_content.append(
                {"type": "tool_result", "tool_use_id": tu["id"], "content": json.dumps(result)}
            )

        if not tool_results_content:
            # Only server-side tools (web_search) were used — continue loop without an extra user turn
            continue

        session.messages.append({"role": "user", "content": tool_results_content})

        # Mission-complete check: if we've hit request_from_partner + send_slack, we're done
        executed_names = {
            b.get("name") for m in session.messages if isinstance(m.get("content"), list)
            for b in m["content"] if isinstance(b, dict) and b.get("type") == "tool_use"
        }
        if session.phase == Phase.EXECUTING and "request_from_partner" in executed_names and "send_slack" in executed_names:
            session.phase = Phase.DONE
            await bus.publish("phase", value=session.phase.value)
            # let the model write one final wrap message
            continue

    else:
        await bus.publish("agent_error", error=f"Hit MAX_ITERATIONS={MAX_ITERATIONS}")


def _clean_block(block: dict[str, Any]) -> dict[str, Any]:
    """Strip fields the API rejects when we replay assistant content on reentry."""
    t = block.get("type")
    if t == "text":
        out = {"type": "text", "text": block.get("text", "")}
        if block.get("citations"):
            out["citations"] = block["citations"]
        return out
    if t == "tool_use":
        return {
            "type": "tool_use",
            "id": block.get("id"),
            "name": block.get("name"),
            "input": block.get("input") or {},
        }
    if t == "server_tool_use":
        return {
            "type": "server_tool_use",
            "id": block.get("id"),
            "name": block.get("name"),
            "input": block.get("input") or {},
        }
    if t == "web_search_tool_result":
        return {
            "type": "web_search_tool_result",
            "tool_use_id": block.get("tool_use_id"),
            "content": block.get("content"),
        }
    if t == "thinking":
        return {"type": "thinking", "thinking": block.get("thinking", ""), "signature": block.get("signature", "")}
    # Fallback: pass through but drop Nones
    return {k: v for k, v in block.items() if v is not None}


def _is_yes(text: str) -> bool:
    t = text.strip().lower()
    if not t:
        return False
    positive = {"yes", "y", "go", "confirm", "do it", "ok", "okay", "sure", "yep", "yeah", "proceed", "let's go", "approve"}
    return any(t == p or t.startswith(p + " ") or p in t for p in positive)


async def _generate_and_publish_image(opt: dict[str, Any]) -> None:
    """Background task: generate one option's cartoon image, publish via SSE."""
    option_id = opt.get("id", "")
    try:
        url = await image_gen.generate_for_option(opt)
    except Exception as e:  # noqa: BLE001 — never break the demo
        print(f"[agent_loop] image gen crash for {option_id}: {e}", flush=True)
        url = None
    if not url:
        await bus.publish("option_image", option_id=option_id, image_url=None, status="failed")
        return
    await bus.publish("option_image", option_id=option_id, image_url=url, status="ok")
