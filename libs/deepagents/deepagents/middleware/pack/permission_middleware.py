"""Permission middleware — evaluates tool calls through the permission pipeline."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from langchain.agents.middleware.types import AgentMiddleware

from deepagents.permissions.pipeline import Decision, PermissionPipeline

if TYPE_CHECKING:
    from langchain_core.runnables.config import RunnableConfig

logger = logging.getLogger(__name__)


class PermissionMiddleware(AgentMiddleware):
    """Evaluates tool calls through the multi-layer permission pipeline.

    Wraps tool execution to check permissions before running. Denied
    calls return error messages that are fed back to the model.

    This middleware replaces the binary approve/reject HITL system
    with a graduated pipeline that learns from user decisions.

    Args:
        pipeline: The permission pipeline instance.
        auto_approve: If True, skip the pipeline entirely (equivalent to -y).
    """

    def __init__(self, pipeline: PermissionPipeline, *, auto_approve: bool = False) -> None:
        self._pipeline = pipeline
        self._auto_approve = auto_approve

    @property
    def pipeline(self) -> PermissionPipeline:
        """Access the permission pipeline."""
        return self._pipeline

    async def wrap_tool_call(
        self,
        tool_name: str,
        args: dict[str, Any],
        config: RunnableConfig,
        *,
        next: Any,
    ) -> Any:
        """Evaluate tool call permissions before execution.

        Args:
            tool_name: Name of the tool being called.
            args: Tool call arguments.
            config: Runtime configuration.
            next: The next middleware or actual tool execution.

        Returns:
            Tool result if allowed, error message if denied.
        """
        if self._auto_approve:
            return await next(tool_name, args, config)

        result = self._pipeline.evaluate(tool_name, args)

        if result.decision == Decision.ALLOW:
            return await next(tool_name, args, config)

        if result.decision == Decision.DENY:
            logger.info("Permission denied for %s: %s", tool_name, result.reason)
            return self._pipeline.format_denial_feedback(result)

        if result.decision == Decision.MANUAL_MODE:
            logger.warning("Circuit breaker tripped — routing to manual approval")
            # In manual mode, fall through to the existing HITL system
            # by letting the call proceed to the next middleware
            return await next(tool_name, args, config)

        # ASK_USER — let it through to the HITL middleware downstream
        # The HITL middleware will prompt the user
        return await next(tool_name, args, config)
