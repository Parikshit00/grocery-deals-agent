"""LangGraph agent: resolve location -> plan items -> retrieve & rank offers.

The flow is deterministic; the LLM is used only to turn a recipe into shopping items.
Tools (resolve_zip, search_offers) are provided by the MCP servers and injected at build time.
"""
from __future__ import annotations

import json
import re
from typing import Any, TypedDict

from langgraph.graph import END, StateGraph

from app.core.llm import decompose_recipe
from app.core.logging import get_logger
from app.services.optimizer import build_baskets

log = get_logger(__name__)


class SearchState(TypedDict, total=False):
    location: str
    query: str
    mode: str  # "list" | "recipe"
    zip_code: str | None
    items: list[str]
    results: list[dict[str, Any]]
    baskets: dict[str, Any]
    error: str | None


def _content_texts(result: Any) -> list[str]:
    if isinstance(result, str):
        return [result]
    texts: list[str] = []
    if isinstance(result, list):
        for item in result:
            if isinstance(item, dict) and item.get("type") == "text":
                texts.append(item.get("text", ""))
            elif isinstance(item, str):
                texts.append(item)
    return texts


def _first_text(result: Any) -> str | None:
    texts = _content_texts(result)
    return texts[0] if texts else None


def _parse_offers(result: Any) -> list[dict]:
    offers: list[dict] = []
    for text in _content_texts(result):
        try:
            offers.append(json.loads(text))
        except json.JSONDecodeError:
            continue
    return offers


def build_graph(tools: dict[str, Any]):
    geo = tools["resolve_zip"]
    browse = tools["search_offers"]

    async def resolve(state: SearchState) -> SearchState:
        zip_code = _first_text(await geo.ainvoke({"location": state["location"]}))
        if not zip_code:
            return {"error": f"Could not resolve a postcode for '{state['location']}'."}
        return {"zip_code": zip_code}

    async def plan(state: SearchState) -> SearchState:
        if state.get("mode") == "recipe":
            items = await decompose_recipe(state["query"])
        else:
            items = [s.strip() for s in re.split(r"[,\n]", state["query"]) if s.strip()]
        return {"items": items or [state["query"].strip()]}

    async def retrieve(state: SearchState) -> SearchState:
        results: list[dict[str, Any]] = []
        for item in state.get("items", []):
            raw = await browse.ainvoke(
                {"zip_code": state["zip_code"], "query": item, "top": 20}
            )
            results.append({"item": item, "offers": _parse_offers(raw)})
        return {"results": results}

    async def optimize(state: SearchState) -> SearchState:
        results = state.get("results", [])
        baskets = build_baskets(results)
        trimmed = [{"item": r["item"], "offers": r["offers"][:6]} for r in results]
        return {"baskets": baskets.model_dump(mode="json"), "results": trimmed}

    graph = StateGraph(SearchState)
    graph.add_node("resolve", resolve)
    graph.add_node("plan", plan)
    graph.add_node("retrieve", retrieve)
    graph.add_node("optimize", optimize)
    graph.set_entry_point("resolve")
    graph.add_conditional_edges(
        "resolve", lambda s: "end" if s.get("error") else "plan", {"end": END, "plan": "plan"}
    )
    graph.add_edge("plan", "retrieve")
    graph.add_edge("retrieve", "optimize")
    graph.add_edge("optimize", END)
    return graph.compile()
