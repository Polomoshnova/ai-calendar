from pathlib import Path


def test_processing_migration_has_expected_chain_and_idempotency_constraints() -> None:
    path = (
        Path(__file__).parents[2]
        / "migrations"
        / "versions"
        / "20260731_10_process_external_calendar_changes.py"
    )
    source = path.read_text()

    assert 'revision: str = "20260731_10"' in source
    assert 'down_revision: str | None = "20260730_09"' in source
    assert 'server_default="pending"' in source
    assert '"processing_result"' in source
    assert '"task_deadline_history"' in source
    assert '"external_calendar_consistency_findings"' in source
    assert '"uq_task_deadline_history_external_change"' in source
    assert '"uq_external_calendar_finding_identity"' in source
    assert "Cannot downgrade external calendar processing" in source
