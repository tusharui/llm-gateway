from pydantic import BaseModel, Field
from typing import List, Optional, Union
from enum import Enum


class RoleEnum(str, Enum):
    system = "system"
    user = "user"
    assistant = "assistant"


class ChatMessage(BaseModel):
    role: RoleEnum
    content: str = Field(min_length=1)


class ChatRequest(BaseModel):
    model: str = Field(min_length=1)
    messages: List[ChatMessage] = Field(min_length=1)
    stream: bool = False
    temperature: Optional[float] = Field(None, ge=0, le=2)
    max_tokens: Optional[int] = Field(None, gt=0)
    top_p: Optional[float] = Field(None, ge=0, le=1)


class EmbeddingRequest(BaseModel):
    model: str = Field(min_length=1)
    input: Union[str, List[str]]


class Choice(BaseModel):
    index: int
    message: ChatMessage
    finish_reason: str


class Usage(BaseModel):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class ChatResponse(BaseModel):
    id: str
    model: str
    provider: str
    choices: List[Choice]
    usage: Usage
    latency_ms: Optional[int] = None
    routing_decision: Optional[str] = None
    cache_hit: Optional[bool] = None
    cache_similarity: Optional[float] = None


class StreamDelta(BaseModel):
    role: Optional[str] = None
    content: Optional[str] = ""


class StreamChoice(BaseModel):
    index: int
    delta: StreamDelta
    finish_reason: Optional[str] = None


class StreamChunk(BaseModel):
    id: str
    model: str
    provider: str
    choices: List[StreamChoice]
    token_count: Optional[int] = None


class EmbeddingData(BaseModel):
    index: int
    embedding: List[float]


class EmbeddingResponse(BaseModel):
    model: str
    provider: str
    data: List[EmbeddingData]
    usage: Usage


class ModelInfo(BaseModel):
    id: str
    provider: str
    capabilities: List[str]
    context_length: Optional[int] = None


class HealthCheck(BaseModel):
    provider: str
    status: str
    latency_ms: int
    last_checked: str
