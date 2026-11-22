"""Prompt templates for the AI step.

Templates are Markdown files in ``icsel/prompts/`` (shipped with the package).
Set ``ICSEL_PROMPTS_DIR`` to a folder with files of the same names to use your
own wording. Each file holds the system prompt, a line ``---USER---``, then the
user prompt. ``$name`` placeholders are filled with :class:`string.Template`.
"""

from __future__ import annotations

import os
from pathlib import Path
from string import Template

from .base import LLMError

PROMPTS_ENV = "ICSEL_PROMPTS_DIR"
USER_MARKER = "---USER---"


def prompts_dir() -> Path:
    """Folder the templates are read from (``ICSEL_PROMPTS_DIR`` or the bundled one)."""
    override = os.environ.get(PROMPTS_ENV)
    if override:
        return Path(override)
    return Path(__file__).resolve().parent.parent / "prompts"


def render_prompt(name: str, **values: str) -> tuple[str, str]:
    """Fill template ``name`` and return ``(system, user)`` prompt text.

    Raises:
        LLMError: If the template is missing, has no ``---USER---`` line, or
            uses a placeholder that was not supplied.
    """
    path = prompts_dir() / f"{name}.md"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise LLMError(f"prompt template not found: {path}") from exc
    if USER_MARKER not in text:
        raise LLMError(f"{path}: missing a '{USER_MARKER}' line between system and user prompt")
    system, user = text.split(USER_MARKER, 1)
    try:
        return (
            Template(system).substitute(values).strip(),
            Template(user).substitute(values).strip(),
        )
    except (KeyError, ValueError) as exc:
        raise LLMError(f"{path}: bad or unknown placeholder ({exc})") from exc
