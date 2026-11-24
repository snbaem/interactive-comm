"""Naming a shared-text candidate: how the model's reply is checked."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from .base import LLMError
from .propose import plain

MAX_NAME = 60
MAX_DESCRIPTION = 160

FRAGMENT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"name": {"type": "string"}, "description": {"type": "string"}},
    "required": ["name", "description"],
    "additionalProperties": False,
}


def parse_fragment(raw: str, drop: Callable[[str], None]) -> dict[str, Any] | None:
    """Validate a name and one-line description; ``None`` (and one drop) if unusable.

    Raises:
        LLMError: If the reply is not the expected JSON object.
    """
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise LLMError(f"model reply is not JSON ({exc})") from exc
    if not isinstance(data, dict) or set(data) != {"name", "description"}:
        raise LLMError("model reply is not an object with 'name' and 'description'")
    if not all(isinstance(v, str) for v in data.values()):
        raise LLMError("model reply has a 'name' or 'description' that is not text")
    name, description = plain(data["name"]), plain(data["description"])
    if not name or not description:
        drop("empty text")
        return None
    if len(name) > MAX_NAME or len(description) > MAX_DESCRIPTION:
        drop("text too long")
        return None
    return {"name": name, "description": description, "ai": True}
