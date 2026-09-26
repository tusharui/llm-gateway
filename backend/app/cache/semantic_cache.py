import json
import time
import math
from datetime import datetime, timezone
from typing import Optional
from sqlalchemy import select
from app.config import settings
from app.database import get_session
from app.models import SemanticCacheEntry


_memory_store: list[dict] = []
MAX_MEMORY_ENTRIES = 500


def cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def get_embedding_provider():
    from app.providers.registry import get_provider
    return get_provider("gemini")


async def embed_query(text: str) -> Optional[list[float]]:
    provider = get_embedding_provider()
    if not provider or not provider.embeddings:
        return None
    try:
        from app.schemas import EmbeddingRequest
        req = EmbeddingRequest(model="gemini-embedding-001", input=text)
        result = await provider.embeddings(req)
        if result.data and len(result.data) > 0:
            return result.data[0].embedding
    except Exception:
        pass
    return None


def _build_query_text(messages: list) -> str:
    parts = []
    for m in messages:
        if hasattr(m, "role") and hasattr(m, "content"):
            parts.append(f"{m.role}: {m.content}")
        elif isinstance(m, dict):
            parts.append(f"{m.get('role', 'user')}: {m.get('content', '')}")
    return "\n".join(parts)


async def semantic_get(model: str, messages: list, similarity_threshold: float = 0.92):
    now_ms = time.time() * 1000

    # Check memory store first
    for entry in _memory_store:
        if entry["expires_at"] < now_ms:
            continue
        if entry["model"] != model:
            continue
        # Already have embedding from DB — check against it
        pass

    # DB lookup
    try:
        session = await get_session()
        if not session:
            return None, 0.0
        try:
            result = await session.execute(
                select(SemanticCacheEntry).where(
                    SemanticCacheEntry.model == model,
                    SemanticCacheEntry.expires_at > datetime.now(timezone.utc),
                )
            )
            rows = result.scalars().all()
            if not rows:
                return None, 0.0

            query_text = _build_query_text(messages)
            query_embedding = await embed_query(query_text)
            if not query_embedding:
                return None, 0.0

            best_match = None
            best_sim = 0.0

            for row in rows:
                stored_embedding = json.loads(row.embedding_json)
                sim = cosine_similarity(query_embedding, stored_embedding)
                if sim > best_sim and sim >= similarity_threshold:
                    best_sim = sim
                    best_match = json.loads(row.response_json)

                    _memory_store.append({
                        "embedding": stored_embedding,
                        "response": best_match,
                        "model": model,
                        "expires_at": row.expires_at.timestamp() * 1000,
                    })
                    if len(_memory_store) > MAX_MEMORY_ENTRIES:
                        _memory_store.pop(0)

            return (best_match, best_sim) if best_match else (None, 0.0)
        finally:
            await session.close()
    except Exception:
        return None, 0.0


async def semantic_set(model: str, messages: list, response: dict, ttl_ms: int | None = None):
    if ttl_ms is None:
        ttl_ms = settings.cache_ttl_ms

    query_text = _build_query_text(messages)
    embedding = await embed_query(query_text)
    if not embedding:
        return

    now_ms = time.time() * 1000
    expires_at_ms = now_ms + ttl_ms
    expires_dt = datetime.fromtimestamp(expires_at_ms / 1000, tz=timezone.utc)

    _memory_store.append({
        "embedding": embedding,
        "response": response,
        "model": model,
        "expires_at": expires_at_ms,
    })
    if len(_memory_store) > MAX_MEMORY_ENTRIES:
        _memory_store.pop(0)

    try:
        session = await get_session()
        if not session:
            return
        try:
            import uuid
            entry_id = str(uuid.uuid4())
            session.add(SemanticCacheEntry(
                id=entry_id,
                query_text=query_text[:2000],
                embedding_json=json.dumps(embedding),
                response_json=json.dumps(response, default=str),
                provider="cache",
                model=model,
                expires_at=expires_dt,
            ))
            await session.commit()
        finally:
            await session.close()
    except Exception:
        pass
