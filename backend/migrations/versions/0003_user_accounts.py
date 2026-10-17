"""add dashboard user accounts and browser sessions

Revision ID: 0003_user_accounts
Revises: 0002_analytics_indexes
Create Date: 2026-10-17

The gateway authenticated API keys but had no users, so the dashboard had one
shared credential and usage could not be attributed to a person.

users holds a folded email, a PBKDF2 password hash, is_active and is_admin.
auth_sessions holds one row per browser session, storing only the SHA-256
digest of the token, matching how api_keys stores key_hash.

ix_users_email is UNIQUE because a check-then-insert in application code races
two concurrent signups for the same address; only the constraint prevents the
duplicate. auth_sessions.user_id cascades so deleting a user leaves no tokens
behind that outlive the account. expires_at is indexed because it is on the
read path of every authenticated request and on the cleanup delete.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0003_user_accounts"
down_revision: Union[str, Sequence[str], None] = "0002_analytics_indexes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column(
            "is_admin",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    op.create_table(
        "auth_sessions",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("token_hash", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_auth_sessions_token_hash",
        "auth_sessions",
        ["token_hash"],
        unique=True,
    )
    op.create_index("ix_auth_sessions_user_id", "auth_sessions", ["user_id"])
    op.create_index(
        "ix_auth_sessions_expires_at", "auth_sessions", ["expires_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_auth_sessions_expires_at", table_name="auth_sessions")
    op.drop_index("ix_auth_sessions_user_id", table_name="auth_sessions")
    op.drop_index("ix_auth_sessions_token_hash", table_name="auth_sessions")
    op.drop_table("auth_sessions")
    op.drop_index("ix_users_email", table_name="users")
    op.drop_table("users")