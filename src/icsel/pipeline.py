"""One letter from structure to ranking, with the optional AI step in between."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .extract import LetterStructure
from .llm import Assistant, LLMError, NoOpAssistant, apply_suggestions, fit_facts
from .match import MatchResult, rank
from .profile import LayoutProfile


@dataclass
class Analysis:
    """Ranking for one letter plus a record of what the AI step did."""

    letter: LetterStructure
    results: list[MatchResult]
    llm: dict[str, Any] = field(default_factory=dict)


def analyze(
    letter: LetterStructure, layouts: list[LayoutProfile], assistant: Assistant | None = None
) -> Analysis:
    """Rank ``layouts`` for ``letter``; with an enabled assistant, label unknown blocks first.

    Order: the assistant may relabel ``unknown`` blocks (guarded, see
    :func:`icsel.llm.apply_suggestions`), then layouts are ranked, then the
    assistant explains the ranking. Any AI failure is recorded in
    ``llm["errors"]`` and matching continues with the rule-based result.
    """
    assistant = assistant or NoOpAssistant()
    info: dict[str, Any] = {**assistant.describe(), "role_changes": [], "explanation": None}
    errors: list[str] = []
    changes = []
    if assistant.enabled:
        try:
            suggestions = assistant.classify_unknown(letter)
            changes = apply_suggestions(letter, suggestions, assistant.min_confidence)
            info["role_changes"] = [c.to_dict() for c in changes]
        except LLMError as exc:
            errors.append(f"labeling unknown blocks failed: {exc}")
    results = rank(letter, layouts)
    if assistant.enabled:
        try:
            info["explanation"] = assistant.explain_fit(fit_facts(letter, results, changes))
        except LLMError as exc:
            errors.append(f"explanation failed: {exc}")
    if assistant.enabled:
        info["errors"] = errors
    return Analysis(letter, results, info)
