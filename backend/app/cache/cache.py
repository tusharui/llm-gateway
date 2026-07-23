import hashlib
import json
import time
from datetime import datetime, timezone
from sqlalchemy import select
from app.config import settings
from app.database import get_session
from app.models import CachedResponse


_memory: dict[str, dict] = {}


def cache_hash(input_str: str) -> str:
    return hashlib.md5(input_str.encode()).hexdigest()


def make_cache_key(model: str, messages: list) -> str:
    h = cache_hash(json.dumps({"model": model, "messages": messages}))
    return f"cache:{model}:{h}"


async def get_cached(key: str):
    entry = _memory.get(key)
    if entry and time.time() * 1000 < entry["expires_at"]:
        return entry["data"]

    try:
        session = await get_session()
        if not session:
            return None
        try:
            result = await session.execute(
                select(CachedResponse).where(CachedResponse.cache_key == key)
            )
            row = result.scalar_one_or_none()
            if row and row.expires_at > datetime.now(timezone.utc):
                _memory[key] = {
                    "data": json.loads(row.response),
                    "expires_at": row.expires_at.timestamp() * 1000,
                }
                return _memory[key]["data"]
        finally:
            await session.close()
    except Exception:
        pass
    return None


async def set_cache(key: str, data, ttl_ms: int | None = None):
    if ttl_ms is None:
        ttl_ms = settings.cache_ttl_ms
    expires_at_ms = time.time() * 1000 + ttl_ms
    _memory[key] = {"data": data, "expires_at": expires_at_ms}

    try:
        session = await get_session()
        if not session:
            return
        try:
            expires_dt = datetime.fromtimestamp(expires_at_ms / 1000, tz=timezone.utc)
            result = await session.execute(
                select(CachedResponse).where(CachedResponse.cache_key == key)
            )
            existing = result.scalar_one_or_none()

            if existing:
                existing.response = json.dumps(data)
                existing.ttl_ms = ttl_ms
                existing.expires_at = expires_dt
            else:
                session.add(CachedResponse(
                    cache_key=key,
                    response=json.dumps(data),
                    provider="cache",
                    model="unknown",
                    ttl_ms=ttl_ms,
                    expires_at=expires_dt,
                ))
            await session.commit()
        finally:
            await session.close()
    except Exception:
        pass
