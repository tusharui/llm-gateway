"""Migration tests.

These exercise the real Alembic chain against a real PostgreSQL instance --
a migration bug that only appears against a live server (wrong nullability, a
DDL statement Postgres rejects) is exactly the kind that is invisible in a
mocked test.

Everything here connects through asyncpg, the driver the application uses.
An earlier version used SQLAlchemy's sync ``create_engine``, which resolves a
bare ``postgresql://`` URL to psycopg2. That driver is not in
requirements-dev.txt, so the suite errored in CI while passing locally. Adding
psycopg2 would have been the wrong fix: it would test a dialect production
never runs, and asyncpg and psycopg2 differ in type handling and parameter
style, so a test could pass while the real stack failed.

Alembic's command functions are synchronous and call asyncio.run()
internally, so the tests that invoke them stay synchronous and use
``asyncio.run`` for their own database access.

Set ``TEST_DATABASE_URL`` to run them; they skip otherwise::

    docker run -d --name gw-pg18 -e POSTGRES_PASSWORD=gwpass -e POSTGRES_USER=gwuser \\
        -e POSTGRES_DB=gateway_test -p 55433:5432 pgvector/pgvector:pg18
    $env:TEST_DATABASE_URL="postgresql://gwuser:gwpass@localhost:55433/gateway_test"
    $env:DATABASE_SSL="disable"
    python -m pytest tests/test_migrations.py -v

Two rules this module follows without exception:

1. The target is set on ``settings.database_url`` directly. Setting the
   DATABASE_URL environment variable does nothing, because ``settings`` was
   already built at import time.

2. Nothing here executes a downgrade. Downgrade SQL is generated in Alembic's
   offline mode instead, which exercises the same code without issuing a
   single DDL statement.
"""

import asyncio
import os
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from conftest import assert_local_or_empty

BACKEND = Path(__file__).resolve().parents[1]
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "")

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL not set; skipping migration tests",
)

TABLES = (
    "usage_records",
    "semantic_cache",
    "chat_messages",
    "chat_sessions",
    "cached_responses",
    "api_keys",
)


def _engine():
    from app.database import build_db_url, build_engine_kwargs

    return create_async_engine(
        build_db_url(TEST_DATABASE_URL), poolclass=None, **build_engine_kwargs()
    )


async def _drop_everything():
    engine = _engine()
    async with engine.begin() as conn:
        for table in TABLES:
            await conn.execute(text(f"DROP TABLE IF EXISTS {table} CASCADE"))
        await conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
    await engine.dispose()


async def _table_names() -> set[str]:
    engine = _engine()
    async with engine.connect() as conn:
        result = await conn.execute(
            text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema='public' AND table_type='BASE TABLE'"
            )
        )
        names = {r[0] for r in result.all()}
    await engine.dispose()
    return names


async def _seed_stamp_fixture():
    engine = _engine()
    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM alembic_version"))
        await conn.execute(
            text(
                "INSERT INTO chat_sessions (id, title, provider, model, created_at, updated_at) "
                "VALUES ('s1','kept','groq','openai/gpt-oss-120b', now(), now())"
            )
        )
        for i in range(17):
            await conn.execute(
                text(
                    "INSERT INTO usage_records (id, api_key_id, provider, model, timestamp) "
                    "VALUES (:i, 'k1', 'groq', 'openai/gpt-oss-120b', now())"
                ),
                {"i": f"u{i}"},
            )
    await engine.dispose()


async def _counts() -> dict:
    engine = _engine()
    async with engine.connect() as conn:
        usage = await conn.scalar(text("SELECT count(*) FROM usage_records"))
        sessions = await conn.scalar(text("SELECT count(*) FROM chat_sessions"))
        version = await conn.scalar(text("SELECT version_num FROM alembic_version"))
    await engine.dispose()
    return {"usage": usage, "sessions": sessions, "version": version}


async def _model_drift():
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    import sys

    if str(BACKEND) not in sys.path:
        sys.path.insert(0, str(BACKEND))
    from app.models import Base

    engine = _engine()
    async with engine.connect() as conn:

        def _compare(sync_conn):
            context = MigrationContext.configure(
                sync_conn, opts={"compare_type": True, "compare_server_default": True}
            )
            return compare_metadata(context, Base.metadata)

        diff = await conn.run_sync(_compare)
    await engine.dispose()
    return diff


@pytest.fixture
def clean_db():
    """Drop every table and reset the Alembic version.

    Refuses to run against anything but a local database.
    """
    assert_local_or_empty(TEST_DATABASE_URL, "TEST_DATABASE_URL")
    asyncio.run(_drop_everything())
    yield


@pytest.fixture
def alembic_config(monkeypatch, clean_db):
    """A config aimed at the test database, and nowhere else.

    Sets the singleton *and* the environment variable. Only the singleton
    matters to Alembic; the env var is set for consistency with subprocesses.
    """
    from alembic.config import Config

    from app.config import settings

    assert_local_or_empty(TEST_DATABASE_URL, "TEST_DATABASE_URL")

    settings.database_url = TEST_DATABASE_URL
    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)

    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "migrations"))
    return cfg


def test_upgrade_creates_all_tables(alembic_config):
    from alembic import command

    command.upgrade(alembic_config, "head")

    tables = asyncio.run(_table_names())
    assert set(TABLES) <= tables


def test_upgrade_is_idempotent(alembic_config):
    """Running upgrade twice must be a no-op, not an error."""
    from alembic import command

    command.upgrade(alembic_config, "head")
    command.upgrade(alembic_config, "head")


def test_stamp_head_preserves_existing_data(alembic_config):
    """The production adoption path.

    Production already has the schema and real rows, but no Alembic version.
    ``stamp head`` must record the revision without running DDL and without
    touching a single row.
    """
    from alembic import command
    from alembic.script import ScriptDirectory

    command.upgrade(alembic_config, "head")
    # Read the real head rather than hardcoding a revision id: that assertion
    # silently rots every time a revision is added.
    head = ScriptDirectory.from_config(alembic_config).get_current_head()

    asyncio.run(_seed_stamp_fixture())
    command.stamp(alembic_config, "head")

    result = asyncio.run(_counts())
    assert result["usage"] == 17
    assert result["sessions"] == 1
    assert result["version"] == head


def test_models_match_migrations(alembic_config):
    """``alembic check`` equivalent.

    Guards against the exact drift found during the baseline audit: ORM types
    and nullability silently diverging from the live schema.
    """
    from alembic import command

    command.upgrade(alembic_config, "head")

    diff = asyncio.run(_model_drift())
    assert diff == [], f"models.py has drifted from migrations: {diff}"


def test_downgrade_sql_generates_without_executing(alembic_config, capsys):
    """Exercise the downgrade path without dropping anything.

    Offline mode runs the real migration functions and renders SQL, so a
    broken downgrade still fails this test -- but no DDL is ever issued. This
    replaces a previous version of this suite that executed
    ``downgrade base``.
    """
    from alembic import command
    from alembic.script import ScriptDirectory

    command.upgrade(alembic_config, "head")
    head = ScriptDirectory.from_config(alembic_config).get_current_head()
    before = asyncio.run(_table_names())

    command.downgrade(alembic_config, f"{head}:base", sql=True)
    out = capsys.readouterr().out

    assert "DROP TABLE" in out
    assert "usage_records" in out
    # The tables must still be there: offline mode renders, it does not run.
    assert asyncio.run(_table_names()) == before


def test_downgrade_guard_blocks_remote_database():
    """A downgrade aimed at a shared database must refuse.

    This is the guard that only covers CLI invocations, so programmatic
    callers bypass it entirely. That is why no test here executes a downgrade.
    """
    from app.migration_safety import guard_destructive

    class Opts:
        cmd = "downgrade"

    with pytest.raises(RuntimeError, match="Refusing to run"):
        guard_destructive(
            "postgresql://u:p@ep-abc.us-east-1.aws.neon.tech/db", Opts()
        )