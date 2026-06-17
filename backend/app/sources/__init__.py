"""Offer source registry.

Sources are instantiated once and reused so per-source state (e.g. the marktguru API
key cache and the HTTP client) survives across requests.
"""
from __future__ import annotations

from app.sources.base import OfferSource
from app.sources.marktguru import MarktguruSource

_FACTORIES = {"marktguru": MarktguruSource}
_instances: list[OfferSource] | None = None


def build_sources(names: list[str] | None = None) -> list[OfferSource]:
    names = names or ["marktguru"]
    return [_FACTORIES[name]() for name in names if name in _FACTORIES]


def get_sources() -> list[OfferSource]:
    global _instances
    if _instances is None:
        _instances = build_sources()
    return _instances


async def close_sources() -> None:
    global _instances
    if _instances:
        for source in _instances:
            await source.aclose()
        _instances = None


__all__ = ["OfferSource", "MarktguruSource", "build_sources", "get_sources", "close_sources"]
