from dataclasses import dataclass
from pathlib import Path

from app.ai_intake.types import PROMPT_VERSION, PROMPT_VERSION_V1

PROMPTS_DIRECTORY = Path(__file__).resolve().parent / "prompt_templates"
SUPPORTED_PROMPTS = {
    PROMPT_VERSION_V1: "task_draft_v1.txt",
    PROMPT_VERSION: "task_draft_v2.txt",
}


class UnknownPromptVersionError(ValueError):
    pass


@dataclass(frozen=True)
class Prompt:
    version: str
    instructions: str


def load_prompt(version: str, prompts_directory: Path = PROMPTS_DIRECTORY) -> Prompt:
    filename = SUPPORTED_PROMPTS.get(version)
    if filename is None:
        raise UnknownPromptVersionError(f"unsupported prompt version: {version}")
    path = (prompts_directory / filename).resolve()
    if path.parent != prompts_directory.resolve():
        raise UnknownPromptVersionError("invalid prompt path")
    instructions = path.read_text().strip()
    if not instructions:
        raise ValueError(f"prompt is empty: {version}")
    return Prompt(version=version, instructions=instructions)
