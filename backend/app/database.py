import ssl
from urllib.parse import quote_plus
from sqlalchemy.ext.asyncio import (
    create_async_engine, AsyncSession, async_sessionmaker,
)
from app.config import settings
from app.models import Base
from app.redact import redact


_engine = None
_session_factory = None
_db_ok = False


def build_db_url(raw_url: str) -> str:
    url = raw_url.replace("postgresql://", "postgresql+asyncpg://")
    if "://" in url:
        scheme_end = url.index("://") + 3
        at_pos = url.index("@", scheme_end)
        authority = url[scheme_end:at_pos]
        if ":" in authority:
            user, password = authority.split(":", 1)
            password = quote_plus(password)
            authority = f"{user}:{password}"
        url = url[:scheme_end] + authority + url[at_pos:]
    for param in ["sslmode=require", "sslmode=disable", "sslmode=allow", "channel_binding=require"]:
        url = url.replace(f"?{param}", "").replace(f"&{param}", "")
        url = url.replace(f"?{param}", "").replace(f"&{param}", "")
    return url


async def init_db():
    global _engine, _session_factory, _db_ok
    try:
        db_url = build_db_url(settings.database_url)
        print(f"[DB] Connecting to: {redact(db_url)}...")

        ssl_ctx = ssl.create_default_context()

        _engine = create_async_engine(
            db_url,
            echo=False,
            pool_size=2,
            max_overflow=5,
            pool_timeout=10,
            connect_args={"ssl": ssl_ctx},
        )
        _session_factory = async_sessionmaker(
            _engine, class_=AsyncSession, expire_on_commit=False
        )

        async with _engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        _db_ok = True
        print("[DB] Connected to PostgreSQL via SQLAlchemy ORM")
    except Exception as e:
        _db_ok = False
        _engine = None
        _session_factory = None
        print(f"[DB] WARNING: Database unavailable ({redact(str(e))}). Server running without DB.")


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
