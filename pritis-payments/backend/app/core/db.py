from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings

_settings = get_settings()

# Tests run each case on its own event loop, so pooled connections can't be reused there.
engine = create_async_engine(
    _settings.sqlalchemy_url,
    pool_pre_ping=True,
    **({"poolclass": NullPool} if _settings.environment == "test" else {}),
)

SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    """One async session per request."""
    async with SessionLocal() as session:
        yield session
