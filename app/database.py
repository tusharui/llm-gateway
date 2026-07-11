from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import (
    create_async_engine, AsyncSession, async_sessionmaker,
)
from app.config import settings
from app.models import Base


_engine = None
_session_factory = None
_db_ok = False


async def init_db():
    global _engine, _session_factory, _db_ok
    try:
        _engine = create_async_engine(
            settings.database_url.replace("postgresql://", "postgresql+asyncpg://"),
            echo=False,
            pool_size=2,
            max_overflow=5,
            pool_timeout=5,
        )
        _session_factory = async_sessionmaker(
            _engine, class_=AsyncSession, expire_on_commit=False
        )

        async with _engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        _db_ok = True
        print("[DB] Connected to Neon PostgreSQL via SQLAlchemy ORM")
    except Exception as e:
        _db_ok = False
        _engine = None
        _session_factory = None
        print(f"[DB] WARNING: Database unavailable ({e}). Server running without DB.")


async def get_session() -> AsyncSession | None:
    if _session_factory is None or not _db_ok:
        return None
    return _session_factory()


async def close_db():
    global _engine, _session_factory, _db_ok
    if _engine:
        await _engine.dispose()
        _engine = None
        _session_factory = None
        _db_ok = False
