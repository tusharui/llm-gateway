"""Password hashing and browser-session tokens for dashboard users.

Uses PBKDF2-HMAC-SHA256 from the standard library: bcrypt and argon2 are not
in requirements.txt, and the repo pins its dependency list deliberately.
600k iterations is the OWASP figure for this construction, and the cost is
stored inside the encoded hash so it can be raised without invalidating
existing passwords.

Session tokens are stored as SHA-256 digests, never in the clear, so a
read-only leak of auth_sessions yields no usable credential.
"""

import base64
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, Request
from sqlalchemy import delete, select

from app.config import settings
from app.database import get_session
from app.middleware.auth import consume_rate_limit
from app.models import AuthSession, User

SCHEME = "pbkdf2_sha256"
SALT_BYTES = 16
HASH_BYTES = 32
LAST_USED_REFRESH_SECONDS = 300


def _b64e(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _b64d(value: str) -> bytes:
    return base64.b64decode(value.encode("ascii"))


def _hash_password(password: str, salt: bytes, iterations: int) -> bytes:
    return hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, iterations, dklen=HASH_BYTES
    )


def hash_password(password: str) -> str:
    iterations = settings.password_hash_iterations
    salt = secrets.token_bytes(SALT_BYTES)
    digest = _hash_password(password, salt, iterations)
    return f"{SCHEME}${iterations}${_b64e(salt)}${_b64e(digest)}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        scheme, iterations_raw, salt_raw, digest_raw = encoded.split("$")
        if scheme != SCHEME:
            return False
        salt = _b64d(salt_raw)
        expected = _b64d(digest_raw)
        candidate = _hash_password(password, salt, int(iterations_raw))
    except Exception:
        return False

    return hmac.compare_digest(candidate, expected)


def needs_rehash(encoded: str) -> bool:
    try:
        scheme, iterations_raw, _, _ = encoded.split("$")
        return scheme == SCHEME and int(iterations_raw) < settings.password_hash_iterations
    except Exception:
        return False


DUMMY_PASSWORD_HASH = hash_password(secrets.token_urlsafe(32))


def normalize_email(email: str) -> str:
    return email.strip().lower()


def new_session_token() -> tuple[str, str]:
    raw = secrets.token_urlsafe(32)
    return raw, hash_token(raw)


def hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode()).hexdigest()


def client_host(request: Request) -> str:
    if settings.trust_forwarded_for:
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def enforce_auth_rate_limit(request: Request) -> None:
    verdict = consume_rate_limit(
        f"auth:{client_host(request)}",
        window_ms=settings.auth_rate_limit_window_ms,
        max_requests=settings.auth_rate_limit_max,
    )
    if not verdict.allowed:
        raise HTTPException(
            status_code=429,
            detail=(
                "Too many authentication attempts. "
                f"Retry after {verdict.retry_after_seconds}s"
            ),
            headers={"Retry-After": str(verdict.retry_after_seconds)},
        )


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


async def verify_login(session, email: str, password: str) -> User | None:
    normalized = normalize_email(email)
    result = await session.execute(select(User).where(User.email == normalized))
    user = result.scalar_one_or_none()

    encoded = user.password_hash if user else DUMMY_PASSWORD_HASH
    ok = verify_password(password, encoded)

    return user if (user is not None and ok) else None


async def load_session_user(session, raw_token: str | None) -> User | None:
    if not raw_token:
        return None

    result = await session.execute(
        select(User, AuthSession)
        .join(AuthSession, AuthSession.user_id == User.id)
        .where(AuthSession.token_hash == hash_token(raw_token))
    )
    row = result.first()
    if row is None:
        return None

    user, auth_session = row[0], row[1]

    expires_at = _as_utc(auth_session.expires_at)
    if expires_at is not None and expires_at <= datetime.now(timezone.utc):
        return None
    if not user.is_active:
        return None

    last_used = _as_utc(auth_session.last_used_at)
    now = datetime.now(timezone.utc)
    if last_used is None or (now - last_used).total_seconds() >= LAST_USED_REFRESH_SECONDS:
        auth_session.last_used_at = now
        await session.commit()

    return user


async def create_session(session, user_id: str, now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    raw_token, token_hash = new_session_token()
    auth_session = AuthSession(
        id=secrets.token_hex(16),
        user_id=user_id,
        token_hash=token_hash,
        expires_at=now + timedelta(seconds=settings.session_ttl_seconds),
        last_used_at=now,
    )
    session.add(auth_session)
    return raw_token


async def purge_expired_sessions(session, user_id: str) -> None:
    await session.execute(
        delete(AuthSession).where(
            AuthSession.user_id == user_id,
            AuthSession.expires_at <= datetime.now(timezone.utc),
        )
    )


async def current_user(request: Request):
    raw_token = request.cookies.get(settings.session_cookie_name)
    if not raw_token:
        raise HTTPException(status_code=401, detail="Not authenticated")

    session = await get_session()
    if not session:
        raise HTTPException(status_code=503, detail="Database unavailable")

    try:
        user = await load_session_user(session, raw_token)
    finally:
        await session.close()

    if user is None:
        raise HTTPException(status_code=401, detail="Session is invalid or has expired")
    return user


async def current_admin_user(user=Depends(current_user)):
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="This account is not an administrator")
    return user