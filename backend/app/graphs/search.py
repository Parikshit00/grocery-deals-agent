"""LangGraph agent: resolve location -> plan items -> retrieve from the prospekt DB -> baskets.

Deterministic flow; the LLM is used only to turn a recipe into shopping items.
"""
from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any, TypedDict

from langgraph.graph import END, StateGraph

from app.clients.llm import decompose_recipe
from app.core.logging import get_logger
from app.services.geo import resolve_zip
from app.services.optimizer import build_baskets
from app.services.search import search_items

log = get_logger(__name__)


class SearchState(TypedDict, total=False):
    location: str
    query: str
    mode: str  # "list" | "recipe"
    on_think: Callable[[str], None] | None  # streams the recipe reasoning trace to the client
    zip_code: str | None
    items: list[str]
    results: list[dict[str, Any]]
    chains: int  # how many cached chains were searched (transparency)
    baskets: dict[str, Any]
    error: str | None


def build_graph():
    async def resolve(state: SearchState) -> SearchState:
        zip_code = await resolve_zip(state["location"])
        if not zip_code:
            return {"error": f"Could not resolve a postcode for '{state['location']}'."}
        return {"zip_code": zip_code}

    async def plan(state: SearchState) -> SearchState:
        if state.get("mode") == "recipe":
            items = await decompose_recipe(state["query"], on_think=state.get("on_think"))
        else:
            items = [s.strip() for s in re.split(r"[,\n]", state["query"]) if s.strip()]
        return {"items": items or [state["query"].strip()]}

    async def retrieve(state: SearchState) -> SearchState:
        results, chains = await search_items(state["zip_code"], state.get("items", []))
        return {"results": results, "chains": chains}

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
