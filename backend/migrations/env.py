"""Alembic environment for the AI Inference Gateway.

Migrations run against the same asyncpg URL the app uses, driven through
``connection.run_sync()`` so Alembic's sync-only API works over the async
engine without a second database URL to keep in sync.
"""

import asyncio
import ssl
from logging.config import fileConfig
from pathlib import Path

from sqlalchemy import pool, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from alembic import context

# Importing the app package is what pulls every mapped class into
# ``Base.metadata``. Without it autogenerate compares against an empty schema
# and proposes dropping every table.
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings  # noqa: E402
from app.database import build_db_url  # noqa: E402
from app.models import Base  # noqa: E402

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _database_url() -> str:
    """Resolve the async URL from app settings, not alembic.ini.

    alembic.ini ships a placeholder URL. Reading it there means two places to
    update per environment and a real risk of pointing a migration at the wrong
    database, so the app's own settings win.
    """
    if not settings.database_url:
        raise RuntimeError(
            "DATABASE_URL is not set. Migrations need an explicit target; "
            "copy backend/.env.example to backend/.env first."
        )
    return build_db_url(settings.database_url)


def run_migrations_offline() -> None:
    """Emit SQL to stdout without connecting to a database (``alembic upgrade --sql``)."""
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


async def _server_requires_tls() -> bool:
    """Probe whether the target accepts a plaintext connection.

    ``app.database.build_db_url`` strips ``sslmode`` from the URL and the app
    passes TLS through ``connect_args`` instead, so a migration cannot rely on
    the URL alone. Managed Postgres (Neon, Supabase, RDS) refuses plaintext; a
    local dev server usually has no certificate at all. Probing beats making
    every operator remember a per-environment flag.
    """
    probe = create_async_engine(_database_url(), poolclass=pool.NullPool)
    try:
        async with probe.connect() as connection:
            await connection.execute(text("SELECT 1"))
        return False
    except Exception:
        return True
    finally:
        await probe.dispose()


async def run_async_migrations() -> None:
    connect_args = {}
    if await _server_requires_tls():
        connect_args["connect_args"] = {"ssl": ssl.create_default_context()}

    connectable = create_async_engine(
        _database_url(),
        poolclass=pool.NullPool,
        **connect_args,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()