"""Chain module registry.

Each chain is a plain module (`agents/<chain>.py`) registered here by name. Chains are
added back one by one as their acquisition tier is rebuilt.
"""
from __future__ import annotations

from types import ModuleType

CHAINS: dict[str, ModuleType] = {}


def available_chains() -> list[str]:
    return list(CHAINS)
