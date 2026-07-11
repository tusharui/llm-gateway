import hashlib
import json
import time
from datetime import datetime, timezone
from app.config import settings
from app.database import db_fetchone, db_execute


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
        row = await db_fetchone(
            "SELECT response, expires_at FROM cached_responses WHERE cache_key = $1",
            (key,),
        )
        if row and row["expires_at"] > datetime.now(timezone.utc):
            _memory[key] = {
                "data": json.loads(row["response"]),
                "expires_at": row["expires_at"].timestamp() * 1000,
            }
            return _memory[key]["data"]
    except Exception:
        pass
    return None


async def set_cache(key: str, data, ttl_ms: int | None = None):
    if ttl_ms is None:
        ttl_ms = settings.cache_ttl_ms
    expires_at = time.time() * 1000 + ttl_ms
    _memory[key] = {"data": data, "expires_at": expires_at}

    try:
        await db_execute(
            """INSERT INTO cached_responses (cache_key, response, provider, model, ttl_ms, expires_at)
               VALUES ($1, $2, 'cache', 'unknown', $3, to_timestamp($4 / 1000.0))
               ON CONFLICT (cache_key) DO UPDATE SET
                 response = EXCLUDED.response,
                 expires_at = EXCLUDED.expires_at,
                 ttl_ms = EXCLUDED.ttl_ms""",
            (key, json.dumps(data), ttl_ms, expires_at),
        )
    except Exception:
        pass
