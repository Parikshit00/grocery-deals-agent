"""Reasoning LLM access via the local vLLM OpenAI-compatible endpoint.

The server runs `--reasoning-parser qwen3`, so the thinking trace arrives in the message's
`reasoning` (older vLLM: `reasoning_content`) field and `content` holds only the answer.
"""
from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from openai import AsyncOpenAI
from pydantic import ValidationError

from app.core import tracing
from app.core.config import get_settings
from app.core.logging import get_logger
from app.schemas.basket import Item
from app.services.offers import parse_pack

log = get_logger(__name__)

NO_THINK = {"chat_template_kwargs": {"enable_thinking": False}}
RECIPE_MAX_TOKENS = 6000
# Past this many thinking tokens the server ends the thinking and the model answers.
RECIPE_THINKING_TOKENS = 1024

_RECIPE_SYSTEM = (
    "You turn a recipe or dish into a grocery shopping list for a German supermarket, scaled to "
    "the given number of servings. Leave out water, salt, pepper and other pantry basics. Return "
    "ONLY a JSON array of objects {\"name\": short German product search term, \"qty\": integer "
    "amount, \"unit\": \"g\" | \"ml\" | \"stk\"}; use null for qty and unit when an amount makes "
    "no sense. Example: [{\"name\": \"Hackfleisch\", \"qty\": 500, \"unit\": \"g\"}, "
    "{\"name\": \"Zwiebeln\", \"qty\": 2, \"unit\": \"stk\"}]."
)

_client: AsyncOpenAI | None = None


def get_client() -> AsyncOpenAI:
    """One shared client (connection pool) for the basket agent and recipe planning."""
    global _client
    if _client is None:
        settings = get_settings()
        _client = AsyncOpenAI(base_url=settings.llm_base_url, api_key=settings.vllm_api_key,
                              max_retries=0, timeout=60.0)
    return _client


def reasoning(obj: Any) -> str:
    """The reasoning text of a message or stream delta, if any."""
    return getattr(obj, "reasoning", None) or getattr(obj, "reasoning_content", None) or ""


def _last_balanced(content: str, open_: str, close: str, kind: type) -> Any:
    """The last bracket-balanced JSON value of `kind` in the text (answers come last)."""
    end = content.rfind(close)
    while end != -1:
        depth = 0
        for i in range(end, -1, -1):
            if content[i] == close:
                depth += 1
            elif content[i] == open_:
                depth -= 1
                if depth == 0:
                    try:
                        value = json.loads(content[i : end + 1])
                    except json.JSONDecodeError:
                        break
                    if isinstance(value, kind):
                        return value
                    break
        end = content.rfind(close, 0, end)
    return None


def last_json_object(content: str) -> dict | None:
    """Last brace-balanced JSON object in the text (the model may write prose first)."""
    return _last_balanced(content, "{", "}", dict)


def _last_json_array(content: str) -> list | None:
    """Last bracket-balanced JSON array: prose before it may restate the prompt's example."""
    return _last_balanced(content, "[", "]", list)


def _item(entry: Any) -> Item | None:
    if isinstance(entry, str):
        entry = {"name": entry}
    if not isinstance(entry, dict) or not str(entry.get("name") or "").strip():
        return None
    name, qty, unit = str(entry["name"]).strip()[:80], entry.get("qty"), entry.get("unit")
    pack = parse_pack(f"{qty} {unit}") if qty and unit else None
    count = round(qty) if not unit and isinstance(qty, int | float) and qty >= 1 else None
    try:
        return Item(name=name, qty=pack[0] if pack else count, unit=pack[1] if pack else None)
    except ValidationError:
        return None


def parse_items(content: str) -> list[Item]:
    """Shopping items from the model's answer: a JSON array of objects (or plain names)."""
    arr = _last_json_array(content) or []
    return [item for entry in arr if (item := _item(entry))]


async def decompose_recipe(
    text: str, servings: int, on_think: Callable[[str], None] | None = None
) -> list[Item]:
    """Turn a recipe or dish into German shopping items for `servings` via the local LLM.

    Streams the reasoning trace (at most RECIPE_THINKING_TOKENS) to `on_think`. If the answer
    still runs out of tokens, retries once with thinking off.
    """
    with tracing.span("recipe_decomposition", "chain", input={"text": text, "servings": servings}
                      ) as sp:
        items = await _decompose(text, servings, on_think)
        tracing.attrs(sp, output=[i.model_dump() for i in items], items=len(items))
        return items


async def _decompose(
    text: str, servings: int, on_think: Callable[[str], None] | None
) -> list[Item]:
    settings = get_settings()
    messages: list[Any] = [{"role": "system", "content": _RECIPE_SYSTEM},
                {"role": "user", "content": f"{text}\nPortionen: {servings}"}]
    for extra in ({"thinking_token_budget": RECIPE_THINKING_TOKENS}, NO_THINK):
        stream = await get_client().chat.completions.create(
            model=settings.llm_model, messages=messages,
            temperature=0.2, max_tokens=RECIPE_MAX_TOKENS, stream=True, extra_body=extra)
        content, finish = "", None
        async for event in stream:
            if not event.choices:
                continue
            choice = event.choices[0]
            if (think := reasoning(choice.delta)) and on_think:
                on_think(think)
            content += choice.delta.content or ""
            finish = choice.finish_reason or finish
        if finish != "length":
            return parse_items(content)
        log.warning("recipe.length", thinking=extra is not NO_THINK)
    return []
