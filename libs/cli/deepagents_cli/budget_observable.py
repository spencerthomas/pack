"""Middleware that surfaces remaining budget to the agent after every tool call.

Appends a ``[budget: 7m 32s remaining | tokens ...]`` line to each tool result.
At ``critical_threshold_sec`` remaining (default 120s), escalates to a
``[CRITICAL]`` marker urging the agent to emit its best-known solution.

No hard cutoff — the outer Harbor timeout still governs abort. This middleware
only exposes budget so the agent can reason about it rather than running the
clock to zero blindly.

Wired via ``agent.py:create_cli_agent()``.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.messages import ToolMessage

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from langchain.tools.tool_node import ToolCallRequest
    from langgraph.types import Command

logger = logging.getLogger(__name__)


def _format_seconds(seconds: float) -> str:
    """Format seconds as 'Nm Ns' or 'Ns'."""
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}s"
    minutes = seconds // 60
    remainder = seconds % 60
    return f"{minutes}m {remainder:02d}s"


class BudgetObservableMiddleware(AgentMiddleware):
    """Append remaining-budget info to every tool result.

    Args:
        total_budget_sec: Total wall-clock budget for the agent run. Agent
            sees ``remaining = total_budget_sec - elapsed``.
        critical_threshold_sec: When remaining time drops below this, flip
            the appended marker to CRITICAL and urge best-so-far submission.
        disabled: If True, the middleware no-ops. Useful for tests or
            interactive CLI mode where budget is not meaningful.
    """

    DEFAULT_TOTAL_BUDGET_SEC = 900  # 15 minutes — common Harbor task timeout
    DEFAULT_CRITICAL_THRESHOLD_SEC = 120  # 2 minutes

    def __init__(
        self,
        *,
        total_budget_sec: int = DEFAULT_TOTAL_BUDGET_SEC,
        critical_threshold_sec: int = DEFAULT_CRITICAL_THRESHOLD_SEC,
        disabled: bool = False,
    ) -> None:
        if total_budget_sec < 1:
            msg = "total_budget_sec must be >= 1"
            raise ValueError(msg)
        if critical_threshold_sec < 0:
            msg = "critical_threshold_sec must be >= 0"
            raise ValueError(msg)
        self.total_budget_sec = total_budget_sec
        self.critical_threshold_sec = critical_threshold_sec
        self.disabled = disabled
        self._started_at = time.monotonic()

    def wrap_tool_call(
        self,
        request: ToolCallRequest,  # noqa: ARG002
        handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
    ) -> ToolMessage | Command[Any]:
        """Append budget info to tool result."""
        result = handler(request)
        return self._append_budget(result)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,  # noqa: ARG002
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        """Async version."""
        result = await handler(request)
        return self._append_budget(result)

    def _append_budget(
        self,
        result: ToolMessage | Command[Any],
    ) -> ToolMessage | Command[Any]:
        if self.disabled or not isinstance(result, ToolMessage):
            return result

        elapsed = time.monotonic() - self._started_at
        remaining = max(0, self.total_budget_sec - elapsed)

        if remaining <= self.critical_threshold_sec:
            marker = (
                f"\n\n[BUDGET CRITICAL: {_format_seconds(remaining)} remaining. "
                f"Emit your best-known solution NOW — do not start new work. "
                f"If tests are passing, declare done. If not, save whatever "
                f"works and submit.]"
            )
        else:
            marker = (
                f"\n\n[budget: {_format_seconds(remaining)} remaining / "
                f"{_format_seconds(self.total_budget_sec)} total]"
            )

        result.content = str(result.content) + marker
        return result


__all__ = ["BudgetObservableMiddleware"]
