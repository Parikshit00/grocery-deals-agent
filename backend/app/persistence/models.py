"""Database models. Changes go through an Alembic revision (app/persistence/migrations)."""
from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import (
    Date,
    DateTime,
    Index,
    Integer,
    LargeBinary,
    SmallInteger,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Leaflet(Base):
    """One leaflet (or offers feed window) of one chain edition, with its offers inline.

    An edition is whatever the chain varies its offers by: a store, a selling region or a
    single national edition.
    """

    __tablename__ = "leaflets"
    __table_args__ = (
        UniqueConstraint("chain", "edition", "leaflet_key", name="uq_leaflets_key"),
        Index("ix_leaflets_chain_edition_valid_to", "chain", "edition", "valid_to"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    chain: Mapped[str] = mapped_column(Text)
    edition: Mapped[str] = mapped_column(Text)
    leaflet_key: Mapped[str] = mapped_column(Text)
    title: Mapped[str | None] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(Text)  # main | extra | offers
    valid_from: Mapped[date] = mapped_column(Date)
    valid_to: Mapped[date] = mapped_column(Date)
    dates_source: Mapped[str] = mapped_column(Text)  # feed | text | derived
    source_url: Mapped[str | None] = mapped_column(Text)
    pdf_url: Mapped[str | None] = mapped_column(Text)
    page_urls: Mapped[list | None] = mapped_column(JSONB)
    tier: Mapped[int] = mapped_column(SmallInteger)
    status: Mapped[str] = mapped_column(Text)  # complete | partial
    page_count: Mapped[int | None] = mapped_column(Integer)
    pages_done: Mapped[list[int]] = mapped_column(ARRAY(Integer), server_default="{}")
    offers: Mapped[list] = mapped_column(JSONB, server_default="[]")
    embeddings: Mapped[bytes | None] = mapped_column(LargeBinary)  # float16 [n, dim], index-aligned
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    scan_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))


class ScanRun(Base):
    """One chain's part of one scan request: the run log and the location -> store cache."""

    __tablename__ = "scan_runs"
    __table_args__ = (
        Index("ix_scan_runs_chain_plz_finished", "chain", "plz", "finished_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    scan_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    chain: Mapped[str] = mapped_column(Text)
    plz: Mapped[str | None] = mapped_column(Text)
    trigger: Mapped[str] = mapped_column(Text)  # user | force | canary
    # ok | partial | failed | blocked | not_covered | no_store | cancelled
    status: Mapped[str] = mapped_column(Text)
    store: Mapped[dict | None] = mapped_column(JSONB)
    stores_nearby: Mapped[list | None] = mapped_column(JSONB)
    tier: Mapped[int | None] = mapped_column(SmallInteger)
    leaflet_ids: Mapped[list | None] = mapped_column(JSONB)
    next_week: Mapped[str | None] = mapped_column(Text)
    pages: Mapped[int] = mapped_column(Integer, server_default="0")
    offers: Mapped[int] = mapped_column(Integer, server_default="0")
    flagged: Mapped[int] = mapped_column(Integer, server_default="0")
    self_heal: Mapped[list | None] = mapped_column(JSONB)
    tokens_in: Mapped[int] = mapped_column(Integer, server_default="0")
    tokens_out: Mapped[int] = mapped_column(Integer, server_default="0")
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
