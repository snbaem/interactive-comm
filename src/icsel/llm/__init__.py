"""Optional AI step (off by default). See :mod:`icsel.llm.base` for the interface."""

from .assistant import LLMAssistant, apply_suggestions, fit_facts
from .base import (
    Assistant,
    CompletionBackend,
    LLMAuthError,
    LLMError,
    NoOpAssistant,
    RoleChange,
    Suggestion,
)
from .registry import api_key_variable, available_backends, get_assistant, register_backend

__all__ = [
    "Assistant",
    "CompletionBackend",
    "LLMAssistant",
    "LLMAuthError",
    "LLMError",
    "NoOpAssistant",
    "RoleChange",
    "Suggestion",
    "api_key_variable",
    "apply_suggestions",
    "available_backends",
    "fit_facts",
    "get_assistant",
    "register_backend",
]
