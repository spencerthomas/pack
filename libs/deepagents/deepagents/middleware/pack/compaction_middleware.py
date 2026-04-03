"""Compaction middleware — proactive context management before model calls."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.messages import AnyMessage

from deepagents.compaction.context_collapse import ContextCollapser
from deepagents.compaction.monitor import CompactionMonitor, CompactionTier
from deepagents.compaction.segment_protocol import SegmentProtocol

if TYPE_CHECKING:
    from langchain.agents.middleware.types import ModelRequest, ModelResponse
    from langchain_core.runnables.config import RunnableConfig

logger = logging.getLogger(__name__)


class CompactionMiddleware(AgentMiddleware):
    """Proactive context compaction before model calls.

    Checks token usage before each model call and applies the
    appropriate compaction tier:

    - Tier 1 (TRIM): Remove old tool results, keep 5 most recent
    - Tier 2 (COLLAPSE): Replace verbose results with summaries
    - Tier 3 (SUMMARIZE): Full 9-segment protocol with sacred user messages

    Args:
        monitor: Token monitor for threshold checks.
        collapser: Context collapser for Tier 2.
        protocol: Segment protocol for Tier 3 summarization.
        summarize_fn: Optional async callable that takes a prompt string
            and returns a summary. Used for Tier 3. If not provided,
            Tier 3 falls back to Tier 2.
    """

    def __init__(
        self,
        monitor: CompactionMonitor,
        collapser: ContextCollapser,
        protocol: SegmentProtocol | None = None,
        *,
        summarize_fn: Any = None,
    ) -> None:
        self._monitor = monitor
        self._collapser = collapser
        self._protocol = protocol or SegmentProtocol()
        self._summarize_fn = summarize_fn

    @property
    def monitor(self) -> CompactionMonitor:
        """Access the compaction monitor."""
        return self._monitor

    @property
    def collapser(self) -> ContextCollapser:
        """Access the context collapser."""
        return self._collapser

    async def wrap_model_call(
        self,
        request: ModelRequest,
        config: RunnableConfig,
        *,
        next: Any,
    ) -> ModelResponse:
        """Check context size and compact if needed before model call.

        Args:
            request: The model request with messages.
            config: Runtime configuration.
            next: The next middleware or model call.

        Returns:
            The model response.
        """
        messages = _extract_messages(request)
        if not messages:
            return await next(request, config)

        tier = self._monitor.check(messages)

        if tier == CompactionTier.NONE:
            return await next(request, config)

        if tier == CompactionTier.TRIM:
            compacted = self._tier1_trim(messages)
            logger.info("Tier 1 compaction: trimmed %d messages to %d", len(messages), len(compacted))
        elif tier == CompactionTier.COLLAPSE:
            compacted = self._tier2_collapse(messages)
            logger.info("Tier 2 compaction: collapsed verbose results")
        else:
            compacted = await self._tier3_summarize(messages)
            logger.info("Tier 3 compaction: full 9-segment summarization")

        request = _replace_messages(request, compacted)
        return await next(request, config)

    def _tier1_trim(self, messages: list[AnyMessage]) -> list[AnyMessage]:
        """Remove old tool results, keeping the 5 most recent tool exchanges."""
        from langchain_core.messages import ToolMessage

        # Find indices of tool messages
        tool_indices = [i for i, m in enumerate(messages) if isinstance(m, ToolMessage)]

        if len(tool_indices) <= 5:
            return messages

        # Keep the last 5 tool messages and remove the rest
        keep_from = tool_indices[-5]
        result: list[AnyMessage] = []
        for i, msg in enumerate(messages):
            if isinstance(msg, ToolMessage) and i < keep_from:
                continue  # Skip old tool results
            result.append(msg)

        return result

    def _tier2_collapse(self, messages: list[AnyMessage]) -> list[AnyMessage]:
        """Replace verbose tool results with summaries."""
        from langchain_core.messages import ToolMessage

        result: list[AnyMessage] = []
        for msg in messages:
            if isinstance(msg, ToolMessage):
                content = msg.content if isinstance(msg.content, str) else str(msg.content)
                if self._collapser.should_collapse(content):
                    # Create a simple summary (first 200 chars + token count)
                    summary = content[:200] + "..." if len(content) > 200 else content
                    entry = self._collapser.collapse(
                        tool_name="tool",
                        content=content,
                        summary=summary,
                    )
                    collapsed_msg = ToolMessage(
                        content=self._collapser.format_collapsed(entry),
                        tool_call_id=msg.tool_call_id,
                    )
                    result.append(collapsed_msg)
                    continue
            result.append(msg)

        return result

    async def _tier3_summarize(self, messages: list[AnyMessage]) -> list[AnyMessage]:
        """Full 9-segment summarization preserving user messages."""
        segments = self._protocol.parse(messages)

        if self._summarize_fn is not None:
            prompt = self._protocol.build_summary_prompt(segments)
            summary = await self._summarize_fn(prompt)
        else:
            # Fallback: use a simple concatenation of key facts
            summary = (
                f"Original request: {segments.original_request}\n"
                f"Files touched: {', '.join(segments.files_touched[:10])}\n"
                f"Current work: {segments.current_work}"
            )

        # Keep the last 5 messages as recent context
        recent = messages[-5:] if len(messages) > 5 else []
        return self._protocol.reconstruct(summary, segments, recent_messages=recent)


def _extract_messages(request: Any) -> list[AnyMessage]:
    """Extract messages from a model request."""
    if hasattr(request, "messages"):
        return list(request.messages)
    if isinstance(request, dict) and "messages" in request:
        return list(request["messages"])
    return []


def _replace_messages(request: Any, messages: list[AnyMessage]) -> Any:
    """Replace messages in a model request."""
    if hasattr(request, "messages"):
        request.messages = messages
    elif isinstance(request, dict):
        request["messages"] = messages
    return request
