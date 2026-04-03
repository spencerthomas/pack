"""Compaction middleware — proactive context management before model calls."""

from __future__ import annotations

import logging
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.messages import AnyMessage, ToolMessage

from deepagents.compaction.context_collapse import ContextCollapser
from deepagents.compaction.monitor import CompactionMonitor, CompactionTier
from deepagents.compaction.segment_protocol import SegmentProtocol

logger = logging.getLogger(__name__)


class CompactionMiddleware(AgentMiddleware):
    """Proactive context compaction before model calls.

    Checks token usage before each model call and applies the
    appropriate compaction tier.

    Args:
        monitor: Token monitor for threshold checks.
        collapser: Context collapser for Tier 2.
        protocol: Segment protocol for Tier 3 summarization.
        summarize_fn: Optional async callable for Tier 3.
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

    async def awrap_model_call(self, request: Any, handler: Any) -> Any:
        """Check context size and compact if needed before model call.

        Args:
            request: The model request with state containing messages.
            handler: Async callback to execute the model call.

        Returns:
            The model response.
        """
        # Extract messages from request state
        messages = _extract_messages(request)
        if not messages:
            return await handler(request)

        tier = self._monitor.check(messages)

        if tier == CompactionTier.NONE:
            return await handler(request)

        if tier == CompactionTier.TRIM:
            logger.info("Tier 1 compaction: trimming old tool results")
            _trim_messages(messages)
        elif tier == CompactionTier.COLLAPSE:
            logger.info("Tier 2 compaction: collapsing verbose results")
            self._collapse_messages(messages)
        else:
            logger.info("Tier 3 compaction: full 9-segment summarization")
            # Tier 3 requires LLM call — skip if no summarizer configured
            if self._summarize_fn:
                await self._summarize_messages(request, messages)

        return await handler(request)

    def _collapse_messages(self, messages: list[AnyMessage]) -> None:
        """Replace verbose tool results in-place with summaries."""
        for i, msg in enumerate(messages):
            if isinstance(msg, ToolMessage):
                content = msg.content if isinstance(msg.content, str) else str(msg.content)
                if self._collapser.should_collapse(content):
                    summary = content[:200] + "..." if len(content) > 200 else content
                    entry = self._collapser.collapse("tool", content, summary)
                    messages[i] = ToolMessage(
                        content=self._collapser.format_collapsed(entry),
                        tool_call_id=msg.tool_call_id,
                    )

    async def _summarize_messages(self, request: Any, messages: list[AnyMessage]) -> None:
        """Full 9-segment summarization."""
        segments = self._protocol.parse(messages)
        prompt = self._protocol.build_summary_prompt(segments)
        summary = await self._summarize_fn(prompt)
        reconstructed = self._protocol.reconstruct(summary, segments, recent_messages=messages[-5:])
        # Replace messages in the request state
        if hasattr(request, "state") and hasattr(request.state, "messages"):
            request.state.messages[:] = reconstructed


def _extract_messages(request: Any) -> list[AnyMessage]:
    """Extract messages from a model request."""
    if hasattr(request, "state") and hasattr(request.state, "messages"):
        return list(request.state.messages)
    return []


def _trim_messages(messages: list[AnyMessage]) -> None:
    """Remove old tool results in-place, keeping the 5 most recent."""
    tool_indices = [i for i, m in enumerate(messages) if isinstance(m, ToolMessage)]
    if len(tool_indices) <= 5:
        return
    # Remove old tool messages (before the last 5)
    remove_before = tool_indices[-5]
    to_remove = {i for i in tool_indices if i < remove_before}
    for i in sorted(to_remove, reverse=True):
        messages.pop(i)
