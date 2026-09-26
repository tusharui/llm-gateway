import asyncio
import time
import uuid
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.responses import Response
from app.schemas import ChatRequest, EmbeddingRequest
from app.engine.router import route_chat, route_chat_stream
from app.engine.failover import route_with_failover
from app.engine.auto_router import auto_route, get_tier_info, classify_complexity
from app.cache.cache import make_cache_key, get_cached, set_cache
from app.cache.semantic_cache import semantic_get, semantic_set
from app.database import get_session
from app.models import UsageRecord
from app.providers.registry import get_available_providers, get_provider
from app.config import settings
from app.redact import redact_error
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


_pending_writes: set = set()


def _record_usage_async(api_key_id: str, data: dict) -> None:
    """Persist usage without blocking the SSE stream.

    Awaiting the write here would stall the terminal `done` event, and the
    Neon round-trip is far slower than the response itself. The task reference is
    held so it is not garbage collected mid-flight.
    """
    task = asyncio.create_task(track_usage(api_key_id, data))
    _pending_writes.add(task)
    task.add_done_callback(_pending_writes.discard)


def resolve_route(req: ChatRequest, available: list) -> tuple[str, str, str]:
    cfg = settings.get_providers()
    requested = (req.model or "").strip()

    if requested.lower() not in ("auto", ""):
        for provider_name, provider_cfg in cfg.items():
            if requested in provider_cfg.models:
                return (
                    provider_name,
                    requested,
                    f"Explicit model={requested} | provider={provider_name}",
                )

    provider_name, model_id, reasoning = auto_route(req, available)
    if requested.lower() not in ("auto", ""):
        reasoning = f"Unknown model '{requested}' -> {reasoning}"
    return provider_name, model_id, reasoning


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
                id=str(uuid.uuid4()),
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
    start = time.perf_counter()

    # --- Auto-route: classify complexity and pick optimal model ---
    available = get_available_providers()
    chosen_provider, chosen_model, routing_reasoning = resolve_route(req, available)

    effective_model = chosen_model
    effective_provider_name = chosen_provider

    # The provider adapters send req.model verbatim, so the routed model must be
    # written back onto the request. Otherwise "auto" reaches the provider and 404s.
    req.model = effective_model

    # --- Semantic Cache Lookup ---
    semantic_result, semantic_sim = await semantic_get(
        effective_model, req.messages, similarity_threshold=0.92
    )
    if semantic_result:
        duration = int((time.perf_counter() - start) * 1000)
        await track_usage(api_key_id, {
            "provider": effective_provider_name,
            "model": effective_model,
            "prompt_tokens": semantic_result.get("usage", {}).get("prompt_tokens", 0),
            "completion_tokens": semantic_result.get("usage", {}).get("completion_tokens", 0),
            "total_tokens": semantic_result.get("usage", {}).get("total_tokens", 0),
            "latency_ms": duration,
            "success": True,
            "cached": True,
        })
        response_data = {
            **semantic_result,
            "provider": effective_provider_name,
            "latency_ms": duration,
            "routing_decision": routing_reasoning,
            "cache_hit": True,
            "cache_similarity": round(semantic_sim, 4),
        }
        return response_data

    # --- Exact Cache Lookup ---
    exact_key = make_cache_key(effective_model, [m.model_dump() for m in req.messages])
    exact_cached = await get_cached(exact_key)
    if exact_cached:
        duration = int((time.perf_counter() - start) * 1000)
        await track_usage(api_key_id, {
            "provider": effective_provider_name,
            "model": effective_model,
            "prompt_tokens": exact_cached.get("usage", {}).get("prompt_tokens", 0),
            "completion_tokens": exact_cached.get("usage", {}).get("completion_tokens", 0),
            "total_tokens": exact_cached.get("usage", {}).get("total_tokens", 0),
            "latency_ms": duration,
            "success": True,
            "cached": True,
        })
        return {
            **exact_cached,
            "provider": effective_provider_name,
            "latency_ms": duration,
            "routing_decision": routing_reasoning,
            "cache_hit": True,
            "cache_similarity": 1.0,
        }

    # --- Streaming Response ---
    if req.stream:
        token_counter = 0

        async def event_stream():
            nonlocal token_counter
            stream_usage = None
            provider_used = effective_provider_name
            model_used = effective_model
            try:
                async for chunk in route_chat_stream(
                    req, preferred_provider=effective_provider_name
                ):
                    token_counter += 1
                    provider_used = chunk.provider
                    model_used = chunk.model
                    if chunk.usage:
                        stream_usage = chunk.usage
                    chunk_data = chunk.model_dump()
                    chunk_data["token_count"] = token_counter
                    yield f"event: chunk\ndata: {json.dumps(chunk_data, default=str)}\n\n"

                # Providers report token counts only on the final streamed frame.
                # Without this the whole streaming path is invisible to analytics.
                _record_usage_async(api_key_id, {
                    "provider": provider_used,
                    "model": model_used,
                    "prompt_tokens": stream_usage.prompt_tokens if stream_usage else 0,
                    "completion_tokens": stream_usage.completion_tokens if stream_usage else 0,
                    "total_tokens": stream_usage.total_tokens if stream_usage else 0,
                    "latency_ms": int((time.perf_counter() - start) * 1000),
                    "success": True,
                    "cached": False,
                })

                yield f"event: done\ndata: {json.dumps({'token_count': token_counter, 'routing_decision': routing_reasoning, 'provider': provider_used, 'model': model_used})}\n\n"
            except Exception as e:
                _record_usage_async(api_key_id, {
                    "provider": provider_used,
                    "model": model_used,
                    "prompt_tokens": stream_usage.prompt_tokens if stream_usage else 0,
                    "completion_tokens": stream_usage.completion_tokens if stream_usage else 0,
                    "total_tokens": stream_usage.total_tokens if stream_usage else 0,
                    "latency_ms": int((time.perf_counter() - start) * 1000),
                    "success": False,
                    "cached": False,
                })
                yield f"event: error\ndata: {json.dumps({'error': redact_error(e), 'routing_decision': routing_reasoning})}\n\n"

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    # --- Non-Streaming Response ---
    try:
        result, provider_used = await route_chat(
            req, preferred_provider=effective_provider_name
        )
    except Exception:
        result, provider_used = await route_with_failover(
            req, preferred_provider=effective_provider_name
        )

    duration = int((time.perf_counter() - start) * 1000)

    response_dict = result.model_dump()

    # Store in semantic cache
    await semantic_set(effective_model, req.messages, response_dict)

    # Store in exact cache
    await set_cache(exact_key, response_dict)

    await track_usage(api_key_id, {
        "provider": provider_used,
        "model": result.model,
        "prompt_tokens": result.usage.prompt_tokens,
        "completion_tokens": result.usage.completion_tokens,
        "total_tokens": result.usage.total_tokens,
        "latency_ms": duration,
        "success": True,
        "cached": False,
    })

    return {
        **response_dict,
        "provider": provider_used,
        "latency_ms": duration,
        "routing_decision": routing_reasoning,
        "cache_hit": False,
        "cache_similarity": None,
    }


@router.get("/routing")
async def routing_info_endpoint():
    return get_tier_info()


@router.post("/routing/classify")
async def classify_endpoint(req: ChatRequest):
    available = get_available_providers()
    chosen_provider, chosen_model, reasoning = auto_route(req, available)
    tier = classify_complexity(req.messages)
    return {
        "tier": tier,
        "selected_provider": chosen_provider,
        "selected_model": chosen_model,
        "reasoning": reasoning,
    }


@router.get("/health")
async def health_endpoint():
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
    embedding_req = EmbeddingRequest(**req)
    provider = get_provider("gemini")
    if not provider or not provider.embeddings:
        return JSONResponse(
            status_code=501,
            content={"error": "Embeddings not supported by any available provider"},
        )
    result = await provider.embeddings(embedding_req)
    return result.model_dump()
