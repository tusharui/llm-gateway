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
    # TLS to Postgres. "require" verifies the server certificate, "disable"
    # sends traffic in cleartext.
    #
    # There is deliberately no "auto": deciding this by probing would mean
    # opening an unencrypted connection first, which transmits the database
    # password in cleartext to whatever host DATABASE_URL happens to name --
    # including a typo'd one. Production runs Neon and must stay on "require";
    # only a local dev container with a self-signed certificate needs
    # "disable", and that has to be an explicit choice.
    database_ssl: str = Field(default="require")
    groq_api_key: str = Field(default="")
    gemini_api_key: str = Field(default="")
    openrouter_api_key: str = Field(default="")

    # Websocket limits. The /ws/chat socket holds an open, billable LLM stream
    # for as long as the client keeps it open, so "one request in, one response
    # out" bounds nothing. Four limits, four different resources:
    ws_max_message_bytes: int = Field(
        default=65536,
        description="Max bytes in one inbound websocket frame. A frame is buffered before the app sees it, so uvicorn --ws-max-size must match; this is the second line, not the first.",
    )
    ws_max_messages_per_request: int = Field(
        default=64,
        description="Max entries in a single frame's messages array. Bounds the pydantic model built from it.",
    )
    ws_max_turns_per_connection: int = Field(
        default=200,
        description="Max request/response cycles on one connection before closing it with 1008. Bounds socket lifetime.",
    )
    ws_rate_limit: bool = Field(
        default=True,
        description="Share the HTTP rate-limit budget with websockets. Off would let an exhausted key get a fresh budget by switching protocol.",
    )
    ws_max_connections_per_key: int = Field(
        default=8,
        description="Max simultaneous websocket connections for one api_key_id. Per-message rate limiting does not bound sockets that never send anything.",
    )
    log_level: str = Field(default="info")

    # CORS. The gateway authenticates with a bearer token, never a cookie, so
    # it has no business reflecting arbitrary origins with credentials
    # enabled. Starlette treats allow_origins=["*"] together with
    # allow_credentials=True as "echo whatever Origin arrives and allow
    # cookies with it", which is the combination browsers warn about.
    #
    # Empty by default: an unset list means no browser origin is allowed, and
    # the frontend reaches the API server-side (Next.js route handlers), where
    # CORS does not apply. Set a comma-separated allowlist only if a browser
    # is expected to call the API directly.
    cors_allow_origins: str = Field(default="")

    @property
    def cors_origin_list(self) -> List[str]:
        return [o.strip() for o in self.cors_allow_origins.split(",") if o.strip()]

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
                    "openai/gpt-oss-120b",
                    "openai/gpt-oss-20b",
                    "qwen/qwen3.8-27b",
                    "allam-2-7b",
                ],
                supported_capabilities=["chat"],
            ),
            "gemini": ProviderConfig(
                name="gemini",
                api_key=self.gemini_api_key,
                base_url="https://generativelanguage.googleapis.com/v1beta",
                models=[
                    "gemini-2.5-flash",
                    "gemini-2.5-flash-lite",
                    "gemini-3.1-flash-lite",
                ],
                supported_capabilities=["chat", "embeddings"],
            ),
            "openrouter": ProviderConfig(
                name="openrouter",
                api_key=self.openrouter_api_key,
                base_url="https://openrouter.ai/api/v1",
                models=[
                    "openai/gpt-4o-mini",
                    "anthropic/claude-haiku-4.5",
                    "google/gemini-2.5-flash",
                    "meta-llama/llama-3.3-70b-instruct",
                    "mistralai/mistral-small-3.2-24b-instruct",
                ],
                supported_capabilities=["chat"],
            ),
        }

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()
