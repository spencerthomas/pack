"""Middleware that gates the agent's 'done' signal behind a verification checklist.

When the model produces an AIMessage with no tool_calls (the natural 'done'
signal in LangGraph's agent loop), this middleware intercepts and injects a
structured verification checklist as a HumanMessage, forcing another model
turn. The agent cannot terminate until it has satisfied the checklist for
N cycles (default 1 — one forced verification pass before done is allowed).

This is the pattern LangChain credits as anchoring their +13.7pp gain on
Terminal Bench 2.0 (52.8% → 66.5%). Unlike external auto-verification,
this runs inline in the agent loop and does not consume separate budget.

Wired via ``agent.py:create_cli_agent()``.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from langchain.agents.middleware.types import (
    AgentMiddleware,
    AgentState,
    hook_config,
)
from langchain_core.messages import AIMessage, HumanMessage

if TYPE_CHECKING:
    from langgraph.runtime import Runtime

logger = logging.getLogger(__name__)

_CHECKLIST_MARKER = "[PRECOMPLETION-CHECKLIST]"

_DEFAULT_CHECKLIST = f"""{_CHECKLIST_MARKER}

Before declaring this task complete, walk through this checklist. Reply with
either (a) your answers + any remaining tool calls to fix gaps, or (b) a clear
"verified, done" response ONLY if every item is satisfied.

1. **Tests** — Have you run the task's verification tests (e.g., `bash /tests/test.sh`,
   `pytest`, or the task-specified command)? What was the output? Did every
   assertion pass?
2. **Requirements** — Walk through the original task requirements item-by-item.
   For each requirement, point to the file/line/output that satisfies it.
3. **File paths and names** — Did you use the EXACT file paths, class names,
   and identifiers from the task? No rename, no abbreviation, no "improvement"?
4. **Edge cases** — What edge cases did the task mention? How did your solution
   handle each one?
5. **Output format** — If the task specified an output format (JSON, CSV,
   specific file), does your solution produce it exactly?

If any item is unverified, use the appropriate tool to verify NOW (read the
test output, grep the file, run the script). Do not declare done on faith.
"""


def _count_checklist_cycles(messages: list[Any]) -> int:
    """Count how many verification-checklist messages were injected."""
    return sum(
        1
        for msg in messages
        if isinstance(msg, HumanMessage) and _CHECKLIST_MARKER in str(msg.content)
    )


def _last_ai_message_declares_done(messages: list[Any]) -> bool:
    """True if the most recent AIMessage has no tool_calls (the 'done' signal)."""
    for msg in reversed(messages):
        if isinstance(msg, AIMessage):
            tool_calls = getattr(msg, "tool_calls", None) or []
            return len(tool_calls) == 0
    return False


class PreCompletionChecklistMiddleware(AgentMiddleware):
    """Force a structured verification checklist before the agent can declare done.

    On the first attempt to terminate (AIMessage with no tool_calls), inject
    a checklist HumanMessage and force another model turn. After ``max_cycles``
    completed verification passes, allow the real termination.

    Args:
        max_cycles: Number of verification cycles required before allowing
            the agent to terminate. Default 1 — a single forced verification
            pass. Set higher for more paranoid tasks.
        checklist_template: The checklist prompt injected on interception.
            Must contain the ``[PRECOMPLETION-CHECKLIST]`` marker so cycle
            counting works. Defaults to a general-purpose template covering
            tests, requirements, exact identifiers, edge cases, and output
            format.
    """

    DEFAULT_MAX_CYCLES = 1

    def __init__(
        self,
        *,
        max_cycles: int = DEFAULT_MAX_CYCLES,
        checklist_template: str = _DEFAULT_CHECKLIST,
    ) -> None:
        if max_cycles < 1:
            msg = "max_cycles must be >= 1"
            raise ValueError(msg)
        if _CHECKLIST_MARKER not in checklist_template:
            msg = f"checklist_template must contain marker {_CHECKLIST_MARKER!r}"
            raise ValueError(msg)
        self.max_cycles = max_cycles
        self.checklist_template = checklist_template

    @hook_config(can_jump_to=["model"])
    def after_model(
        self,
        state: AgentState[Any],
        runtime: Runtime[Any],  # noqa: ARG002
    ) -> dict[str, Any] | None:
        """Intercept 'done' signals and inject checklist until max_cycles met."""
        return self._maybe_inject_checklist(state)

    @hook_config(can_jump_to=["model"])
    async def aafter_model(
        self,
        state: AgentState[Any],
        runtime: Runtime[Any],  # noqa: ARG002
    ) -> dict[str, Any] | None:
        """Async version."""
        return self._maybe_inject_checklist(state)

    def _maybe_inject_checklist(
        self,
        state: AgentState[Any],
    ) -> dict[str, Any] | None:
        messages = state.get("messages", [])

        # Only act when the last AIMessage is a 'done' signal (no tool calls).
        if not _last_ai_message_declares_done(messages):
            return None

        cycles_completed = _count_checklist_cycles(messages)
        if cycles_completed >= self.max_cycles:
            # Agent has already gone through the required verification pass(es).
            return None

        logger.info(
            "PreCompletionChecklist: injecting verification cycle %d/%d",
            cycles_completed + 1,
            self.max_cycles,
        )
        return {
            "messages": [HumanMessage(content=self.checklist_template)],
            "jump_to": "model",
        }


__all__ = ["PreCompletionChecklistMiddleware"]
