"""Request schema for the search endpoint."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class SearchRequest(BaseModel):
    location: str = Field(..., description="German postcode or address")
    query: str = Field(..., description="Comma-separated items, or a recipe/dish")
    mode: Literal["list", "recipe"] = "list"
    user_id: str | None = Field(default=None, description="Client id for long-term memory")
