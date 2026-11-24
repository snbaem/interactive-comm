"""Optional AI step (off by default). See :mod:`icsel.llm.base` for the interface."""

from .assistant import LLMAssistant, apply_suggestions, fit_facts
from .base import (
    Assistant,
    CompletionBackend,
    LLMAuthError,
    LLMCallLimit,
    LLMError,
    NoOpAssistant,
    RoleChange,
    Suggestion,
)
from .cache import ReplyCache, default_cache_dir
from .fields import FieldSpan
from .purpose import load_purposes
from .registry import api_key_variable, available_backends, get_assistant, register_backend

__all__ = [
    "Assistant",
    "CompletionBackend",
    "FieldSpan",
    "LLMAssistant",
    "LLMAuthError",
    "LLMCallLimit",
    "LLMError",
    "NoOpAssistant",
    "ReplyCache",
    "RoleChange",
    "Suggestion",
    "api_key_variable",
    "apply_suggestions",
    "available_backends",
    "default_cache_dir",
    "fit_facts",
    "get_assistant",
    "load_purposes",
    "register_backend",
]
