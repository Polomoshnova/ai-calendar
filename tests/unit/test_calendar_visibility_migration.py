import importlib.util
from pathlib import Path
from types import ModuleType
from unittest.mock import Mock

from app.models import CalendarSelection


def load_migration() -> ModuleType:
    path = (
        Path(__file__).parents[2]
        / "migrations"
        / "versions"
        / "20261103_13_calendar_day_visibility.py"
    )
    spec = importlib.util.spec_from_file_location("calendar_visibility_migration", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_upgrade_adds_backfills_and_enforces_non_null_column() -> None:
    migration = load_migration()
    operations: list[str] = []
    migration.op.add_column = Mock(side_effect=lambda *_args: operations.append("add"))
    migration.op.execute = Mock(
        side_effect=lambda *_args: operations.append("backfill")
    )
    migration.op.alter_column = Mock(
        side_effect=lambda *_args, **_kwargs: operations.append("alter")
    )

    migration.upgrade()

    assert operations == ["add", "backfill", "alter"]
    column = migration.op.add_column.call_args.args[1]
    assert column.name == "include_in_day_view"
    assert column.nullable is True
    statement = str(migration.op.execute.call_args.args[0])
    assert "include_in_day_view = include_in_availability" in statement
    alter_args = migration.op.alter_column.call_args
    assert alter_args.args == ("calendar_selections", "include_in_day_view")
    assert isinstance(alter_args.kwargs["existing_type"], migration.sa.Boolean)
    assert alter_args.kwargs["nullable"] is False
    assert str(alter_args.kwargs["server_default"]) == "false"


def test_model_column_is_non_null_with_server_default() -> None:
    column = CalendarSelection.__table__.c.include_in_day_view

    assert column.nullable is False
    assert column.server_default is not None


def test_downgrade_removes_visibility_column() -> None:
    migration = load_migration()
    migration.op.drop_column = Mock()

    migration.downgrade()

    migration.op.drop_column.assert_called_once_with(
        "calendar_selections", "include_in_day_view"
    )
