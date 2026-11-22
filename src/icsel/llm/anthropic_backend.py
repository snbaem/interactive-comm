"""Example backend: Claude through the official Anthropic Python SDK.

Install with ``pip install "icsel[llm]"``. Credentials are resolved by the SDK
from the environment (``ANTHROPIC_API_KEY``, ``ANTHROPIC_AUTH_TOKEN`` or an
``ant auth login`` profile); icsel never takes a key as an argument or stores one.

Configuration (environment variables, all optional):

``ICSEL_LLM_MODEL``       model id (default ``claude-opus-5-5``)
``ICSEL_LLM_EFFORT``      ``low`` | ``medium`` | ``high`` | ``xhigh`` | ``max`` (default ``low``;
                          both jobs are short and simple)
``ICSEL_LLM_MAX_TOKENS``  reply token cap (default 4000)
``ICSEL_LLM_TIMEOUT``     seconds per request (default 60)
``ICSEL_LLM_FALLBACKS``   ``1`` (default) lets the API retry a declined request on a
                          fallback model in the same call; set ``0`` on platforms
                          without server-side fallbacks
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

from .base import LLMError

DEFAULT_MODEL = "claude-opus-5-5"
EFFORTS = ("low", "medium", "high", "xhigh", "max")
FALLBACK_BETA = "server-side-fallback-2026-07-01"


def _api_errors() -> tuple[type[BaseException], ...]:
    try:
        import anthropic
    except ImportError:  # a client was injected without the SDK installed (tests)
        return (Exception,)
    return (anthropic.APIError,)


class AnthropicBackend:
    """:class:`~icsel.llm.base.CompletionBackend` for Claude models."""

    name = "anthropic"

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        effort: str = "low",
        max_tokens: int = 4000,
        timeout: float = 60.0,
        fallbacks: bool = True,
        client: Any = None,
    ) -> None:
        """Create the backend.

        Args:
            model: Claude model id.
            effort: Output effort level.
            max_tokens: Cap on reply tokens.
            timeout: Seconds per request.
            fallbacks: Use server-side refusal fallbacks.
            client: An ``anthropic.Anthropic`` client to use instead of creating one
                (for tests or custom HTTP settings).

        Raises:
            LLMError: If the SDK is not installed or a setting is invalid.
        """
        if effort not in EFFORTS:
            raise LLMError(f"ICSEL_LLM_EFFORT must be one of {', '.join(EFFORTS)}; got {effort!r}")
        if client is None:
            try:
                import anthropic
            except ImportError as exc:
                raise LLMError(
                    "the 'anthropic' backend needs the optional extra: pip install \"icsel[llm]\""
                ) from exc
            client = anthropic.Anthropic(timeout=timeout)
        self.client = client
        self.model = model
        self.effort = effort
        self.max_tokens = max_tokens
        self.fallbacks = fallbacks

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> AnthropicBackend:
        """Build from ``ICSEL_LLM_*`` environment variables (see module docstring)."""
        env = os.environ if env is None else env
        try:
            return cls(
                model=env.get("ICSEL_LLM_MODEL", DEFAULT_MODEL),
                effort=env.get("ICSEL_LLM_EFFORT", "low"),
                max_tokens=int(env.get("ICSEL_LLM_MAX_TOKENS", "4000")),
                timeout=float(env.get("ICSEL_LLM_TIMEOUT", "60")),
                fallbacks=env.get("ICSEL_LLM_FALLBACKS", "1").lower() not in ("0", "false", "no"),
            )
        except ValueError as exc:
            raise LLMError(f"invalid ICSEL_LLM_* setting: {exc}") from exc

    def complete(self, system: str, prompt: str, json_schema: dict[str, Any] | None = None) -> str:
        """Send one request and return the reply text (see ``CompletionBackend``)."""
        output_config: dict[str, Any] = {"effort": self.effort}
        if json_schema is not None:
            output_config["format"] = {"type": "json_schema", "schema": json_schema}
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": prompt}],
            "output_config": output_config,
        }
        if self.fallbacks:
            kwargs["betas"] = [FALLBACK_BETA]
            kwargs["fallbacks"] = "default"
        try:
            response = self.client.beta.messages.create(**kwargs)
        except _api_errors() as exc:
            raise LLMError(f"Anthropic API call failed: {exc}") from exc
        if response.stop_reason == "refusal":
            raise LLMError("the model declined this request")
        if response.stop_reason == "max_tokens":
            raise LLMError("the reply was cut off; raise ICSEL_LLM_MAX_TOKENS")
        text = "".join(b.text for b in response.content if b.type == "text")
        if not text.strip():
            raise LLMError("the model returned no text")
        return text
