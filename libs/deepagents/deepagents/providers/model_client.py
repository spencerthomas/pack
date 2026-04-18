"""PackModelClient: resilient wrapper around langchain BaseChatModel.

Provides retry with exponential backoff, optional cost tracking,
structured logging, and duration tracking for all LLM calls made
by Pack internals. Does NOT replace the langchain model interface --
it wraps ``ainvoke()`` for Pack-specific operational concerns.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage

if TYPE_CHECKING:
    from deepagents.cost.tracker import CostTracker

logger = logging.getLogger(__name__)

# Errors considered transient and eligible for retry.
_TRANSIENT_ERRORS: tuple[type[BaseException], ...] = (
    ConnectionError,
    TimeoutError,
)

# Base delay in seconds for exponential backoff.
_BASE_DELAY: float = 2.0


@dataclass
class CompletionResult:
    """Result of a single model completion.

    Attributes:
        content: The text content returned by the model.
        usage: Token usage dict with keys like ``input_tokens``,
            ``output_tokens``, ``total_tokens``. None if the model
            did not report usage.
        cost: Dollar cost for this completion, or None if no
            CostTracker was provided.
        duration_ms: Wall-clock duration of the call in milliseconds.
    """

    content: str
    usage: dict[str, int] | None = None
    cost: float | None = None
    duration_ms: float | None = None


def _is_transient(error: BaseException) -> bool:
    """Determine whether an error is transient and should be retried.

    Args:
        error: The exception to evaluate.

    Returns:
        True if the error is a known transient type or looks like
        a server-side (5xx) error.
    """
    if isinstance(error, _TRANSIENT_ERRORS):
        return True
    # Catch 5xx-like errors from HTTP client libraries that embed
    # status codes in the exception string or attributes.
    error_str = str(error).lower()
    if any(code in error_str for code in ("500", "502", "503", "504", "529")):
        return True
    status = getattr(error, "status_code", None) or getattr(error, "status", None)
    if isinstance(status, int) and 500 <= status < 600:
        return True
    return False


class PackModelClient:
    """Resilient wrapper around a langchain ``BaseChatModel``.

    Adds retry logic, optional cost tracking, structured logging, and
    duration measurement to every LLM call. Intended for internal Pack
    usage (compaction, classification, memory extraction) where direct
    ``ainvoke()`` calls need operational hardening.

    Args:
        model: A langchain ``BaseChatModel`` instance.
        cost_tracker: Optional ``CostTracker`` for recording token
            usage and dollar costs.
        max_retries: Maximum number of attempts for transient errors.
            Defaults to 3.

    Example::

        client = PackModelClient(model, cost_tracker=tracker)
        result = await client.complete("Summarize this.", purpose="compaction")
        print(result.content, result.duration_ms)
    """

    def __init__(
        self,
        model: BaseChatModel,
        *,
        cost_tracker: CostTracker | None = None,
        max_retries: int = 3,
    ) -> None:
        self._model = model
        self._cost_tracker = cost_tracker
        self._max_retries = max_retries

    @property
    def model(self) -> BaseChatModel:
        """The underlying langchain model."""
        return self._model

    @property
    def cost_tracker(self) -> CostTracker | None:
        """The cost tracker, if one was provided."""
        return self._cost_tracker

    @property
    def max_retries(self) -> int:
        """Maximum number of attempts for transient errors."""
        return self._max_retries

    async def complete(
        self,
        prompt: str,
        *,
        purpose: str = "general",
    ) -> CompletionResult:
        """Send a prompt to the model and return a structured result.

        Retries on transient errors with exponential backoff (2s, 4s, 8s, ...).
        Non-transient errors are raised immediately.

        Args:
            prompt: The text prompt to send to the model.
            purpose: A tag describing why this call is being made
                (e.g., ``"compaction"``, ``"classification"``). Used
                for structured logging.

        Returns:
            A ``CompletionResult`` with content, optional usage/cost,
            and duration.

        Raises:
            Exception: The last transient error if all retries are
                exhausted, or any non-transient error immediately.
        """
        last_error: BaseException | None = None

        for attempt in range(1, self._max_retries + 1):
            start = time.monotonic()
            try:
                logger.debug(
                    "PackModelClient [%s] attempt %d/%d",
                    purpose,
                    attempt,
                    self._max_retries,
                )
                response = await self._model.ainvoke([HumanMessage(content=prompt)])
                duration_ms = (time.monotonic() - start) * 1000

                # Extract content.
                content = (
                    response.content
                    if isinstance(response.content, str)
                    else str(response.content)
                )

                # Extract usage metadata.
                usage: dict[str, int] | None = None
                usage_meta = getattr(response, "usage_metadata", None)
                if usage_meta and isinstance(usage_meta, dict):
                    usage = {k: v for k, v in usage_meta.items() if isinstance(v, int)}

                # Record cost if tracker is available.
                cost: float | None = None
                if self._cost_tracker is not None and usage is not None:
                    model_name = self._resolve_model_name()
                    turn = self._cost_tracker.record_turn(
                        model=model_name,
                        input_tokens=usage.get("input_tokens", 0),
                        output_tokens=usage.get("output_tokens", 0),
                        cached_tokens=usage.get("cached_tokens", 0),
                    )
                    cost = turn.cost

                logger.info(
                    "PackModelClient [%s] completed in %.1fms",
                    purpose,
                    duration_ms,
                )

                return CompletionResult(
                    content=content,
                    usage=usage,
                    cost=cost,
                    duration_ms=duration_ms,
                )

            except Exception as exc:  # noqa: BLE001
                duration_ms = (time.monotonic() - start) * 1000
                last_error = exc

                if not _is_transient(exc):
                    logger.error(
                        "PackModelClient [%s] non-transient error: %s",
                        purpose,
                        exc,
                    )
                    raise

                if attempt < self._max_retries:
                    delay = _BASE_DELAY * (2 ** (attempt - 1))
                    logger.warning(
                        "PackModelClient [%s] transient error on attempt %d/%d, "
                        "retrying in %.1fs: %s",
                        purpose,
                        attempt,
                        self._max_retries,
                        delay,
                        exc,
                    )
                    await asyncio.sleep(delay)
                else:
                    logger.error(
                        "PackModelClient [%s] all %d retries exhausted: %s",
                        purpose,
                        self._max_retries,
                        exc,
                    )

        # All retries exhausted -- raise the last error with context.
        msg = (
            f"PackModelClient [{purpose}]: all {self._max_retries} "
            f"retries exhausted"
        )
        raise type(last_error)(msg) from last_error  # type: ignore[arg-type, union-attr]

    def _resolve_model_name(self) -> str:
        """Extract a model name string from the underlying model.

        Returns:
            The model name, or ``"unknown"`` if it cannot be determined.
        """
        # langchain models typically expose model_name or model.
        for attr in ("model_name", "model"):
            value = getattr(self._model, attr, None)
            if isinstance(value, str):
                return value
        return "unknown"
