from pydantic_settings import BaseSettings
from pydantic import Field
from typing import Dict, List


class ProviderConfig:
    def __init__(
        self,
        name: str,
        api_key: str,
        base_url: str,
        models: List[str],
        supported_capabilities: List[str],
        timeout_ms: int = 30000,
        retry_count: int = 3,
        retry_delay_ms: int = 1000,
        cooldown_ms: int = 60000,
        failure_threshold: int = 5,
        weight: int = 1,
    ):
        self.name = name
        self.api_key = api_key
        self.base_url = base_url
        self.models = models
        self.supported_capabilities = supported_capabilities
        self.timeout_ms = timeout_ms
        self.retry_count = retry_count
        self.retry_delay_ms = retry_delay_ms
        self.cooldown_ms = cooldown_ms
        self.failure_threshold = failure_threshold
        self.weight = weight


class Settings(BaseSettings):
    port: int = Field(default=8000)
    node_env: str = Field(default="development")
    gateway_api_key: str = Field(default="sk-gateway-dev-key")
    rate_limit_window_ms: int = Field(default=60000)
    rate_limit_max_requests: int = Field(default=60)
    cache_ttl_ms: int = Field(default=300000)
    cache_max_size: int = Field(default=1000)
    database_url: str = Field(default="")
    groq_api_key: str = Field(default="")
    gemini_api_key: str = Field(default="")
    openrouter_api_key: str = Field(default="")
    log_level: str = Field(default="info")

    @property
    def is_dev(self) -> bool:
        return self.node_env == "development"

    def get_providers(self) -> Dict[str, ProviderConfig]:
        return {
            "groq": ProviderConfig(
                name="groq",
                api_key=self.groq_api_key,
                base_url="https://api.groq.com/openai/v1",
                models=[
                    "llama-3.3-70b-versatile",
                    "llama-3.1-8b-instant",
                    "mixtral-8x7b-32768",
                    "gemma2-9b-it",
                ],
                supported_capabilities=["chat"],
            ),
            "gemini": ProviderConfig(
                name="gemini",
                api_key=self.gemini_api_key,
                base_url="https://generativelanguage.googleapis.com/v1beta",
                models=[
                    "gemini-2.0-flash",
                    "gemini-2.0-flash-lite",
                    "gemini-1.5-flash",
                    "gemini-1.5-pro",
                ],
                supported_capabilities=["chat", "embeddings"],
            ),
            "openrouter": ProviderConfig(
                name="openrouter",
                api_key=self.openrouter_api_key,
                base_url="https://openrouter.ai/api/v1",
                models=[
                    "openai/gpt-4o-mini",
                    "anthropic/claude-3.5-haiku",
                    "google/gemini-2.0-flash-001",
                    "meta-llama/llama-3.3-70b-instruct",
                ],
                supported_capabilities=["chat"],
            ),
        }

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()
