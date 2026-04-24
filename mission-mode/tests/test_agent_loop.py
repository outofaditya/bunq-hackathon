"""End-to-end agent loop test — runs a full conversation against sandbox bunq + real Anthropic.

Run: python -m tests.test_agent_loop
"""
from __future__ import annotations

import asyncio
import json

from orchestrator import agent_loop
from orchestrator.events import bus
from orchestrator.sessions import create_session


async def event_printer(stop: asyncio.Event) -> None:
    q = bus.subscribe()
    try:
        while not stop.is_set():
            try:
                msg = await asyncio.wait_for(q.get(), timeout=0.5)
            except asyncio.TimeoutError:
                continue
            data = json.loads(msg)
            kind = data["type"]
            if kind == "agent_text_delta":
                print(data["text"], end="", flush=True)
            elif kind == "agent_message":
                print()  # newline after streaming
            elif kind == "tool_call":
                print(f"\n  [tool_call] {data['name']} status={data['status']}")
                if data["status"] == "ok" and "result" in data:
                    print(f"    → {json.dumps(data['result'])[:200]}")
                if data["status"] == "failed":
                    print(f"    ✗ {data.get('error')}")
            elif kind == "options":
                print(f"\n  [options] {data['intro']}")
                for opt in data["options"]:
                    print(f"    - {opt['id']}: {opt['hotel']} — €{opt['total_eur']}")
            elif kind == "confirmation_request":
                print(f"\n  [confirm] {data['summary']}")
            elif kind == "phase":
                print(f"\n  [phase] → {data['value']}")
            elif kind == "narration":
                print(f"\n  [narrate] {data['text']}")
            elif kind == "agent_error":
                print(f"\n  ✗ {data['error']}")
            # skip user_message echoes
    finally:
        bus.unsubscribe(q)


async def main() -> None:
    session = create_session()
    stop = asyncio.Event()
    printer = asyncio.create_task(event_printer(stop))

    print("\n>>> Turn 1 — user describes trip")
    await agent_loop.run_turn(
        session,
        "I want to surprise Sara with a weekend in Amsterdam, budget around €500, Valentine's weekend.",
    )

    print(f"\n\n[phase after turn 1: {session.phase.value}]")

    # If agent asked clarifying questions, answer them
    if session.phase.value == "UNDERSTANDING":
        print("\n>>> Turn 2 — user answers any clarifications")
        await agent_loop.run_turn(
            session,
            "City break, cozy not fancy. €500 is firm. Dates flexible.",
        )

    print(f"\n\n[phase after clarifications: {session.phase.value}]")

    # Select the first option
    print("\n>>> Turn 3 — user picks option")
    await agent_loop.run_turn(session, "I'll take option A.")

    print(f"\n\n[phase after selection: {session.phase.value}]")

    # Confirm
    print("\n>>> Turn 4 — user says yes")
    await agent_loop.run_turn(session, "yes, go")

    print(f"\n\n[phase final: {session.phase.value}]")

    await asyncio.sleep(1.5)
    stop.set()
    await printer

    print("\n\n=== DONE ===")


if __name__ == "__main__":
    asyncio.run(main())
