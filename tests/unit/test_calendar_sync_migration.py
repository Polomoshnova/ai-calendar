from pathlib import Path


def test_calendar_sync_migration_has_expected_chain_and_operations() -> None:
    path = (
        Path(__file__).parents[2]
        / "migrations"
        / "versions"
        / "20260728_05_calendar_sync_domain_foundation.py"
    )
    source = path.read_text()

    assert 'revision: str = "20260728_07"' in source
    assert 'down_revision: str | None = "20260728_06"' in source
    assert '"calendar_event_mappings"' in source
    assert '"external_calendar_changes"' in source
    assert '"busy_sources_snapshot"' in source
    assert 'op.drop_column("scheduled_sessions", "external_event_id")' in source
