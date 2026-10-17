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
    """Choose a provider for `model`.

    Deterministic by design. This function used to finish with

        weighted = []
        for p in candidates:
            weighted.extend([p] * cfg[p.name].weight)
        return random.choice(weighted)

    Every provider has weight=1 in config.py, so the draw was uniform, and
    the branch is only reached when `preferred_provider` is None or was
    rejected -- which routes/chat.py never does, because it always supplies
    one. So it was non-determinism in the routing path with no weighting
    behind it: two identical requests could pick different providers, and no
    test could assert which without patching random.choice.

    Order is the configured provider order, so the same inputs always give
    the same answer. If weighted routing is ever wanted it should be an
    explicit, configurable policy with a test, not an accident of every
    weight defaulting to 1.
    """
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

    return candidates[0]


def failover_chain(
    model: str,
    preferred_provider: str | None = None,
    allow_unlisted_model: bool = False,
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
            # Only lead with the preferred provider if it actually hosts the
            # model. This branch used to append unconditionally, so a caller
            # that named a provider which does not serve the model got a chain
            # that opened with a guaranteed 404 and burned the first failover
            # attempt before reaching anyone who could have answered.
            #
            # allow_unlisted_model is the caller vouching for the pairing: the
            # model came out of that provider's own GET /models catalogue, which
            # is far larger than the routing list in config. Without it a client
            # that picked one of those models got a chain that omitted the
            # provider it had explicitly asked for.
            if allow_unlisted_model or model in cfg[p.name].models:
                plan.append((p, model))
            continue

        # Ask for a substitute that THIS provider can serve. Passing the target
        # provider is what stops the chain from proposing, say,
        # gemini-2.5-flash to OpenRouter (which lists it as
        # google/gemini-2.5-flash and would 404).
        substitute = alternate_model(model, preferred, target_provider=p.name)
        if substitute:
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
        # Deliberately does NOT touch the circuit breaker.
        #
        # This closure is what with_retry re-invokes, so recording breaker
        # state here counted every retry as an independent failure. With
        # retry_count=3 a single client request produced four upstream calls
        # and four failures, against a configured threshold of five -- so two
        # client requests ejected a provider. That makes failure_threshold
        # mean "1.25 requests", which is neither what the config says nor
        # what an operator tuning it would expect, and it ejects a provider
        # off one blip rather than a sustained outage.
        #
        # Retry accounting belongs to the retry layer. The breaker answers a
        # different question: is this provider healthy right now?
        return await provider.chat(req)

    try:
        result = await with_retry(
            _call, retries=cfg.retry_count, base_delay_ms=cfg.retry_delay_ms
        )
    except Exception:
        # One failure per client request, no matter how many attempts it took.
        record_failure(provider.name, cfg.failure_threshold, cfg.cooldown_ms)
        raise

    record_success(provider.name)
    return result, provider.name


async def route_chat_stream(
    req: ChatRequest,
    preferred_provider: str | None = None,
    allow_unlisted_model: bool = False,
) -> AsyncGenerator[StreamChunk, None]:
    chain = failover_chain(req.model, preferred_provider, allow_unlisted_model)
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
