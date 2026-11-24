"""Naming a proposed layout: how the model's reply is checked.

Markup is stripped before the length checks, because the text ends up in a Markdown
report: tags, links, and the characters Markdown gives a meaning to.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any

from .base import LLMError

MAX_NAME = 60
MAX_RATIONALE = 700

PROPOSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"name": {"type": "string"}, "rationale": {"type": "string"}},
    "required": ["name", "rationale"],
    "additionalProperties": False,
}


def plain(text: str) -> str:
    """``text`` on one line without anything that looks like markup."""
    text = re.sub(r"<[^>]*>", " ", text)
    text = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", text)  # links and images keep their label
    text = re.sub(r"\b\w+://\S+", " ", text)
    text = re.sub(r"[`*_#~|<>\[\]\\{}]", " ", text)
    text = "".join(c if c.isprintable() else " " for c in text)
    return re.sub(r"\s+", " ", text).strip()


def parse_proposal(raw: str, drop: Callable[[str], None]) -> dict[str, Any] | None:
    """Validate a name and rationale; ``None`` (and one drop) if either is unusable.

    Raises:
        LLMError: If the reply is not the expected JSON object.
    """
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise LLMError(f"model reply is not JSON ({exc})") from exc
    if not isinstance(data, dict) or set(data) != {"name", "rationale"}:
        raise LLMError("model reply is not an object with 'name' and 'rationale'")
    if not all(isinstance(v, str) for v in data.values()):
        raise LLMError("model reply has a 'name' or 'rationale' that is not text")
    name, rationale = plain(data["name"]), plain(data["rationale"])
    if not name or not rationale:
        drop("empty text")
        return None
    if len(name) > MAX_NAME or len(rationale) > MAX_RATIONALE:
        drop("text too long")
        return None
    return {"name": name, "rationale": rationale, "ai": True}
