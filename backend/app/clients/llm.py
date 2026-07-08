"""Reasoning-LLM access via the local vLLM OpenAI-compatible endpoint."""
from __future__ import annotations

import json
import re
from collections.abc import Callable

from openai import AsyncOpenAI

from app.core.config import get_settings
from app.core.logging import get_logger

log = get_logger(__name__)

_RECIPE_SYSTEM = (
    "You convert a cooking recipe or dish name into a concise grocery shopping list for a "
    "German supermarket. Return ONLY a JSON array of short German product search terms, no "
    'quantities and no commentary. Example: ["Rindersteak", "Kartoffeln", "Butter"].'
)


def get_client() -> AsyncOpenAI:
    settings = get_settings()
    return AsyncOpenAI(base_url=settings.llm_base_url, api_key=settings.llm_api_key)


def strip_think(content: str) -> str:
    """Drop the reasoning trace: Qwen3-Thinking-2507 ends its <think> block with </think>."""
    return content.rsplit("</think>", 1)[-1] if "</think>" in content else content


def _last_json_array(content: str) -> list[str] | None:
    """The last bracket-balanced JSON array in the text.

    The reasoning model reasons in prose and does not reliably emit a `</think>` delimiter, and
    that prose restates the example list, so a first-`[`-to-last-`]` span is not valid JSON. We
    instead scan back from each `]` to its matching `[` and take the last array that parses -
    the model's actual answer, which it always writes last.
    """
    end = content.rfind("]")
    while end != -1:
        depth = 0
        for i in range(end, -1, -1):
            if content[i] == "]":
                depth += 1
            elif content[i] == "[":
                depth -= 1
                if depth == 0:
                    try:
                        arr = json.loads(content[i : end + 1])
                    except json.JSONDecodeError:
                        break
                    if isinstance(arr, list):
                        return [str(x).strip() for x in arr if str(x).strip()]
                    break
        end = content.rfind("]", 0, end)
    return None


def _parse_list(content: str) -> list[str]:
    content = strip_think(content)
    arr = _last_json_array(content)
    if arr is not None:
        return arr
    # no JSON array at all: split only the last non-empty line (never the whole reasoning)
    last = next((ln for ln in reversed(content.splitlines()) if ln.strip()), "")
    return [w.strip(' -*\t"') for w in re.split(r"[\n,]", last) if w.strip()]


async def decompose_recipe(
    text: str, on_think: Callable[[str], None] | None = None
) -> list[str]:
    """Turn a recipe or dish into German grocery search terms via the local LLM.

    When `on_think` is given, streams the reasoning trace to it live: Qwen3-Thinking-2507 emits
    its reasoning first and closes it with `</think>` (no opening tag), so everything before that
    marker is the model thinking out loud; the remainder is parsed into the item list as before.
    """
    settings = get_settings()
    client = get_client()
    try:
        stream = await client.chat.completions.create(
            model=settings.llm_model,
            messages=[
                {"role": "system", "content": _RECIPE_SYSTEM},
                {"role": "user", "content": text},
            ],
            temperature=0.2,
            stream=True,
        )
        full = ""
        thinking = True
        emitted = 0
        async for event in stream:
            delta = event.choices[0].delta.content or ""
            if not delta:
                continue
            full += delta
            if not (thinking and on_think):
                continue
            idx = full.find("</think>")
            if idx != -1:
                if idx > emitted:
                    on_think(full[emitted:idx])
                thinking = False
            else:
                # hold back the last 7 chars in case they are the start of a split "</think>" tag
                safe = len(full) - 7
                if safe > emitted:
                    on_think(full[emitted:safe])
                    emitted = safe
        return _parse_list(full or "[]")
    finally:
        await client.close()
