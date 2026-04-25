"""Browser agent — Claude Vision drives a real Playwright Chromium against
the local mock restaurant site, navigates the booking flow, and returns the
confirmed price. Screenshots stream to the dashboard via the SSE bus.

Hardened for stage demo:
  - Hard step cap so a confused agent never loops forever.
  - DOM scrape (data-booking-* attributes) as the source of truth for price.
  - All Playwright I/O inside an `async with` so the browser is always closed.
"""

from __future__ import annotations

import asyncio
import base64
import os
from typing import Any

import anthropic
from playwright.async_api import Page, async_playwright

from .events import bus


VISION_TOOLS: list[dict[str, Any]] = [
    {
        "name": "click_text",
        "description": (
            "Click the first visible UI element whose visible text matches the given string. "
            "Use the exact button/label text seen in the screenshot."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    },
    {
        "name": "wait",
        "description": "Wait for the page to settle. Use sparingly — only after a click that triggers a transition.",
        "input_schema": {
            "type": "object",
            "properties": {"seconds": {"type": "number", "minimum": 0.2, "maximum": 3.0}},
            "required": ["seconds"],
        },
    },
    {
        "name": "complete",
        "description": (
            "Call when the booking is confirmed (you see a confirmation/'booked' screen with a total and "
            "reference number). Provide the observed restaurant name, total in EUR, time slot, and "
            "reference id."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "restaurant_name": {"type": "string"},
                "price_eur": {"type": "number"},
                "time_slot": {"type": "string"},
                "reference": {"type": "string"},
            },
            "required": ["restaurant_name", "price_eur"],
        },
    },
]


SYSTEM_PROMPT_TEMPLATE = """\
You are a browser agent driving a real Chromium against a restaurant-booking site.

# Goal
Book a table that matches the user's preferences for {when}. Stay at or under €{max_budget}.
User hint: "{restaurant_hint}"

# Site flow (3 screens)
1. Browse — list of restaurants with photo, name, cuisine, "Book €N" button
2. Pick a time — restaurant detail, time-slot grid, "Continue to confirmation" button
3. Confirm — summary card, "Confirm booking" button, then a confirmation screen

# Action rules
- One screenshot at a time. Examine the latest screenshot, decide one tool call.
- To navigate, use click_text with the EXACT visible text on a button/element.
- The booking is final only after the confirmation screen shows "Booking confirmed" with a reference number.
- When you see the confirmation screen, call `complete` with the observed details.
- If the page seems mid-transition, call `wait` with seconds=0.5.

# Budget
Pick the cheapest restaurant matching the hint within budget. Tie-break: pick the higher-rated.
"""


async def _emit_screenshot(page: Page, label: str) -> None:
    png = await page.screenshot(type="png", full_page=False)
    b64 = base64.b64encode(png).decode()
    bus.publish("browser_screenshot", {"label": label, "b64": b64, "mime": "image/png"})


async def _click_first_text(page: Page, text: str) -> bool:
    """Try several locator strategies; return True if click succeeded."""
    candidates = [
        page.get_by_role("button", name=text, exact=False),
        page.get_by_text(text, exact=False).first,
    ]
    for loc in candidates:
        try:
            await loc.first.scroll_into_view_if_needed(timeout=1500)
            await loc.first.click(timeout=2500)
            return True
        except Exception:
            continue
    return False


async def book_restaurant_via_browser(
    restaurant_hint: str,
    max_budget: float,
    when: str,
    base_url: str,
    max_steps: int = 12,
    model: str | None = None,
) -> dict[str, Any]:
    """Drive the local mock restaurant site to a confirmed booking.

    Returns a dict with at least {restaurant_name, price_eur, time_slot, reference}.
    Raises if it can't complete inside max_steps.
    """
    model = model or os.getenv("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001").strip()
    client = anthropic.Anthropic()
    system = SYSTEM_PROMPT_TEMPLATE.format(
        when=when, max_budget=max_budget, restaurant_hint=restaurant_hint
    )

    bus.publish("browser_started", {
        "site": base_url,
        "restaurant_hint": restaurant_hint,
        "max_budget": max_budget,
        "when": when,
    })

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            context = await browser.new_context(viewport={"width": 1100, "height": 760})
            page = await context.new_page()
            await page.goto(base_url, wait_until="networkidle")
            await asyncio.sleep(0.3)
            await _emit_screenshot(page, "Loaded site")

            messages: list[dict[str, Any]] = []
            history_text: list[str] = []

            for step in range(max_steps):
                # Capture current screenshot
                png = await page.screenshot(type="png", full_page=False)
                b64 = base64.b64encode(png).decode()
                bus.publish("browser_screenshot", {"label": f"step {step+1}", "b64": b64, "mime": "image/png"})

                user_content: list[dict[str, Any]] = [
                    {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64}},
                    {"type": "text", "text": (
                        f"Step {step+1}/{max_steps}. "
                        f"Recent actions: {history_text[-3:] if history_text else 'none'}. "
                        f"Decide the next single action."
                    )},
                ]
                messages.append({"role": "user", "content": user_content})

                resp = client.messages.create(
                    model=model,
                    max_tokens=512,
                    system=system,
                    tools=VISION_TOOLS,
                    messages=messages,
                )
                messages.append({"role": "assistant", "content": resp.content})

                tool_uses = [b for b in resp.content if b.type == "tool_use"]
                if not tool_uses:
                    bus.publish("browser_idle", {"step": step})
                    history_text.append("idle")
                    messages.append({"role": "user", "content": [{"type": "tool_result", "tool_use_id": "noop", "content": "noop"}]})
                    continue

                tool_results = []
                completed: dict[str, Any] | None = None

                for tu in tool_uses:
                    name = tu.name
                    args = tu.input or {}
                    if name == "click_text":
                        text = str(args.get("text", "")).strip()
                        bus.publish("browser_action", {"action": "click_text", "text": text})
                        ok = await _click_first_text(page, text)
                        history_text.append(f"click_text({text!r}) -> {'ok' if ok else 'miss'}")
                        await asyncio.sleep(0.4)
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": tu.id,
                            "content": "ok" if ok else f"could not find element with text {text!r}",
                            "is_error": not ok,
                        })
                    elif name == "wait":
                        secs = float(args.get("seconds", 0.5))
                        bus.publish("browser_action", {"action": "wait", "seconds": secs})
                        await asyncio.sleep(min(secs, 3.0))
                        history_text.append(f"wait({secs:.1f})")
                        tool_results.append({
                            "type": "tool_result", "tool_use_id": tu.id, "content": "waited"
                        })
                    elif name == "complete":
                        # Cross-check with DOM scrape — the source of truth.
                        scraped = await page.evaluate(
                            "() => ({\n"
                            "  status: document.body.dataset.bookingStatus || '',\n"
                            "  restaurant: document.body.dataset.bookingRestaurant || '',\n"
                            "  price: parseFloat(document.body.dataset.bookingPrice || '0'),\n"
                            "  ref: document.body.dataset.bookingRef || '',\n"
                            "  time: document.body.dataset.bookingTime || ''\n"
                            "})"
                        )
                        if scraped.get("status") != "confirmed":
                            history_text.append("complete-too-early; not on confirmation screen")
                            tool_results.append({
                                "type": "tool_result",
                                "tool_use_id": tu.id,
                                "content": "Booking not yet confirmed — keep going (haven't reached the success screen).",
                                "is_error": True,
                            })
                            continue
                        completed = {
                            "restaurant_name": scraped["restaurant"] or args.get("restaurant_name", ""),
                            "price_eur": float(scraped["price"]) or float(args.get("price_eur", 0)),
                            "time_slot": scraped["time"] or args.get("time_slot", when),
                            "reference": scraped["ref"] or args.get("reference", ""),
                        }
                        bus.publish("browser_complete", completed)
                        tool_results.append({
                            "type": "tool_result", "tool_use_id": tu.id, "content": "complete acknowledged"
                        })
                    else:
                        tool_results.append({
                            "type": "tool_result", "tool_use_id": tu.id, "content": f"unknown tool {name}", "is_error": True
                        })

                if completed:
                    return completed
                messages.append({"role": "user", "content": tool_results})

            raise RuntimeError(f"browser_agent did not complete inside {max_steps} steps")
        finally:
            await browser.close()
