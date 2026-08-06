import hashlib
import time
from datetime import datetime, timezone
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


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.method == "OPTIONS":
            return await call_next(request)

        path = request.url.path
        needs_auth = requires_auth(path)
        if not needs_auth:
            return await call_next(request)

        auth_header = request.headers.get("authorization")
        if not auth_header:
            raise HTTPException(status_code=401, detail="Missing Authorization header")

        parts = auth_header.split(" ", 1)
        if len(parts) != 2 or parts[0] != "Bearer":
            raise HTTPException(status_code=401, detail="Invalid Authorization header")

        token = parts[1]

        if token.startswith("sk-gateway-"):
            if token != settings.gateway_api_key:
                raise HTTPException(status_code=401, detail="Invalid API key")
            request.state.api_key_id = "gateway_admin"
            return await call_next(request)

        key_hash = hash_string(token)

        session = await get_session()
        if session is None:
            request.state.api_key_id = "db_unavailable"
            return await call_next(request)

        try:
            result = await session.execute(
                select(ApiKey).where(ApiKey.key_hash == key_hash)
            )
            api_key = result.scalar_one_or_none()

            if not api_key:
                raise HTTPException(status_code=401, detail="Invalid API key")
            if not api_key.is_active:
                raise HTTPException(status_code=401, detail="API key is disabled")

            api_key.last_used_at = datetime.now(timezone.utc)
            await session.commit()
            request.state.api_key_id = api_key.id
        finally:
            await session.close()

        return await call_next(request)


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        needs_limit = requires_auth(path)
        if not needs_limit:
            return await call_next(request)

        api_key_id = getattr(request.state, "api_key_id", "anonymous")
        now = time.time() * 1000
        window_ms = settings.rate_limit_window_ms
        max_reqs = settings.rate_limit_max_requests

        entry = rate_limit_store.get(api_key_id)
        if not entry or now > entry["reset_at"]:
            rate_limit_store[api_key_id] = {"count": 1, "reset_at": now + window_ms}
            return await call_next(request)

        if entry["count"] >= max_reqs:
            retry_after = int((entry["reset_at"] - now) / 1000)
            raise HTTPException(
                status_code=429,
                detail=f"Rate limit exceeded. Retry after {retry_after}s",
                headers={
                    "Retry-After": str(retry_after),
                    "X-RateLimit-Limit": str(max_reqs),
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Reset": str(int(entry["reset_at"])),
                },
            )

        entry["count"] += 1
        remaining = max_reqs - entry["count"]
        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = str(max_reqs)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        response.headers["X-RateLimit-Reset"] = str(int(entry["reset_at"]))
        return response
