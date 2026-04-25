"""Browser agent — Claude Vision drives Playwright Chromium against local
mock booking flows. One generic engine + task-specific wrappers.

Tasks supported:
  - book_restaurant_via_browser   (Weekend mission)
  - book_hotel_via_browser        (Travel mission)
  - subscribe_to_service_via_browser (Payday mission)

Hardened for stage demo:
  - Hard step cap so a confused agent never loops forever.
  - DOM scrape (data-booking-* attributes) as the source of truth.
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


# ----------------------------------------------------------------------
# Shared navigation primitives
# ----------------------------------------------------------------------

_BASE_TOOLS: list[dict[str, Any]] = [
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
]


_CURSOR_INJECT_JS = r"""
() => {
  if (document.getElementById('__agent_cursor')) return;
  const c = document.createElement('div');
  c.id = '__agent_cursor';
  c.style.cssText =
    'position:fixed;pointer-events:none;width:18px;height:18px;border-radius:50%;'
    +'background:rgba(255,82,82,0.7);border:2px solid #fff;'
    +'transform:translate(-50%,-50%) scale(1);'
    +'z-index:99999;box-shadow:0 4px 14px rgba(0,0,0,0.35);'
    +'left:50%;top:30%;'
    +'transition:left .42s cubic-bezier(.4,.0,.2,1), top .42s cubic-bezier(.4,.0,.2,1), transform .12s ease, background .12s ease;';
  document.body.appendChild(c);

  const ring = document.createElement('div');
  ring.id = '__agent_ring';
  ring.style.cssText =
    'position:fixed;pointer-events:none;width:36px;height:36px;border-radius:50%;'
    +'border:2px solid rgba(255,82,82,0.85);'
    +'transform:translate(-50%,-50%) scale(0);opacity:0;'
    +'z-index:99998;'
    +'transition:transform .42s ease-out, opacity .42s ease-out;'
    +'left:50%;top:30%;';
  document.body.appendChild(ring);
}
"""


async def _move_cursor(page: Page, x: float, y: float) -> None:
    await page.evaluate(
        "(p) => { const c=document.getElementById('__agent_cursor');"
        " if(c){ c.style.left=p.x+'px'; c.style.top=p.y+'px'; }"
        " const r=document.getElementById('__agent_ring');"
        " if(r){ r.style.left=p.x+'px'; r.style.top=p.y+'px'; r.style.opacity='0'; r.style.transform='translate(-50%,-50%) scale(0)'; }"
        " }",
        {"x": x, "y": y},
    )


async def _pulse_cursor(page: Page) -> None:
    await page.evaluate(
        "() => { const c=document.getElementById('__agent_cursor');"
        " if(c){ c.style.transform='translate(-50%,-50%) scale(0.7)'; c.style.background='rgba(0, 226, 196, 0.85)'; }"
        " const r=document.getElementById('__agent_ring');"
        " if(r){ r.style.opacity='1'; r.style.transform='translate(-50%,-50%) scale(1.6)'; }"
        " }"
    )


async def _restore_cursor(page: Page) -> None:
    await page.evaluate(
        "() => { const c=document.getElementById('__agent_cursor');"
        " if(c){ c.style.transform='translate(-50%,-50%) scale(1)'; c.style.background='rgba(255,90,90,0.55)'; }"
        " }"
    )


async def _emit_screenshot_quick(page: Page, label: str) -> None:
    png = await page.screenshot(type="png", full_page=False)
    bus.publish("browser_screenshot", {
        "label": label,
        "b64": base64.b64encode(png).decode(),
        "mime": "image/png",
    })


async def _click_first_text(page: Page, text: str) -> bool:
    """Locate, animate cursor to target, click — emitting transition frames."""
    candidates = [
        page.get_by_role("button", name=text, exact=False),
        page.get_by_text(text, exact=False).first,
    ]
    for loc in candidates:
        try:
            target = loc.first
            await target.scroll_into_view_if_needed(timeout=1500)
            box = await target.bounding_box()
            if not box:
                await target.click(timeout=2500)
                return True
            cx = box["x"] + box["width"] / 2
            cy = box["y"] + box["height"] / 2

            # Animate the cursor toward the target across many frames so the
            # motion looks fluid (~6 frames per ~420 ms transition ~= 14 fps).
            await page.evaluate(_CURSOR_INJECT_JS)
            await _move_cursor(page, cx, cy)
            for delay in (0.06, 0.12, 0.18, 0.24, 0.30):
                await asyncio.sleep(0.06)
                await _emit_screenshot_quick(page, f"moving · {int(delay*1000)}ms")

            # Click + visual pulse
            await _pulse_cursor(page)
            await _emit_screenshot_quick(page, "click")
            await target.click(timeout=2500)
            await asyncio.sleep(0.10)
            await _emit_screenshot_quick(page, "settling")
            await asyncio.sleep(0.18)
            await _emit_screenshot_quick(page, "after click")
            await _restore_cursor(page)
            return True
        except Exception:
            continue
    return False


async def _drive_booking_flow(
    base_url: str,
    system_prompt: str,
    complete_tool: dict[str, Any],
    scrape_js: str,
    confirmed_check: str,
    success_event_label: str,
    started_event_label: str,
    started_event_data: dict[str, Any],
    max_steps: int = 12,
    model: str | None = None,
) -> dict[str, Any]:
    """Generic Vision + Playwright loop. The caller defines:

    - `complete_tool` — the JSON schema for the terminal tool the agent calls
    - `scrape_js`     — JS expression evaluated on completion to read DOM truth
    - `confirmed_check` — predicate (Python expression evaluated on the scrape
       dict) that returns True iff the page is on the success screen
    - `success_event_label` — name of the bus event when the flow completes
    - `started_event_label` / `_data` — what to publish before the loop starts
    """
    model = model or os.getenv("ANTHROPIC_MODEL", "").strip() or "claude-haiku-4-5-20251001"
    client = anthropic.Anthropic()
    tools = _BASE_TOOLS + [complete_tool]

    bus.publish(started_event_label, started_event_data)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            context = await browser.new_context(viewport={"width": 1100, "height": 760})
            page = await context.new_page()
            await page.goto(base_url, wait_until="networkidle")
            await asyncio.sleep(0.25)
            # Inject the visible cursor + ring overlay once.
            await page.evaluate(_CURSOR_INJECT_JS)
            await asyncio.sleep(0.2)

            messages: list[dict[str, Any]] = []
            history: list[str] = []

            for step in range(max_steps):
                png = await page.screenshot(type="png", full_page=False)
                b64 = base64.b64encode(png).decode()
                bus.publish("browser_screenshot", {"label": f"step {step+1}", "b64": b64, "mime": "image/png"})

                user_content: list[dict[str, Any]] = [
                    {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64}},
                    {"type": "text", "text": (
                        f"Step {step+1}/{max_steps}. Recent: {history[-3:] if history else 'none'}. "
                        f"Decide the next single action."
                    )},
                ]
                messages.append({"role": "user", "content": user_content})

                resp = client.messages.create(
                    model=model,
                    max_tokens=512,
                    system=system_prompt,
                    tools=tools,
                    messages=messages,
                )
                messages.append({"role": "assistant", "content": resp.content})

                tool_uses = [b for b in resp.content if b.type == "tool_use"]
                if not tool_uses:
                    history.append("idle")
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
                        history.append(f"click_text({text!r}) -> {'ok' if ok else 'miss'}")
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
                        history.append(f"wait({secs:.1f})")
                        tool_results.append({"type": "tool_result", "tool_use_id": tu.id, "content": "waited"})
                    elif name == "complete":
                        scraped = await page.evaluate(scrape_js)
                        if not eval(confirmed_check, {"s": scraped}):  # noqa: S307
                            history.append("complete-too-early; not on confirmation screen")
                            tool_results.append({
                                "type": "tool_result",
                                "tool_use_id": tu.id,
                                "content": "Not yet confirmed — keep going.",
                                "is_error": True,
                            })
                            continue
                        # Merge: scraped DOM truth wins over any agent-supplied values.
                        completed = {**args, **{k: v for k, v in scraped.items() if v not in (None, "", 0)}}
                        bus.publish(success_event_label, completed)
                        tool_results.append({"type": "tool_result", "tool_use_id": tu.id, "content": "complete acknowledged"})
                    else:
                        tool_results.append({"type": "tool_result", "tool_use_id": tu.id, "content": f"unknown tool {name}", "is_error": True})

                if completed:
                    return completed
                messages.append({"role": "user", "content": tool_results})

            raise RuntimeError(f"browser_agent did not complete inside {max_steps} steps")
        finally:
            await browser.close()


# ----------------------------------------------------------------------
# Task 1: book a restaurant (Weekend)
# ----------------------------------------------------------------------

_RESTAURANT_SYSTEM = """\
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
- The booking is final only after the confirmation screen shows "Booking confirmed" with a reference.
- When you see the confirmation screen, call `complete` with the observed details.

# Budget
Pick the cheapest restaurant matching the hint within budget. Tie-break: higher rating.
"""

_RESTAURANT_COMPLETE_TOOL = {
    "name": "complete",
    "description": (
        "Call when the booking is confirmed. Provide the observed restaurant name, "
        "total EUR, time slot, and reference id."
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
}

_RESTAURANT_SCRAPE_JS = (
    "() => ({ "
    "  status: document.body.dataset.bookingStatus || '',"
    "  restaurant_name: document.body.dataset.bookingRestaurant || '',"
    "  price_eur: parseFloat(document.body.dataset.bookingPrice || '0') || 0,"
    "  reference: document.body.dataset.bookingRef || '',"
    "  time_slot: document.body.dataset.bookingTime || '' "
    "})"
)


async def book_restaurant_via_browser(
    restaurant_hint: str,
    max_budget: float,
    when: str,
    base_url: str,
    max_steps: int = 12,
    model: str | None = None,
) -> dict[str, Any]:
    return await _drive_booking_flow(
        base_url=base_url,
        system_prompt=_RESTAURANT_SYSTEM.format(when=when, max_budget=max_budget, restaurant_hint=restaurant_hint),
        complete_tool=_RESTAURANT_COMPLETE_TOOL,
        scrape_js=_RESTAURANT_SCRAPE_JS,
        confirmed_check="s.get('status') == 'confirmed'",
        success_event_label="browser_complete",
        started_event_label="browser_started",
        started_event_data={
            "site": base_url, "task": "book_restaurant",
            "restaurant_hint": restaurant_hint, "max_budget": max_budget, "when": when,
        },
        max_steps=max_steps,
        model=model,
    )


# ----------------------------------------------------------------------
# Task 2: book a hotel (Travel)
# ----------------------------------------------------------------------

_HOTEL_SYSTEM = """\
You are a browser agent driving a real Chromium against a hotel-booking site.

# Goal
Book a hotel in {city} for {nights} night(s). Stay at or under €{max_budget} total.

# Site flow (3 screens)
1. Browse — list of hotels with photo, name, area, rating, "Book €N/night" button
2. Pick nights — confirmation of nights/total, "Continue" button
3. Confirm — summary, "Confirm booking" button, then confirmation screen

# Action rules
- One screenshot at a time. Use click_text with the EXACT visible text.
- The booking is final only after the confirmation screen shows "Hotel booked" with a reference.
- When you see the confirmation, call `complete` with the observed details.

# Budget
Pick the cheapest hotel within budget. Tie-break: higher rating.
"""

_HOTEL_COMPLETE_TOOL = {
    "name": "complete",
    "description": "Call when the hotel booking is confirmed. Provide the observed hotel name, total EUR, nights, reference id.",
    "input_schema": {
        "type": "object",
        "properties": {
            "hotel_name": {"type": "string"},
            "price_eur": {"type": "number"},
            "nights": {"type": "integer"},
            "reference": {"type": "string"},
        },
        "required": ["hotel_name", "price_eur"],
    },
}

_HOTEL_SCRAPE_JS = (
    "() => ({ "
    "  status: document.body.dataset.bookingStatus || '',"
    "  hotel_name: document.body.dataset.bookingHotel || '',"
    "  price_eur: parseFloat(document.body.dataset.bookingPrice || '0') || 0,"
    "  reference: document.body.dataset.bookingRef || '',"
    "  nights: parseInt(document.body.dataset.bookingNights || '0', 10) || 0 "
    "})"
)


async def book_hotel_via_browser(
    city: str,
    nights: int,
    max_budget: float,
    base_url: str,
    max_steps: int = 12,
    model: str | None = None,
) -> dict[str, Any]:
    return await _drive_booking_flow(
        base_url=base_url,
        system_prompt=_HOTEL_SYSTEM.format(city=city, nights=nights, max_budget=max_budget),
        complete_tool=_HOTEL_COMPLETE_TOOL,
        scrape_js=_HOTEL_SCRAPE_JS,
        confirmed_check="s.get('status') == 'confirmed'",
        success_event_label="browser_complete",
        started_event_label="browser_started",
        started_event_data={
            "site": base_url, "task": "book_hotel",
            "city": city, "nights": nights, "max_budget": max_budget,
        },
        max_steps=max_steps,
        model=model,
    )


# ----------------------------------------------------------------------
# Task 3: subscribe to a service (Payday)
# ----------------------------------------------------------------------

_SUB_SYSTEM = """\
You are a browser agent driving a real Chromium against a subscription comparison site.

# Goal
Pick the cheapest plan in the "{category}" category that costs at most €{max_monthly} per month.

# Site flow (2 screens)
1. Browse — grid of plans with provider, plan name, monthly price, "Subscribe €N/mo" button
2. Confirm — summary card, "Confirm subscription" button, then confirmation screen

# Action rules
- One screenshot at a time. Use click_text with the EXACT visible button text.
- The subscription is final only after "Subscription active" appears with a reference.
- When you see the confirmation, call `complete` with the observed details.

# Budget
Pick the cheapest plan within budget. Tie-break: longest list of features.
"""

_SUB_COMPLETE_TOOL = {
    "name": "complete",
    "description": "Call when the subscription confirmation screen is showing. Provide service name, plan, monthly EUR, and reference.",
    "input_schema": {
        "type": "object",
        "properties": {
            "service_name": {"type": "string"},
            "plan": {"type": "string"},
            "monthly_eur": {"type": "number"},
            "reference": {"type": "string"},
        },
        "required": ["service_name", "monthly_eur"],
    },
}

_SUB_SCRAPE_JS = (
    "() => ({ "
    "  status: document.body.dataset.subscriptionStatus || '',"
    "  service_name: document.body.dataset.subscriptionService || '',"
    "  plan: document.body.dataset.subscriptionPlan || '',"
    "  monthly_eur: parseFloat(document.body.dataset.subscriptionMonthly || '0') || 0,"
    "  reference: document.body.dataset.subscriptionRef || '' "
    "})"
)


async def subscribe_to_service_via_browser(
    category: str,
    max_monthly_eur: float,
    base_url: str,
    max_steps: int = 12,
    model: str | None = None,
) -> dict[str, Any]:
    return await _drive_booking_flow(
        base_url=base_url,
        system_prompt=_SUB_SYSTEM.format(category=category, max_monthly=max_monthly_eur),
        complete_tool=_SUB_COMPLETE_TOOL,
        scrape_js=_SUB_SCRAPE_JS,
        confirmed_check="s.get('status') == 'active'",
        success_event_label="browser_complete",
        started_event_label="browser_started",
        started_event_data={
            "site": base_url, "task": "subscribe_to_service",
            "category": category, "max_monthly_eur": max_monthly_eur,
        },
        max_steps=max_steps,
        model=model,
    )


# ============================================================================
# search_trip_options — visible Playwright web-search beat (Trip mission)
# ============================================================================

import html as _html
import re as _re
import urllib.parse as _urlparse

import httpx as _httpx


_TRIPLENS_VIEWPORT = {"width": 900, "height": 560}
_TRIPLENS_FRAME_INTERVAL_S = 0.35
_TRIPLENS_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


async def _stream_search_frames(page: Page, stop: asyncio.Event, label_prefix: str = "search") -> None:
    """JPEG frames every 0.35s on the council `browser_screenshot` channel."""
    i = 0
    while not stop.is_set():
        try:
            jpeg = await page.screenshot(type="jpeg", quality=65, full_page=False)
            bus.publish("browser_screenshot", {
                "label": f"{label_prefix} frame {i}",
                "b64": base64.b64encode(jpeg).decode("ascii"),
                "mime": "image/jpeg",
            })
            i += 1
        except Exception:
            pass
        try:
            await asyncio.wait_for(stop.wait(), timeout=_TRIPLENS_FRAME_INTERVAL_S)
        except asyncio.TimeoutError:
            continue


def _ddg_blocking(query: str, max_results: int) -> list[dict[str, Any]]:
    """Plain HTTP DuckDuckGo HTML endpoint scrape — synchronous, runs off-loop."""
    headers = {"User-Agent": _TRIPLENS_USER_AGENT, "Referer": "https://html.duckduckgo.com/"}
    data = {"q": query, "b": "", "kl": "us-en"}
    with _httpx.Client(headers=headers, timeout=12.0, follow_redirects=True) as c:
        r = c.post("https://html.duckduckgo.com/html/", data=data)
        r.raise_for_status()
        body = r.text

    blocks = _re.findall(
        r'<div[^>]*class="result[^"]*"[^>]*>([\s\S]*?)</div>\s*</div>\s*</div>',
        body,
    )
    out: list[dict[str, Any]] = []
    for blk in blocks:
        m_title = _re.search(
            r'<a\s+rel="nofollow"\s+class="result__a"\s+href="([^"]+)"[^>]*>([\s\S]*?)</a>',
            blk,
        )
        if not m_title:
            continue
        url = _html.unescape(m_title.group(1))
        parsed = _urlparse.urlparse(url)
        if "duckduckgo.com" in parsed.netloc and parsed.path.startswith(("/l/", "/y.js")):
            qs = _urlparse.parse_qs(parsed.query)
            real = qs.get("uddg", [""])[0]
            if real:
                url = _urlparse.unquote(real)
        title = _re.sub(r"<[^>]+>", "", m_title.group(2))
        title = _html.unescape(title).strip()

        m_snip = _re.search(
            r'<a[^>]*class="result__snippet"[^>]*>([\s\S]*?)</a>', blk
        )
        snippet = ""
        if m_snip:
            snippet = _re.sub(r"<[^>]+>", "", m_snip.group(1))
            snippet = _html.unescape(snippet).strip()

        if "/y.js" in (m_title.group(1) or ""):
            continue

        out.append({"title": title, "url": url, "snippet": snippet})
        if len(out) >= max_results:
            break
    return out


async def _ddg_fetch(query: str, max_results: int) -> list[dict[str, Any]]:
    return await asyncio.to_thread(_ddg_blocking, query, max_results)


async def search_trip_options(query: str, max_results: int = 6) -> dict[str, Any]:
    """Visible web-search beat. Streams a TripLens animation while running a real DDG search.

    Used by the trip mission. Publishes events:
      - browser_started     — kick-off marker (also used by booking flows)
      - browser_screenshot  — JPEG frames every 0.35s
      - search_results      — final {query, results} payload for the Research feed
      - browser_complete    — end marker
    """
    server_base = os.getenv("PUBLIC_BASE_URL", "").strip() or "http://localhost:8000"
    server_loopback = "http://127.0.0.1:8000"

    bus.publish("browser_started", {"site": "TripLens", "task": "search_trip_options", "query": query})

    try:
        results = await _ddg_fetch(query, max_results)
    except Exception as e:  # noqa: BLE001
        bus.publish("step_error", {"tool": "search_trip_options", "error": str(e)})
        results = []

    import json as _json
    payload_json = _json.dumps(results, ensure_ascii=False)
    payload_b64 = base64.b64encode(payload_json.encode("utf-8")).decode("ascii")
    mock_url = (
        f"{server_loopback}/mock-search/"
        f"?q={_urlparse.quote(query)}&data={_urlparse.quote(payload_b64)}"
    )

    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            ctx = await browser.new_context(
                viewport=_TRIPLENS_VIEWPORT,
                device_scale_factor=1,
                user_agent=_TRIPLENS_USER_AGENT,
                locale="en-US",
            )
            page = await ctx.new_page()

            stop = asyncio.Event()
            streamer = asyncio.create_task(_stream_search_frames(page, stop))

            try:
                await page.goto(mock_url, wait_until="domcontentloaded", timeout=10000)
                animation_ms = 45 * len(query) + 450 + 1100 + 160 * len(results) + 800
                await asyncio.sleep(min(animation_ms, 8000) / 1000.0)

                await page.evaluate("window.scrollBy({ top: 240, behavior: 'smooth' })")
                await asyncio.sleep(0.9)

                bus.publish("search_results", {"query": query, "results": results})
                bus.publish("browser_complete", {"task": "search_trip_options", "query": query, "result_count": len(results)})
                await asyncio.sleep(0.4)
            finally:
                stop.set()
                try:
                    await streamer
                except Exception:
                    pass
                try:
                    await browser.close()
                except Exception:
                    pass
    except Exception as e:  # noqa: BLE001 — don't break demo on Playwright crash
        bus.publish("step_error", {"tool": "search_trip_options", "error": str(e)})

    return {"query": query, "results": results, "result_count": len(results)}
