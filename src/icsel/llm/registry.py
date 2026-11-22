"""Pick the assistant from ``--llm`` or the ``ICSEL_LLM`` environment variable.

``ICSEL_LLM`` unset, empty or ``none`` means the no-op assistant: the AI step is off
by default. Other settings:

``ICSEL_LLM_MIN_CONFIDENCE``  ignore suggestions below this (default 0.5)
``ICSEL_LLM_TASKS``           comma list of ``classify``, ``explain`` (default both)
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping

from .anthropic_backend import AnthropicBackend
from .assistant import TASKS, LLMAssistant
from .base import Assistant, CompletionBackend, LLMError, NoOpAssistant

ENV_VAR = "ICSEL_LLM"
OFF = ("", "none", "off", "0", "false", "no")

BackendFactory = Callable[[Mapping[str, str]], CompletionBackend]

_FACTORIES: dict[str, BackendFactory] = {
    "anthropic": AnthropicBackend.from_env,
}


def register_backend(name: str, factory: BackendFactory) -> None:
    """Make a backend selectable as ``--llm NAME`` / ``ICSEL_LLM=NAME``."""
    _FACTORIES[name.lower()] = factory


def available_backends() -> list[str]:
    """Names accepted by :func:`get_assistant` (``none`` first)."""
    return ["none", *sorted(_FACTORIES)]


def get_assistant(name: str | None = None, env: Mapping[str, str] | None = None) -> Assistant:
    """Return the configured assistant (the no-op one unless explicitly enabled).

    Raises:
        LLMError: For an unknown backend name or invalid settings.
    """
    env = os.environ if env is None else env
    choice = (env.get(ENV_VAR, "") if name is None else name).strip().lower()
    if choice in OFF:
        return NoOpAssistant()
    factory = _FACTORIES.get(choice)
    if factory is None:
        raise LLMError(
            f"unknown AI backend {choice!r}; choose from {', '.join(available_backends())}"
        )
    try:
        min_confidence = float(env.get("ICSEL_LLM_MIN_CONFIDENCE", "0.5"))
    except ValueError as exc:
        raise LLMError(f"invalid ICSEL_LLM_MIN_CONFIDENCE: {exc}") from exc
    tasks = tuple(
        t.strip() for t in env.get("ICSEL_LLM_TASKS", ",".join(TASKS)).split(",") if t.strip()
    )
    return LLMAssistant(factory(env), min_confidence=min_confidence, tasks=tasks)
