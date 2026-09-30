"""Alembic environment for the AI Inference Gateway.

Migrations run against the same asyncpg URL the app uses, driven through
``connection.run_sync()`` so Alembic's sync-only API works over the async
engine without a second database URL to keep in sync.
"""

import asyncio
import sys
from logging.config import fileConfig
from pathlib import Path

from sqlalchemy import pool, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from alembic import context

# Importing the app package is what pulls every mapped class into
# ``Base.metadata``. Without it autogenerate compares against an empty schema
# and proposes dropping every table.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings  # noqa: E402
from app.database import build_db_url, build_engine_kwargs  # noqa: E402
from app.migration_safety import guard_destructive  # noqa: E402
from app.models import Base  # noqa: E402

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# Arbitrary but fixed. Every migration run in every environment contends for
# this one lock, which is the point: it serialises concurrent upgrades.
ADVISORY_LOCK_ID = 728_441_903_517


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
    guard_destructive(settings.database_url, getattr(config, "cmd_opts", None))

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
    guard_destructive(settings.database_url, getattr(config, "cmd_opts", None))

    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        # Serialise migrations across instances. Two replicas starting at the
        # same time both run `upgrade head` and race on DDL locks.
        #
        # The lock must be taken INSIDE Alembic's transaction. Executing any
        # statement on the connection first opens an implicit transaction of
        # its own; Alembic then sees a transaction already in progress and
        # never commits it. The result is the worst possible failure mode:
        # Alembic logs "Running upgrade" and the schema silently does not
        # change. A transaction-scoped lock also releases itself on commit or
        # rollback, so there is no unlock path to forget.
        connection.execute(
            text("SELECT pg_advisory_xact_lock(:k)"), {"k": ADVISORY_LOCK_ID}
        )
        context.run_migrations()


async def run_async_migrations() -> None:
    # Before anything touches the database. A destructive command should be
    # refused without opening a connection, so the refusal cannot be confused
    # with a connection failure.
    guard_destructive(settings.database_url, getattr(config, "cmd_opts", None))

    connectable = create_async_engine(
        _database_url(),
        poolclass=pool.NullPool,
        **build_engine_kwargs(),
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