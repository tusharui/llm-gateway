from abc import ABC, abstractmethod
from typing import AsyncGenerator, Optional, List
from app.schemas import ChatRequest, ChatResponse, StreamChunk, EmbeddingRequest, EmbeddingResponse, ModelInfo


class AIProvider(ABC):
    def __init__(self, name: str):
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    @abstractmethod
    async def chat(self, req: ChatRequest) -> ChatResponse:
        ...

    @abstractmethod
    async def chat_stream(self, req: ChatRequest) -> AsyncGenerator[StreamChunk, None]:
        ...

    async def embeddings(self, req: EmbeddingRequest) -> Optional[EmbeddingResponse]:
        return None

    @abstractmethod
    async def models(self) -> List[ModelInfo]:
        ...

    @abstractmethod
    async def health_check(self) -> dict:
        ...
