from pathlib import Path


def test_backlog_migration_defines_expected_schema_and_chain() -> None:
    path = (
        Path(__file__).parents[2]
        / "migrations"
        / "versions"
        / "20260802_11_backlog_domain_foundation.py"
    )
    source = path.read_text()
    assert 'revision: str = "20260802_11"' in source
    assert 'down_revision: str | None = "20260731_10"' in source
    assert '"backlog_entries"' in source
    assert '"backlog_entry_origin"' in source
    assert 'sa.Column("origin", backlog_origin, nullable=False)' in source
    assert "calendar_unavailable" not in source
    assert "ck_backlog_entries_other_note" in source
    assert '"uq_backlog_entries_open_task"' in source
    assert "postgresql_where" in source
    assert "Cannot downgrade backlog foundation" in source
    for index in (
        "ix_backlog_entries_user_id",
        "ix_backlog_entries_task_id",
        "ix_backlog_entries_status",
        "ix_backlog_entries_next_review_at",
        "ix_backlog_entries_deferred_until",
    ):
        assert index in source
