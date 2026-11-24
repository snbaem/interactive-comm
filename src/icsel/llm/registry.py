"""Pick the assistant from ``--llm`` or the ``ICSEL_LLM`` environment variable.

``ICSEL_LLM`` unset, empty or ``none`` means the no-op assistant: the AI step is off
by default. Other settings:

``ICSEL_LLM_MIN_CONFIDENCE``  ignore suggestions below this (default 0.5)
``ICSEL_LLM_TASKS``           comma list of tasks (default ``classify,explain``)
``ICSEL_LLM_<TASK>_SEND_TEXT``  ``1`` lets a task that sends letter text do so (``FIELDS``)
"""

from __future__ import annotations

import os
from collections.abc import Callable, Collection, Mapping

from .anthropic_backend import AnthropicBackend
from .assistant import DEFAULT_TASKS, TEXT_TASKS, LLMAssistant
from .base import Assistant, CompletionBackend, LLMError, NoOpAssistant
from .cache import ReplyCache

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


def get_assistant(
    name: str | None = None,
    env: Mapping[str, str] | None = None,
    tasks: str | None = None,
    max_calls: int | None = None,
    send_text: Collection[str] = (),
    cache: ReplyCache | None = None,
) -> Assistant:
    """Return the configured assistant (the no-op one unless explicitly enabled).

    Args:
        name: Backend name; ``None`` uses ``ICSEL_LLM``.
        env: Settings; defaults to the process environment.
        tasks: Comma list overriding ``ICSEL_LLM_TASKS``.
        max_calls: Hard cap on model calls for this run.
        send_text: Tasks allowed to send letter text, besides those allowed in ``env``.
        cache: Where validated replies are kept; ``None`` turns caching off.

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
    listed = env.get("ICSEL_LLM_TASKS", ",".join(DEFAULT_TASKS)) if tasks is None else tasks
    chosen = tuple(t.strip() for t in listed.split(",") if t.strip())
    allowed = {
        t
        for t in TEXT_TASKS
        if t in send_text
        or env.get(f"ICSEL_LLM_{t.upper()}_SEND_TEXT", "").strip().lower() not in OFF
    }
    return LLMAssistant(
        factory(env),
        min_confidence=min_confidence,
        tasks=chosen,
        max_calls=max_calls,
        send_text=allowed,
        cache=cache,
    )
