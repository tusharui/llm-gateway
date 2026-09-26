import httpx
import json
import asyncio
from typing import AsyncGenerator, List
from app.providers.interface import AIProvider
from app.schemas import ChatRequest, ChatResponse, StreamChunk, ModelInfo, Usage
from app.config import settings


class OpenRouterProvider(AIProvider):
    def __init__(self):
        super().__init__("openrouter")
        self.cfg = settings.get_providers()["openrouter"]

    async def chat(self, req: ChatRequest) -> ChatResponse:
        body = {
            "model": req.model,
            "messages": [m.model_dump() for m in req.messages],
            "stream": False,
        }
        if req.temperature is not None:
            body["temperature"] = req.temperature
        if req.max_tokens is not None:
            body["max_tokens"] = req.max_tokens
        if req.top_p is not None:
            body["top_p"] = req.top_p

        async with httpx.AsyncClient(timeout=self.cfg.timeout_ms / 1000) as client:
            res = await client.post(
                f"{self.cfg.base_url}/chat/completions",
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self.cfg.api_key}",
                    "HTTP-Referer": "https://github.com/ai-inference-gateway",
                    "X-Title": "AI Inference Gateway",
                },
                json=body,
            )
            res.raise_for_status()
            data = res.json()

        usage = data.get("usage", {})
        return ChatResponse(
            id=data["id"],
            model=data["model"],
            provider=self.name,
            choices=[
                {
                    "index": c["index"],
                    "message": {"role": "assistant", "content": c["message"]["content"]},
                    "finish_reason": c["finish_reason"],
                }
                for c in data["choices"]
            ],
            usage={
                "prompt_tokens": usage.get("prompt_tokens", 0),
                "completion_tokens": usage.get("completion_tokens", 0),
                "total_tokens": usage.get("total_tokens", 0),
            },
        )

    async def chat_stream(self, req: ChatRequest) -> AsyncGenerator[StreamChunk, None]:
        body = {
            "model": req.model,
            "messages": [m.model_dump() for m in req.messages],
            "stream": True,
            # OpenRouter only reports token counts on a streamed request when asked.
            "stream_options": {"include_usage": True},
        }
        if req.temperature is not None:
            body["temperature"] = req.temperature
        if req.max_tokens is not None:
            body["max_tokens"] = req.max_tokens
        if req.top_p is not None:
            body["top_p"] = req.top_p

        async with httpx.AsyncClient(timeout=self.cfg.timeout_ms / 1000) as client:
            async with client.stream(
                "POST",
                f"{self.cfg.base_url}/chat/completions",
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self.cfg.api_key}",
                    "HTTP-Referer": "https://github.com/ai-inference-gateway",
                    "X-Title": "AI Inference Gateway",
                },
                json=body,
            ) as res:
                res.raise_for_status()
                buffer = ""
                async for chunk in res.aiter_text():
                    buffer += chunk
                    lines = buffer.split("\n")
                    buffer = lines.pop()
                    for line in lines:
                        line = line.strip()
                        if not line or not line.startswith("data: "):
                            continue
                        data_str = line[6:]
                        if data_str == "[DONE]":
                            return
                        try:
                            data = json.loads(data_str)
                            usage = data.get("usage")
                            yield StreamChunk(
                                id=data.get("id", ""),
                                model=data.get("model", req.model),
                                provider=self.name,
                                choices=[
                                    {
                                        "index": c.get("index", 0),
                                        "delta": {
                                            "role": "assistant",
                                            "content": c.get("delta", {}).get("content", "") or "",
                                        },
                                        "finish_reason": c.get("finish_reason"),
                                    }
                                    for c in data.get("choices", [])
                                ],
                                usage=Usage(
                                    prompt_tokens=usage.get("prompt_tokens", 0),
                                    completion_tokens=usage.get("completion_tokens", 0),
                                    total_tokens=usage.get("total_tokens", 0),
                                ) if usage else None,
                            )
                        except json.JSONDecodeError:
                            continue

    async def models(self) -> List[ModelInfo]:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                res = await client.get(
                    f"{self.cfg.base_url}/models",
                    headers={"Authorization": f"Bearer {self.cfg.api_key}"},
                )
                if not res.is_success:
                    return []
                data = res.json()
                return [
                    ModelInfo(
                        id=m["id"],
                        provider=self.name,
                        capabilities=["chat"],
                        context_length=m.get("context_length"),
                    )
                    for m in data.get("data", [])
                ]
        except Exception:
            return []

    async def health_check(self) -> dict:
        start = asyncio.get_event_loop().time()
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                res = await client.get(
                    f"{self.cfg.base_url}/models",
                    headers={"Authorization": f"Bearer {self.cfg.api_key}"},
                )
                latency = int((asyncio.get_event_loop().time() - start) * 1000)
                return {"ok": res.is_success, "latency_ms": latency}
        except Exception:
            latency = int((asyncio.get_event_loop().time() - start) * 1000)
            return {"ok": False, "latency_ms": latency}
