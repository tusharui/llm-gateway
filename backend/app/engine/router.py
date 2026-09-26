import random
from typing import AsyncGenerator
from app.providers.interface import AIProvider
from app.providers.registry import get_available_providers
from app.engine.circuit_breaker import is_allowed, record_success, record_failure
from app.engine.auto_router import alternate_model
from app.engine.retry import with_retry
from app.schemas import ChatRequest, ChatResponse, StreamChunk
from app.config import settings
from app.redact import redact_error


def pick_provider(model: str, preferred_provider: str | None = None) -> AIProvider:
    available = get_available_providers()
    if not available:
        raise Exception("No providers available")

    cfg = settings.get_providers()

    # The auto-router already decided which provider owns this model; trust it
    # instead of re-rolling a weighted random pick.
    if preferred_provider:
        for p in available:
            if p.name == preferred_provider and is_allowed(p.name):
                return p

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


def failover_chain(
    model: str, preferred_provider: str | None = None
) -> list[tuple[AIProvider, str]]:
    """Ordered (provider, model) attempts: routed provider first, then substitutes."""
    available = get_available_providers()
    if not available:
        raise Exception("No providers available")

    cfg = settings.get_providers()
    preferred = preferred_provider or pick_provider(model).name

    # The routed provider must be attempted first, then same-tier substitutes.
    ordered = [p for p in available if p.name == preferred]
    ordered += [p for p in available if p.name != preferred]

    plan: list[tuple[AIProvider, str]] = []
    for p in ordered:
        if p.name == preferred:
            plan.append((p, model))
            continue
        substitute = alternate_model(model, preferred)
        if substitute and substitute in cfg[p.name].models:
            plan.append((p, substitute))

    # Unknown/unmapped model: fall back to providers that serve the id verbatim.
    if len(plan) == 1:
        plan.extend(
            (p, model) for p in available
            if p.name != preferred and model in cfg[p.name].models
        )

    return [(p, m) for p, m in plan if is_allowed(p.name)]


async def route_chat(
    req: ChatRequest, preferred_provider: str | None = None
) -> tuple[ChatResponse, str]:
    provider = pick_provider(req.model, preferred_provider)
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


async def route_chat_stream(
    req: ChatRequest, preferred_provider: str | None = None
) -> AsyncGenerator[StreamChunk, None]:
    chain = failover_chain(req.model, preferred_provider)
    if not chain:
        raise Exception("All providers are in cooldown")

    errors: list[str] = []
    for provider, model in chain:
        cfg = settings.get_providers()[provider.name]
        req.model = model
        emitted = False
        try:
            async for chunk in provider.chat_stream(req):
                emitted = True
                yield chunk
            record_success(provider.name)
            return
        except Exception as e:
            record_failure(provider.name, cfg.failure_threshold, cfg.cooldown_ms)
            errors.append(f"{provider.name}/{model}: {redact_error(e)}")
            # Tokens already sent to the client — switching providers mid-stream
            # would splice two answers together, so surface the error instead.
            if emitted:
                raise

    raise Exception(f"All providers failed: {'; '.join(errors)}")
