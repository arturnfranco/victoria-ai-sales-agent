"""Safe loading and versioning for Git-managed prompts."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


PROMPT_VERSION_PATTERN = re.compile(r"^sales_v[1-9]\d*$")


class PromptLoadError(ValueError):
    """Raised when a requested prompt is unsafe or unavailable."""


@dataclass(frozen=True)
class VersionedPrompt:
    """Loaded prompt content and its stable version identifier."""

    version: str
    content: str


class PromptLoader:
    """Load allow-listed sales prompts from one configured directory."""

    def __init__(self, prompt_directory: Path | None = None) -> None:
        self._prompt_directory = (
            prompt_directory
            if prompt_directory is not None
            else Path(__file__).resolve().parents[2] / "prompts"
        )

    def load(self, version: str) -> VersionedPrompt:
        if not PROMPT_VERSION_PATTERN.fullmatch(version):
            raise PromptLoadError(f"invalid sales prompt version: {version!r}")
        path = self._prompt_directory / f"{version}.md"
        try:
            content = path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise PromptLoadError(f"prompt version {version!r} is unavailable") from exc
        if not content:
            raise PromptLoadError(f"prompt version {version!r} is empty")
        return VersionedPrompt(version=version, content=content)
