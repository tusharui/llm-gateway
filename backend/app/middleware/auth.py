import hashlib
import hmac
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from fastapi import Request, HTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from sqlalchemy import select, update
from app.config import settings
from app.database import get_session
from app.models import ApiKey


rate_limit_store: dict[str, dict] = {}


def hash_string(input_str: str) -> str:
    return hashlib.sha256(input_str.encode()).hexdigest()


def requires_auth(path: str) -> bool:
    return (
        any(path.startswith(p) for p in ["/chat", "/embeddings", "/batch"])
        and not path.startswith("/chat-history")
    )


class TokenStatus(str, Enum):
    VALID = "valid"
    INVALID = "invalid"
    STORE_UNAVAILABLE = "store_unavailable"


@dataclass(frozen=True)
class TokenCheck:
    """Outcome of validating one bearer token.

    Returned rather than raised so the same validation can serve HTTP, where a
    401 is the right response, and the websocket handshake, where a close frame
    is. A single definition of "is this token valid" is the point: two copies
    drift, and the one that drifts is always the one without tests.
    """

    status: TokenStatus
    api_key_id: str | None = None
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.status is TokenStatus.VALID


async def check_api_key(token: str) -> TokenCheck:
    """Validate a bearer token.

    Three outcomes, and the third is the one that needs a decision at each call
    site. An admin key prefixed ``sk-gateway-`` is compared in constant time and
    never touches the database, so it keeps working when the database is down.
    Everything else is a database lookup, and a lookup that cannot run is not a
    pass or a fail -- it is a third thing, and callers choose.

    Never raises: a transport failure becomes STORE_UNAVAILABLE.
    """
    if not token:
        return TokenCheck(TokenStatus.INVALID, detail="Missing bearer token")

    if token.startswith("sk-gateway-"):
        # compare_digest rather than !=. Both are constant-time for equal-length
        # strings in CPython, but only one is documented to be, and this is a
        # secret comparison against attacker-supplied input.
        if not hmac.compare_digest(token, settings.gateway_api_key):
            return TokenCheck(TokenStatus.INVALID, detail="Invalid API key")
        return TokenCheck(TokenStatus.VALID, api_key_id="gateway_admin")

    key_hash = hash_string(token)

    try:
        session = await get_session()
    except Exception as exc:  # noqa: BLE001 - any transport failure is "unavailable"
        return TokenCheck(
            TokenStatus.STORE_UNAVAILABLE,
            detail=f"key store unreachable: {type(exc).__name__}",
        )

    if session is None:
        return TokenCheck(TokenStatus.STORE_UNAVAILABLE, detail="key store not configured")

    try:
        result = await session.execute(
            select(ApiKey).where(ApiKey.key_hash == key_hash)
        )
        api_key = result.scalar_one_or_none()

        if not api_key:
            return TokenCheck(TokenStatus.INVALID, detail="Invalid API key")
        if not api_key.is_active:
            return TokenCheck(TokenStatus.INVALID, detail="API key is disabled")

        api_key.last_used_at = datetime.now(timezone.utc)
        await session.commit()
        return TokenCheck(TokenStatus.VALID, api_key_id=api_key.id)
    except Exception as exc:  # noqa: BLE001 - do not 500 on a key lookup
        return TokenCheck(
            TokenStatus.STORE_UNAVAILABLE,
            detail=f"key store error: {type(exc).__name__}",
        )
    finally:
        await session.close()


def parse_bearer(header_value: str | None) -> str | None:
    """Extract the token from an ``Authorization: Bearer <token>`` value."""
    if not header_value:
        return None
    parts = header_value.split(" ", 1)
    if len(parts) != 2 or parts[0] != "Bearer":
        return None
    return parts[1]


@dataclass(frozen=True)
class RateLimitVerdict:
    allowed: bool
    remaining: int
    retry_after_seconds: int
    reset_at: float


def consume_rate_limit(
    api_key_id: str,
    *,
    window_ms: int | None = None,
    max_requests: int | None = None,
) -> RateLimitVerdict:
    """Charge one request against the shared rate-limit budget.

    Extracted from RateLimitMiddleware so the websocket can spend the same
    budget. Two stores would mean an API key that is exhausted over /chat gets
    a fresh allowance by opening a socket instead, which makes the HTTP limit
    decorative.

    The sliding-window logic is unchanged from the middleware: a new window
    starts at count 1, a key is refused once count reaches the maximum, and the
    counter is only incremented for requests that are allowed through.
    """
    now = time.time() * 1000
    effective_window = settings.rate_limit_window_ms if window_ms is None else window_ms
    effective_max = settings.rate_limit_max_requests if max_requests is None else max_requests

    entry = rate_limit_store.get(api_key_id)
    if not entry or now > entry["reset_at"]:
        rate_limit_store[api_key_id] = {"count": 1, "reset_at": now + effective_window}
        return RateLimitVerdict(
            allowed=True,
            remaining=effective_max - 1,
            retry_after_seconds=0,
            reset_at=now + effective_window,
        )

    if entry["count"] >= effective_max:
        return RateLimitVerdict(
            allowed=False,
            remaining=0,
            retry_after_seconds=int((entry["reset_at"] - now) / 1000) + 1,
            reset_at=entry["reset_at"],
        )

    entry["count"] += 1
    return RateLimitVerdict(
        allowed=True,
        remaining=effective_max - entry["count"],
        retry_after_seconds=0,
        reset_at=entry["reset_at"],
    )


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.method == "OPTIONS":
            return await call_next(request)

        path = request.url.path
        needs_auth = requires_auth(path)
        if not needs_auth:
            return await call_next(request)

        token = parse_bearer(request.headers.get("authorization"))
        if token is None:
            raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")

        check = await check_api_key(token)
        if check.status is TokenStatus.VALID:
            request.state.api_key_id = check.api_key_id
            return await call_next(request)

        if check.status is TokenStatus.STORE_UNAVAILABLE:
            # Fail open, deliberately and unchanged: this gateway is designed
            # to keep serving LLM traffic when Postgres is unreachable, and a
            # 401 storm during an incident would be worse than the risk. The
            # admin key still has to be correct, because it is checked without
            # the database at all.
            #
            # The websocket handshake does NOT inherit this: see
            # app/routes/websocket.py for why a long-lived streaming socket
            # fails closed on an unavailable key store.
            request.state.api_key_id = "db_unavailable"
            return await call_next(request)

        raise HTTPException(status_code=401, detail=check.detail)


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        needs_limit = requires_auth(path)
        if not needs_limit:
            return await call_next(request)

        api_key_id = getattr(request.state, "api_key_id", "anonymous")
        verdict = consume_rate_limit(api_key_id)
        if not verdict.allowed:
            raise HTTPException(
                status_code=429,
                detail=f"Rate limit exceeded. Retry after {verdict.retry_after_seconds}s",
                headers={
                    "Retry-After": str(verdict.retry_after_seconds),
                    "X-RateLimit-Limit": str(settings.rate_limit_max_requests),
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Reset": str(int(verdict.reset_at)),
                },
            )

        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = str(settings.rate_limit_max_requests)
        response.headers["X-RateLimit-Remaining"] = str(verdict.remaining)
        response.headers["X-RateLimit-Reset"] = str(int(verdict.reset_at))
        return response
