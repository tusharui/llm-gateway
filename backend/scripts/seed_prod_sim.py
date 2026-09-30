"""Seed a database with production-shaped data, for testing migrations.

Row counts default to the live production volumes (17 usage records, 11 chat
sessions, 50 messages) so that ``alembic stamp head`` is proven against data
that actually has to survive.

Usage:
    python -m scripts.seed_prod_sim postgresql://...
"""

import asyncio
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402

from app.database import build_db_url  # noqa: E402

PRODUCERS = ["groq", "gemini", "openrouter"]
MODELS = [
    "openai/gpt-oss-120b",
    "openai/gpt-oss-20b",
    "gemini-2.5-flash",
    "openai/gpt-4o-mini",
    "qwen/qwen3.8-27b",
]


async def seed(url: str, sessions: int = 11, messages: int = 50, usage: int = 17) -> None:
    engine = create_async_engine(build_db_url(url), poolclass=None)
    now = datetime.now(timezone.utc)

    async with engine.begin() as conn:
        key_id = str(uuid.uuid4())
        await conn.execute(
            text(
                "INSERT INTO api_keys (id, key_prefix, key_hash, name) "
                "VALUES (:id, :p, :h, :n)"
            ),
            {
                "id": key_id,
                "p": "sk-gw-abcd",
                "h": "seed" + "0" * 60,
                "n": "prod-sim key",
            },
        )

        session_ids = []
        for i in range(sessions):
            sid = str(uuid.uuid4())
            session_ids.append(sid)
            await conn.execute(
                text(
                    "INSERT INTO chat_sessions (id, title, provider, model, created_at, updated_at) "
                    "VALUES (:id, :t, :pr, :m, :c, :u)"
                ),
                {
                    "id": sid,
                    "t": f"Session {i + 1}",
                    "pr": PRODUCERS[i % len(PRODUCERS)],
                    "m": MODELS[i % len(MODELS)],
                    "c": now - timedelta(days=i),
                    "u": now - timedelta(days=i),
                },
            )

        for i in range(messages):
            await conn.execute(
                text(
                    "INSERT INTO chat_messages (id, session_id, role, content, created_at) "
                    "VALUES (:id, :s, :r, :c, :t)"
                ),
                {
                    "id": str(uuid.uuid4()),
                    "s": session_ids[i % len(session_ids)],
                    "r": "user" if i % 2 == 0 else "assistant",
                    "c": f"Seed message {i + 1}",
                    "t": now - timedelta(hours=i),
                },
            )

        for i in range(usage):
            await conn.execute(
                text(
                    "INSERT INTO usage_records (id, api_key_id, provider, model, "
                    "prompt_tokens, completion_tokens, total_tokens, cost_usd, "
                    "latency_ms, success, cached, timestamp) "
                    "VALUES (:id, :k, :p, :m, :pt, :ct, :tt, :c, :l, :s, :ca, :t)"
                ),
                {
                    "id": str(uuid.uuid4()),
                    "k": key_id,
                    "p": PRODUCERS[i % len(PRODUCERS)],
                    "m": MODELS[i % len(MODELS)],
                    "pt": 100 + i,
                    "ct": 50 + i,
                    "tt": 150 + 2 * i,
                    "c": 0.0001 * (i + 1),
                    "l": 400 + i * 10,
                    "s": i % 5 != 0,
                    "ca": i % 3 == 0,
                    "t": now - timedelta(minutes=i),
                },
            )

    await engine.dispose()
    print(f"Seeded: {sessions} sessions, {messages} messages, {usage} usage records")


async def counts(url: str) -> dict[str, int]:
    engine = create_async_engine(build_db_url(url), poolclass=None)
    async with engine.connect() as conn:
        out = {}
        for table in (
            "api_keys",
            "chat_sessions",
            "chat_messages",
            "usage_records",
            "cached_responses",
            "semantic_cache",
        ):
            n = await conn.scalar(text(f"SELECT count(*) FROM {table}"))
            out[table] = n
    await engine.dispose()
    return out


if __name__ == "__main__":
    asyncio.run(seed(sys.argv[1]))
    print(asyncio.run(counts(sys.argv[1])))