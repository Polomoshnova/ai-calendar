from pathlib import Path


def test_pull_sync_migration_has_expected_chain_and_safe_nullable_baseline() -> None:
    path = (
        Path(__file__).parents[2]
        / "migrations"
        / "versions"
        / "20260730_09_pull_calendar_event_sync.py"
    )
    source = path.read_text()

    assert 'revision: str = "20260730_09"' in source
    assert 'down_revision: str | None = "20260730_08"' in source
    assert '"last_synced_snapshot"' in source
    assert '"last_synced_snapshot_hash"' in source
    assert '"transition_hash"' in source
    assert "nullable=True" in source
    assert '"uq_external_calendar_changes_mapping_transition"' in source
    assert "Cannot downgrade pull calendar synchronization" in source
