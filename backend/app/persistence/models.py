"""Database models."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class OfferCache(Base):
    """Cached offer-search result for a (postcode, query) pair (hybrid freshness)."""

    __tablename__ = "offer_cache"
    __table_args__ = (UniqueConstraint("zip_code", "query", name="uq_offer_cache_zip_query"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    zip_code: Mapped[str] = mapped_column(String(16), index=True)
    query: Mapped[str] = mapped_column(String(256), index=True)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    payload: Mapped[dict] = mapped_column(JSONB)
