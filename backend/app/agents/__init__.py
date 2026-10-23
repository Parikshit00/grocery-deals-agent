"""Chain module registry.

Each chain is a plain module (`agents/<chain>.py`) that implements the contract in
`agents/base.py`, registered here by its NAME. CHAINS maps that name to the module.
"""
from __future__ import annotations

from types import ModuleType

from app.agents import aldi, kaufland, lidl, netto, penny, rewe

CHAINS: dict[str, ModuleType] = {m.NAME: m for m in (penny, kaufland, aldi, netto, lidl, rewe)}
