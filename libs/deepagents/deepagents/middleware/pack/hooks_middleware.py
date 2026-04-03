"""Hooks middleware — fires lifecycle events around tool calls."""

from __future__ import annotations

import logging
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware

from deepagents.hooks.engine import HookEngine
from deepagents.hooks.events import HookEvent

logger = logging.getLogger(__name__)


class HooksMiddleware(AgentMiddleware):
    """Fires hook events around model and tool calls.

    Args:
        engine: The hook engine with registered hook definitions.
    """

    def __init__(self, engine: HookEngine) -> None:
        self._engine = engine
        self._pending_injections: list[str] = []

    @property
    def engine(self) -> HookEngine:
        """Access the hook engine."""
        return self._engine

    async def awrap_model_call(self, request: Any, handler: Any) -> Any:
        """Fire pre/post model call hooks.

        Args:
            request: The model request.
            handler: Async callback to execute the model call.

        Returns:
            The model response.
        """
        await self._engine.fire(HookEvent.PRE_MODEL_CALL, {})
        response = await handler(request)
        await self._engine.fire(HookEvent.POST_MODEL_CALL, {})
        return response

    async def awrap_tool_call(self, request: Any, handler: Any) -> Any:
        """Fire pre/post tool call hooks.

        Args:
            request: Tool call request.
            handler: Async callback to execute the tool.

        Returns:
            Tool result.
        """
        tool_name = request.call.get("name", "") if hasattr(request, "call") else ""
        args = request.call.get("args", {}) if hasattr(request, "call") else {}
        context = {"tool_name": tool_name, "args": str(args)}

        if "path" in args:
            context["file_path"] = str(args["path"])
        if "command" in args:
            context["command"] = str(args["command"])

        await self._engine.fire(HookEvent.PRE_TOOL_CALL, context)
        result = await handler(request)

        context["result"] = str(result)[:500]
        post_results = await self._engine.fire(HookEvent.POST_TOOL_CALL, context)

        for hook_result in post_results:
            if hook_result.inject and hook_result.stdout.strip():
                logger.debug("Hook output for %s: %s", tool_name, hook_result.stdout[:100])

        return result
