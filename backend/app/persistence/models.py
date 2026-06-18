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


class UserProfile(Base):
    """Long-term per-user memory (keyed by a client-generated id)."""

    __tablename__ = "user_profile"

    user_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    last_location: Mapped[str | None] = mapped_column(String(256), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class SearchHistory(Base):
    __tablename__ = "search_history"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    location: Mapped[str] = mapped_column(String(256))
    query: Mapped[str] = mapped_column(String(512))
    mode: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
