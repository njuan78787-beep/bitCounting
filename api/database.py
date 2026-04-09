# =============================================================================
# api/database.py
# Async SQLAlchemy engine + Redis client for Bit-Counting.
#
# USAGE:
#   from .database import get_db, get_redis, Base
#
#   # In an endpoint:
#   async def my_endpoint(db: AsyncSession = Depends(get_db)):
#       ...
#
#   # In lifespan:
#   await create_tables()
#   await init_redis()
#
# GRACEFUL DEGRADATION:
#   - If DATABASE_URL is not set, DB operations raise RuntimeError.
#   - If REDIS_URL is unreachable, token blacklist + rate limiting fall back
#     to in-memory equivalents (suitable for single-process dev use only).
# =============================================================================

from __future__ import annotations

import logging
import os
from typing import AsyncGenerator, Optional

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------

def _make_async_url(url: str) -> str:
    """Convert a standard postgres:// URL to the asyncpg driver scheme."""
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+asyncpg://", 1)
    if url.startswith("postgresql://") and "+asyncpg" not in url:
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return url


# ---------------------------------------------------------------------------
# SQLAlchemy engine + session factory
# ---------------------------------------------------------------------------

DATABASE_URL: str = os.environ.get("DATABASE_URL", "")

_engine: Optional[AsyncEngine] = None
_session_factory: Optional[async_sessionmaker[AsyncSession]] = None

if DATABASE_URL:
    _engine = create_async_engine(
        _make_async_url(DATABASE_URL),
        echo=False,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=10,
        connect_args={"server_settings": {"application_name": "bit-counting-api"}},
    )
    _session_factory = async_sessionmaker(_engine, expire_on_commit=False)
    logger.info("DB engine created for: %s", DATABASE_URL.split("@")[-1])  # hide creds
else:
    logger.warning("DATABASE_URL not set — DB features unavailable")


class Base(DeclarativeBase):
    """SQLAlchemy declarative base for all ORM models."""
    pass


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    FastAPI dependency that yields an AsyncSession.
    Raises RuntimeError if DATABASE_URL is not configured.
    """
    if _session_factory is None:
        raise RuntimeError(
            "DATABASE_URL is not set. Configure it in your environment or .env file."
        )
    async with _session_factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


async def create_tables() -> None:
    """
    Create all tables defined in Base.metadata (idempotent).
    Called during application startup.
    """
    if _engine is None:
        logger.warning("No DB engine — skipping table creation")
        return

    # Ensure all model modules are imported before create_all
    from .db import models as _  # noqa: F401

    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    logger.info("Database tables created / verified OK")


# ---------------------------------------------------------------------------
# Redis client
# ---------------------------------------------------------------------------

REDIS_URL: str = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

_redis_client = None   # redis.asyncio.Redis or None if unavailable


async def init_redis() -> None:
    """
    Initialize the global async Redis client.
    Falls back to None (in-memory) if Redis is unreachable.
    """
    global _redis_client
    try:
        import redis.asyncio as aioredis  # type: ignore[import]
        client = aioredis.from_url(REDIS_URL, decode_responses=True, socket_connect_timeout=2)
        await client.ping()
        _redis_client = client
        logger.info("Redis connected: %s", REDIS_URL.split("@")[-1])
    except Exception as exc:
        logger.warning(
            "Redis unavailable (%s) — falling back to in-memory blacklist/rate-limiter. "
            "Not suitable for multi-process production deployments.",
            exc,
        )
        _redis_client = None


async def get_redis():
    """
    Return the shared Redis client (may be None if Redis is not available).
    Routes that use Redis must handle the None case gracefully.
    """
    return _redis_client


async def close_connections() -> None:
    """Gracefully close all database and cache connections on shutdown."""
    global _redis_client

    if _engine is not None:
        await _engine.dispose()
        logger.info("DB engine disposed")

    if _redis_client is not None:
        await _redis_client.aclose()
        _redis_client = None
        logger.info("Redis connection closed")
