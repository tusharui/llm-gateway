from typing import List
from app.providers.interface import AIProvider
from app.providers.registry import get_available_providers
from app.engine.circuit_breaker import is_allowed
from app.engine.retry import with_retry
from app.schemas import ChatRequest, ChatResponse


async def route_with_failover(
    req: ChatRequest, preferred_provider: str | None = None
) -> tuple[ChatResponse, str]:
    providers = get_available_providers()
    if not providers:
        raise Exception("No providers available")

    if preferred_provider:
        ordered = [p for p in providers if p.name == preferred_provider] + [
            p for p in providers if p.name != preferred_provider
        ]
    else:
        ordered = providers

    errors = []
    for provider in ordered:
        if not is_allowed(provider.name):
            continue
        try:
            result = await with_retry(
                lambda p=provider: p.chat(req), retries=1, base_delay_ms=500
            )
            return result, provider.name
        except Exception as e:
            errors.append(f"{provider.name}: {str(e)}")

    raise Exception(f"All providers failed: {'; '.join(errors)}")
