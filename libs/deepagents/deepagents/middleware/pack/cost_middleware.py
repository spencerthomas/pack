"""Cost tracking middleware — records token usage and costs per model call."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from langchain.agents.middleware.types import AgentMiddleware

from deepagents.cost.tracker import CostTracker

if TYPE_CHECKING:
    from langchain.agents.middleware.types import ModelRequest, ModelResponse
    from langchain_core.runnables.config import RunnableConfig

logger = logging.getLogger(__name__)


class CostMiddleware(AgentMiddleware):
    """Tracks token usage and costs for every model call.

    Wraps each model call to extract token counts from the response
    metadata and record them in the cost tracker.

    Args:
        tracker: The cost tracker instance to record usage to.
        model_name: Default model name for cost lookups when not
            available in response metadata.
    """

    def __init__(self, tracker: CostTracker, *, model_name: str = "unknown") -> None:
        self._tracker = tracker
        self._model_name = model_name

    @property
    def tracker(self) -> CostTracker:
        """Access the cost tracker."""
        return self._tracker

    async def wrap_model_call(
        self,
        request: ModelRequest,
        config: RunnableConfig,
        *,
        next: Any,
    ) -> ModelResponse:
        """Intercept model calls to record token usage.

        Args:
            request: The model request being sent.
            config: Runtime configuration.
            next: The next middleware or model call.

        Returns:
            The model response, unmodified.
        """
        response = await next(request, config)

        # Extract token usage from response metadata
        try:
            usage = _extract_usage(response)
            if usage:
                self._tracker.record_turn(
                    model=usage.get("model", self._model_name),
                    input_tokens=usage.get("input_tokens", 0),
                    output_tokens=usage.get("output_tokens", 0),
                    cached_tokens=usage.get("cached_tokens", 0),
                )
        except Exception:  # noqa: BLE001  # Cost tracking should never crash the agent
            logger.debug("Failed to extract token usage from response", exc_info=True)

        return response


def _extract_usage(response: Any) -> dict[str, Any] | None:
    """Extract token usage from a model response.

    Handles various response formats from different providers.

    Args:
        response: The model response object.

    Returns:
        Dictionary with usage data, or None if not available.
    """
    # Try ExtendedModelResponse format
    if hasattr(response, "response_metadata"):
        metadata = response.response_metadata
        if isinstance(metadata, dict):
            usage = metadata.get("token_usage") or metadata.get("usage") or {}
            if usage:
                return {
                    "model": metadata.get("model_name", metadata.get("model", "unknown")),
                    "input_tokens": usage.get("prompt_tokens", usage.get("input_tokens", 0)),
                    "output_tokens": usage.get("completion_tokens", usage.get("output_tokens", 0)),
                    "cached_tokens": usage.get("prompt_tokens_details", {}).get("cached_tokens", 0)
                    if isinstance(usage.get("prompt_tokens_details"), dict)
                    else 0,
                }

    # Try message-level usage_metadata (LangChain standard)
    if hasattr(response, "message") and hasattr(response.message, "usage_metadata"):
        um = response.message.usage_metadata
        if um:
            return {
                "model": getattr(response.message, "response_metadata", {}).get("model_name", "unknown"),
                "input_tokens": getattr(um, "input_tokens", 0),
                "output_tokens": getattr(um, "output_tokens", 0),
                "cached_tokens": getattr(um, "input_token_details", {}).get("cache_read", 0)
                if isinstance(getattr(um, "input_token_details", None), dict)
                else 0,
            }

    return None
