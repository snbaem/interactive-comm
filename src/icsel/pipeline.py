"""One letter from structure to ranking, with the optional AI step in between."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .config import ScoringConfig
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


def _with_drops(
    assistant: Any, task: str, run: Callable[[Any], Any], errors: list[str]
) -> dict[str, Any]:
    """Run one task; return its result and how many model items validation dropped."""
    before = dict(assistant.drops)
    result: Any = None
    try:
        result = run(assistant)
    except LLMError as exc:
        errors.append(f"{task} failed: {exc}")
    prefix = f"{task}: "
    dropped = {
        k.removeprefix(prefix): n - before.get(k, 0)
        for k, n in assistant.drops.items()
        if k.startswith(prefix) and n > before.get(k, 0)
    }
    return {"result": result, "dropped": dropped}


def analyze(
    letter: LetterStructure,
    layouts: list[LayoutProfile],
    assistant: Assistant | None = None,
    config: ScoringConfig | None = None,
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
    results = rank(letter, layouts, config)
    tasks = getattr(assistant, "tasks", ())
    if "fields" in tasks:
        info["fields"] = _with_drops(
            assistant, "fields", lambda ai: [f.to_dict() for f in ai.detect_fields(letter)], errors
        )
    if assistant.enabled:
        try:
            info["explanation"] = assistant.explain_fit(fit_facts(letter, results, changes))
        except LLMError as exc:
            errors.append(f"explanation failed: {exc}")
    if assistant.enabled:
        info["errors"] = errors
    return Analysis(letter, results, info)
