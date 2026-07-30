"""Add schedule plan revalidation audits.

Revision ID: 20260728_06
Revises: 20260727_05
Create Date: 2026-07-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260728_06"
down_revision: str | None = "20260727_05"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

revalidation_status = postgresql.ENUM(
    "pending",
    "valid",
    "conflict",
    "provider_partial_failure",
    "provider_failure",
    "invalid_plan_state",
    name="schedule_plan_revalidation_status",
    create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()
    revalidation_status.create(bind, checkfirst=True)
    op.create_table(
        "schedule_plan_revalidations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("plan_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("connection_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("status", revalidation_status, nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("planning_window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("planning_window_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_snapshot_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("provider_busy_interval_count", sa.Integer(), nullable=False),
        sa.Column("merged_busy_interval_count", sa.Integer(), nullable=False),
        sa.Column("conflicting_session_count", sa.Integer(), nullable=False),
        sa.Column(
            "diagnostics",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("provider_request_id", sa.String(length=255), nullable=True),
        sa.Column("request_id", sa.String(length=255), nullable=True),
        sa.Column("failure_code", sa.String(length=100), nullable=True),
        sa.Column("failure_message", sa.Text(), nullable=True),
        sa.Column("plan_version", sa.Integer(), nullable=False),
        sa.Column("plan_updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sessions_hash", sa.String(length=64), nullable=False),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["connection_id"],
            ["calendar_connections.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["plan_id"],
            ["schedule_plans.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "plan_id",
            "request_id",
            name="uq_schedule_plan_revalidations_plan_request",
        ),
    )
    op.create_index(
        op.f("ix_schedule_plan_revalidations_connection_id"),
        "schedule_plan_revalidations",
        ["connection_id"],
        unique=False,
    )
    op.create_index(
        "ix_schedule_plan_revalidations_checked_at",
        "schedule_plan_revalidations",
        ["checked_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_schedule_plan_revalidations_plan_id"),
        "schedule_plan_revalidations",
        ["plan_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_schedule_plan_revalidations_status"),
        "schedule_plan_revalidations",
        ["status"],
        unique=False,
    )
    op.create_index(
        op.f("ix_schedule_plan_revalidations_user_id"),
        "schedule_plan_revalidations",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        "ix_schedule_plan_revalidations_valid_until",
        "schedule_plan_revalidations",
        ["valid_until"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_schedule_plan_revalidations_valid_until",
        table_name="schedule_plan_revalidations",
    )
    op.drop_index(
        op.f("ix_schedule_plan_revalidations_user_id"),
        table_name="schedule_plan_revalidations",
    )
    op.drop_index(
        op.f("ix_schedule_plan_revalidations_status"),
        table_name="schedule_plan_revalidations",
    )
    op.drop_index(
        op.f("ix_schedule_plan_revalidations_plan_id"),
        table_name="schedule_plan_revalidations",
    )
    op.drop_index(
        "ix_schedule_plan_revalidations_checked_at",
        table_name="schedule_plan_revalidations",
    )
    op.drop_index(
        op.f("ix_schedule_plan_revalidations_connection_id"),
        table_name="schedule_plan_revalidations",
    )
    op.drop_table("schedule_plan_revalidations")
    revalidation_status.drop(op.get_bind(), checkfirst=True)
