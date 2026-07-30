"""Add persistent schedule plans and scheduled sessions.

Revision ID: 20260727_05
Revises: 20260726_04
Create Date: 2026-07-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260727_05"
down_revision: str | None = "20260726_04"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

schedule_plan_source = postgresql.ENUM(
    "manual_preview",
    "ai_workflow",
    "calendar_backed_preview",
    name="schedule_plan_source",
    create_type=False,
)
schedule_plan_status = postgresql.ENUM(
    "proposed",
    "confirmed",
    "obsolete",
    "revalidation_required",
    "applying",
    "applied",
    "partially_applied",
    "failed",
    name="schedule_plan_status",
    create_type=False,
)
scheduled_session_status = postgresql.ENUM(
    "proposed",
    "confirmed",
    "applying",
    "applied",
    "failed",
    "obsolete",
    name="scheduled_session_status",
    create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()
    schedule_plan_source.create(bind, checkfirst=True)
    schedule_plan_status.create(bind, checkfirst=True)
    scheduled_session_status.create(bind, checkfirst=True)

    op.create_table(
        "schedule_plans",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("task_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("plan_group_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source", schedule_plan_source, nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", schedule_plan_status, nullable=False),
        sa.Column("timezone", sa.String(length=64), nullable=False),
        sa.Column("planning_window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("planning_window_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "source_calendar_snapshot_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column("scheduler_version", sa.String(length=100), nullable=False),
        sa.Column("workflow_version", sa.String(length=100), nullable=True),
        sa.Column("confirmation_note", sa.Text(), nullable=True),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column(
            "confirmed_task_snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "scheduling_preferences_snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "busy_context_summary",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "preview_metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
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
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_code", sa.String(length=100), nullable=True),
        sa.CheckConstraint(
            "version > 0",
            name="ck_schedule_plans_positive_version",
        ),
        sa.CheckConstraint(
            "planning_window_start < planning_window_end",
            name="ck_schedule_plans_valid_window",
        ),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["tasks.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "idempotency_key",
            name="uq_schedule_plans_idempotency_key",
        ),
        sa.UniqueConstraint(
            "plan_group_id",
            "version",
            name="uq_schedule_plans_group_version",
        ),
        sa.UniqueConstraint(
            "task_id",
            "version",
            name="uq_schedule_plans_task_version",
        ),
    )
    op.create_index(
        op.f("ix_schedule_plans_status"),
        "schedule_plans",
        ["status"],
        unique=False,
    )
    op.create_index(
        op.f("ix_schedule_plans_task_id"),
        "schedule_plans",
        ["task_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_schedule_plans_user_id"),
        "schedule_plans",
        ["user_id"],
        unique=False,
    )

    op.create_table(
        "scheduled_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("plan_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("task_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("step_order", sa.Integer(), nullable=True),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("duration_minutes", sa.Integer(), nullable=False),
        sa.Column("order", sa.Integer(), nullable=False),
        sa.Column("status", scheduled_session_status, nullable=False),
        sa.Column("external_provider", sa.String(length=50), nullable=True),
        sa.Column("external_calendar_id", sa.Text(), nullable=True),
        sa.Column("external_event_id", sa.Text(), nullable=True),
        sa.Column("provider_etag", sa.Text(), nullable=True),
        sa.Column("failure_code", sa.String(length=100), nullable=True),
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
        sa.CheckConstraint(
            "\"end\" - start = duration_minutes * interval '1 minute'",
            name="ck_scheduled_sessions_duration_matches_interval",
        ),
        sa.CheckConstraint(
            '"order" > 0',
            name="ck_scheduled_sessions_positive_order",
        ),
        sa.CheckConstraint(
            "duration_minutes > 0",
            name="ck_scheduled_sessions_positive_duration",
        ),
        sa.CheckConstraint(
            'start < "end"',
            name="ck_scheduled_sessions_valid_interval",
        ),
        sa.ForeignKeyConstraint(
            ["plan_id"],
            ["schedule_plans.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["tasks.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "plan_id",
            "order",
            name="uq_scheduled_sessions_plan_order",
        ),
    )
    op.create_index(
        op.f("ix_scheduled_sessions_plan_id"),
        "scheduled_sessions",
        ["plan_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_scheduled_sessions_status"),
        "scheduled_sessions",
        ["status"],
        unique=False,
    )
    op.create_index(
        op.f("ix_scheduled_sessions_task_id"),
        "scheduled_sessions",
        ["task_id"],
        unique=False,
    )
    op.create_index(
        "uq_scheduled_sessions_external_event",
        "scheduled_sessions",
        ["external_provider", "external_calendar_id", "external_event_id"],
        unique=True,
        postgresql_where=sa.text("external_event_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_scheduled_sessions_external_event",
        table_name="scheduled_sessions",
        postgresql_where=sa.text("external_event_id IS NOT NULL"),
    )
    op.drop_index(
        op.f("ix_scheduled_sessions_task_id"),
        table_name="scheduled_sessions",
    )
    op.drop_index(
        op.f("ix_scheduled_sessions_status"),
        table_name="scheduled_sessions",
    )
    op.drop_index(
        op.f("ix_scheduled_sessions_plan_id"),
        table_name="scheduled_sessions",
    )
    op.drop_table("scheduled_sessions")
    op.drop_index(op.f("ix_schedule_plans_user_id"), table_name="schedule_plans")
    op.drop_index(op.f("ix_schedule_plans_task_id"), table_name="schedule_plans")
    op.drop_index(op.f("ix_schedule_plans_status"), table_name="schedule_plans")
    op.drop_table("schedule_plans")

    bind = op.get_bind()
    scheduled_session_status.drop(bind, checkfirst=True)
    schedule_plan_status.drop(bind, checkfirst=True)
    schedule_plan_source.drop(bind, checkfirst=True)
