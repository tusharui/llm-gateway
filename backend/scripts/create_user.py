"""Create a dashboard user from the command line.

The counterpart to ALLOW_REGISTRATION=false: turn self-service signup off, then
mint accounts from the terminal:

    python -m scripts.create_user --email you@example.com --admin

The password is prompted for rather than passed as a flag, so it stays out of
shell history. Re-running with an existing address resets the password instead
of failing.
"""

import argparse
import asyncio
import getpass
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import select  # noqa: E402

from app.database import close_db, get_session, init_db  # noqa: E402
from app.models import User  # noqa: E402
from app.routes.auth import MAX_PASSWORD_LENGTH, MIN_PASSWORD_LENGTH  # noqa: E402
from app.security import hash_password, normalize_email  # noqa: E402


def read_password(flag: str | None) -> str:
    if flag is not None:
        return flag
    if not sys.stdin.isatty():
        print(
            "No TTY available. Pass --password or run from an interactive shell.",
            file=sys.stderr,
        )
        raise SystemExit(2)
    first = getpass.getpass("Password: ")
    if first != getpass.getpass("Confirm: "):
        print("Passwords did not match.", file=sys.stderr)
        raise SystemExit(1)
    return first


async def create(email: str, password: str, *, is_admin: bool) -> int:
    session = await get_session()
    if not session:
        print(
            "Database unavailable. Check DATABASE_URL and that "
            "`alembic upgrade head` has been run.",
            file=sys.stderr,
        )
        return 1

    normalized = normalize_email(email)
    try:
        result = await session.execute(select(User).where(User.email == normalized))
        user = result.scalar_one_or_none()

        if user is None:
            user = User(
                id=str(uuid.uuid4()),
                email=normalized,
                password_hash=hash_password(password),
                is_active=True,
                is_admin=is_admin,
            )
            session.add(user)
            action = "created"
        else:
            user.password_hash = hash_password(password)
            user.is_active = True
            if is_admin:
                user.is_admin = True
            action = "password reset for"

        await session.commit()
        suffix = " (admin)" if user.is_admin else ""
        print(f"User {normalized} {action}{suffix}")
        return 0
    finally:
        await session.close()


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email", required=True)
    parser.add_argument("--password", default=None)
    parser.add_argument("--admin", action="store_true")
    args = parser.parse_args()

    password = read_password(args.password)
    if not (MIN_PASSWORD_LENGTH <= len(password) <= MAX_PASSWORD_LENGTH):
        print(
            f"Password must be {MIN_PASSWORD_LENGTH}-{MAX_PASSWORD_LENGTH} "
            "characters.",
            file=sys.stderr,
        )
        return 1

    await init_db()
    try:
        return await create(args.email, password, is_admin=args.admin)
    finally:
        await close_db()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))