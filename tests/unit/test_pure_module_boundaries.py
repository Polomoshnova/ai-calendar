import ast
from pathlib import Path

FORBIDDEN_IMPORT_ROOTS = {
    "fastapi",
    "sqlalchemy",
    "google",
    "openai",
    "app.core",
    "app.models",
}


def test_availability_and_scheduling_are_infrastructure_independent() -> None:
    project_root = Path(__file__).parents[2]
    source_files = [
        *project_root.glob("app/availability/*.py"),
        *project_root.glob("app/scheduling/*.py"),
    ]

    for source_file in source_files:
        tree = ast.parse(source_file.read_text())
        imported = _imported_modules(tree)
        forbidden = {
            module
            for module in imported
            if any(
                module == root or module.startswith(f"{root}.")
                for root in FORBIDDEN_IMPORT_ROOTS
            )
        }
        assert forbidden == set(), f"{source_file} imports {sorted(forbidden)}"


def test_ai_intake_is_isolated_from_planning_and_persistence() -> None:
    project_root = Path(__file__).parents[2]
    source_files = list(project_root.glob("app/ai_intake/*.py"))
    forbidden_roots = {
        "fastapi",
        "sqlalchemy",
        "app.availability",
        "app.models",
        "app.scheduling",
        "app.services",
    }

    for source_file in source_files:
        tree = ast.parse(source_file.read_text())
        imported = _imported_modules(tree)
        forbidden = {
            module
            for module in imported
            if any(
                module == root or module.startswith(f"{root}.")
                for root in forbidden_roots
            )
        }
        assert forbidden == set(), f"{source_file} imports {sorted(forbidden)}"

    route_file = project_root / "app" / "internal" / "ai_intake_router.py"
    imported = _imported_modules(ast.parse(route_file.read_text()))
    route_forbidden_roots = forbidden_roots - {"fastapi"}
    forbidden = {
        module
        for module in imported
        if any(
            module == root or module.startswith(f"{root}.")
            for root in route_forbidden_roots
        )
    }
    assert forbidden == set(), f"{route_file} imports {sorted(forbidden)}"


def test_task_confirmation_is_pure_application_logic() -> None:
    project_root = Path(__file__).parents[2]
    source_files = list(project_root.glob("app/task_confirmation/*.py"))
    assert source_files
    forbidden_roots = {
        "fastapi",
        "sqlalchemy",
        "app.core",
        "app.models",
        "app.scheduling",
        "app.services",
        "app.internal",
        "app.api",
        "app.ai_intake.gateway",
        "app.ai_intake.openai_provider",
        "app.ai_intake.provider",
    }

    for source_file in source_files:
        tree = ast.parse(source_file.read_text())
        imported = _imported_modules(tree)
        forbidden = {
            module
            for module in imported
            if any(
                module == root or module.startswith(f"{root}.")
                for root in forbidden_roots
            )
        }
        assert forbidden == set(), f"{source_file} imports {sorted(forbidden)}"


def test_workflows_orchestrate_without_infrastructure_or_scheduler_heuristics() -> None:
    project_root = Path(__file__).parents[2]
    source_files = list(project_root.glob("app/workflows/*.py"))
    assert source_files
    forbidden_roots = {
        "fastapi",
        "sqlalchemy",
        "google",
        "app.core.database",
        "app.models",
        "app.scheduling.scheduler",
        "app.internal.router",
    }

    for source_file in source_files:
        tree = ast.parse(source_file.read_text())
        imported = _imported_modules(tree)
        forbidden = {
            module
            for module in imported
            if any(
                module == root or module.startswith(f"{root}.")
                for root in forbidden_roots
            )
        }
        assert forbidden == set(), f"{source_file} imports {sorted(forbidden)}"

    route_file = project_root / "app" / "internal" / "workflow_router.py"
    route_source = route_file.read_text()
    imported = _imported_modules(ast.parse(route_source))
    assert not any(module.startswith("sqlalchemy") for module in imported)
    assert "DatabaseSession" not in route_source
    assert "get_db" not in route_source


def test_calendar_boundaries_keep_google_out_of_scheduler() -> None:
    project_root = Path(__file__).parents[2]
    protocol_files = [
        project_root / "app" / "calendar_integration" / "models.py",
        project_root / "app" / "calendar_integration" / "protocols.py",
    ]
    for source_file in protocol_files:
        imported = _imported_modules(ast.parse(source_file.read_text()))
        assert not any(
            module == root or module.startswith(f"{root}.")
            for module in imported
            for root in {"fastapi", "sqlalchemy", "app.models"}
        )

    scheduler_files = [
        *project_root.glob("app/availability/*.py"),
        *project_root.glob("app/scheduling/*.py"),
    ]
    for source_file in scheduler_files:
        imported = _imported_modules(ast.parse(source_file.read_text()))
        assert not any(
            module.startswith("app.calendar_integration") for module in imported
        )

    google_client = (
        project_root / "app" / "calendar_integration" / "google" / "client.py"
    )
    imported = _imported_modules(ast.parse(google_client.read_text()))
    assert "app.scheduling.scheduler" not in imported


def _imported_modules(tree: ast.AST) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules
