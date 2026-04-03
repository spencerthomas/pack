"""Permission middleware — evaluates tool calls through the permission pipeline."""

from __future__ import annotations

import logging
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.messages import ToolMessage

from deepagents.permissions.pipeline import Decision, PermissionPipeline

logger = logging.getLogger(__name__)


class PermissionMiddleware(AgentMiddleware):
    """Evaluates tool calls through the multi-layer permission pipeline.

    Wraps tool execution to check permissions before running. Denied
    calls return error messages that are fed back to the model.

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

    async def awrap_tool_call(self, request: Any, handler: Any) -> Any:
        """Evaluate tool call permissions before execution.

        Args:
            request: Tool call request with call dict, tool, state, runtime.
            handler: Async callback to execute the tool.

        Returns:
            Tool result if allowed, error ToolMessage if denied.
        """
        if self._auto_approve:
            return await handler(request)

        # Extract tool name and args from the request
        call = getattr(request, "call", None) or {}
        tool_name = call.get("name", "") if isinstance(call, dict) else ""
        args = call.get("args", {}) if isinstance(call, dict) else {}
        tool_call_id = call.get("id", "") if isinstance(call, dict) else ""

        result = self._pipeline.evaluate(tool_name, args)

        if result.decision == Decision.ALLOW:
            return await handler(request)

        if result.decision == Decision.DENY:
            logger.info("Permission denied for %s: %s", tool_name, result.reason)
            return ToolMessage(
                content=self._pipeline.format_denial_feedback(result),
                tool_call_id=tool_call_id,
            )

        # ASK_USER or MANUAL_MODE — let it through to the HITL middleware
        return await handler(request)
