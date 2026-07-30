import importlib.util
from pathlib import Path
from types import ModuleType
from unittest.mock import Mock

import pytest


def load_migration() -> ModuleType:
    path = (
        Path(__file__).parents[2]
        / "migrations"
        / "versions"
        / "20260730_08_multi_account_google_connections.py"
    )
    spec = importlib.util.spec_from_file_location("multi_account_migration", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_upgrade_replaces_only_the_uniqueness_constraint() -> None:
    migration = load_migration()
    migration.op.drop_constraint = Mock()
    migration.op.create_unique_constraint = Mock()

    migration.upgrade()

    migration.op.drop_constraint.assert_called_once_with(
        "uq_calendar_connection_user_provider",
        "calendar_connections",
        type_="unique",
    )
    migration.op.create_unique_constraint.assert_called_once_with(
        "uq_calendar_connection_user_provider_account",
        "calendar_connections",
        ["user_id", "provider", "provider_account_id"],
    )


def test_downgrade_restores_old_constraint_when_representable() -> None:
    migration = load_migration()
    result = Mock()
    result.first.return_value = None
    bind = Mock()
    bind.execute.return_value = result
    migration.op.get_bind = Mock(return_value=bind)
    migration.op.drop_constraint = Mock()
    migration.op.create_unique_constraint = Mock()

    migration.downgrade()

    migration.op.drop_constraint.assert_called_once_with(
        "uq_calendar_connection_user_provider_account",
        "calendar_connections",
        type_="unique",
    )
    migration.op.create_unique_constraint.assert_called_once_with(
        "uq_calendar_connection_user_provider",
        "calendar_connections",
        ["user_id", "provider"],
    )


def test_downgrade_fails_before_schema_changes_for_multiple_accounts() -> None:
    migration = load_migration()
    result = Mock()
    result.first.return_value = ("user-id", "google")
    bind = Mock()
    bind.execute.return_value = result
    migration.op.get_bind = Mock(return_value=bind)
    migration.op.drop_constraint = Mock()
    migration.op.create_unique_constraint = Mock()

    with pytest.raises(RuntimeError, match="more than one connection"):
        migration.downgrade()

    migration.op.drop_constraint.assert_not_called()
    migration.op.create_unique_constraint.assert_not_called()
