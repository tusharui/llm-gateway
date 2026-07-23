import random
from typing import AsyncGenerator
from app.providers.interface import AIProvider
from app.providers.registry import get_available_providers
from app.engine.circuit_breaker import is_allowed, record_success, record_failure
from app.engine.retry import with_retry
from app.schemas import ChatRequest, ChatResponse, StreamChunk
from app.config import settings


def pick_provider(model: str) -> AIProvider:
    available = get_available_providers()
    if not available:
        raise Exception("No providers available")

    cfg = settings.get_providers()
    matching = [
        p for p in available
        if is_allowed(p.name) and model in cfg[p.name].models
    ]

    candidates = matching or [p for p in available if is_allowed(p.name)]
    if not candidates:
        raise Exception("All providers are in cooldown")

    weighted = []
    for p in candidates:
        weighted.extend([p] * cfg[p.name].weight)

    return random.choice(weighted)


async def route_chat(req: ChatRequest) -> tuple[ChatResponse, str]:
    provider = pick_provider(req.model)
    cfg = settings.get_providers()[provider.name]

    async def _call():
        try:
            result = await provider.chat(req)
            record_success(provider.name)
            return result
        except Exception:
            record_failure(provider.name, cfg.failure_threshold, cfg.cooldown_ms)
            raise

    return await with_retry(
        _call, retries=cfg.retry_count, base_delay_ms=cfg.retry_delay_ms
    ), provider.name


async def route_chat_stream(req: ChatRequest) -> AsyncGenerator[StreamChunk, None]:
    provider = pick_provider(req.model)
    cfg = settings.get_providers()[provider.name]
    try:
        async for chunk in provider.chat_stream(req):
            yield chunk
        record_success(provider.name)
    except Exception:
        record_failure(provider.name, cfg.failure_threshold, cfg.cooldown_ms)
        raise
