"""add indexes for usage analytics and cache eviction

Revision ID: 0002_analytics_indexes
Revises: 0001_baseline
Create Date: 2026-09-30

usage_records has no index on api_key_id or timestamp, and semantic_cache has
none on expires_at. Every read path that matters touches them:

- /analytics groups usage by api_key and orders by timestamp. At the current
  17 rows this is invisible; it is a sequential scan from the first real
  customer onwards, and the table only ever grows.
- The semantic cache is currently a linear scan over every row (see
  SemanticCacheEntry). Eviction needs expires_at indexed before switching the
  cache to pgvector, otherwise replacing the scan with a vector index just
  moves the problem.

ChatMessage.session_id already had an index; nothing else was unindexed.

Plain CREATE INDEX rather than CREATE INDEX CONCURRENTLY: Alembic wraps
migrations in a transaction and CONCURRENTLY cannot run inside one. For a
table this small the rewrite lock is negligible. If usage_records ever grows
large enough for that to matter, this revision needs an autocommit block.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0002_analytics_indexes"
down_revision: Union[str, Sequence[str], None] = "0001_baseline"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

INDEXES = [
    ("ix_usage_records_api_key_id", "usage_records", ["api_key_id"]),
    ("ix_usage_records_timestamp", "usage_records", ["timestamp"]),
    ("ix_semantic_cache_expires_at", "semantic_cache", ["expires_at"]),
]


def upgrade() -> None:
    for name, table, columns in INDEXES:
        op.create_index(name, table, columns, unique=False)


def downgrade() -> None:
    for name, table, _ in reversed(INDEXES):
        op.drop_index(name, table_name=table)