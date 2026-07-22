"""Add cancelled task status.

Revision ID: 20260722_03
Revises: 20260722_02
Create Date: 2026-07-22
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260722_03"
down_revision: str | None = "20260722_02"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TYPE task_status ADD VALUE IF NOT EXISTS 'cancelled'")


def downgrade() -> None:
    op.execute("CREATE TYPE task_status_previous AS ENUM ('pending', 'completed')")
    op.execute(
        """
        ALTER TABLE tasks
        ALTER COLUMN status TYPE task_status_previous
        USING (
            CASE
                WHEN status::text = 'cancelled' THEN 'pending'
                ELSE status::text
            END
        )::task_status_previous
        """
    )
    op.execute("DROP TYPE task_status")
    op.execute("ALTER TYPE task_status_previous RENAME TO task_status")
