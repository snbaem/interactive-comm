"""Variable-field detection: what is sent, and how the model's reply is checked.

Sent: the text of every block except the letterhead and footer (:func:`letter_blocks`),
including the recipient's name and address and the body. That is why the task needs its
own opt-in switch (``--fields-send-text``).

Checked: each item must have exactly the expected keys and types, its text must be an
exact substring of one block that was sent, variable names must be simple identifiers and
unique in the letter, and variable items need enough confidence. Everything else is
dropped and counted.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ..extract import LetterStructure
from .base import LLMError

#: Roles whose text is never sent: page furniture, the same on every letter.
NOT_SENT = ("letterhead", "footer")
#: Most characters of letter text sent; later whole blocks are left out.
MAX_LETTER_TEXT = 12_000
NAME_RE = re.compile(r"[a-z][A-Za-z0-9]{0,39}")
KINDS = ("fixed", "variable")

FIELDS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "fields": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "text": {"type": "string"},
                    "kind": {"type": "string", "enum": list(KINDS)},
                    "confidence": {"type": "number"},
                },
                "required": ["name", "text", "kind", "confidence"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["fields"],
    "additionalProperties": False,
}


@dataclass
class FieldSpan:
    """A validated span of letter text, marked fixed or variable by the model."""

    name: str
    text: str
    kind: str
    confidence: float

    def to_dict(self) -> dict[str, Any]:
        """Plain-data view; always flagged as AI output."""
        return {
            "name": self.name,
            "text": self.text,
            "kind": self.kind,
            "confidence": round(self.confidence, 2),
            "ai": True,
        }


def letter_blocks(letter: LetterStructure) -> list[str]:
    """The block texts that are sent, in reading order, up to :data:`MAX_LETTER_TEXT`."""
    out: list[str] = []
    size = 0
    for block in letter.blocks:
        if block.role in NOT_SENT:
            continue
        size += len(block.text)
        if size > MAX_LETTER_TEXT:
            break
        out.append(block.text)
    return out


def items_of(raw: str, key: str) -> list[Any]:
    """The list under ``key`` of a JSON reply that holds nothing else.

    Raises:
        LLMError: If the reply is not exactly such an object.
    """
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise LLMError(f"model reply is not JSON ({exc})") from exc
    if not isinstance(data, dict) or set(data) != {key} or not isinstance(data[key], list):
        raise LLMError(f"model reply is not a JSON object with a '{key}' list")
    return data[key]


def confidence_of(value: Any) -> float | None:
    """``value`` as a confidence from 0 to 1, or ``None`` if it is not one."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1:
        return None
    return float(value)


def parse_fields(
    raw: str, blocks: list[str], min_confidence: float, drop: Callable[[str], None]
) -> list[FieldSpan]:
    """Validate the model's reply against the block texts that were sent.

    Args:
        raw: The model's reply.
        blocks: Output of :func:`letter_blocks`.
        min_confidence: Variable fields below this are dropped.
        drop: Called with a short reason for every item that is dropped.

    Raises:
        LLMError: If the reply as a whole is not the expected JSON.
    """
    out: list[FieldSpan] = []
    names: set[str] = set()
    texts: set[str] = set()
    for item in items_of(raw, "fields"):
        if not isinstance(item, dict) or set(item) != {"name", "text", "kind", "confidence"}:
            drop("not the expected schema")
            continue
        name, text, kind = item["name"], item["text"], item["kind"]
        confidence = confidence_of(item["confidence"])
        if (
            not isinstance(name, str)
            or not isinstance(text, str)
            or confidence is None
            or kind not in KINDS
        ):
            drop("not the expected schema")
        elif not text.strip() or not any(text in block for block in blocks):
            drop("text is not in the letter")
        elif text in texts:
            drop("duplicate text")
        elif kind == "variable" and not NAME_RE.fullmatch(name):
            drop("bad name")
        elif kind == "variable" and name in names:
            drop("duplicate name")
        elif kind == "variable" and confidence < min_confidence:
            drop("low confidence")
        else:
            texts.add(text)
            if kind == "variable":
                names.add(name)
            # A fixed span is not a field: it keeps no name.
            out.append(FieldSpan(name if kind == "variable" else "", text, kind, confidence))
    return out
