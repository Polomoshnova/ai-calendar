"""Add scheduling task fields and structured user preferences.

Revision ID: 20260722_02
Revises: 20260722_01
Create Date: 2026-07-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260722_02"
down_revision: str | None = "20260722_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    preferred_time = postgresql.ENUM(
        "any",
        "morning",
        "afternoon",
        "evening",
        name="preferred_time_of_day",
        create_type=False,
    )
    preferred_time.create(op.get_bind())

    op.add_column(
        "tasks",
        sa.Column(
            "preferred_time_of_day",
            preferred_time,
            server_default="any",
            nullable=False,
        ),
    )
    op.add_column(
        "tasks",
        sa.Column(
            "is_splittable", sa.Boolean(), server_default=sa.false(), nullable=False
        ),
    )
    op.add_column(
        "tasks",
        sa.Column(
            "minimum_session_minutes",
            sa.Integer(),
            server_default="15",
            nullable=False,
        ),
    )
    op.add_column(
        "tasks",
        sa.Column(
            "maximum_sessions_per_day",
            sa.Integer(),
            server_default="1",
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "ck_tasks_positive_minimum_session", "tasks", "minimum_session_minutes > 0"
    )
    op.create_check_constraint(
        "ck_tasks_positive_maximum_sessions",
        "tasks",
        "maximum_sessions_per_day > 0",
    )
    op.create_check_constraint(
        "ck_tasks_splittable_minimum_session",
        "tasks",
        "NOT is_splittable OR minimum_session_minutes <= duration_minutes",
    )
    for column in (
        "preferred_time_of_day",
        "is_splittable",
        "minimum_session_minutes",
        "maximum_sessions_per_day",
    ):
        op.alter_column("tasks", column, server_default=None)

    op.add_column(
        "user_preferences",
        sa.Column("working_hours", postgresql.JSONB(), nullable=True),
    )
    op.execute(
        """
        UPDATE user_preferences
        SET working_hours = jsonb_build_object(
            'monday', jsonb_build_array(jsonb_build_object(
                'start', to_char(workday_start, 'HH24:MI'),
                'end', to_char(workday_end, 'HH24:MI'))),
            'tuesday', jsonb_build_array(jsonb_build_object(
                'start', to_char(workday_start, 'HH24:MI'),
                'end', to_char(workday_end, 'HH24:MI'))),
            'wednesday', jsonb_build_array(jsonb_build_object(
                'start', to_char(workday_start, 'HH24:MI'),
                'end', to_char(workday_end, 'HH24:MI'))),
            'thursday', jsonb_build_array(jsonb_build_object(
                'start', to_char(workday_start, 'HH24:MI'),
                'end', to_char(workday_end, 'HH24:MI'))),
            'friday', jsonb_build_array(jsonb_build_object(
                'start', to_char(workday_start, 'HH24:MI'),
                'end', to_char(workday_end, 'HH24:MI'))),
            'saturday', jsonb_build_array(),
            'sunday', jsonb_build_array()
        )
        """
    )
    op.alter_column("user_preferences", "working_hours", nullable=False)
    op.add_column(
        "user_preferences",
        sa.Column(
            "preferred_task_time",
            preferred_time,
            server_default="any",
            nullable=False,
        ),
    )
    op.add_column(
        "user_preferences",
        sa.Column(
            "minimum_break_minutes",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
    )
    op.add_column(
        "user_preferences",
        sa.Column("no_deep_work_after", sa.Time(), nullable=True),
    )
    op.add_column(
        "user_preferences",
        sa.Column(
            "default_minimum_session_minutes",
            sa.Integer(),
            server_default="15",
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "ck_user_preferences_nonnegative_break",
        "user_preferences",
        "minimum_break_minutes >= 0",
    )
    op.create_check_constraint(
        "ck_user_preferences_positive_default_session",
        "user_preferences",
        "default_minimum_session_minutes > 0",
    )
    for column in (
        "preferred_task_time",
        "minimum_break_minutes",
        "default_minimum_session_minutes",
    ):
        op.alter_column("user_preferences", column, server_default=None)
    op.drop_column("user_preferences", "workday_end")
    op.drop_column("user_preferences", "workday_start")


def downgrade() -> None:
    op.add_column(
        "user_preferences",
        sa.Column("workday_start", sa.Time(), server_default="09:00", nullable=False),
    )
    op.add_column(
        "user_preferences",
        sa.Column("workday_end", sa.Time(), server_default="17:00", nullable=False),
    )
    op.execute(
        """
        UPDATE user_preferences
        SET workday_start = COALESCE(
                (working_hours->'monday'->0->>'start')::time, '09:00'::time),
            workday_end = COALESCE(
                (working_hours->'monday'->0->>'end')::time, '17:00'::time)
        """
    )
    op.alter_column("user_preferences", "workday_start", server_default=None)
    op.alter_column("user_preferences", "workday_end", server_default=None)
    op.drop_constraint(
        "ck_user_preferences_positive_default_session",
        "user_preferences",
        type_="check",
    )
    op.drop_constraint(
        "ck_user_preferences_nonnegative_break",
        "user_preferences",
        type_="check",
    )
    op.drop_column("user_preferences", "default_minimum_session_minutes")
    op.drop_column("user_preferences", "no_deep_work_after")
    op.drop_column("user_preferences", "minimum_break_minutes")
    op.drop_column("user_preferences", "preferred_task_time")
    op.drop_column("user_preferences", "working_hours")

    op.drop_constraint("ck_tasks_splittable_minimum_session", "tasks", type_="check")
    op.drop_constraint("ck_tasks_positive_maximum_sessions", "tasks", type_="check")
    op.drop_constraint("ck_tasks_positive_minimum_session", "tasks", type_="check")
    op.drop_column("tasks", "maximum_sessions_per_day")
    op.drop_column("tasks", "minimum_session_minutes")
    op.drop_column("tasks", "is_splittable")
    op.drop_column("tasks", "preferred_time_of_day")

    postgresql.ENUM(name="preferred_time_of_day").drop(op.get_bind())
