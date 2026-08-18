"""Link schedule plans to backlog entries.

Revision ID: 20260818_12
Revises: 20260802_11
Create Date: 2026-08-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260818_12"
down_revision: str | None = "20260802_11"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        "uq_schedule_plans_task_version",
        "schedule_plans",
        type_="unique",
    )
    op.add_column(
        "schedule_plans",
        sa.Column("backlog_entry_id", sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        "fk_schedule_plans_backlog_entry_id",
        "schedule_plans",
        "backlog_entries",
        ["backlog_entry_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_schedule_plans_backlog_entry_id",
        "schedule_plans",
        ["backlog_entry_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_schedule_plans_backlog_entry_id", table_name="schedule_plans")
    op.drop_constraint(
        "fk_schedule_plans_backlog_entry_id",
        "schedule_plans",
        type_="foreignkey",
    )
    op.drop_column("schedule_plans", "backlog_entry_id")
    op.create_unique_constraint(
        "uq_schedule_plans_task_version",
        "schedule_plans",
        ["task_id", "version"],
    )
