"""Database models."""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Date, DateTime, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


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


class ProspektOffers(Base):
    """VLM-extracted offers from a retailer's prospekt, cached per region until valid_to."""

    __tablename__ = "prospekt_offers"
    __table_args__ = (
        UniqueConstraint("retailer", "region_key", name="uq_prospekt_retailer_region"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    retailer: Mapped[str] = mapped_column(String(32), index=True)
    region_key: Mapped[str] = mapped_column(String(64), index=True)
    valid_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    valid_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    payload: Mapped[dict] = mapped_column(JSONB)
