"""Global test configuration.

The single most important thing in this file is the fail-closed guard below.

An earlier version of the migration tests set DATABASE_URL with
``monkeypatch.setenv`` and expected Alembic to follow it. It did not:
``app.config.settings`` is a module-level singleton built at import time, so
changing the environment afterwards has no effect on it. Every migration test
therefore ran against the production Neon database, and
``test_downgrade_removes_all_tables`` executed ``alembic downgrade base``
there. That dropped all six production tables and the following
``upgrade head`` recreated them empty.

These tests now fail closed. If any test would connect to a database that is
not obviously local, the run aborts before a connection is opened rather than
after.
"""

import os
import sys
from pathlib import Path
from urllib.parse import urlparse

import pytest

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

LOCAL_HOSTS = {
    "localhost",
    "127.0.0.1",
    "::1",
    "0.0.0.0",
    "gw-pg",
    "gw-pg18",
    "postgres",
    "db",
}


def is_local(url: str) -> bool:
    host = (urlparse(url or "").hostname or "").lower()
    return host in LOCAL_HOSTS or host.endswith(".local") or host.endswith(".test")


def _redact(url: str) -> str:
    """Mask the password before a URL reaches a log or a traceback.

    Postgres URLs carry the credential in the authority section, so printing
    one verbatim leaks the password into CI output and terminal scrollback.
    """
    if not url:
        return url
    try:
        parsed = urlparse(url)
        if not parsed.password:
            return url
        netloc = parsed.netloc.replace(parsed.password, "***")
        return url.replace(parsed.netloc, netloc, 1)
    except Exception:
        return "<unparseable database url>"


def assert_local_or_empty(url: str, label: str) -> None:
    """Refuse to proceed if a test is pointed at anything but localhost."""
    if not url:
        return
    if not is_local(url):
        raise RuntimeError(
            f"BLOCKED: {label} points at {_redact(url)}, which is not a local "
            "database. Tests must never run DDL against a shared environment. "
            "If you really mean to, export ALLOW_REMOTE_TEST_DB=1 to bypass "
            "this check deliberately."
        )


@pytest.fixture(autouse=True)
def isolate_database_settings():
    """Keep every test away from whatever DATABASE_URL happens to be set.

    Resets ``settings.database_url`` for the duration of each test so a
    developer's real .env can never leak into a test, and restores it
    afterwards.
    """
    from app.config import settings
    import app.database as database

    original_url = settings.database_url

    settings.database_url = ""
    database._db_connected = False
    database._engine = None
    database._schema_error = None

    yield

    settings.database_url = original_url
    database._db_connected = False
    database._engine = None


@pytest.fixture(autouse=True, scope="session")
def guard_against_remote_databases(request):
    """Session-level check that the configured test database is local."""
    url = os.environ.get("TEST_DATABASE_URL", "")
    if url and os.environ.get("ALLOW_REMOTE_TEST_DB") != "1":
        assert_local_or_empty(url, "TEST_DATABASE_URL")
    yield