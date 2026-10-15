"""Self-healing navigator with real Chromium on the fixture store finder and a FAKE vision model
(httpx.MockTransport answering with scripted actions, picking elements by their text)."""
from __future__ import annotations

import asyncio
import json
import time

import httpx2 as httpx  # the HTTP library the pinned openai client is built on
import pytest
from openai import AsyncOpenAI

from app.agents import browser, navigator
from app.agents.base import Blocked, StepFailed
from tests.unit.test_browser import ALLOW
from tests.unit.test_browser import site as site  # pytest fixture

INPUTS = {"plz": "80331"}
GOAL = "Search the PLZ and select the store"


def el(text: str, needle: str) -> int:
    """Number of the first listed element whose description contains `needle`."""
    listed = text.split("Elements:\n", 1)[1].split("\nEarlier actions:", 1)[0]
    return next(json.loads(line)["i"] for line in listed.splitlines() if needle in line)


def fake_model(monkeypatch, replies, delay: float = 0) -> list[dict]:
    """Serve `replies` in order (the last one repeats); each is a string or text -> string."""
    requests: list[dict] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        requests.append(body)
        await asyncio.sleep(delay)
        reply = replies[min(len(requests), len(replies)) - 1]
        text = body["messages"][1]["content"][0]["text"]
        content = reply(text) if callable(reply) else reply
        return httpx.Response(200, json={
            "id": "x", "object": "chat.completion", "created": 0, "model": "vlm",
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": content}}],
        })

    monkeypatch.setattr(navigator, "_client", lambda: AsyncOpenAI(
        base_url="http://vlm.test/v1", api_key="k", max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    ))
    return requests


def act(**a) -> str:
    return json.dumps(a)


TYPE_PLZ = lambda t: act(action="type", el=el(t, "PLZ oder Ort"), value="plz", enter=True)  # noqa: E731
CLICK_STORE = lambda t: act(action="click", el=el(t, "Markt wählen"))  # noqa: E731


async def _heal_on_shop(site, **kwargs):
    async with browser.browser_page(ALLOW) as page:
        await page.goto(f"{site}/shop.html")
        events: list[dict] = []
        result = await navigator.heal(
            page, GOAL, lambda: page.locator("#selected").is_visible(),
            allow=ALLOW, inputs=INPUTS, emit=events.append, **kwargs,
        )
        fields = [await page.input_value(s) for s in ("#plz", "#pw")]
        return result, events, fields, page.url


def test_invalid_rejects_everything_outside_the_action_space():
    elements = [{"i": 0, "tag": "input", "type": "text"}, {"i": 1, "tag": "input",
                                                           "type": "password"},
                {"i": 2, "tag": "button", "type": "button"}]
    ok = [{"action": "click", "el": 2}, {"action": "type", "el": 0, "value": "plz"},
          {"action": "scroll", "dir": "up"}, {"action": "back"}, {"action": "done"}]
    for a in ok:
        assert navigator._invalid(a, elements, INPUTS) is None
    bad = [None, {"action": "eval", "js": "alert(1)"}, {"action": "goto", "url": "https://x.test"},
           {"action": "click", "el": 3}, {"action": "click", "el": "2"},
           {"action": "type", "el": 0, "value": "80331"},
           {"action": "type", "el": 1, "value": "plz"},
           {"action": "type", "el": 2, "value": "plz"}, {"action": "scroll", "dir": "left"}]
    for a in bad:
        assert navigator._invalid(a, elements, INPUTS), a


def test_step_heals_a_broken_selector_and_logs_every_action(site, monkeypatch):
    requests = fake_model(monkeypatch, ["Ich suche zuerst die PLZ.", TYPE_PLZ, CLICK_STORE])

    async def go():
        async with browser.browser_page(ALLOW) as page:
            await page.goto(f"{site}/shop.html")

            async def do():  # the site renamed its button: the scripted selector is stale
                await page.click("#select-store", timeout=500)

            events: list[dict] = []
            heal_log: list = []
            result = await browser.step(
                page, "select_store", GOAL, do, lambda: page.locator("#selected").is_visible(),
                allow=ALLOW, inputs=INPUTS, emit=events.append, heal_log=heal_log,
            )
            return result, events, heal_log, await page.text_content("#selected")

    result, events, heal_log, selected = asyncio.run(go())
    assert result.ok and not result.forced
    assert selected.startswith("Ihr Markt: Testmarkt")
    assert [a["action"] for a in heal_log] == ["invalid", "type", "click"]
    assert heal_log[1]["value"] == "plz" and heal_log[2]["el_text"] == "Markt wählen"
    assert heal_log[0]["reason"] == "Ich suche zuerst die PLZ." and heal_log[1]["reason"] == ""
    assert all(a["step"] == "select_store" and "ms" in a for a in heal_log[1:])
    assert [e["event"] for e in events] == ["heal"] * 3
    assert {k for k in events[2]} >= {"step", "action", "el_text", "url", "ok", "forced"}
    for body in requests:
        assert body["max_tokens"] == 300 and body["priority"] == 0
        assert body["chat_template_kwargs"] == {"enable_thinking": False}
        assert body["messages"][1]["content"][1]["image_url"]["url"].startswith(
            "data:image/jpeg;base64,")
        assert "80331" not in body["messages"][1]["content"][0]["text"]  # names, never values
    assert "invalid answer" in requests[1]["messages"][1]["content"][0]["text"]


def test_forced_heal_skips_the_script(site, monkeypatch):
    fake_model(monkeypatch, [TYPE_PLZ, CLICK_STORE])

    async def go():
        async with browser.browser_page(ALLOW) as page:
            await page.goto(f"{site}/shop.html")

            async def do():
                raise AssertionError("the script must not run")

            events: list[dict] = []
            result = await browser.step(
                page, "select_store", GOAL, do, lambda: page.locator("#selected").is_visible(),
                allow=ALLOW, inputs=INPUTS, emit=events.append, heal_log=[], force_heal=True,
            )
            return result, events

    result, events = asyncio.run(go())
    assert result.ok and result.forced
    assert [e["forced"] for e in events] == [True, True]


def test_typing_is_limited_to_input_values_and_never_a_password(site, monkeypatch):
    fake_model(monkeypatch, [
        lambda t: act(action="type", el=el(t, "PLZ oder Ort"), value="80331"),
        lambda t: act(action="type", el=el(t, "Passwort"), value="plz"),
        lambda t: act(action="type", el=el(t, "Suchen"), value="plz"),
    ])
    result, events, fields, _ = asyncio.run(_heal_on_shop(site))
    assert not result.ok
    assert [a["action"] for a in result.actions] == ["invalid"] * 3  # max_invalid=3
    assert fields == ["", ""]


def test_failed_heal_makes_the_step_fail(site, monkeypatch):
    fake_model(monkeypatch, ["no idea"])

    async def go():
        async with browser.browser_page(ALLOW) as page:
            await page.goto(f"{site}/shop.html")

            async def do():
                await page.click("#select-store", timeout=500)

            heal_log: list = []
            try:
                await browser.step(
                    page, "select_store", GOAL, do,
                    lambda: page.locator("#selected").is_visible(),
                    allow=ALLOW, inputs=INPUTS, emit=lambda e: None, heal_log=heal_log,
                )
            finally:
                assert len(heal_log) == 3

    with pytest.raises(StepFailed, match="select_store"):
        asyncio.run(go())


@pytest.mark.parametrize("link", ["Partnerseite", "Weiterleitung"])
def test_off_domain_navigation_is_undone_and_logged(site, monkeypatch, link):
    fake_model(monkeypatch, [lambda t: act(action="click", el=el(t, link)), act(action="done")])
    result, events, _, url = asyncio.run(_heal_on_shop(site, max_actions=2))
    first, second = result.actions
    assert not result.ok and url == f"{site}/shop.html"
    assert first["violation"] and not first["ok"] and first["url"] == url
    assert second == {**second, "action": "done", "ok": False, "error": "goal not reached"}


def test_action_budget(site, monkeypatch):
    fake_model(monkeypatch, [act(action="scroll", dir="down")])
    result, events, _, _ = asyncio.run(_heal_on_shop(site, max_actions=3))
    assert not result.ok and [a["action"] for a in result.actions] == ["scroll"] * 3
    assert len(events) == 3


def test_time_budget(site, monkeypatch):
    fake_model(monkeypatch, [TYPE_PLZ], delay=5)
    t0 = time.monotonic()
    result, events, _, _ = asyncio.run(_heal_on_shop(site, max_seconds=1))
    assert not result.ok and result.actions == [] and time.monotonic() - t0 < 4


def test_model_down_ends_the_heal(site, monkeypatch):
    async def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(navigator, "_client", lambda: AsyncOpenAI(
        base_url="http://vlm.test/v1", api_key="k", max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(down)),
    ))
    result, _, _, _ = asyncio.run(_heal_on_shop(site))
    assert not result.ok and result.actions == []


def test_bot_wall_raises_blocked(site, monkeypatch):
    fake_model(monkeypatch, [act(action="done")])

    async def go():
        async with browser.browser_page(ALLOW) as page:
            await page.goto(f"{site}/cloudflare.html")
            await navigator.heal(page, GOAL, lambda: page.locator("#selected").is_visible(),
                                 allow=ALLOW, inputs=INPUTS, emit=lambda e: None)

    with pytest.raises(Blocked):
        asyncio.run(go())


def test_heal_log_keeps_actions_when_the_heal_hits_a_bot_wall(site, monkeypatch):
    fake_model(monkeypatch, [lambda t: act(action="click", el=el(t, "Angebote"))])
    heal_log: list = []
    events: list[dict] = []

    async def go():
        async with browser.browser_page(ALLOW) as page:
            await page.goto(f"{site}/shop.html")
            await browser.step(
                page, "open", GOAL, lambda: page.wait_for_timeout(0),
                lambda: page.locator("#selected").is_visible(),
                allow=ALLOW, inputs=INPUTS, emit=events.append, heal_log=heal_log,
            )

    with pytest.raises(Blocked):
        asyncio.run(go())
    assert [a["action"] for a in heal_log] == ["click"] and len(events) == 1


def test_shadow_dom_banner_is_numbered_and_clickable(site):
    async def go():
        async with browser.browser_page(ALLOW) as page:
            await page.goto(f"{site}/shop.html")
            elements, _ = await navigator._observe(page)
            i = next(e["i"] for e in elements if e["text"] == "Alle akzeptieren")
            await page.locator(f'[data-gda-i="{i}"]').click(timeout=2000)
            return await page.locator("#consent").is_hidden()

    assert asyncio.run(go()) is True


def test_elements_under_a_modal_sheet_are_not_numbered(site):
    links = "".join(f'<a href="#">Stadt {k}</a><br>' for k in range(80))
    sheet = ('<div style="position:fixed;inset:0;background:#0008">'
             '<div style="position:absolute;right:0;top:0;width:400px;height:100%;background:#fff">'
             '<button>Meine Filiale</button></div></div>')

    async def go():
        async with browser.browser_page(ALLOW) as page:
            await page.set_content(f"<body>{links}{sheet}</body>")
            elements, _ = await navigator._observe(page)
            return [e["text"] for e in elements]

    assert asyncio.run(go()) == ["Meine Filiale"]
