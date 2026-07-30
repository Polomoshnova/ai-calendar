"""Allow multiple Google Calendar connections per user.

Revision ID: 20260730_08
Revises: 20260728_07
Create Date: 2026-07-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260730_08"
down_revision: str | None = "20260728_07"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_CONSTRAINT = "uq_calendar_connection_user_provider"
NEW_CONSTRAINT = "uq_calendar_connection_user_provider_account"


def upgrade() -> None:
    op.drop_constraint(
        OLD_CONSTRAINT,
        "calendar_connections",
        type_="unique",
    )
    op.create_unique_constraint(
        NEW_CONSTRAINT,
        "calendar_connections",
        ["user_id", "provider", "provider_account_id"],
    )


def downgrade() -> None:
    duplicate = (
        op.get_bind()
        .execute(
            sa.text(
                """
            SELECT user_id, provider
            FROM calendar_connections
            GROUP BY user_id, provider
            HAVING count(*) > 1
            LIMIT 1
            """
            )
        )
        .first()
    )
    if duplicate is not None:
        raise RuntimeError(
            "Cannot downgrade multi-account Google Calendar connections: "
            "more than one connection exists for a user/provider pair."
        )
    op.drop_constraint(
        NEW_CONSTRAINT,
        "calendar_connections",
        type_="unique",
    )
    op.create_unique_constraint(
        OLD_CONSTRAINT,
        "calendar_connections",
        ["user_id", "provider"],
    )
