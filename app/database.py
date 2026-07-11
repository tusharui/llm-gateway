import asyncio
import psycopg
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import uuid
from app.config import settings


_pool: AsyncConnectionPool | None = None
_db_ok = False


async def init_db():
    global _pool, _db_ok
    try:
        _pool = AsyncConnectionPool(
            conninfo=settings.database_url + ("&" if "?" in settings.database_url else "?") + "connect_timeout=5",
            min_size=1,
            max_size=5,
            kwargs={"row_factory": dict_row, "connect_timeout": 5},
            open=False,
        )
        await asyncio.wait_for(_pool.open(), timeout=10)
        async with _pool.connection() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS api_keys (
                    id TEXT PRIMARY KEY,
                    key_prefix TEXT NOT NULL,
                    key_hash TEXT UNIQUE NOT NULL,
                    name TEXT NOT NULL,
                    is_active BOOLEAN DEFAULT TRUE,
                    rate_limit_max INTEGER DEFAULT 60,
                    rate_limit_window_ms INTEGER DEFAULT 60000,
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    last_used_at TIMESTAMPTZ
                )
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS usage_records (
                    id TEXT PRIMARY KEY,
                    api_key_id TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    model TEXT NOT NULL,
                    prompt_tokens INTEGER DEFAULT 0,
                    completion_tokens INTEGER DEFAULT 0,
                    total_tokens INTEGER DEFAULT 0,
                    cost_usd DOUBLE PRECISION DEFAULT 0,
                    latency_ms INTEGER DEFAULT 0,
                    success BOOLEAN DEFAULT TRUE,
                    cached BOOLEAN DEFAULT FALSE,
                    timestamp TIMESTAMPTZ DEFAULT NOW()
                )
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS cached_responses (
                    cache_key TEXT PRIMARY KEY,
                    response TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    model TEXT NOT NULL,
                    cached_at TIMESTAMPTZ DEFAULT NOW(),
                    ttl_ms INTEGER NOT NULL,
                    expires_at TIMESTAMPTZ NOT NULL
                )
            """)
            await conn.commit()
        _db_ok = True
        print("[DB] Connected to Neon PostgreSQL")
    except Exception as e:
        _db_ok = False
        _pool = None
        print(f"[DB] WARNING: Database unavailable ({e}). Server running without DB.")


async def get_pool() -> AsyncConnectionPool | None:
    global _pool
    if _pool is None or not _db_ok:
        return None
    return _pool


async def close_db():
    global _pool, _db_ok
    if _pool:
        await _pool.close()
        _pool = None
        _db_ok = False


async def db_fetchone(query: str, params: tuple = ()):
    pool = await get_pool()
    if not pool:
        return None
    async with pool.connection() as conn:
        row = await conn.execute(query, params)
        return await row.fetchone()


async def db_fetchall(query: str, params: tuple = ()):
    pool = await get_pool()
    if not pool:
        return []
    async with pool.connection() as conn:
        row = await conn.execute(query, params)
        return await row.fetchall()


async def db_execute(query: str, params: tuple = ()):
    pool = await get_pool()
    if not pool:
        return
    async with pool.connection() as conn:
        await conn.execute(query, params)
        await conn.commit()
