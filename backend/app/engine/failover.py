from typing import List
from app.providers.interface import AIProvider
from app.providers.registry import get_available_providers
from app.engine.circuit_breaker import is_allowed
from app.engine.auto_router import alternate_model
from app.engine.retry import with_retry
from app.config import settings
from app.redact import redact_error
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

    original_model = req.model
    errors = []
    for provider in ordered:
        if not is_allowed(provider.name):
            continue

        provider_cfg = settings.get_providers()[provider.name]
        if provider.name == preferred_provider:
            req.model = original_model
        else:
            substitute = alternate_model(original_model, preferred_provider or "")
            if substitute and substitute in provider_cfg.models:
                req.model = substitute
            elif original_model in provider_cfg.models:
                req.model = original_model
            else:
                continue

        try:
            result = await with_retry(
                lambda p=provider: p.chat(req), retries=1, base_delay_ms=500
            )
            return result, provider.name
        except Exception as e:
            errors.append(f"{provider.name}: {redact_error(e)}")

    req.model = original_model
    raise Exception(f"All providers failed: {'; '.join(errors)}")
