"""ORM models.

Column types and nullability mirror the production database exactly, as
recorded in ``migrations/versions/0001_baseline.py``. ``alembic check`` compares
these definitions against the live schema, so any drift between this file and
the database shows up as a failing CI step rather than a surprise during an
incident.

Two consequences worth knowing:

* ``text`` is used where the live tables are ``text`` (not ``varchar(n)``).
  Tightening to ``varchar`` would mean a rewrite of every existing row.
* Nullable columns carry an explicit ``nullable=True``. A non-Optional
  ``Mapped[...]`` annotation implies NOT NULL in SQLAlchemy 2.0, which is
  stricter than several timestamp columns in the live schema.
"""

from datetime import datetime, timezone
from sqlalchemy import String, Boolean, Integer, Float, DateTime, Text, Index, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ApiKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    key_prefix: Mapped[str] = mapped_column(Text)
    # Declared as a UNIQUE constraint to match the live table. PostgreSQL
    # backs it with an index named api_keys_key_hash_key, so the default
    # naming already agrees -- but the catalog object has to match too, or
    # `alembic check` reports a drop/create pair on every run.
    key_hash: Mapped[str] = mapped_column(Text, unique=True)
    name: Mapped[str] = mapped_column(Text)
    is_active: Mapped[bool | None] = mapped_column(
        Boolean, default=True, server_default=text("true"), nullable=True
    )
    rate_limit_max: Mapped[int | None] = mapped_column(
        Integer, default=60, server_default=text("60"), nullable=True
    )
    rate_limit_window_ms: Mapped[int | None] = mapped_column(
        Integer, default=60000, server_default=text("60000"), nullable=True
    )
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        default=_utcnow,
        server_default=text("now()"),
        nullable=True,
    )
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class UsageRecord(Base):
    __tablename__ = "usage_records"
    # Indexed in 0002_analytics_indexes; declared here so autogenerate agrees.
    __table_args__ = (
        Index("ix_usage_records_api_key_id", "api_key_id"),
        Index("ix_usage_records_timestamp", "timestamp"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    api_key_id: Mapped[str] = mapped_column(Text)
    provider: Mapped[str] = mapped_column(Text)
    model: Mapped[str] = mapped_column(Text)
    prompt_tokens: Mapped[int | None] = mapped_column(
        Integer, default=0, server_default=text("0"), nullable=True
    )
    completion_tokens: Mapped[int | None] = mapped_column(
        Integer, default=0, server_default=text("0"), nullable=True
    )
    total_tokens: Mapped[int | None] = mapped_column(
        Integer, default=0, server_default=text("0"), nullable=True
    )
    cost_usd: Mapped[float | None] = mapped_column(
        Float, default=0.0, server_default=text("0"), nullable=True
    )
    latency_ms: Mapped[int | None] = mapped_column(
        Integer, default=0, server_default=text("0"), nullable=True
    )
    success: Mapped[bool | None] = mapped_column(
        Boolean, default=True, server_default=text("true"), nullable=True
    )
    cached: Mapped[bool | None] = mapped_column(
        Boolean, default=False, server_default=text("false"), nullable=True
    )
    timestamp: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        default=_utcnow,
        server_default=text("now()"),
        nullable=True,
    )


class CachedResponse(Base):
    __tablename__ = "cached_responses"

    cache_key: Mapped[str] = mapped_column(Text, primary_key=True)
    response: Mapped[str] = mapped_column(Text)
    provider: Mapped[str] = mapped_column(Text)
    model: Mapped[str] = mapped_column(Text)
    cached_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        default=_utcnow,
        server_default=text("now()"),
        nullable=True,
    )
    ttl_ms: Mapped[int] = mapped_column(Integer)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ChatSession(Base):
    __tablename__ = "chat_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    title: Mapped[str] = mapped_column(String(255), default="New chat")
    provider: Mapped[str] = mapped_column(String(50), default="groq")
    model: Mapped[str] = mapped_column(String(100), default="openai/gpt-oss-120b")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(36), index=True)
    role: Mapped[str] = mapped_column(String(20))
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )


class SemanticCacheEntry(Base):
    __tablename__ = "semantic_cache"
    __table_args__ = (Index("ix_semantic_cache_expires_at", "expires_at"),)

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    query_text: Mapped[str] = mapped_column(Text)
    embedding_json: Mapped[str] = mapped_column(Text)
    response_json: Mapped[str] = mapped_column(Text)
    provider: Mapped[str] = mapped_column(Text, default="cache")
    model: Mapped[str] = mapped_column(Text, default="unknown")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))