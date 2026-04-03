"""OpenRouter provider using `langchain-openai` with a custom base URL.

OpenRouter acts as a unified gateway to many LLM providers. Model strings
follow the `provider/model` format (e.g., `anthropic/claude-sonnet-4-6`,
`deepseek/deepseek-chat`).
"""

from __future__ import annotations

import os
from typing import Any

from langchain_core.language_models import BaseChatModel

from deepagents.providers.base import (
    AuxiliaryModelConfig,
    AuxiliaryTask,
    ProviderType,
)

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
"""Base URL for the OpenRouter API, compatible with OpenAI's chat format."""

OPENROUTER_API_KEY_ENV = "OPENROUTER_API_KEY"
"""Environment variable name for the OpenRouter API key."""

_DEFAULT_AUX_MODEL = "mistralai/mistral-small"
"""Default cheap model on OpenRouter for auxiliary tasks when Ollama is unavailable."""


def _get_api_key() -> str | None:
    """Read the OpenRouter API key from the environment.

    Returns:
        The API key string, or `None` if not set.
    """
    return os.environ.get(OPENROUTER_API_KEY_ENV)


def parse_model_string(model_string: str) -> tuple[str | None, str]:
    """Parse a model string into provider prefix and model name.

    Supports two formats:
    - `provider/model` (e.g., `anthropic/claude-sonnet-4-6`)
    - bare model name (e.g., `mistral-small`)

    Args:
        model_string: The model identifier to parse.

    Returns:
        A tuple of (provider_prefix, model_name). The provider prefix
        is `None` for bare model names.
    """
    if "/" in model_string:
        prefix, _, name = model_string.partition("/")
        return prefix, f"{prefix}/{name}"
    return None, model_string


class OpenRouterProvider:
    """Provider that routes requests through OpenRouter's API.

    Wraps `langchain-openai`'s `ChatOpenAI` with OpenRouter's base URL
    and API key. Supports both primary and auxiliary model creation.

    Args:
        api_key: OpenRouter API key. Falls back to the
            `OPENROUTER_API_KEY` environment variable.
        default_model: Default model for primary tasks.
        auxiliary_config: Optional config for cheap auxiliary tasks.
        model_kwargs: Extra kwargs forwarded to `ChatOpenAI`.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        default_model: str = "anthropic/claude-sonnet-4-6",
        auxiliary_config: AuxiliaryModelConfig | None = None,
        model_kwargs: dict[str, Any] | None = None,
    ) -> None:
        self._api_key = api_key or _get_api_key()
        self._default_model = default_model
        self._auxiliary_config = auxiliary_config
        self._model_kwargs: dict[str, Any] = model_kwargs or {}

    @property
    def api_key(self) -> str | None:
        """The configured API key (may be `None` if unset)."""
        return self._api_key

    @property
    def default_model(self) -> str:
        """The default primary model identifier."""
        return self._default_model

    @property
    def auxiliary_config(self) -> AuxiliaryModelConfig | None:
        """The auxiliary model configuration, if any."""
        return self._auxiliary_config

    def create_model(
        self,
        model: str | None = None,
        **kwargs: Any,
    ) -> BaseChatModel:
        """Create a `ChatOpenAI` instance pointed at OpenRouter.

        Args:
            model: Model identifier in `provider/model` format.
                Uses `default_model` if not provided.
            **kwargs: Additional keyword arguments forwarded to
                `ChatOpenAI`.

        Returns:
            A configured `BaseChatModel` for the requested model.

        Raises:
            ValueError: If no API key is available.
        """
        if not self._api_key:
            msg = (
                f"OpenRouter API key not found. Set the "
                f"`{OPENROUTER_API_KEY_ENV}` environment variable or "
                f"pass `api_key` to the provider constructor."
            )
            raise ValueError(msg)

        from langchain_openai import ChatOpenAI

        _, resolved_model = parse_model_string(model or self._default_model)
        merged_kwargs = {**self._model_kwargs, **kwargs}

        # Filter out kwargs that ChatOpenAI doesn't accept
        # (e.g., app_url, app_title from OpenRouter attribution defaults)
        _UNSUPPORTED_KWARGS = {"app_url", "app_title", "app_categories"}  # noqa: N806
        filtered_kwargs = {k: v for k, v in merged_kwargs.items() if k not in _UNSUPPORTED_KWARGS}

        # Pass attribution headers via default_headers instead
        headers: dict[str, str] = {}
        if "app_url" in merged_kwargs:
            headers["HTTP-Referer"] = str(merged_kwargs["app_url"])
        if "app_title" in merged_kwargs:
            headers["X-Title"] = str(merged_kwargs["app_title"])
        if headers:
            existing = filtered_kwargs.get("default_headers", {})
            filtered_kwargs["default_headers"] = {**existing, **headers}

        return ChatOpenAI(
            model=resolved_model,
            openai_api_key=self._api_key,
            openai_api_base=OPENROUTER_BASE_URL,
            **filtered_kwargs,
        )

    def create_auxiliary_model(
        self,
        task: AuxiliaryTask,
        **kwargs: Any,
    ) -> BaseChatModel:
        """Create a model for an auxiliary task.

        If an `AuxiliaryModelConfig` is set and covers the requested task,
        the auxiliary model is used. Otherwise falls back to a default
        cheap OpenRouter model.

        Args:
            task: The auxiliary task type to create a model for.
            **kwargs: Additional keyword arguments forwarded to the
                model constructor.

        Returns:
            A `BaseChatModel` configured for auxiliary use.

        Raises:
            ValueError: If no API key is available.
        """
        if self._auxiliary_config and task in self._auxiliary_config.tasks:
            if self._auxiliary_config.provider == ProviderType.OLLAMA:
                from deepagents.providers.ollama import OllamaProvider

                ollama = OllamaProvider()
                if ollama.is_available():
                    merged = {**self._auxiliary_config.model_kwargs, **kwargs}
                    return ollama.create_model(
                        model=self._auxiliary_config.model,
                        **merged,
                    )
            elif self._auxiliary_config.provider == ProviderType.OPENROUTER:
                merged = {**self._auxiliary_config.model_kwargs, **kwargs}
                return self.create_model(
                    model=self._auxiliary_config.model,
                    **merged,
                )

        # Fallback: cheap OpenRouter model
        return self.create_model(model=_DEFAULT_AUX_MODEL, **kwargs)
