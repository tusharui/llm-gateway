"""baseline: existing schema

Revision ID: 0001_baseline
Revises:
Create Date: 2026-09-30

Authored from a direct read of ``information_schema.columns`` and
``pg_indexes`` on the production Neon database, not from ``app/models.py``.

That distinction matters. The live tables predate the current ORM definitions
and have drifted from them: ``api_keys`` and ``usage_records`` use ``text``
where the ORM declares ``String(n)``, and several timestamp columns are
nullable in the database while the ORM annotates them non-Optional (which
SQLAlchemy renders as NOT NULL). This revision records what the database
actually contains so ``alembic stamp head`` can adopt it without DDL.

``app/models.py`` is reconciled to match in the following commit.

Data types and nullability here are load-bearing: a mismatch between this file
and the live database shows up as a spurious autogenerate diff, or worse, as a
migration that quietly rewrites production tables.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0001_baseline"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "api_keys",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("key_prefix", sa.Text(), nullable=False),
        sa.Column("key_hash", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=True),
        sa.Column("rate_limit_max", sa.Integer(), server_default=sa.text("60"), nullable=True),
        sa.Column("rate_limit_window_ms", sa.Integer(), server_default=sa.text("60000"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="api_keys_pkey"),
    )
    op.create_index("api_keys_key_hash_key", "api_keys", ["key_hash"], unique=True)

    op.create_table(
        "cached_responses",
        sa.Column("cache_key", sa.Text(), nullable=False),
        sa.Column("response", sa.Text(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("cached_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.Column("ttl_ms", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("cache_key", name="cached_responses_pkey"),
    )

    op.create_table(
        "chat_sessions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("model", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="chat_sessions_pkey"),
    )

    op.create_table(
        "chat_messages",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("session_id", sa.String(length=36), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="chat_messages_pkey"),
    )
    op.create_index("ix_chat_messages_session_id", "chat_messages", ["session_id"], unique=False)

    op.create_table(
        "semantic_cache",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("query_text", sa.Text(), nullable=False),
        sa.Column("embedding_json", sa.Text(), nullable=False),
        sa.Column("response_json", sa.Text(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="semantic_cache_pkey"),
    )

    op.create_table(
        "usage_records",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("api_key_id", sa.Text(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), server_default=sa.text("0"), nullable=True),
        sa.Column("completion_tokens", sa.Integer(), server_default=sa.text("0"), nullable=True),
        sa.Column("total_tokens", sa.Integer(), server_default=sa.text("0"), nullable=True),
        sa.Column("cost_usd", sa.Float(), server_default=sa.text("0"), nullable=True),
        sa.Column("latency_ms", sa.Integer(), server_default=sa.text("0"), nullable=True),
        sa.Column("success", sa.Boolean(), server_default=sa.text("true"), nullable=True),
        sa.Column("cached", sa.Boolean(), server_default=sa.text("false"), nullable=True),
        sa.Column("timestamp", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.PrimaryKeyConstraint("id", name="usage_records_pkey"),
    )


def downgrade() -> None:
    op.drop_table("usage_records")
    op.drop_table("semantic_cache")
    op.drop_table("chat_messages")
    op.drop_table("chat_sessions")
    op.drop_table("cached_responses")
    op.drop_table("api_keys")