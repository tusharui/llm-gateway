import ssl
from urllib.parse import quote_plus
from sqlalchemy import inspect, text as sa_text
from sqlalchemy.ext.asyncio import (
    create_async_engine, AsyncSession, async_sessionmaker,
)
from app.config import settings
from app.models import Base
from app.redact import redact


_engine = None
_session_factory = None
_db_connected = False
_schema_ok = False
_schema_error: str | None = None


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
    return url


def build_engine_kwargs() -> dict:
    """Connection arguments for the database engine.

    Lives here so the app and Alembic cannot disagree about TLS. Previously
    the app hardcoded a verified SSL context and migrations had to guess for
    themselves, which is how the plaintext-probe ended up existing at all.
    """
    mode = settings.database_ssl.lower()
    if mode == "require":
        return {"connect_args": {"ssl": ssl.create_default_context()}}
    if mode == "disable":
        return {}
    raise ValueError(
        f"DATABASE_SSL must be 'require' or 'disable', got {mode!r}. "
        "There is no auto mode: probing would send the database password "
        "unencrypted."
    )


def _assert_schema_present(connection) -> None:
    """Fail loudly if Alembic has not been run.

    This used to call ``Base.metadata.create_all``, which silently invented
    whatever tables the ORM happened to describe at boot. That made schema
    drift invisible: the app would happily create a half-correct schema on a
    fresh database and nobody would notice production had drifted. Alembic now
    owns the schema (``alembic upgrade head``).
    """
    expected = set(Base.metadata.tables)
    present = set(inspect(connection).get_table_names())
    missing = expected - present
    if missing:
        raise RuntimeError(
            f"missing tables: {sorted(missing)} - run `alembic upgrade head`"
        )


async def init_db():
    global _engine, _session_factory, _db_connected, _schema_ok, _schema_error
    _schema_error = None
    try:
        db_url = build_db_url(settings.database_url)
        print(f"[DB] Connecting to: {redact(db_url)}...")

        _engine = create_async_engine(
            db_url,
            echo=False,
            pool_size=2,
            max_overflow=5,
            pool_timeout=10,
            **build_engine_kwargs(),
        )
        _session_factory = async_sessionmaker(
            _engine, class_=AsyncSession, expire_on_commit=False
        )

        async with _engine.begin() as conn:
            await conn.run_sync(_assert_schema_present)
            await conn.execute(sa_text("SELECT 1"))

        _db_connected = True
        _schema_ok = True
        print("[DB] Connected to PostgreSQL via SQLAlchemy ORM")
    except Exception as e:
        message = redact(str(e))
        # A missing schema is a deployment error, not a degraded dependency.
        # It gets its own flag so /ready can report the actual reason instead
        # of a generic "unavailable".
        if isinstance(e, RuntimeError):
            _schema_error = message
        else:
            _schema_error = None
        _db_connected = False
        _schema_ok = False
        _engine = None
        _session_factory = None
        print(f"[DB] ERROR: {message}. Server running without DB.")


async def db_status() -> dict:
    """Readiness detail: can we actually serve traffic?

    Distinguishes "cannot reach the database" from "reached it, but the schema
    is wrong", because those need different responses from an operator.
    """
    if not _db_connected or _engine is None:
        return {
            "ok": False,
            "connected": False,
            "schema_ok": False,
            "detail": _schema_error or "database not initialised",
        }

    try:
        async with _engine.connect() as conn:
            await conn.execute(sa_text("SELECT 1"))
    except Exception as e:
        return {
            "ok": False,
            "connected": False,
            "schema_ok": _schema_ok,
            "detail": redact(str(e)),
        }

    return {"ok": True, "connected": True, "schema_ok": _schema_ok, "detail": None}


async def get_session() -> AsyncSession | None:
    if _session_factory is None or not _db_connected:
        return None
    return _session_factory()


async def close_db():
    global _engine, _session_factory, _db_connected, _schema_ok
    if _engine:
        await _engine.dispose()
        _engine = None
        _session_factory = None
        _db_connected = False
        _schema_ok = False