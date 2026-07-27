"""Add read-only Google Calendar connections.

Revision ID: 20260726_04
Revises: 20260722_03
Create Date: 2026-07-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260726_04"
down_revision: str | None = "20260722_03"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

provider_enum = postgresql.ENUM("google", name="calendar_provider", create_type=False)
status_enum = postgresql.ENUM(
    "active",
    "expired",
    "revoked",
    "error",
    name="calendar_connection_status",
    create_type=False,
)


def upgrade() -> None:
    provider_enum.create(op.get_bind(), checkfirst=True)
    status_enum.create(op.get_bind(), checkfirst=True)
    op.create_table(
        "calendar_connections",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider", provider_enum, nullable=False),
        sa.Column("provider_account_id", sa.String(length=255), nullable=True),
        sa.Column("provider_account_email", sa.String(length=320), nullable=True),
        sa.Column("access_token_encrypted", sa.Text(), nullable=True),
        sa.Column("refresh_token_encrypted", sa.Text(), nullable=True),
        sa.Column("token_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("scopes", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("status", status_enum, nullable=False, server_default="active"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("last_successful_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(length=100), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id", "provider", name="uq_calendar_connection_user_provider"
        ),
    )
    op.create_index(
        "ix_calendar_connections_user_id",
        "calendar_connections",
        ["user_id"],
    )
    op.create_table(
        "calendar_oauth_states",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("state_hash", sa.String(length=64), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider", provider_enum, nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_calendar_oauth_states_state_hash",
        "calendar_oauth_states",
        ["state_hash"],
        unique=True,
    )
    op.create_index(
        "ix_calendar_oauth_states_user_id",
        "calendar_oauth_states",
        ["user_id"],
    )
    op.create_table(
        "calendar_selections",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("connection_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("external_calendar_id", sa.String(length=1024), nullable=False),
        sa.Column("display_name", sa.String(length=500), nullable=False),
        sa.Column("timezone", sa.String(length=64), nullable=True),
        sa.Column("primary", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "include_in_availability",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["connection_id"], ["calendar_connections.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "connection_id",
            "external_calendar_id",
            name="uq_calendar_selection_connection_external",
        ),
    )
    op.create_index(
        "ix_calendar_selections_connection_id",
        "calendar_selections",
        ["connection_id"],
    )


def downgrade() -> None:
    op.drop_table("calendar_selections")
    op.drop_table("calendar_oauth_states")
    op.drop_table("calendar_connections")
    status_enum.drop(op.get_bind(), checkfirst=True)
    provider_enum.drop(op.get_bind(), checkfirst=True)
