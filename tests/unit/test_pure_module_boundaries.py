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


def _imported_modules(tree: ast.AST) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules
