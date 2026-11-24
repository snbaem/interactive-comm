"""Purpose tagging: the list of purposes, what is sent, and how the reply is checked.

Sent: the subject line and the first body paragraph, nothing else. The reply must be one
value of the list; anything else becomes ``other`` and is counted.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from ..extract import LetterStructure
from .base import LLMError

OTHER = "other"
#: Most characters of the first paragraph that are sent.
MAX_PARAGRAPH = 800
_ID_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,39}")


def default_purposes_path() -> Path:
    """The bundled starter list."""
    return Path(__file__).resolve().parent.parent / "data" / "purposes.json"


def load_purposes(path: Path | None = None) -> tuple[str, ...]:
    """Read a purposes file: ``{"purposes": ["billing-notice", ...]}``.

    ``other`` is added when the list does not have it.

    Raises:
        LLMError: If the file is unreadable or an entry is not a simple id.
    """
    source = path or default_purposes_path()
    try:
        data = json.loads(source.read_text(encoding="utf-8"))
        values = data["purposes"]
    except OSError as exc:
        raise LLMError(f"{source}: cannot read purposes ({exc.strerror or exc})") from exc
    except (ValueError, KeyError, TypeError) as exc:
        raise LLMError(f"{source}: expected a JSON object with a 'purposes' list") from exc
    if not isinstance(values, list) or not values:
        raise LLMError(f"{source}: 'purposes' must be a non-empty list")
    for v in values:
        if not isinstance(v, str) or not _ID_RE.fullmatch(v):
            raise LLMError(f"{source}: {v!r} is not a lower-case id such as 'billing-notice'")
    if len(set(values)) != len(values):
        raise LLMError(f"{source}: 'purposes' has duplicates")
    return tuple(values) if OTHER in values else (*values, OTHER)


def purpose_payload(letter: LetterStructure) -> dict[str, str]:
    """The subject line and first body paragraph; empty when the letter has neither."""
    out: dict[str, str] = {}
    subject = letter.region("subject")
    if subject is not None:
        out["subject"] = subject.text
    paragraph = next((b.text for b in letter.blocks if b.role == "body"), "")
    if paragraph:
        out["first_paragraph"] = paragraph[:MAX_PARAGRAPH]
    return out


def purpose_schema(purposes: Sequence[str]) -> dict[str, Any]:
    """JSON schema of the reply."""
    return {
        "type": "object",
        "properties": {"purpose": {"type": "string", "enum": list(purposes)}},
        "required": ["purpose"],
        "additionalProperties": False,
    }


def parse_purpose(raw: str, purposes: Sequence[str], drop: Callable[[str], None]) -> str:
    """One value of ``purposes``; anything else counts as a drop and becomes ``other``.

    Raises:
        LLMError: If the reply is not JSON at all.
    """
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise LLMError(f"model reply is not JSON ({exc})") from exc
    if isinstance(data, dict) and set(data) == {"purpose"} and data["purpose"] in list(purposes):
        return str(data["purpose"])
    drop("not in the list")
    return OTHER
