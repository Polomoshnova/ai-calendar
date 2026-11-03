"""Add independent Calendar Day visibility selection.

Revision ID: 20261103_13
Revises: 20260818_12
Create Date: 2026-11-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261103_13"
down_revision: str | None = "20260818_12"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "calendar_selections",
        sa.Column(
            "include_in_day_view",
            sa.Boolean(),
            nullable=True,
            server_default=sa.false(),
        ),
    )
    op.execute(
        sa.text(
            """
            UPDATE calendar_selections
            SET include_in_day_view = include_in_availability
            """
        )
    )
    op.alter_column(
        "calendar_selections",
        "include_in_day_view",
        existing_type=sa.Boolean(),
        nullable=False,
        server_default=sa.false(),
    )


def downgrade() -> None:
    op.drop_column("calendar_selections", "include_in_day_view")
