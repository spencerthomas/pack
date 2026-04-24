"""Progressive tool disclosure — prune distractor tools per task.

The full Deep Agents tool surface (read_file, write_file, edit_file,
glob, grep, list_directory, execute, fetch_url, web_search,
compact_conversation, delegate_to_*, plus any MCP tools) is shipped to
the model on every turn. For Terminal Bench tasks the external
research tools (``fetch_url``, ``web_search``) are never useful and
consume tokens in every tool-schema render. Trajectory analysis shows
the strongest pass-predictor is low per-step completion tokens, so
removing distractors compounds.

This middleware intercepts ``wrap_model_call`` and drops known-distractor
tools from the request's tool list when the task has been classified
(i.e. ``task_hints`` is non-empty). Core tools are always preserved.

Conservative by design: only names on an explicit deny-list are
dropped, and only when the classifier has identified a task phase
(signalling a coding task). If no hints were derived, the middleware
no-ops so unusual tasks (chat, research) keep the full surface.

Wired via ``agent.py:create_cli_agent()`` after task_hints is known.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from langchain.agents.middleware.types import AgentMiddleware

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from langchain.agents.middleware.types import ModelRequest, ModelResponse

logger = logging.getLogger(__name__)


# Tools that aren't useful for coding tasks and add schema overhead on
# every request. Dropping them keeps the tool surface focused.
DEFAULT_DISTRACTOR_TOOLS: frozenset[str] = frozenset(
    {
        "fetch_url",
        "web_search",
        "compact_conversation",
    }
)


def _tool_name(tool: Any) -> str | None:
    """Best-effort name extraction for tool list entries."""
    if isinstance(tool, dict):
        return tool.get("name")
    name = getattr(tool, "name", None)
    if isinstance(name, str):
        return name
    return None


class ProgressiveDisclosureMiddleware(AgentMiddleware):
    """Drop known-distractor tools from the model request for coding tasks.

    Args:
        task_hints: Classifier output. When empty/None, the middleware
            no-ops (full tool surface). When non-empty, distractor tools
            are filtered from the request each turn.
        distractor_tools: Names of tools to drop. Defaults to the
            ``DEFAULT_DISTRACTOR_TOOLS`` set above.
        disabled: If True, the middleware no-ops regardless of hints.
    """

    def __init__(
        self,
        *,
        task_hints: dict[str, str] | None = None,
        distractor_tools: frozenset[str] | None = None,
        disabled: bool = False,
    ) -> None:
        self.task_hints = task_hints or {}
        self.distractor_tools = (
            distractor_tools if distractor_tools is not None else DEFAULT_DISTRACTOR_TOOLS
        )
        self.disabled = disabled

    def _should_filter(self) -> bool:
        """True when we have a classified task and are enabled."""
        if self.disabled:
            return False
        # Fire only when the classifier identified at least one signal —
        # phase or domain. If both are absent the task shape is unknown,
        # so don't prune.
        return bool(self.task_hints.get("phase") or self.task_hints.get("domain"))

    def _filter_tools(self, tools: list[Any]) -> tuple[list[Any], list[str]]:
        """Return (kept, dropped_names)."""
        kept: list[Any] = []
        dropped: list[str] = []
        for tool in tools:
            name = _tool_name(tool)
            if name and name in self.distractor_tools:
                dropped.append(name)
            else:
                kept.append(tool)
        return kept, dropped

    def wrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], ModelResponse[Any]],
    ) -> ModelResponse[Any]:
        """Prune distractor tools before the model is invoked."""
        if not self._should_filter():
            return handler(request)

        kept, dropped = self._filter_tools(list(request.tools))
        if not dropped:
            return handler(request)

        logger.debug("ProgressiveDisclosure: dropped tools %s", dropped)
        return handler(request.override(tools=kept))

    async def awrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], Awaitable[ModelResponse[Any]]],
    ) -> ModelResponse[Any]:
        """Async version."""
        if not self._should_filter():
            return await handler(request)

        kept, dropped = self._filter_tools(list(request.tools))
        if not dropped:
            return await handler(request)

        logger.debug("ProgressiveDisclosure: dropped tools %s", dropped)
        return await handler(request.override(tools=kept))


__all__ = [
    "DEFAULT_DISTRACTOR_TOOLS",
    "ProgressiveDisclosureMiddleware",
]
