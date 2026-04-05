"""Async PostgreSQL database session factory using SQLAlchemy."""
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.core.config import settings


def _pool_size() -> int:
    if settings.PG_POOL_SIZE is not None:
        return settings.PG_POOL_SIZE
    return {"development": 5, "staging": 15, "production": 20}.get(settings.APP_ENV, 5)


def _max_overflow() -> int:
    if settings.PG_MAX_OVERFLOW is not None:
        return settings.PG_MAX_OVERFLOW
    return {"development": 10, "staging": 30, "production": 50}.get(settings.APP_ENV, 10)


# Create async engine with environment-aware pool sizing
engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.is_development,
    pool_pre_ping=True,
    pool_size=_pool_size(),
    max_overflow=_max_overflow(),
    pool_recycle=settings.PG_POOL_RECYCLE,
    pool_timeout=settings.PG_POOL_TIMEOUT,
)

# Session factory
AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


class Base(DeclarativeBase):
    """Base class for all SQLAlchemy ORM models."""
    pass


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency that provides a database session."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def init_db() -> None:
    """Create all tables on startup (development only). Use migrations in production."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def close_db() -> None:
    """Dispose of the connection pool on shutdown."""
    await engine.dispose()
