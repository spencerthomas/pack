"""ReviewerMiddleware — policy-gated reviewer invocation in the agent loop.

When a ``TaskPolicy`` marks ``require_reviewer=True``, this middleware
intercepts the main agent's declared-done signal (an ``AIMessage`` with
no tool calls) and invokes the reviewer sub-agent before allowing
termination.

Branching on verdict status:

- ``approve`` → do nothing; the main agent terminates normally.
- ``request_changes`` → inject the verdict as a ``HumanMessage`` and
  jump back to the model. The main agent addresses the fixes and tries
  again. Fires at most ``max_reviews`` times per run.
- ``block`` → inject the verdict and jump back to the model once; if
  still blocked on the next cycle, allow termination so the run isn't
  stuck forever. Policy-layer human approval handles the "still bad"
  case separately.

Phase C.3 of the agent-harness roadmap. Consumes ``ReviewerSubAgent``
from ``reviewer.py`` and ``TaskPolicy`` from ``policy.py``.
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

from deepagents_cli.reviewer import ReviewerSubAgent

if TYPE_CHECKING:
    from langgraph.runtime import Runtime

    from deepagents_cli.policy import TaskPolicy

logger = logging.getLogger(__name__)


_VERDICT_MARKER = "[REVIEWER VERDICT:"


def _count_reviews(messages: list[Any]) -> int:
    """Count how many reviewer verdicts are already in the conversation.

    The marker is the text prefix that ``ReviewVerdict.as_feedback_text``
    always emits. Counting injections is how we enforce
    ``max_reviews`` across multiple after-model passes.
    """
    return sum(
        1
        for msg in messages
        if isinstance(msg, HumanMessage) and _VERDICT_MARKER in str(msg.content)
    )


def _last_ai_declares_done(messages: list[Any]) -> bool:
    """True if the most recent AIMessage has no tool calls (the done signal)."""
    for msg in reversed(messages):
        if isinstance(msg, AIMessage):
            tool_calls = getattr(msg, "tool_calls", None) or []
            return len(tool_calls) == 0
    return False


def _extract_task_instruction(messages: list[Any]) -> str:
    """Pull the original task instruction from the conversation.

    The first ``HumanMessage`` with substantive content is the task by
    convention. If that's missing (shouldn't happen in Harbor runs but
    possible in tests), returns an empty string so the reviewer can
    still do its best without the prefix context.
    """
    for msg in messages:
        if isinstance(msg, HumanMessage) and _VERDICT_MARKER not in str(msg.content):
            content = msg.content if isinstance(msg.content, str) else str(msg.content)
            if content.strip():
                return content
    return ""


def _recent_agent_messages(messages: list[Any], n: int = 6) -> list[Any]:
    """Last N messages for the reviewer.

    Trimming keeps token cost bounded and focuses the reviewer on the
    work that led to the done signal.
    """
    if not messages:
        return []
    return messages[-n:]


_WRITE_TOOL_NAMES = frozenset({"write_file", "edit_file"})


def _extract_diff_summary(messages: list[Any], *, max_files: int = 25) -> str:
    """Summarize the files the main agent touched via write_file/edit_file.

    Scans every ``AIMessage.tool_calls`` for write_file / edit_file
    invocations and groups them by path. Output is a short bulleted
    list — path + write count — that the reviewer can compare against
    the task description. Deliberately **doesn't** include the raw
    content: if the reviewer needs the text it's already in the
    trajectory dump under the adjacent ToolMessage.

    Returns an empty string when the agent didn't write anything, so
    the caller can drop the section from the prompt.
    """
    counts: dict[str, int] = {}
    order: list[str] = []
    for msg in messages:
        if not isinstance(msg, AIMessage):
            continue
        for call in getattr(msg, "tool_calls", None) or []:
            name = call.get("name") if isinstance(call, dict) else None
            if name not in _WRITE_TOOL_NAMES:
                continue
            args = call.get("args") or {}
            path = args.get("path") or args.get("file_path")
            if not isinstance(path, str) or not path.strip():
                continue
            if path not in counts:
                order.append(path)
            counts[path] = counts.get(path, 0) + 1

    if not order:
        return ""

    # Cap the list so a runaway iterative-write pattern doesn't
    # explode the reviewer's token budget.
    truncated = order[:max_files]
    lines = [
        f"- `{path}` ({counts[path]} write{'s' if counts[path] > 1 else ''})"
        for path in truncated
    ]
    if len(order) > max_files:
        lines.append(f"- ... {len(order) - max_files} more file(s) not listed")
    return "\n".join(lines)


class ReviewerMiddleware(AgentMiddleware):
    """Invoke the reviewer when policy requires it; gate termination on verdict.

    Args:
        reviewer: A configured ``ReviewerSubAgent``. The caller builds
            this so the same model+config chosen for the main agent (or
            a cheaper one) is used.
        policy: The active ``TaskPolicy``. The middleware no-ops when
            ``policy.require_reviewer`` is False or policy is None, so
            unreviewed task types pay no cost.
        max_reviews: Cap on total reviewer passes per run. Default 2 —
            one initial pass, one retry after addressing changes.
            Higher values risk infinite loops on stubborn tasks; lower
            removes the retry opportunity entirely.
        disabled: Hard kill-switch for tests and emergencies.
    """

    DEFAULT_MAX_REVIEWS = 2

    def __init__(
        self,
        *,
        reviewer: ReviewerSubAgent,
        policy: TaskPolicy | None = None,
        max_reviews: int = DEFAULT_MAX_REVIEWS,
        disabled: bool = False,
    ) -> None:
        if max_reviews < 1:
            msg = "max_reviews must be >= 1"
            raise ValueError(msg)
        self.reviewer = reviewer
        self.policy = policy
        self.max_reviews = max_reviews
        self.disabled = disabled

    def _active(self) -> bool:
        if self.disabled:
            return False
        if self.policy is None:
            return False
        return bool(self.policy.require_reviewer)

    @hook_config(can_jump_to=["model"])
    def after_model(
        self,
        state: AgentState[Any],
        runtime: Runtime[Any],  # noqa: ARG002
    ) -> dict[str, Any] | None:
        """Review the main agent's done signal before allowing termination."""
        if not self._active():
            return None

        messages = state.get("messages", [])
        if not _last_ai_declares_done(messages):
            return None

        reviews_so_far = _count_reviews(messages)
        if reviews_so_far >= self.max_reviews:
            logger.debug(
                "ReviewerMiddleware: max_reviews=%d reached, allowing termination",
                self.max_reviews,
            )
            return None

        task_instruction = _extract_task_instruction(messages)
        recent = _recent_agent_messages(messages)
        evidence = self._assemble_evidence(messages)
        verdict = self.reviewer.review(
            task_instruction=task_instruction,
            main_agent_messages=recent,
            evidence=evidence,
        )

        logger.info(
            "ReviewerMiddleware: verdict=%s (pass %d/%d)",
            verdict.status,
            reviews_so_far + 1,
            self.max_reviews,
        )

        if verdict.status == "approve":
            return None

        # Inject the verdict and jump back to model so the main agent
        # can address the concerns. On the next after_model pass we'll
        # either approve or hit max_reviews and allow termination.
        return {
            "messages": [HumanMessage(content=verdict.as_feedback_text())],
            "jump_to": "model",
        }

    def _assemble_evidence(self, messages: list[Any]) -> dict[str, str]:
        """Collect structured signals the reviewer should see beyond prose.

        Current set (PR 5): the diff summary — which files the agent
        wrote and how many times each. Future additions (harness
        check output, arch-lint JSON) can extend this dict without
        changing the reviewer contract.

        Returns an empty dict when nothing is worth sending so the
        prompt stays tight.
        """
        evidence: dict[str, str] = {}
        diff = _extract_diff_summary(messages)
        if diff:
            evidence["diff_summary"] = diff
        return evidence

    @hook_config(can_jump_to=["model"])
    async def aafter_model(
        self,
        state: AgentState[Any],
        runtime: Runtime[Any],  # noqa: ARG002
    ) -> dict[str, Any] | None:
        """Async version."""
        if not self._active():
            return None

        messages = state.get("messages", [])
        if not _last_ai_declares_done(messages):
            return None

        reviews_so_far = _count_reviews(messages)
        if reviews_so_far >= self.max_reviews:
            return None

        task_instruction = _extract_task_instruction(messages)
        recent = _recent_agent_messages(messages)
        evidence = self._assemble_evidence(messages)
        verdict = await self.reviewer.areview(
            task_instruction=task_instruction,
            main_agent_messages=recent,
            evidence=evidence,
        )

        logger.info(
            "ReviewerMiddleware: verdict=%s (pass %d/%d)",
            verdict.status,
            reviews_so_far + 1,
            self.max_reviews,
        )

        if verdict.status == "approve":
            return None

        return {
            "messages": [HumanMessage(content=verdict.as_feedback_text())],
            "jump_to": "model",
        }


__all__ = ["ReviewerMiddleware"]
