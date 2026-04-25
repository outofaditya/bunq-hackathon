"""Playwright driver for the mock vendor booking flow.

Runs headless Chromium, clicks through the 4-stage booking page at /mock-vendor/,
and streams live JPEG screenshots to the dashboard via the SSE bus. The visible
browser beat during EXECUTING is the "agent went and booked it for you" moment.

Usage:
    from .browser_agent import book_hotel
    result = await book_hotel(hotel="Hotel V", amount_eur=445, ...)

Returns:
    {"booking_ref": "STH-XXXXXX", "hotel": ..., "amount_eur": ...}
"""
from __future__ import annotations

import asyncio
import base64
import html
import re
import urllib.parse
from typing import Any

import httpx
from playwright.async_api import Page, async_playwright

from .events import bus

# Keep frame size modest: 900x560 ≈ dashboard panel aspect, ~20-35KB per JPEG at q=70
VIEWPORT = {"width": 900, "height": 560}
FRAME_INTERVAL_S = 0.35
# Local URL Playwright hits — always loopback, never through ngrok.
VENDOR_URL = "http://127.0.0.1:8000/mock-vendor/"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


# ─── Animated cursor overlay ─────────────────────────────────────────────
# Injects a fake "agent cursor" into the page so screenshots show where the
# agent is clicking. Colored bunq-orange (#FF7819) per brand.

_CURSOR_INJECT_JS = r"""
() => {
  if (document.getElementById('__agent_cursor')) return;
  const c = document.createElement('div');
  c.id = '__agent_cursor';
  c.style.cssText =
    'position:fixed;pointer-events:none;width:18px;height:18px;border-radius:50%;'
    +'background:rgba(255,120,25,0.85);border:2px solid #fff;'
    +'transform:translate(-50%,-50%) scale(1);'
    +'z-index:99999;box-shadow:0 4px 14px rgba(0,0,0,0.35);'
    +'left:50%;top:30%;'
    +'transition:left .42s cubic-bezier(.4,.0,.2,1), top .42s cubic-bezier(.4,.0,.2,1), transform .12s ease, background .12s ease;';
  document.body.appendChild(c);

  const ring = document.createElement('div');
  ring.id = '__agent_ring';
  ring.style.cssText =
    'position:fixed;pointer-events:none;width:36px;height:36px;border-radius:50%;'
    +'border:2px solid rgba(255,120,25,0.9);'
    +'transform:translate(-50%,-50%) scale(0);opacity:0;'
    +'z-index:99998;'
    +'transition:transform .42s ease-out, opacity .42s ease-out;'
    +'left:50%;top:30%;';
  document.body.appendChild(ring);
}
"""

_CURSOR_MOVE_JS = (
    "(p) => { const c=document.getElementById('__agent_cursor');"
    " if(c){ c.style.left=p.x+'px'; c.style.top=p.y+'px'; }"
    " const r=document.getElementById('__agent_ring');"
    " if(r){ r.style.left=p.x+'px'; r.style.top=p.y+'px'; r.style.opacity='0';"
    "        r.style.transform='translate(-50%,-50%) scale(0)'; }"
    " }"
)

_CURSOR_PULSE_JS = (
    "() => { const c=document.getElementById('__agent_cursor');"
    " if(c){ c.style.transform='translate(-50%,-50%) scale(0.7)';"
    "        c.style.background='rgba(250,200,0,0.95)'; }"
    " const r=document.getElementById('__agent_ring');"
    " if(r){ r.style.opacity='1'; r.style.transform='translate(-50%,-50%) scale(1.6)'; }"
    " }"
)

_CURSOR_RESTORE_JS = (
    "() => { const c=document.getElementById('__agent_cursor');"
    " if(c){ c.style.transform='translate(-50%,-50%) scale(1)';"
    "        c.style.background='rgba(255,120,25,0.85)'; } }"
)


async def _click_with_cursor(page: Page, selector: str, settle_s: float = 0.18) -> None:
    """Animate the agent cursor toward `selector`, pulse, then click.

    Falls back to a plain page.click() if the bounding box can't be resolved
    (still emits a real click — we just skip the visual flourish).
    """
    try:
        loc = page.locator(selector).first
        await loc.scroll_into_view_if_needed(timeout=1500)
        box = await loc.bounding_box()
    except Exception:
        await page.click(selector)
        return
    if not box:
        await page.click(selector)
        return
    cx = box["x"] + box["width"] / 2
    cy = box["y"] + box["height"] / 2
    await page.evaluate(_CURSOR_MOVE_JS, {"x": cx, "y": cy})
    # Let the CSS transition play out so the JPEG stream catches the motion.
    await asyncio.sleep(0.42)
    await page.evaluate(_CURSOR_PULSE_JS)
    await asyncio.sleep(0.12)
    await page.click(selector)
    await asyncio.sleep(settle_s)
    await page.evaluate(_CURSOR_RESTORE_JS)


async def _stream_frames(page: Page, stop: asyncio.Event) -> None:
    """Publish one JPEG frame every FRAME_INTERVAL_S until stop is set."""
    while not stop.is_set():
        try:
            png = await page.screenshot(type="jpeg", quality=65, full_page=False)
            b64 = base64.b64encode(png).decode("ascii")
            await bus.publish("browser_frame", jpeg_b64=b64)
        except Exception:
            # Page may be mid-navigation; skip this tick
            pass
        try:
            await asyncio.wait_for(stop.wait(), timeout=FRAME_INTERVAL_S)
        except asyncio.TimeoutError:
            continue


async def _ddg_fetch(query: str, max_results: int) -> list[dict[str, Any]]:
    """Fetch real web results via DuckDuckGo's HTML endpoint (plain HTTP, no bot blocks).

    Returns up to max_results items of {title, url, snippet}.
    """
    def _blocking() -> list[dict[str, Any]]:
        headers = {
            "User-Agent": USER_AGENT,
            "Referer": "https://html.duckduckgo.com/",
        }
        data = {"q": query, "b": "", "kl": "us-en"}
        with httpx.Client(headers=headers, timeout=12.0, follow_redirects=True) as c:
            r = c.post("https://html.duckduckgo.com/html/", data=data)
            r.raise_for_status()
            body = r.text

        # Each result is <div class="result">...</div> containing <a class="result__a" href=...>
        # and a sibling <a class="result__snippet"> with the description.
        blocks = re.findall(
            r'<div[^>]*class="result[^"]*"[^>]*>([\s\S]*?)</div>\s*</div>\s*</div>',
            body,
        )
        out: list[dict[str, Any]] = []
        for blk in blocks:
            m_title = re.search(
                r'<a\s+rel="nofollow"\s+class="result__a"\s+href="([^"]+)"[^>]*>([\s\S]*?)</a>',
                blk,
            )
            if not m_title:
                continue
            url = html.unescape(m_title.group(1))
            # DDG wraps external URLs in /l/?uddg=<encoded>
            parsed = urllib.parse.urlparse(url)
            if "duckduckgo.com" in parsed.netloc and parsed.path.startswith(("/l/", "/y.js")):
                qs = urllib.parse.parse_qs(parsed.query)
                real = qs.get("uddg", [""])[0]
                if real:
                    url = urllib.parse.unquote(real)
            title = re.sub(r"<[^>]+>", "", m_title.group(2))
            title = html.unescape(title).strip()

            m_snip = re.search(
                r'<a[^>]*class="result__snippet"[^>]*>([\s\S]*?)</a>',
                blk,
            )
            snippet = ""
            if m_snip:
                snippet = re.sub(r"<[^>]+>", "", m_snip.group(1))
                snippet = html.unescape(snippet).strip()

            # Skip sponsored/ad slots that route through y.js
            if "/y.js" in (m_title.group(1) or ""):
                continue

            out.append({"title": title, "url": url, "snippet": snippet})
            if len(out) >= max_results:
                break
        return out

    return await asyncio.to_thread(_blocking)


async def search_trip_options(query: str, max_results: int = 6) -> dict[str, Any]:
    """Run a live, visible web search. Streams frames + publishes found links.

    Real results come from DuckDuckGo's HTML endpoint via httpx (reliable, no bot blocking).
    Those results are then rendered in a local TripLens-styled search page that Playwright
    navigates — so the user sees a full "typing the query → searching → results scroll in"
    animation in the browser panel, and the Research feed on the dashboard lists the links.
    """
    await bus.publish("browser_status", status="launching", query=query)

    # 1. Fetch real results via plain HTTP (fast, reliable, no anti-bot)
    try:
        results = await _ddg_fetch(query, max_results)
    except Exception as e:
        results = []
        await bus.publish("browser_status", status="fetch_error", step=str(e)[:120])

    # 2. Build local mock-search URL with results embedded as base64 JSON
    import json as _json
    payload_json = _json.dumps(results, ensure_ascii=False)
    payload_b64 = base64.b64encode(payload_json.encode("utf-8")).decode("ascii")
    mock_url = (
        "http://127.0.0.1:8000/mock-search/"
        f"?q={urllib.parse.quote(query)}&data={urllib.parse.quote(payload_b64)}"
    )

    # 3. Stream Playwright rendering the search experience
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        ctx = await browser.new_context(
            viewport=VIEWPORT,
            device_scale_factor=1,
            user_agent=USER_AGENT,
            locale="en-US",
        )
        page = await ctx.new_page()

        stop = asyncio.Event()
        streamer = asyncio.create_task(_stream_frames(page, stop))

        try:
            await bus.publish("browser_status", status="loading", step="TripLens")
            await page.goto(mock_url, wait_until="domcontentloaded", timeout=10000)

            # The page auto-animates: types the query, "searches", renders results one by one.
            # Total animation time ≈ 45ms * len(query) + 450 + 1100 + 120 * len(results).
            animation_ms = 45 * len(query) + 450 + 1100 + 160 * len(results) + 800
            await asyncio.sleep(min(animation_ms, 8000) / 1000.0)

            # Gentle scroll so later results come into frame
            await page.evaluate("window.scrollBy({ top: 240, behavior: 'smooth' })")
            await asyncio.sleep(0.9)

            await bus.publish("search_results", query=query, results=results)
            await bus.publish("browser_status", status="done", result_count=len(results))
            await asyncio.sleep(0.6)

            return {"query": query, "results": results, "result_count": len(results)}
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


async def book_hotel(
    hotel: str,
    amount_eur: float,
    location: str = "Amsterdam, Netherlands",
    checkin: str = "Sat 26 Apr",
    checkout: str = "Sun 27 Apr",
    guest: str = "Sara van Doorn",
) -> dict[str, Any]:
    """Drive the mock vendor booking end-to-end. Returns booking reference."""
    params = urllib.parse.urlencode({
        "hotel": hotel,
        "location": location,
        "price": f"{amount_eur:.0f}",
        "checkin": checkin,
        "checkout": checkout,
        "guest": guest,
    })
    url = f"{VENDOR_URL}?{params}"

    await bus.publish("browser_status", status="launching", hotel=hotel)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context(viewport=VIEWPORT, device_scale_factor=1)
        page = await context.new_page()

        stop = asyncio.Event()
        streamer = asyncio.create_task(_stream_frames(page, stop))

        try:
            await bus.publish("browser_status", status="loading", step="landing")
            await page.goto(url, wait_until="domcontentloaded")
            await page.wait_for_selector('[data-stage="landing"]:not([hidden])', timeout=5000)
            await page.evaluate(_CURSOR_INJECT_JS)
            await asyncio.sleep(1.4)

            await bus.publish("browser_status", status="clicking", step="book-now")
            await _click_with_cursor(page, "#book-now")
            await page.wait_for_selector('[data-stage="details"]:not([hidden])', timeout=3000)
            await page.evaluate(_CURSOR_INJECT_JS)  # re-inject after stage swap
            await asyncio.sleep(0.6)

            first, *rest = guest.split(" ")
            last = " ".join(rest) or "Guest"
            await bus.publish("browser_status", status="typing", step="guest-first")
            await page.fill("#guest-first", first, force=True)
            await asyncio.sleep(0.3)
            await page.fill("#guest-last", last, force=True)
            await asyncio.sleep(0.3)
            await page.fill("#guest-email", f"{first.lower()}@bunq.example", force=True)
            await asyncio.sleep(0.5)

            await bus.publish("browser_status", status="clicking", step="continue")
            await _click_with_cursor(page, "#continue")
            await page.wait_for_selector('[data-stage="payment"]:not([hidden])', timeout=3000)
            await page.evaluate(_CURSOR_INJECT_JS)
            await asyncio.sleep(0.8)

            await bus.publish("browser_status", status="clicking", step="confirm-pay")
            await _click_with_cursor(page, "#confirm-pay")
            await page.wait_for_selector('[data-stage="done"]:not([hidden])', timeout=5000)
            await asyncio.sleep(0.9)

            ref_el = await page.wait_for_selector("#booking-ref", timeout=2000)
            ref = (await ref_el.text_content() or "").strip() or "STH-UNKNOWN"

            # Hold on the confirmation screen for a beat so judges can read it
            await asyncio.sleep(1.0)

            await bus.publish("browser_status", status="done", booking_ref=ref)
            return {
                "booking_ref": ref,
                "hotel": hotel,
                "amount_eur": amount_eur,
                "url": url,
            }
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
