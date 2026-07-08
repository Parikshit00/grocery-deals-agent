"""Async SQLAlchemy engine + session factory. Shared across the app."""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import get_settings

_settings = get_settings()
engine = create_async_engine(_settings.database_url, pool_pre_ping=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def ping() -> bool:
    """True if a trivial query succeeds."""
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
    return True


async def init_models() -> None:
    """Create tables that do not yet exist."""
    from app.persistence.models import Base

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
