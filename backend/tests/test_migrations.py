"""Migration tests.

These exercise the real Alembic chain against a real PostgreSQL instance --
a migration bug that only appears against a live server (wrong nullability, a
DDL statement Postgres rejects, a downgrade that leaves debris) is exactly the
kind that is invisible in a mocked test and expensive in production.

Set ``TEST_DATABASE_URL`` to run them; they skip otherwise::

    docker run -d --name gw-pg -e POSTGRES_PASSWORD=gwpass -e POSTGRES_USER=gwuser \\
        -e POSTGRES_DB=gateway_test -p 55432:5432 pgvector/pgvector:pg16
    $env:TEST_DATABASE_URL="postgresql://gwuser:gwpass@localhost:55432/gateway_test"
    python -m pytest tests/test_migrations.py -v
"""

import os
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

BACKEND = Path(__file__).resolve().parents[1]
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "")

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL not set; skipping migration tests",
)


def _sync_url() -> str:
    return TEST_DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
def clean_db():
    """Truncate every app table and reset the Alembic version."""
    engine = create_engine(_sync_url())
    with engine.begin() as conn:
        for table in (
            "usage_records",
            "semantic_cache",
            "chat_messages",
            "chat_sessions",
            "cached_responses",
            "api_keys",
        ):
            conn.execute(text(f"DROP TABLE IF EXISTS {table} CASCADE"))
        conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
    engine.dispose()
    yield
    engine.dispose()


@pytest.fixture
def alembic_config(monkeypatch, clean_db):
    from alembic.config import Config

    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "migrations"))
    return cfg


def _table_names(url: str) -> set[str]:
    engine = create_engine(url)
    with engine.connect() as conn:
        names = {
            r[0]
            for r in conn.execute(
                text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema='public' AND table_type='BASE TABLE'"
                )
            ).all()
        }
    engine.dispose()
    return names


def test_upgrade_creates_all_tables(alembic_config):
    from alembic import command

    command.upgrade(alembic_config, "head")

    tables = _table_names(_sync_url())
    assert {
        "api_keys",
        "usage_records",
        "cached_responses",
        "chat_sessions",
        "chat_messages",
        "semantic_cache",
    } <= tables


def test_downgrade_removes_all_tables(alembic_config):
    from alembic import command

    command.upgrade(alembic_config, "head")
    command.downgrade(alembic_config, "base")

    tables = _table_names(_sync_url())
    assert not (tables - {"alembic_version"})


def test_upgrade_downgrade_is_repeatable(alembic_config):
    """A revision that only works once is not a revision."""
    from alembic import command

    for _ in range(2):
        command.upgrade(alembic_config, "head")
        command.downgrade(alembic_config, "base")


def test_stamp_head_preserves_existing_data(alembic_config):
    """The production adoption path.

    Production already has the schema and real rows, but no Alembic version.
    ``stamp head`` must record the revision without running DDL and without
    touching a single row.
    """
    from alembic import command

    command.upgrade(alembic_config, "head")

    engine = create_engine(_sync_url())
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM alembic_version"))
        conn.execute(
            text(
                "INSERT INTO chat_sessions (id, title, provider, model, created_at, updated_at) "
                "VALUES ('s1','kept','groq','openai/gpt-oss-120b', now(), now())"
            )
        )
        for i in range(17):
            conn.execute(
                text(
                    "INSERT INTO usage_records (id, api_key_id, provider, model, timestamp) "
                    "VALUES (:i, 'k1', 'groq', 'openai/gpt-oss-120b', now())"
                ),
                {"i": f"u{i}"},
            )
    engine.dispose()

    command.stamp(alembic_config, "head")

    engine = create_engine(_sync_url())
    with engine.connect() as conn:
        assert conn.scalar(text("SELECT count(*) FROM usage_records")) == 17
        assert conn.scalar(text("SELECT count(*) FROM chat_sessions")) == 1
        assert conn.scalar(text("SELECT version_num FROM alembic_version")) == "0001_baseline"
    engine.dispose()


def test_models_match_migrations(alembic_config):
    """``alembic check`` equivalent.

    Guards against the exact drift found during the baseline audit: ORM types
    and nullability silently diverging from the live schema.
    """
    from alembic import command
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext
    from sqlalchemy import create_engine as ce

    command.upgrade(alembic_config, "head")

    sys_path_insert = str(BACKEND)
    import sys

    if sys_path_insert not in sys.path:
        sys.path.insert(0, sys_path_insert)
    from app.models import Base

    engine = ce(_sync_url())
    with engine.connect() as conn:
        context = MigrationContext.configure(
            conn, opts={"compare_type": True, "compare_server_default": True}
        )
        diff = compare_metadata(context, Base.metadata)
    engine.dispose()

    assert diff == [], f"models.py has drifted from migrations: {diff}"