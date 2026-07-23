import json
from pathlib import Path

from pydantic import ValidationError

from app.internal.schemas import ScenarioDocument, ScenarioSummary

EXAMPLES_DIRECTORY = (
    Path(__file__).resolve().parents[2] / "tests" / "product" / "examples"
)


class InvalidScenarioError(ValueError):
    pass


def list_scenarios(
    examples_directory: Path = EXAMPLES_DIRECTORY,
) -> list[ScenarioSummary]:
    summaries: list[ScenarioSummary] = []
    for path in sorted(examples_directory.glob("*.json")):
        scenario = load_scenario(path.name, examples_directory)
        summaries.append(
            ScenarioSummary(
                filename=path.name,
                name=scenario.name,
                description=scenario.description,
            )
        )
    return summaries


def load_scenario(
    filename: str, examples_directory: Path = EXAMPLES_DIRECTORY
) -> ScenarioDocument:
    if Path(filename).name != filename or not filename.endswith(".json"):
        raise InvalidScenarioError("invalid scenario filename")
    path = (examples_directory / filename).resolve()
    directory = examples_directory.resolve()
    if path.parent != directory:
        raise InvalidScenarioError("invalid scenario filename")
    if not path.is_file():
        raise FileNotFoundError(filename)
    try:
        return ScenarioDocument.model_validate_json(path.read_text())
    except (OSError, json.JSONDecodeError, ValidationError, ValueError) as exc:
        raise InvalidScenarioError(f"invalid scenario file: {filename}") from exc
