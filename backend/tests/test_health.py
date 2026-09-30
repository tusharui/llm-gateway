"""Tests for the health/readiness split and the migration safety guards.

These exist because the failure they cover is silent: an instance that cannot
reach its database used to report 200 "healthy", so a broken deploy passed its
healthcheck and took production traffic.
"""

import asyncio
import os
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import settings
from app.routes.health import router as health_router


@pytest.fixture(autouse=True)
def no_db(monkeypatch):
    """Default every test to 'no database', then let each test opt in.

    Patches ``app.routes.health.db_status`` rather than
    ``app.database.db_status``: the route binds the name at import time, so
    replacing it on the source module would leave the route holding the
    original reference.
    """
    import app.database as db

    monkeypatch.setattr(db, "_db_connected", False)
    monkeypatch.setattr(db, "_engine", None)
    monkeypatch.setattr(db, "_schema_error", None)

    async def unavailable():
        return {
            "ok": False,
            "connected": False,
            "schema_ok": False,
            "detail": "connection refused",
        }

    monkeypatch.setattr("app.routes.health.db_status", unavailable)
    return db


def build_app() -> FastAPI:
    app = FastAPI()
    app.include_router(health_router)
    return app


def test_health_is_liveness_and_always_ok():
    """Liveness must not depend on anything external, including the database."""
    client = TestClient(build_app())
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "alive"


def test_health_does_not_query_the_database(no_db):
    """If liveness touched the database it would be slow and would flap."""
    called = False

    async def spy():
        nonlocal called
        called = True
        return {"ok": True, "connected": True, "schema_ok": True, "detail": None}

    no_db.db_status = spy
    TestClient(build_app()).get("/health")
    assert called is False


def test_ready_is_503_when_database_unreachable():
    client = TestClient(build_app())
    r = client.get("/ready")
    assert r.status_code == 503
    assert r.json()["status"] == "not_ready"


def test_ready_reports_the_actual_reason(monkeypatch, no_db):
    """An operator needs to know whether to fix the network or run migrations."""

    async def schema_missing():
        return {
            "ok": False,
            "connected": True,
            "schema_ok": False,
            "detail": "missing tables: ['api_keys'] - run `alembic upgrade head`",
        }

    monkeypatch.setattr("app.routes.health.db_status", schema_missing)
    r = TestClient(build_app()).get("/ready")
    assert r.status_code == 503
    assert "alembic upgrade head" in r.json()["database"]["detail"]


def test_ready_is_200_when_database_healthy(monkeypatch, no_db):
    async def healthy():
        return {"ok": True, "connected": True, "schema_ok": True, "detail": None}

    monkeypatch.setattr("app.routes.health.db_status", healthy)
    monkeypatch.setattr("app.routes.health.get_available_providers", lambda: [])
    r = TestClient(build_app()).get("/ready")
    assert r.status_code == 200
    assert r.json()["status"] == "ready"


def test_build_engine_kwargs_rejects_unknown_ssl_mode(monkeypatch):
    """A typo must fail loudly, not silently downgrade to cleartext."""
    from app.database import build_engine_kwargs

    monkeypatch.setattr(settings, "database_ssl", "auto")
    with pytest.raises(ValueError, match="no auto mode"):
        build_engine_kwargs()


def test_build_engine_kwargs_disable_has_no_ssl(monkeypatch):
    from app.database import build_engine_kwargs

    monkeypatch.setattr(settings, "database_ssl", "disable")
    assert "connect_args" not in build_engine_kwargs()


def test_build_engine_kwargs_require_verifies_certificate(monkeypatch):
    import ssl

    from app.database import build_engine_kwargs

    monkeypatch.setattr(settings, "database_ssl", "require")
    ctx = build_engine_kwargs()["connect_args"]["ssl"]
    assert isinstance(ctx, ssl.SSLContext)
    # A context that does not verify would be worse than no context at all:
    # it would accept a forged certificate.
    assert ctx.verify_mode == ssl.CERT_REQUIRED
    assert ctx.check_hostname is True


def test_schema_assertion_names_the_missing_tables(monkeypatch):
    """The unmigrated-database path must tell you what to do, not just fail."""
    import app.database as db

    class _Inspector:
        def get_table_names(self):
            return ["api_keys"]

    monkeypatch.setattr(db, "inspect", lambda conn: _Inspector())
    with pytest.raises(RuntimeError, match="alembic upgrade head"):
        db._assert_schema_present(object())


# --- migration guards -------------------------------------------------------


class _Opts:
    """Mirrors Alembic's real argparse namespace.

    Alembic sets ``Namespace.cmd`` to the subcommand ("upgrade", "downgrade").
    It does NOT put it in ``Namespace.args``. An earlier version of this test
    built a fake namespace with the command in ``args``, which matched a wrong
    implementation and passed while the real guard never fired.
    """

    def __init__(self, cmd, *args):
        self.cmd = cmd
        self.args = list(args)


PROD_URL = "postgresql://u:p@ep-abc.us-east-1.aws.neon.tech/db"
LOCAL_URL = "postgresql://u:p@localhost:5432/gateway"


def test_invoked_command_reads_alembic_namespace():
    """Guards against reintroducing the cmd/args mix-up."""
    from app.migration_safety import invoked_command

    assert invoked_command(_Opts("downgrade", "base")) == "downgrade"
    assert invoked_command(_Opts("upgrade", "head")) == "upgrade"
    assert invoked_command(None) == ""


def test_downgrade_guard_refuses_shared_database():
    """The single most dangerous command in the repo must refuse by default.

    Verified by calling the guard directly. Deliberately NOT verified by
    running a real downgrade against production: if the guard were broken, the
    test would destroy production to find out.
    """
    from app.migration_safety import guard_destructive

    with pytest.raises(RuntimeError, match="Refusing to run"):
        guard_destructive(PROD_URL, _Opts("downgrade", "base"))


def test_downgrade_guard_refuses_single_step_too():
    from app.migration_safety import guard_destructive

    with pytest.raises(RuntimeError, match="Refusing to run"):
        guard_destructive(PROD_URL, _Opts("downgrade", "-1"))


def test_downgrade_guard_allows_local_database():
    from app.migration_safety import guard_destructive

    guard_destructive(LOCAL_URL, _Opts("downgrade", "base"))


def test_downgrade_guard_ignores_upgrade():
    """Upgrading production is normal and must never be blocked."""
    from app.migration_safety import guard_destructive

    guard_destructive(PROD_URL, _Opts("upgrade", "head"))


def test_downgrade_guard_honours_explicit_opt_in(monkeypatch):
    from app.migration_safety import ALLOW_DESTRUCTIVE_ENV, guard_destructive

    monkeypatch.setenv(ALLOW_DESTRUCTIVE_ENV, "1")
    guard_destructive(PROD_URL, _Opts("downgrade", "base"))


def test_downgrade_guard_without_cli_namespace_is_permissive():
    """Programmatic Alembic has no cmd_opts to inspect.

    Documenting the limitation rather than pretending it is airtight: a driver
    that calls downgrade() through the API must set the opt-in itself.
    """
    from app.migration_safety import guard_destructive

    guard_destructive(PROD_URL, None)


def test_is_local_target_classifies_hosts():
    from app.migration_safety import is_local_target

    assert is_local_target("postgresql://u:p@localhost:5432/db")
    assert is_local_target("postgresql://u:p@127.0.0.1:5432/db")
    assert is_local_target("postgresql://u:p@gw-pg:5432/db")
    assert not is_local_target(PROD_URL)
    # A hostname that merely contains "postgres" is not local.
    assert not is_local_target("postgresql://u:p@postgres.internal.example.com/db")