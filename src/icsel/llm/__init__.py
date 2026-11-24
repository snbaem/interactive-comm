"""Optional AI step (off by default). See :mod:`icsel.llm.base` for the interface."""

from .assistant import LLMAssistant, apply_suggestions, fit_facts
from .base import (
    Assistant,
    CompletionBackend,
    LLMCallLimit,
    LLMError,
    NoOpAssistant,
    RoleChange,
    Suggestion,
)
from .registry import available_backends, get_assistant, register_backend

__all__ = [
    "Assistant",
    "CompletionBackend",
    "LLMAssistant",
    "LLMCallLimit",
    "LLMError",
    "NoOpAssistant",
    "RoleChange",
    "Suggestion",
    "apply_suggestions",
    "available_backends",
    "fit_facts",
    "get_assistant",
    "register_backend",
]
