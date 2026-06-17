"""Common interface for offer sources (tiered acquisition adapters)."""
from __future__ import annotations

from typing import Protocol

from app.schemas.offer import OfferSearchResult


class OfferSource(Protocol):
    name: str

    async def search(
        self, zip_code: str, query: str, limit: int = 20, offset: int = 0
    ) -> OfferSearchResult: ...

    async def aclose(self) -> None: ...
