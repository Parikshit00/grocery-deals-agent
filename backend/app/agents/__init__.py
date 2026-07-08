"""Per-retailer prospekt recipe registry.

Add a retailer by dropping in `<retailer>.py` (a Recipe) + `prompts/<retailer>_extract.txt`, then
registering it here. See docs/adding-a-retailer.md.
"""
from __future__ import annotations

from app.agents.aldi import AldiRecipe
from app.agents.base import ProspektPages, Recipe
from app.agents.kaufland import KauflandRecipe
from app.agents.lidl import LidlRecipe
from app.agents.netto import NettoRecipe
from app.agents.penny import PennyRecipe
from app.agents.rewe import ReweRecipe

_RECIPES: dict[str, type] = {
    "lidl": LidlRecipe,
    "kaufland": KauflandRecipe,
    "aldi": AldiRecipe,
    "penny": PennyRecipe,
    "rewe": ReweRecipe,
    "netto": NettoRecipe,
}


def get_recipe(name: str) -> Recipe:
    if name not in _RECIPES:
        raise KeyError(f"no prospekt recipe for '{name}' (have: {', '.join(_RECIPES)})")
    return _RECIPES[name]()


def available_retailers() -> list[str]:
    return list(_RECIPES)


__all__ = ["ProspektPages", "Recipe", "LidlRecipe", "get_recipe", "available_retailers"]
