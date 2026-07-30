"""Add calendar synchronization domain foundation.

Revision ID: 20260728_07
Revises: 20260728_06
Create Date: 2026-07-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260728_07"
down_revision: str | None = "20260728_06"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

sync_status = postgresql.ENUM(
    "pending",
    "synced",
    "failed",
    "externally_deleted",
    name="calendar_event_sync_status",
    create_type=False,
)
external_change_type = postgresql.ENUM(
    "created",
    "updated",
    "moved",
    "deleted",
    name="external_calendar_change_type",
    create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()
    sync_status.create(bind, checkfirst=True)
    external_change_type.create(bind, checkfirst=True)

    op.add_column(
        "schedule_plans",
        sa.Column(
            "busy_sources_snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.add_column(
        "schedule_plans",
        sa.Column(
            "write_targets_snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.add_column(
        "schedule_plans",
        sa.Column("calendar_selection_hash", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "schedule_plans",
        sa.Column(
            "calendar_context_captured_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )

    op.create_table(
        "calendar_event_mappings",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "scheduled_session_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column(
            "calendar_connection_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column(
            "provider",
            postgresql.ENUM(
                "google",
                name="calendar_provider",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column("provider_account_id", sa.String(length=255), nullable=True),
        sa.Column("calendar_id", sa.Text(), nullable=False),
        sa.Column("external_event_id", sa.Text(), nullable=False),
        sa.Column("etag", sa.Text(), nullable=True),
        sa.Column("provider_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sync_status", sync_status, nullable=False),
        sa.Column("last_sync_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sync_error_code", sa.String(length=100), nullable=True),
        sa.Column("sync_error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["calendar_connection_id"],
            ["calendar_connections.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["scheduled_session_id"],
            ["scheduled_sessions.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "calendar_connection_id",
            "calendar_id",
            "external_event_id",
            name="uq_calendar_event_mappings_external_identity",
        ),
        sa.UniqueConstraint(
            "scheduled_session_id",
            name="uq_calendar_event_mappings_scheduled_session",
        ),
    )
    op.create_index(
        op.f("ix_calendar_event_mappings_calendar_connection_id"),
        "calendar_event_mappings",
        ["calendar_connection_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_calendar_event_mappings_scheduled_session_id"),
        "calendar_event_mappings",
        ["scheduled_session_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_calendar_event_mappings_sync_status"),
        "calendar_event_mappings",
        ["sync_status"],
        unique=False,
    )

    op.create_table(
        "external_calendar_changes",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("mapping_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("change_type", external_change_type, nullable=False),
        sa.Column("provider_timestamp", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "detected_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "old_values",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "new_values",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "raw_payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["mapping_id"],
            ["calendar_event_mappings.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_external_calendar_changes_change_type"),
        "external_calendar_changes",
        ["change_type"],
        unique=False,
    )
    op.create_index(
        op.f("ix_external_calendar_changes_mapping_id"),
        "external_calendar_changes",
        ["mapping_id"],
        unique=False,
    )

    op.drop_index(
        "uq_scheduled_sessions_external_event",
        table_name="scheduled_sessions",
        postgresql_where=sa.text("external_event_id IS NOT NULL"),
    )
    op.drop_column("scheduled_sessions", "provider_etag")
    op.drop_column("scheduled_sessions", "external_event_id")
    op.drop_column("scheduled_sessions", "external_calendar_id")
    op.drop_column("scheduled_sessions", "external_provider")


def downgrade() -> None:
    op.add_column(
        "scheduled_sessions",
        sa.Column("external_provider", sa.String(length=50), nullable=True),
    )
    op.add_column(
        "scheduled_sessions",
        sa.Column("external_calendar_id", sa.Text(), nullable=True),
    )
    op.add_column(
        "scheduled_sessions",
        sa.Column("external_event_id", sa.Text(), nullable=True),
    )
    op.add_column(
        "scheduled_sessions",
        sa.Column("provider_etag", sa.Text(), nullable=True),
    )
    op.create_index(
        "uq_scheduled_sessions_external_event",
        "scheduled_sessions",
        ["external_provider", "external_calendar_id", "external_event_id"],
        unique=True,
        postgresql_where=sa.text("external_event_id IS NOT NULL"),
    )

    op.drop_index(
        op.f("ix_external_calendar_changes_mapping_id"),
        table_name="external_calendar_changes",
    )
    op.drop_index(
        op.f("ix_external_calendar_changes_change_type"),
        table_name="external_calendar_changes",
    )
    op.drop_table("external_calendar_changes")
    op.drop_index(
        op.f("ix_calendar_event_mappings_sync_status"),
        table_name="calendar_event_mappings",
    )
    op.drop_index(
        op.f("ix_calendar_event_mappings_scheduled_session_id"),
        table_name="calendar_event_mappings",
    )
    op.drop_index(
        op.f("ix_calendar_event_mappings_calendar_connection_id"),
        table_name="calendar_event_mappings",
    )
    op.drop_table("calendar_event_mappings")
    op.drop_column("schedule_plans", "calendar_context_captured_at")
    op.drop_column("schedule_plans", "calendar_selection_hash")
    op.drop_column("schedule_plans", "write_targets_snapshot")
    op.drop_column("schedule_plans", "busy_sources_snapshot")

    bind = op.get_bind()
    external_change_type.drop(bind, checkfirst=True)
    sync_status.drop(bind, checkfirst=True)
