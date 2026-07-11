from fastapi import Request, HTTPException
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response
import hashlib
import time
from app.config import settings
from app.database import db_fetchone, db_fetchall, db_execute


rate_limit_store: dict[str, dict] = {}


def hash_string(input_str: str) -> str:
    return hashlib.sha256(input_str.encode()).hexdigest()


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.method == "OPTIONS":
            return await call_next(request)

        path = request.url.path
        needs_auth = any(path.startswith(p) for p in ["/chat", "/embeddings", "/batch"])
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
        row = await db_fetchone(
            "SELECT id, is_active FROM api_keys WHERE key_hash = $1", (key_hash,)
        )
        if row is None:
            from app.database import _db_ok
            if not _db_ok:
                request.state.api_key_id = "db_unavailable"
                return await call_next(request)
            raise HTTPException(status_code=401, detail="Invalid API key")
        if not row["is_active"]:
            raise HTTPException(status_code=401, detail="API key is disabled")

        await db_execute(
            "UPDATE api_keys SET last_used_at = NOW() WHERE id = $1", (row["id"],)
        )
        request.state.api_key_id = row["id"]
        return await call_next(request)


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        needs_limit = any(path.startswith(p) for p in ["/chat", "/embeddings", "/batch"])
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
