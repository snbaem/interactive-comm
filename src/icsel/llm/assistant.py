"""Provider-independent AI assistant: prompts, data minimisation and guard rails.

What is sent to the model:

* **Labeling unknown blocks:** the page size and, for every block, its index,
  page, rule-based role, position and size (inches), line count and boldness.
  Only blocks the rules left ``unknown`` include their text (capped at
  :data:`MAX_BLOCK_TEXT` characters). Text of recognised blocks (address,
  salutation, body, ...) is never sent.
* **Explaining the fit:** scores, offsets, hard-check failures and role counts
  (:func:`fit_facts`). No letter text at all.

What the model may change (:func:`apply_suggestions`): only blocks the rules left
``unknown``, only to a known role, only above a confidence threshold, and never a
second date, address or subject on page 1. Rule-based roles are never overridden.
"""

from __future__ import annotations

import json
from collections import Counter
from typing import Any

from ..extract import LetterStructure
from ..match import LOW_FIT_THRESHOLD, MatchResult
from ..profile import ROLES
from .base import CompletionBackend, LLMError, RoleChange, Suggestion
from .prompts import render_prompt

#: Most characters of one unknown block's text that are sent.
MAX_BLOCK_TEXT = 600
#: Roles a letter has at most once (on page 1).
SINGLE_ROLES = ("date", "address", "subject")
#: Jobs the assistant can run; ``ICSEL_LLM_TASKS`` picks a subset.
TASKS = ("classify", "explain")

CLASSIFY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "suggestions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "role": {"type": "string", "enum": list(ROLES)},
                    "confidence": {"type": "number"},
                    "reason": {"type": "string"},
                },
                "required": ["index", "role", "confidence", "reason"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["suggestions"],
    "additionalProperties": False,
}


def _inch(points: float) -> float:
    return round(points / 72.0, 2)


def classification_payload(letter: LetterStructure) -> dict[str, Any]:
    """The data sent for labeling: layout facts for all blocks, text for unknown ones only."""
    blocks = []
    unknown = []
    for i, b in enumerate(letter.blocks):
        entry: dict[str, Any] = {
            "index": i,
            "page": b.page,
            "role": b.role,
            "top_in": _inch(b.y),
            "left_in": _inch(b.x),
            "width_in": _inch(b.width),
            "height_in": _inch(b.height),
            "lines": b.line_count,
            "bold": b.bold,
        }
        if b.role == "unknown":
            entry["text"] = "\n".join(b.lines)[:MAX_BLOCK_TEXT]
            unknown.append(i)
        blocks.append(entry)
    return {
        "page": {"width_in": _inch(letter.page_width), "height_in": _inch(letter.page_height)},
        "page_count": letter.page_count,
        "unknown_indexes": unknown,
        "blocks": blocks,
    }


def parse_suggestions(raw: str) -> list[Suggestion]:
    """Parse and sanity-check the model's JSON reply. Malformed entries are skipped.

    Raises:
        LLMError: If the reply is not JSON with a ``suggestions`` list.
    """
    try:
        data = json.loads(raw)
        items = data["suggestions"]
        if not isinstance(items, list):
            raise TypeError("suggestions is not a list")
    except (ValueError, KeyError, TypeError) as exc:
        raise LLMError(f"model reply is not the expected JSON ({exc})") from exc
    out = []
    for item in items:
        try:
            index = int(item["index"])
            role = str(item["role"])
            confidence = min(1.0, max(0.0, float(item["confidence"])))
            reason = str(item.get("reason", ""))[:300]
        except (KeyError, TypeError, ValueError):
            continue
        out.append(Suggestion(index, role, confidence, reason))
    return out


def apply_suggestions(
    letter: LetterStructure, suggestions: list[Suggestion], min_confidence: float
) -> list[RoleChange]:
    """Apply the suggestions that pass the guard rails; return what changed.

    Changes the blocks in ``letter`` in place (``role``, ``role_source="llm"``,
    ``note``). Higher-confidence suggestions win when two compete for a
    single-instance role.
    """
    blocks = letter.blocks
    taken = {b.role for b in blocks if b.page == 1 and b.role in SINGLE_ROLES}
    changes: list[RoleChange] = []
    for s in sorted(suggestions, key=lambda s: -s.confidence):
        if not 0 <= s.index < len(blocks):
            continue
        b = blocks[s.index]
        if b.role != "unknown" or b.role_source != "rules":
            continue  # only blocks the rules could not label, and only once
        if s.role not in ROLES or s.role == "unknown" or s.confidence < min_confidence:
            continue
        if s.role in SINGLE_ROLES:
            if b.page != 1 or s.role in taken:
                continue
            taken.add(s.role)
        changes.append(RoleChange(s.index, b.page, b.role, s.role, s.confidence, s.reason))
        b.role, b.role_source, b.note = s.role, "llm", s.reason
    return sorted(changes, key=lambda c: c.index)


def fit_facts(
    letter: LetterStructure, results: list[MatchResult], changes: list[RoleChange] | None = None
) -> dict[str, Any]:
    """Scoring facts for the explanation. Contains no letter text."""
    ranking = []
    for r in results[:3]:
        roles = []
        for rs in r.roles:
            entry: dict[str, Any] = {
                "role": rs.role,
                "score": None if rs.score is None else round(rs.score, 2),
            }
            if rs.dx is not None and rs.dy is not None:
                entry["offset_right_in"] = _inch(rs.dx)
                entry["offset_down_in"] = _inch(rs.dy)
            if rs.note:
                entry["note"] = rs.note
            roles.append(entry)
        ranking.append(
            {
                "layout": r.layout_id,
                "name": r.layout_name,
                "score": round(r.score, 3),
                "fits": r.fits,
                "failures": r.failures,
                "roles": roles,
            }
        )
    return {
        "low_fit_threshold": LOW_FIT_THRESHOLD,
        "page_count": letter.page_count,
        "detected_roles": dict(Counter(b.role for b in letter.blocks)),
        "ai_relabelled": [{"from": c.old_role, "to": c.new_role} for c in changes or []],
        "ranking": ranking,
    }


class LLMAssistant:
    """Both AI jobs on top of any :class:`~icsel.llm.base.CompletionBackend`."""

    enabled = True

    def __init__(
        self,
        backend: CompletionBackend,
        min_confidence: float = 0.5,
        tasks: tuple[str, ...] = TASKS,
    ) -> None:
        unknown = set(tasks) - set(TASKS)
        if unknown:
            raise LLMError(f"unknown AI task(s) {sorted(unknown)}; choose from {list(TASKS)}")
        self.backend = backend
        self.min_confidence = min_confidence
        self.tasks = tuple(tasks)

    def describe(self) -> dict[str, Any]:
        """Backend, model and enabled tasks."""
        return {
            "enabled": True,
            "backend": self.backend.name,
            "model": self.backend.model,
            "tasks": list(self.tasks),
        }

    def check(self) -> None:
        """Verify the backend's credentials, if it can do so (raises ``LLMError``)."""
        check = getattr(self.backend, "check", None)
        if check is not None:
            check()

    def classify_unknown(self, letter: LetterStructure) -> list[Suggestion]:
        """Ask the model about ``unknown`` blocks. No call is made if there are none."""
        if "classify" not in self.tasks:
            return []
        payload = classification_payload(letter)
        if not payload["unknown_indexes"]:
            return []
        system, prompt = render_prompt(
            "classify_blocks",
            roles=", ".join(r for r in ROLES if r != "unknown"),
            payload=json.dumps(payload, indent=2),
        )
        return parse_suggestions(self.backend.complete(system, prompt, CLASSIFY_SCHEMA))

    def explain_fit(self, facts: dict[str, Any]) -> str | None:
        """Ask the model for a short explanation of the ranking."""
        if "explain" not in self.tasks:
            return None
        system, prompt = render_prompt("explain_fit", facts=json.dumps(facts, indent=2))
        text = self.backend.complete(system, prompt).strip()
        return text or None
