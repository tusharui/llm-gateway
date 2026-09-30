"""Safety guards for destructive Alembic operations.

Kept out of ``migrations/env.py`` on purpose. That module executes top-level
code against Alembic's context object on import, so anything in it can only be
tested by stubbing a fake Alembic around it -- which is exactly the kind of
test that passes while asserting nothing. This is importable, plain, and
directly testable.

The problem being guarded: ``alembic downgrade`` issues DROP TABLE with no
confirmation and no undo. A mistyped target, a stale DATABASE_URL, or one extra
keystroke destroys every row in the database. It is the highest-consequence
command in this repository.
"""

import os
from urllib.parse import urlparse

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

ALLOW_DESTRUCTIVE_ENV = "ALLOW_DESTRUCTIVE_MIGRATIONS"


def target_host(database_url: str) -> str:
    return (urlparse(database_url).hostname or "").lower()


def is_local_target(database_url: str) -> bool:
    host = target_host(database_url)
    return (
        host in LOCAL_HOSTS
        or host.endswith(".local")
        or host.endswith(".internal")
        or host.startswith("localhost:")
    )


def invoked_command(cmd_opts) -> str:
    """Read the Alembic subcommand from its argparse namespace.

    Alembic puts the subcommand in ``Namespace.cmd`` ("upgrade", "downgrade"),
    not in ``Namespace.args``. Returns "" when driven programmatically rather
    than from the CLI, because ``cmd_opts`` is only populated by the command
    line entry point.
    """
    if cmd_opts is None:
        return ""
    cmd = getattr(cmd_opts, "cmd", None)
    if cmd:
        return str(cmd)
    return " ".join(getattr(cmd_opts, "args", []) or [])


def guard_destructive(database_url: str, cmd_opts=None) -> None:
    """Refuse a downgrade against a shared database.

    Local development is unrestricted. Anything else requires an explicit
    opt-in, so the default answer to "drop production?" is no.
    """
    command = invoked_command(cmd_opts)
    if "downgrade" not in command:
        return
    if os.environ.get(ALLOW_DESTRUCTIVE_ENV) == "1":
        return
    if is_local_target(database_url):
        return

    raise RuntimeError(
        f"Refusing to run `alembic downgrade` against "
        f"{target_host(database_url)!r}, which is not a local database.\n"
        "Downgrade drops tables and cannot be undone. If you are certain, set "
        f"{ALLOW_DESTRUCTIVE_ENV}=1 and re-run."
    )