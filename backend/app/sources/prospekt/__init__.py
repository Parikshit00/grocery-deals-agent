"""Per-retailer prospekt recipe registry.

Add a retailer by dropping in `<retailer>.py` (a Recipe) + `prompts/<retailer>_extract.txt`, then
registering it here. See docs/adding-a-retailer.md.
"""
from __future__ import annotations

from app.sources.prospekt.base import ProspektPages, Recipe
from app.sources.prospekt.kaufland import KauflandRecipe
from app.sources.prospekt.lidl import LidlRecipe

_RECIPES: dict[str, type] = {"lidl": LidlRecipe, "kaufland": KauflandRecipe}


def get_recipe(name: str) -> Recipe:
    if name not in _RECIPES:
        raise KeyError(f"no prospekt recipe for '{name}' (have: {', '.join(_RECIPES)})")
    return _RECIPES[name]()


def available_retailers() -> list[str]:
    return list(_RECIPES)


__all__ = ["ProspektPages", "Recipe", "LidlRecipe", "get_recipe", "available_retailers"]
