"""LLM access via a local OpenAI-compatible endpoint (Ollama by default, vLLM optional)."""
from __future__ import annotations

import json
import re

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


def _parse_list(content: str) -> list[str]:
    start, end = content.find("["), content.rfind("]")
    if start != -1 and end > start:
        try:
            return [str(x).strip() for x in json.loads(content[start : end + 1]) if str(x).strip()]
        except json.JSONDecodeError:
            pass
    return [w.strip(" -*\t") for w in re.split(r"[\n,]", content) if w.strip()]


async def decompose_recipe(text: str) -> list[str]:
    """Turn a recipe or dish into German grocery search terms via the local LLM."""
    settings = get_settings()
    client = get_client()
    try:
        resp = await client.chat.completions.create(
            model=settings.llm_model,
            messages=[
                {"role": "system", "content": _RECIPE_SYSTEM},
                {"role": "user", "content": text},
            ],
            temperature=0.2,
        )
        return _parse_list(resp.choices[0].message.content or "[]")
    finally:
        await client.close()
