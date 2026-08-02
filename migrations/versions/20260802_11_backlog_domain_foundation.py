"""Add backlog domain foundation.

Revision ID: 20260802_11
Revises: 20260731_10
Create Date: 2026-08-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260802_11"
down_revision: str | None = "20260731_10"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    backlog_status = postgresql.ENUM(
        "active",
        "deferred",
        "resolved",
        "cancelled",
        name="backlog_entry_status",
        create_type=False,
    )
    backlog_reason = postgresql.ENUM(
        "no_deadline",
        "no_available_slot",
        "insufficient_capacity",
        "planning_horizon_exceeded",
        "awaiting_user_confirmation",
        "manual_defer",
        "partially_scheduled",
        "other",
        name="backlog_entry_reason",
        create_type=False,
    )
    backlog_origin = postgresql.ENUM(
        "user",
        "scheduler",
        "system",
        "calendar_sync",
        name="backlog_entry_origin",
        create_type=False,
    )
    op.execute(
        "CREATE TYPE backlog_entry_status AS ENUM "
        "('active', 'deferred', 'resolved', 'cancelled')"
    )
    op.execute(
        "CREATE TYPE backlog_entry_reason AS ENUM "
        "('no_deadline', 'no_available_slot', 'insufficient_capacity', "
        "'planning_horizon_exceeded', 'awaiting_user_confirmation', "
        "'manual_defer', 'partially_scheduled', 'other')"
    )
    op.execute(
        "CREATE TYPE backlog_entry_origin AS ENUM "
        "('user', 'scheduler', 'system', 'calendar_sync')"
    )
    op.create_table(
        "backlog_entries",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("task_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", backlog_status, nullable=False),
        sa.Column("origin", backlog_origin, nullable=False),
        sa.Column("reason", backlog_reason, nullable=False),
        sa.Column("remaining_duration_minutes", sa.Integer(), nullable=False),
        sa.Column(
            "entered_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("next_review_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "last_scheduling_attempt_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column(
            "scheduling_attempt_count",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
        sa.Column("deferred_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
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
            "remaining_duration_minutes >= 0",
            name="ck_backlog_entries_nonnegative_remaining",
        ),
        sa.CheckConstraint(
            "scheduling_attempt_count >= 0",
            name="ck_backlog_entries_nonnegative_attempt_count",
        ),
        sa.CheckConstraint(
            "(status IN ('active', 'deferred') AND "
            "remaining_duration_minutes > 0 AND resolved_at IS NULL) OR "
            "(status = 'resolved' AND remaining_duration_minutes = 0 "
            "AND resolved_at IS NOT NULL) OR "
            "(status = 'cancelled' AND remaining_duration_minutes = 0 "
            "AND resolved_at IS NULL)",
            name="ck_backlog_entries_status_resolution",
        ),
        sa.CheckConstraint(
            "status <> 'deferred' OR deferred_until IS NOT NULL "
            "OR next_review_at IS NOT NULL",
            name="ck_backlog_entries_deferred_review",
        ),
        sa.CheckConstraint(
            "reason <> 'other' OR (note IS NOT NULL AND length(trim(note)) > 0)",
            name="ck_backlog_entries_other_note",
        ),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_backlog_entries_user_id", "backlog_entries", ["user_id"])
    op.create_index("ix_backlog_entries_task_id", "backlog_entries", ["task_id"])
    op.create_index("ix_backlog_entries_status", "backlog_entries", ["status"])
    op.create_index(
        "ix_backlog_entries_next_review_at", "backlog_entries", ["next_review_at"]
    )
    op.create_index(
        "ix_backlog_entries_deferred_until", "backlog_entries", ["deferred_until"]
    )
    op.create_index(
        "uq_backlog_entries_open_task",
        "backlog_entries",
        ["task_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('active', 'deferred')"),
    )


def downgrade() -> None:
    populated = (
        op.get_bind().execute(sa.text("SELECT 1 FROM backlog_entries LIMIT 1")).first()
    )
    if populated is not None:
        raise RuntimeError(
            "Cannot downgrade backlog foundation while backlog data exists."
        )
    op.drop_table("backlog_entries")
    postgresql.ENUM(name="backlog_entry_reason").drop(op.get_bind())
    postgresql.ENUM(name="backlog_entry_origin").drop(op.get_bind())
    postgresql.ENUM(name="backlog_entry_status").drop(op.get_bind())
