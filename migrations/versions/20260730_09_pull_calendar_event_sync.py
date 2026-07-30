"""Add pull calendar event synchronization baseline and idempotency.

Revision ID: 20260730_09
Revises: 20260730_08
Create Date: 2026-07-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260730_09"
down_revision: str | None = "20260730_08"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "calendar_event_mappings",
        sa.Column(
            "last_synced_snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.add_column(
        "calendar_event_mappings",
        sa.Column("last_synced_snapshot_hash", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "external_calendar_changes",
        sa.Column("transition_hash", sa.String(length=64), nullable=True),
    )
    op.create_unique_constraint(
        "uq_external_calendar_changes_mapping_transition",
        "external_calendar_changes",
        ["mapping_id", "transition_hash"],
    )


def downgrade() -> None:
    populated = (
        op.get_bind()
        .execute(
            sa.text(
                """
                SELECT 1
                FROM calendar_event_mappings
                WHERE last_synced_snapshot IS NOT NULL
                   OR last_synced_snapshot_hash IS NOT NULL
                UNION ALL
                SELECT 1
                FROM external_calendar_changes
                WHERE transition_hash IS NOT NULL
                LIMIT 1
                """
            )
        )
        .first()
    )
    if populated is not None:
        raise RuntimeError(
            "Cannot downgrade pull calendar synchronization while persisted "
            "snapshot or transition-hash data exists."
        )
    op.drop_constraint(
        "uq_external_calendar_changes_mapping_transition",
        "external_calendar_changes",
        type_="unique",
    )
    op.drop_column("external_calendar_changes", "transition_hash")
    op.drop_column("calendar_event_mappings", "last_synced_snapshot_hash")
    op.drop_column("calendar_event_mappings", "last_synced_snapshot")
