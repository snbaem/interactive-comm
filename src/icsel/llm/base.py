"""Interfaces for the optional AI step.

icsel talks to an :class:`Assistant`, which has two jobs:

* label blocks the rules left as ``unknown`` (:meth:`Assistant.classify_unknown`);
* write a short "why this fit" paragraph from the structured result
  (:meth:`Assistant.explain_fit`).

The default is :class:`NoOpAssistant`, which does nothing and sends nothing.
:class:`icsel.llm.assistant.LLMAssistant` implements both jobs on top of any
:class:`CompletionBackend`: one method that sends a system prompt and a user
prompt to a model and returns its text. Adding a provider means writing one
small backend class; prompts, validation and safety checks stay shared.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from ..extract import LetterStructure


class LLMError(RuntimeError):
    """The AI step failed or is misconfigured. Matching continues without it."""


class LLMCallLimit(LLMError):
    """The run reached its cap on model calls (``--max-calls``)."""


@runtime_checkable
class CompletionBackend(Protocol):
    """Transport to one model provider."""

    #: Short provider name, e.g. ``"anthropic"``.
    name: str
    #: Model identifier sent to the provider.
    model: str

    def complete(self, system: str, prompt: str, json_schema: dict[str, Any] | None = None) -> str:
        """Return the model's text reply.

        Args:
            system: System prompt.
            prompt: User prompt.
            json_schema: If given, the reply must be JSON matching this schema.

        Raises:
            LLMError: On any transport, refusal or format problem.
        """
        ...


@dataclass
class Suggestion:
    """A model's proposed role for one block (not yet checked or applied)."""

    index: int
    role: str
    confidence: float
    reason: str


@dataclass
class RoleChange:
    """A role the AI step actually changed on a block."""

    index: int
    page: int
    old_role: str
    new_role: str
    confidence: float
    reason: str

    def to_dict(self) -> dict[str, Any]:
        """Plain-data view for JSON output."""
        return {
            "index": self.index,
            "page": self.page,
            "from": self.old_role,
            "to": self.new_role,
            "confidence": round(self.confidence, 2),
            "reason": self.reason,
        }


class Assistant(Protocol):
    """What icsel calls. See :class:`NoOpAssistant` and ``LLMAssistant``."""

    #: False for the no-op default.
    enabled: bool
    #: Suggestions below this confidence are ignored.
    min_confidence: float

    def describe(self) -> dict[str, Any]:
        """Backend and model, for logs and the result JSON."""
        ...

    def classify_unknown(self, letter: LetterStructure) -> list[Suggestion]:
        """Propose roles for the letter's ``unknown`` blocks."""
        ...

    def explain_fit(self, facts: dict[str, Any]) -> str | None:
        """Write a short plain-language explanation from scoring facts."""
        ...


class NoOpAssistant:
    """The default: no AI, no network, no data leaves the machine."""

    enabled = False
    min_confidence = 1.0

    def describe(self) -> dict[str, Any]:
        """Report that the AI step is off."""
        return {"enabled": False, "backend": "none", "model": None}

    def classify_unknown(self, letter: LetterStructure) -> list[Suggestion]:
        """Return no suggestions."""
        return []

    def explain_fit(self, facts: dict[str, Any]) -> str | None:
        """Return no explanation."""
        return None
