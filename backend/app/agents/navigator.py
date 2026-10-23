"""Bounded self-healing for one failed browser step, driven by the local vision model.

Each turn the model sees a set-of-marks screenshot (numbered boxes on <= 60 visible interactive
elements), the same elements as text, the step goal and its earlier actions, and answers with one
JSON action: click, type (a value from `inputs`, by key), scroll, back or done. The model never
supplies JavaScript or URLs. The browser's route guard aborts direct navigations off the chain's
allowlist; a redirect that lands off it is undone (back) and logged after the action.

robots.txt is never healed around: each turn starts with `browser.robots_checkpoint`, and so
does the return after the last turn (an action may redirect onto a disallowed page); it ends
the heal (and the run) with RobotsBlocked once a navigation was refused or a redirect took the
page through a URL robots.txt disallows.
"""
from __future__ import annotations

import asyncio
import base64
import contextlib
import io
import json
import time
from collections.abc import Callable
from typing import Any

from openai import AsyncOpenAI, OpenAIError
from PIL import Image, ImageDraw, ImageFont
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Page

from app.agents.base import Blocked
from app.agents.browser import (
    Check,
    HealResult,
    allowed,
    is_challenge,
    passes,
    robots_checkpoint,
)
from app.clients.llm import last_json_object
from app.core import tracing
from app.core.config import get_settings
from app.core.logging import get_logger

log = get_logger(__name__)

_MAX_ELEMENTS = 60
_WIDTH = 1024

# Tags the visible, uncovered interactive elements in the viewport, open shadow roots included
# (consent banners), and describes them; fixed code, no input.
_TAG_JS = """(max) => {
  const found = [];
  const walk = root => root.querySelectorAll('*').forEach(e => {
    e.removeAttribute('data-gda-i');
    if (e.matches('a,button,input,select,textarea,[role=button]')) found.push(e);
    if (e.shadowRoot) walk(e.shadowRoot);
  });
  walk(document);
  const out = [];
  for (const e of found) {
    if (out.length >= max) break;
    const r = e.getBoundingClientRect();
    const s = getComputedStyle(e);
    if (r.width < 2 || r.height < 2 || s.visibility === 'hidden' || s.display === 'none'
        || r.bottom < 0 || r.right < 0 || r.top > innerHeight || r.left > innerWidth
        || e.type === 'hidden' || e.disabled) continue;
    // Hit test at the center of the element's visible part: anything a modal sheet or overlay
    // covers would fill the list and hide its own buttons, and its clicks would time out.
    const hit = e.getRootNode().elementFromPoint(
      (Math.max(r.left, 0) + Math.min(r.right, innerWidth)) / 2,
      (Math.max(r.top, 0) + Math.min(r.bottom, innerHeight)) / 2);
    if (!hit || !(hit === e || e.contains(hit))) continue;
    e.setAttribute('data-gda-i', out.length);
    const isField = ['input', 'select', 'textarea'].includes(e.tagName.toLowerCase());
    out.push({i: out.length, tag: e.tagName.toLowerCase(), type: e.type || '',
      text: (isField ? '' : e.innerText || '').trim().replace(/\\s+/g, ' ').slice(0, 60),
      aria: (e.getAttribute('aria-label') || '').slice(0, 60),
      placeholder: (e.getAttribute('placeholder') || '').slice(0, 60),
      box: [r.left, r.top, r.width, r.height]});
  }
  return out;
}"""

_SYSTEM = (
    "You operate a web browser to finish one step on a retailer's website. You get a screenshot "
    "whose interactive elements carry numbered red boxes, the same elements as a list, the goal, "
    "the input names you may type, and your earlier actions. Reply with exactly one JSON object "
    "and nothing else, one of:\n"
    '{"action":"click","el":N}\n'
    '{"action":"type","el":N,"value":"NAME","enter":true}\n'
    '{"action":"scroll","dir":"down"} or {"action":"scroll","dir":"up"}\n'
    '{"action":"back"}\n'
    '{"action":"done"}\n'
    "N is an element number from the list. NAME is one of the input names; the browser types its "
    "value. Close or accept a banner that covers the page. Answer done only when the goal is "
    "visibly reached."
)


def _client() -> AsyncOpenAI:
    s = get_settings()
    return AsyncOpenAI(base_url=s.vlm_base_url, api_key=s.vllm_api_key, max_retries=0)


def _marks(shot: bytes, elements: list[dict]) -> bytes:
    """Screenshot at 1024 px wide with each element's number drawn on its box (set-of-marks)."""
    im = Image.open(io.BytesIO(shot)).convert("RGB")
    scale = _WIDTH / im.width
    im = im.resize((_WIDTH, round(im.height * scale)))
    draw = ImageDraw.Draw(im)
    font = ImageFont.load_default(size=14)
    for e in elements:
        x, y, w, h = (v * scale for v in e["box"])
        draw.rectangle((x, y, x + w, y + h), outline=(230, 0, 0), width=2)
        label = str(e["i"])
        draw.rectangle((x, y, x + 9 * len(label) + 4, y + 16), fill=(230, 0, 0))
        draw.text((x + 2, y), label, fill=(255, 255, 255), font=font)
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=80)
    return buf.getvalue()


def _invalid(act: dict | None, elements: list[dict], inputs: dict[str, str]) -> str | None:
    """Why the model's action cannot be executed, or None when it can."""
    if act is None:
        return "no JSON object"
    name = act.get("action")
    if name in ("back", "done"):
        return None
    if name == "scroll":
        return None if act.get("dir") in ("down", "up") else "dir must be down or up"
    if name not in ("click", "type"):
        return f"unknown action {name!r}"
    el = act.get("el")
    if not isinstance(el, int) or not 0 <= el < len(elements):
        return f"el must be an element number 0..{len(elements) - 1}"
    if name == "type":
        if act.get("value") not in inputs:
            return f"value must be one of the input names {sorted(inputs)}"
        if elements[el]["tag"] not in ("input", "textarea") or elements[el]["type"] == "password":
            return "type only into a text field (never a password field)"
    return None


async def _ask(client: AsyncOpenAI, goal: str, url: str, elements: list[dict],
               inputs: dict[str, str], history: list[str], image: bytes) -> str:
    listed = "\n".join(
        json.dumps({k: e[k] for k in ("i", "tag", "text", "aria", "placeholder") if e[k] != ""},
                   ensure_ascii=False)
        for e in elements
    )
    text = (
        f"Goal: {goal}\nURL: {url}\nInput names: {sorted(inputs)}\n"
        f"Elements:\n{listed or '(none visible)'}\n"
        f"Earlier actions:\n{chr(10).join(history) or '(none)'}"
    )
    s = get_settings()
    resp = await client.chat.completions.create(
        model=s.vision_model,
        messages=[
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": [
                {"type": "text", "text": text},
                {"type": "image_url", "image_url": {
                    "url": "data:image/jpeg;base64," + base64.b64encode(image).decode()}},
            ]},
        ],
        temperature=0,
        max_tokens=300,
        extra_body={"priority": 0, "chat_template_kwargs": {"enable_thinking": False}},
    )
    return resp.choices[0].message.content or ""


async def _observe(page: Page) -> tuple[list[dict], bytes]:
    """Numbered elements and the marked screenshot; one retry when a navigation races us."""
    try:
        elements = await page.evaluate(_TAG_JS, _MAX_ELEMENTS)
        return elements, _marks(await page.screenshot(type="jpeg"), elements)
    except PlaywrightError:
        with contextlib.suppress(PlaywrightError):
            await page.wait_for_load_state("load", timeout=5000)
        elements = await page.evaluate(_TAG_JS, _MAX_ELEMENTS)
        return elements, _marks(await page.screenshot(type="jpeg"), elements)


def _reason(reply: str) -> str:
    """The model's prose before its JSON answer (the hint a developer reads in the log)."""
    i = reply.rfind("{")
    return (reply[:i] if i >= 0 else reply).strip()[:200]


async def _perform(page: Page, act: dict, elements: list[dict], inputs: dict[str, str]) -> None:
    name = act["action"]
    if name == "scroll":
        await page.mouse.wheel(0, 700 if act["dir"] == "down" else -700)
    elif name == "back":
        await page.go_back(timeout=10000)
    else:
        el = page.locator(f'[data-gda-i="{act["el"]}"]')
        if name == "click":
            await el.click(timeout=5000)
        else:
            await el.fill(inputs[act["value"]], timeout=5000)
            if act.get("enter"):
                await el.press("Enter", timeout=5000)
    with contextlib.suppress(PlaywrightError):
        await page.wait_for_load_state("load", timeout=5000)
    await page.wait_for_timeout(500)  # client-side rendering after the action


async def heal(page: Page, goal: str, check: Check, **kw: Any) -> HealResult:
    """Traced self-heal of one step (see _heal); each model turn is a heal_step."""
    with tracing.span("heal", "agent", input=goal, url=page.url) as sp:
        result = await _heal(page, goal, check, **kw)
        tracing.attrs(sp, ok=result.ok, actions=len(result.actions), ms=result.ms,
                      output=result.actions)
        return result


async def _heal(
    page: Page,
    goal: str,
    check: Check,
    *,
    allow: tuple[str, ...],
    inputs: dict[str, str],
    emit: Callable[[dict], None],
    max_actions: int = 10,
    max_seconds: float = 90,
    max_invalid: int = 3,
) -> HealResult:
    """Let the vision model act until `check()` passes or a budget runs out. Raises Blocked
    (RobotsBlocked after a robots.txt refusal)."""
    t0 = time.monotonic()
    actions: list[dict] = []
    history: list[str] = []
    done = invalid = 0
    ok = False
    if not allowed(page.url, allow):  # e.g. Chromium's error page after a blocked navigation
        with contextlib.suppress(PlaywrightError):
            await page.go_back(timeout=10000)
    try:
        async with asyncio.timeout(max_seconds), _client() as client:
            while done < max_actions and invalid < max_invalid:
                with tracing.span("heal_step", "tool", url=page.url) as step:
                    await robots_checkpoint(page, allow)  # RobotsBlocked: never worked around
                    if await is_challenge(page):
                        raise Blocked("bot protection")
                    elements, shot = await _observe(page)
                    reply = await _ask(client, goal, page.url, elements, inputs, history, shot)
                    act = last_json_object(reply)
                    why = _invalid(act, elements, inputs)
                    if act is None or why:
                        invalid += 1
                        actions.append({"action": "invalid", "error": why, "ok": False,
                                        "url": page.url, "el_text": "", "reason": _reason(reply)})
                        emit(actions[-1])
                        tracing.attrs(step, output=actions[-1], ok=False)
                        history.append(f"invalid answer ({why})")
                        continue
                    done += 1
                    t = time.monotonic()
                    el = elements[act["el"]] if act["action"] in ("click", "type") else {}
                    rec = {k: act[k] for k in ("action", "el", "value", "enter", "dir") if k in act}
                    rec["el_text"] = (el.get("text") or el.get("aria") or el.get("placeholder")
                                      or "")
                    rec["reason"] = _reason(reply)
                    rec["ok"] = True
                    if act["action"] != "done":
                        try:
                            await _perform(page, act, elements, inputs)
                        except PlaywrightError as exc:
                            rec.update(ok=False, error=str(exc).splitlines()[0][:200])
                        if not allowed(page.url, allow):
                            rec.update(ok=False, violation=page.url)
                            log.warning("nav.off_domain", url=page.url)
                            with contextlib.suppress(PlaywrightError):
                                await page.go_back(timeout=10000)
                    rec["url"] = page.url
                    rec["ms"] = int((time.monotonic() - t) * 1000)
                    ok = await passes(check)
                    if act["action"] == "done" and not ok:
                        rec["ok"] = False
                        rec["error"] = "goal not reached"
                    actions.append(rec)
                    emit(rec)
                    tracing.attrs(step, action=rec["action"], output=rec, ok=rec["ok"])
                    history.append(
                        json.dumps({k: rec[k] for k in ("action", "el", "value", "dir", "error",
                                                        "violation") if k in rec})
                        + (" -> goal reached" if ok else f" -> now at {page.url}")
                    )
                    if ok:
                        break
    except (TimeoutError, OpenAIError, PlaywrightError) as exc:
        log.warning("nav.heal_stopped", error=f"{type(exc).__name__}: {exc}"[:200])
    # The last action may have redirected onto a disallowed page before check() passed.
    await robots_checkpoint(page, allow)
    ms = int((time.monotonic() - t0) * 1000)
    log.info("nav.heal", goal=goal, ok=ok, actions=done, invalid=invalid, ms=ms)
    return HealResult(ok=ok, actions=actions, ms=ms)
