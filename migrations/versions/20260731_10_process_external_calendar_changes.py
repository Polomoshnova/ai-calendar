"""Add external calendar change processing persistence.

Revision ID: 20260731_10
Revises: 20260730_09
Create Date: 2026-07-31
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260731_10"
down_revision: str | None = "20260730_09"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    processing_status = postgresql.ENUM(
        "pending",
        "processing",
        "processed",
        "failed",
        name="external_calendar_change_processing_status",
    )
    processing_status.create(op.get_bind())
    op.add_column(
        "external_calendar_changes",
        sa.Column(
            "processing_status",
            processing_status,
            nullable=False,
            server_default="pending",
        ),
    )
    op.add_column(
        "external_calendar_changes",
        sa.Column(
            "processing_result",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_external_calendar_changes_processing_status",
        "external_calendar_changes",
        ["processing_status"],
    )

    op.create_table(
        "task_deadline_history",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("task_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "external_calendar_change_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("previous_deadline", sa.DateTime(timezone=True), nullable=True),
        sa.Column("new_deadline", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason", sa.String(length=100), nullable=False),
        sa.Column(
            "changed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["external_calendar_change_id"],
            ["external_calendar_changes.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "external_calendar_change_id",
            name="uq_task_deadline_history_external_change",
        ),
    )
    op.create_index(
        "ix_task_deadline_history_task_id", "task_deadline_history", ["task_id"]
    )

    op.create_table(
        "external_calendar_consistency_findings",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "external_calendar_change_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("schedule_plan_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("scheduled_session_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("code", sa.String(length=100), nullable=False),
        sa.Column("severity", sa.String(length=30), nullable=False),
        sa.Column("identity_key", sa.String(length=500), nullable=False),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "detected_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["external_calendar_change_id"],
            ["external_calendar_changes.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["schedule_plan_id"], ["schedule_plans.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["scheduled_session_id"],
            ["scheduled_sessions.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "external_calendar_change_id",
            "code",
            "identity_key",
            name="uq_external_calendar_finding_identity",
        ),
    )
    op.create_index(
        "ix_external_calendar_findings_change",
        "external_calendar_consistency_findings",
        ["external_calendar_change_id"],
    )
    op.create_index(
        "ix_external_calendar_findings_plan",
        "external_calendar_consistency_findings",
        ["schedule_plan_id"],
    )


def downgrade() -> None:
    populated = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT 1 FROM external_calendar_changes "
                "WHERE processing_status <> 'pending' OR processing_result IS NOT NULL "
                "UNION ALL SELECT 1 FROM task_deadline_history "
                "UNION ALL SELECT 1 FROM external_calendar_consistency_findings LIMIT 1"
            )
        )
        .first()
    )
    if populated is not None:
        raise RuntimeError(
            "Cannot downgrade external calendar processing while processing "
            "data exists."
        )
    op.drop_table("external_calendar_consistency_findings")
    op.drop_table("task_deadline_history")
    op.drop_index(
        "ix_external_calendar_changes_processing_status",
        table_name="external_calendar_changes",
    )
    op.drop_column("external_calendar_changes", "processing_result")
    op.drop_column("external_calendar_changes", "processing_status")
    postgresql.ENUM(
        "pending",
        "processing",
        "processed",
        "failed",
        name="external_calendar_change_processing_status",
    ).drop(op.get_bind())
