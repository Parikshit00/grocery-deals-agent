"""Chain module registry.

Each chain is a plain module (`agents/<chain>.py`) registered here by name. Chains are
added back one by one as their acquisition tier is rebuilt.
"""
from __future__ import annotations

from types import ModuleType

from app.agents import aldi, kaufland, lidl, netto, penny, rewe

CHAINS: dict[str, ModuleType] = {m.NAME: m for m in (penny, kaufland, aldi, netto, lidl, rewe)}


def available_chains() -> list[str]:
    return list(CHAINS)
