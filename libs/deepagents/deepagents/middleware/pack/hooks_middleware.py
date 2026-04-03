"""Hooks middleware — fires lifecycle events around tool calls."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from langchain.agents.middleware.types import AgentMiddleware

from deepagents.hooks.engine import HookEngine
from deepagents.hooks.events import HookEvent

if TYPE_CHECKING:
    from langchain.agents.middleware.types import ModelRequest, ModelResponse
    from langchain_core.runnables.config import RunnableConfig

logger = logging.getLogger(__name__)


class HooksMiddleware(AgentMiddleware):
    """Fires hook events around model and tool calls.

    Integrates the hook engine into the middleware pipeline so hooks
    fire at the right points in the agent loop:

    - PRE_TOOL_CALL / POST_TOOL_CALL around tool execution
    - PRE_MODEL_CALL / POST_MODEL_CALL around model calls

    Hook output can be injected into the model context when
    `inject_output=True` is set on the hook definition.

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

    async def wrap_model_call(
        self,
        request: ModelRequest,
        config: RunnableConfig,
        *,
        next: Any,
    ) -> ModelResponse:
        """Fire pre/post model call hooks.

        Args:
            request: The model request.
            config: Runtime configuration.
            next: The next middleware or model call.

        Returns:
            The model response.
        """
        # Inject any pending hook outputs into the request
        if self._pending_injections:
            _inject_hook_context(request, self._pending_injections)
            self._pending_injections.clear()

        # Fire pre-model hooks
        await self._engine.fire(HookEvent.PRE_MODEL_CALL, {})

        response = await next(request, config)

        # Fire post-model hooks
        await self._engine.fire(HookEvent.POST_MODEL_CALL, {})

        return response

    async def wrap_tool_call(
        self,
        tool_name: str,
        args: dict[str, Any],
        config: RunnableConfig,
        *,
        next: Any,
    ) -> Any:
        """Fire pre/post tool call hooks.

        Args:
            tool_name: Name of the tool being called.
            args: Tool call arguments.
            config: Runtime configuration.
            next: The next middleware or tool execution.

        Returns:
            Tool result.
        """
        context = {"tool_name": tool_name, "args": str(args)}

        # Add common template variables
        if "path" in args:
            context["file_path"] = str(args["path"])
        if "command" in args:
            context["command"] = str(args["command"])

        # Fire pre-tool hooks
        pre_results = await self._engine.fire(HookEvent.PRE_TOOL_CALL, context)

        # Check for blocking hooks that failed
        for result in pre_results:
            if result.return_code != 0:
                # Check if the hook definition was blocking
                # The engine handles blocking logic — if we get here, non-blocking hooks
                # just report their failure
                logger.warning("Pre-tool hook failed with code %d: %s", result.return_code, result.stderr)

        # Execute the tool
        tool_result = await next(tool_name, args, config)

        # Fire post-tool hooks
        context["result"] = str(tool_result)[:500]  # Truncate for template safety
        post_results = await self._engine.fire(HookEvent.POST_TOOL_CALL, context)

        # Collect outputs for injection into next model call
        for result in post_results:
            if result.inject and result.stdout.strip():
                self._pending_injections.append(
                    f"[Hook output for {tool_name}]: {result.stdout.strip()}"
                )

        return tool_result


def _inject_hook_context(request: Any, injections: list[str]) -> None:
    """Inject hook outputs into model request messages.

    Adds hook outputs as system reminders appended to the message list.

    Args:
        request: The model request to modify.
        injections: List of hook output strings to inject.
    """
    from langchain_core.messages import SystemMessage

    combined = "\n".join(injections)
    reminder = SystemMessage(content=f"[Hook output]\n{combined}")

    if hasattr(request, "messages") and isinstance(request.messages, list):
        request.messages.append(reminder)
    elif isinstance(request, dict) and "messages" in request:
        request["messages"].append(reminder)
