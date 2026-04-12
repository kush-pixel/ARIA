"""FastAPI dependency for injecting an async database session.

Usage::

    from app.db.session import get_session
    from sqlalchemy.ext.asyncio import AsyncSession

    @router.get("/example")
    async def handler(session: AsyncSession = Depends(get_session)):
        ...
"""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import AsyncSessionLocal


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Yield a database session; the context manager handles cleanup on exit."""
    async with AsyncSessionLocal() as session:
        yield session
