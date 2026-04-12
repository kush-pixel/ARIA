"""SQLAlchemy async engine, session factory, and declarative base for ARIA.

All ORM models inherit from ``Base``.  The async engine is wired to the
DATABASE_URL from settings, which must use the asyncpg driver
(``postgresql+asyncpg://...``).
"""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import settings


def _async_url(url: str) -> str:
    """Ensure the DATABASE_URL uses the asyncpg driver.

    Supabase connection strings often ship as ``postgresql://`` or
    ``postgres://``.  SQLAlchemy async engine requires ``postgresql+asyncpg://``.
    """
    for prefix in ("postgresql://", "postgres://"):
        if url.startswith(prefix):
            return "postgresql+asyncpg://" + url[len(prefix):]
    return url


class Base(DeclarativeBase):
    """Declarative base shared by all ARIA ORM models."""


engine = create_async_engine(
    _async_url(settings.database_url),
    echo=settings.app_debug,
    pool_pre_ping=True,
)

AsyncSessionLocal: async_sessionmaker[AsyncSession] = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)
