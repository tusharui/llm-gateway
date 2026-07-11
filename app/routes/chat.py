from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from app.schemas import ChatRequest
from app.engine.router import route_chat, route_chat_stream
from app.engine.failover import route_with_failover
from app.cache.cache import make_cache_key, get_cached, set_cache
from app.database import get_session
from app.models import UsageRecord
from datetime import datetime, timezone
import json

router = APIRouter()

COST_RATES = {
    "groq": {"input": 0.00000015, "output": 0.0000006},
    "gemini": {"input": 0.0000001, "output": 0.0000004},
    "openrouter": {"input": 0.0000005, "output": 0.0000015},
}


def calculate_cost(provider: str, prompt_tokens: int, completion_tokens: int) -> float:
    rate = COST_RATES.get(provider, {"input": 0.0000005, "output": 0.0000015})
    return prompt_tokens * rate["input"] + completion_tokens * rate["output"]


async def track_usage(api_key_id: str, data: dict):
    try:
        cost = calculate_cost(
            data["provider"], data["prompt_tokens"], data["completion_tokens"]
        )
        session = await get_session()
        if not session:
            return
        try:
            record = UsageRecord(
                id=str(__import__("uuid").uuid4()),
                api_key_id=api_key_id,
                provider=data["provider"],
                model=data["model"],
                prompt_tokens=data["prompt_tokens"],
                completion_tokens=data["completion_tokens"],
                total_tokens=data["total_tokens"],
                cost_usd=cost,
                latency_ms=data["latency_ms"],
                success=data["success"],
                cached=data["cached"],
            )
            session.add(record)
            await session.commit()
        finally:
            await session.close()
    except Exception:
        pass


@router.post("/chat")
async def chat_endpoint(req: ChatRequest, request: Request):
    api_key_id = getattr(request.state, "api_key_id", "anonymous")
    start = __import__("time").perf_counter()

    if req.stream:
        from starlette.responses import StreamingResponse

        async def event_stream():
            try:
                async for chunk in route_chat_stream(req):
                    yield f"event: chunk\ndata: {json.dumps(chunk.model_dump(), default=str)}\n\n"
                yield f"event: done\ndata: {{}}\n\n"
            except Exception as e:
                yield f"event: error\ndata: {json.dumps({'error': str(e)})}\n\n"

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    try:
        result, provider_used = await route_chat(req)
    except Exception:
        result, provider_used = await route_with_failover(req)

    duration = int((__import__("time").perf_counter() - start) * 1000)

    await track_usage(
        api_key_id,
        {
            "provider": provider_used,
            "model": result.model,
            "prompt_tokens": result.usage.prompt_tokens,
            "completion_tokens": result.usage.completion_tokens,
            "total_tokens": result.usage.total_tokens,
            "latency_ms": duration,
            "success": True,
            "cached": False,
        },
    )

    return {
        **result.model_dump(),
        "provider": provider_used,
        "latency_ms": duration,
    }


@router.get("/health")
async def health_endpoint():
    from app.providers.registry import get_available_providers
    import asyncio

    providers = get_available_providers()
    checks = []
    for p in providers:
        result = await p.health_check()
        checks.append(
            {
                "provider": p.name,
                "status": "healthy" if result["ok"] else "degraded",
                "latency_ms": result["latency_ms"],
                "last_checked": datetime.now(timezone.utc).isoformat(),
            }
        )

    all_healthy = all(c["status"] == "healthy" for c in checks)
    return {
        "status": "healthy" if all_healthy else "degraded",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "providers": checks,
    }


@router.get("/models")
async def models_endpoint():
    from app.providers.registry import get_available_providers

    providers = get_available_providers()
    all_models = []
    for p in providers:
        try:
            models = await p.models()
            all_models.extend(models)
        except Exception:
            pass

    return {
        "object": "list",
        "data": [
            {
                "id": m.id,
                "provider": m.provider,
                "capabilities": m.capabilities,
                "context_length": m.context_length,
            }
            for m in all_models
        ],
    }


@router.get("/models/{provider_name}")
async def models_by_provider_endpoint(provider_name: str):
    from app.config import settings

    cfg = settings.get_providers().get(provider_name)
    if not cfg:
        return JSONResponse(status_code=404, content={"error": "Unknown provider"})
    return {
        "provider": cfg.name,
        "models": cfg.models,
        "capabilities": cfg.supported_capabilities,
    }


@router.post("/embeddings")
async def embeddings_endpoint(req: dict):
    from app.providers.registry import get_provider
    from app.schemas import EmbeddingRequest

    embedding_req = EmbeddingRequest(**req)
    provider = get_provider("gemini")
    if not provider or not provider.embeddings:
        return JSONResponse(
            status_code=501,
            content={"error": "Embeddings not supported by any available provider"},
        )
    result = await provider.embeddings(embedding_req)
    return result.model_dump()
