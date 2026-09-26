import httpx
import json
import uuid
import asyncio
from typing import AsyncGenerator, List
from app.providers.interface import AIProvider
from app.schemas import ChatRequest, ChatResponse, StreamChunk, EmbeddingRequest, EmbeddingResponse, ModelInfo, Usage
from app.config import settings


class GeminiProvider(AIProvider):
    def __init__(self):
        super().__init__("gemini")
        self.cfg = settings.get_providers()["gemini"]

    def _gemini_model(self, model: str) -> str:
        return model if model.startswith("models/") else f"models/{model}"

    def _to_gemini_messages(self, messages):
        system = next((m for m in messages if m.role == "system"), None)
        contents = [
            {
                "role": "model" if m.role == "assistant" else "user",
                "parts": [{"text": m.content}],
            }
            for m in messages
            if m.role != "system"
        ]
        result = {"contents": contents}
        if system:
            result["systemInstruction"] = {"parts": [{"text": system.content}]}
        return result

    def _from_gemini_response(self, data: dict, model: str) -> ChatResponse:
        candidate = (data.get("candidates") or [{}])[0]
        usage = data.get("usageMetadata", {})
        return ChatResponse(
            id=data.get("id", str(uuid.uuid4())),
            model=model,
            provider=self.name,
            choices=[
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": candidate.get("content", {})
                        .get("parts", [{}])[0]
                        .get("text", ""),
                    },
                    "finish_reason": candidate.get("finishReason", "stop"),
                }
            ],
            usage={
                "prompt_tokens": usage.get("promptTokenCount", 0),
                "completion_tokens": usage.get("candidatesTokenCount", 0),
                "total_tokens": usage.get("totalTokenCount", 0),
            },
        )

    async def chat(self, req: ChatRequest) -> ChatResponse:
        model_path = self._gemini_model(req.model)
        url = f"{self.cfg.base_url}/{model_path}:generateContent?key={self.cfg.api_key}"
        body = self._to_gemini_messages(req.messages)
        gen_config = {}
        if req.temperature is not None:
            gen_config["temperature"] = req.temperature
        if req.max_tokens is not None:
            gen_config["maxOutputTokens"] = req.max_tokens
        if req.top_p is not None:
            gen_config["topP"] = req.top_p
        if gen_config:
            body["generationConfig"] = gen_config

        async with httpx.AsyncClient(timeout=self.cfg.timeout_ms / 1000) as client:
            res = await client.post(
                url, headers={"Content-Type": "application/json"}, json=body
            )
            res.raise_for_status()
            data = res.json()

        return self._from_gemini_response(data, req.model)

    async def chat_stream(self, req: ChatRequest) -> AsyncGenerator[StreamChunk, None]:
        model_path = self._gemini_model(req.model)
        url = f"{self.cfg.base_url}/{model_path}:streamGenerateContent?alt=sse&key={self.cfg.api_key}"
        body = self._to_gemini_messages(req.messages)
        gen_config = {}
        if req.temperature is not None:
            gen_config["temperature"] = req.temperature
        if req.max_tokens is not None:
            gen_config["maxOutputTokens"] = req.max_tokens
        if req.top_p is not None:
            gen_config["topP"] = req.top_p
        if gen_config:
            body["generationConfig"] = gen_config

        async with httpx.AsyncClient(timeout=self.cfg.timeout_ms / 1000) as client:
            async with client.stream(
                "POST",
                url,
                headers={"Content-Type": "application/json"},
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
                        if not line.startswith("data: "):
                            continue
                        data_str = line[6:]
                        if not data_str or data_str == "[]":
                            continue
                        try:
                            data = json.loads(data_str)
                            candidate = (data.get("candidates") or [{}])[0]
                            content = (
                                candidate.get("content", {})
                                .get("parts", [{}])[0]
                                .get("text", "")
                            )
                            finish = candidate.get("finishReason")
                            usage_meta = data.get("usageMetadata")
                            yield StreamChunk(
                                id=data.get("id", str(uuid.uuid4())),
                                model=req.model,
                                provider=self.name,
                                choices=[
                                    {
                                        "index": 0,
                                        "delta": {"content": content},
                                        "finish_reason": finish,
                                    }
                                ],
                                usage=Usage(
                                    prompt_tokens=usage_meta.get("promptTokenCount", 0),
                                    completion_tokens=usage_meta.get("candidatesTokenCount", 0),
                                    total_tokens=usage_meta.get("totalTokenCount", 0),
                                ) if usage_meta else None,
                            )
                        except json.JSONDecodeError:
                            continue

    async def embeddings(self, req: EmbeddingRequest) -> EmbeddingResponse:
        model_path = self._gemini_model(req.model)
        url = f"{self.cfg.base_url}/{model_path}:embedContent?key={self.cfg.api_key}"
        texts = [req.input] if isinstance(req.input, str) else req.input

        results = []
        async with httpx.AsyncClient(timeout=self.cfg.timeout_ms / 1000) as client:
            for text in texts:
                res = await client.post(
                    url,
                    headers={"Content-Type": "application/json"},
                    json={"content": {"parts": [{"text": text}]}},
                )
                res.raise_for_status()
                data = res.json()
                results.append(data.get("embedding", {}).get("values", []))

        return EmbeddingResponse(
            model=req.model,
            provider=self.name,
            data=[{"index": i, "embedding": emb} for i, emb in enumerate(results)],
            usage={"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        )

    async def models(self) -> List[ModelInfo]:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                res = await client.get(
                    f"{self.cfg.base_url}/models?key={self.cfg.api_key}"
                )
                if not res.is_success:
                    return []
                data = res.json()
                return [
                    ModelInfo(
                        id=m["name"].replace("models/", ""),
                        provider=self.name,
                        capabilities=["chat", "embeddings"],
                        context_length=m.get("inputTokenLimit"),
                    )
                    for m in data.get("models", [])
                    if "generateContent" in m.get("supportedGenerationMethods", [])
                ]
        except Exception:
            return []

    async def health_check(self) -> dict:
        start = asyncio.get_event_loop().time()
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                res = await client.get(
                    f"{self.cfg.base_url}/models?key={self.cfg.api_key}"
                )
                latency = int((asyncio.get_event_loop().time() - start) * 1000)
                return {"ok": res.is_success, "latency_ms": latency}
        except Exception:
            latency = int((asyncio.get_event_loop().time() - start) * 1000)
            return {"ok": False, "latency_ms": latency}
