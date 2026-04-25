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

Phase C.3 of the agent-harness roadmap; sharp-edge 6 from the second
review extends the evidence assembly to include arch-lint and
business-rule output for the files the agent touched.

Consumes ``ReviewerSubAgent`` from ``reviewer.py`` and ``TaskPolicy``
from ``policy.py``.
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


def _extract_touched_paths(messages: list[Any]) -> dict[str, int]:
    """Return ``{path: write_count}`` for files the main agent edited.

    Order of keys follows first-write order so renderers produce
    stable output. Used by both the diff-summary renderer and the
    arch-lint evidence runner.
    """
    counts: dict[str, int] = {}
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
            counts[path] = counts.get(path, 0) + 1
    return counts


def _format_diff_summary(touched: dict[str, int], *, max_files: int = 25) -> str:
    """Render the touched-paths map as a bulleted markdown summary.

    Returns an empty string when the agent didn't write anything, so
    the caller can drop the section from the prompt.
    """
    if not touched:
        return ""
    paths = list(touched)
    truncated = paths[:max_files]
    lines = [
        f"- `{path}` ({touched[path]} write{'s' if touched[path] > 1 else ''})"
        for path in truncated
    ]
    if len(paths) > max_files:
        lines.append(f"- ... {len(paths) - max_files} more file(s) not listed")
    return "\n".join(lines)


def _extract_diff_summary(messages: list[Any], *, max_files: int = 25) -> str:
    """Backwards-compatible wrapper used by tests.

    Equivalent to ``_format_diff_summary(_extract_touched_paths(messages))``;
    kept as a thin alias so the test suite doesn't have to import two
    helpers when one is enough for the diff-only path.
    """
    return _format_diff_summary(_extract_touched_paths(messages), max_files=max_files)


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
        repo_root: str | None = None,
        arch_edges: dict[str, frozenset[str]] | None = None,
    ) -> None:
        if max_reviews < 1:
            msg = "max_reviews must be >= 1"
            raise ValueError(msg)
        self.reviewer = reviewer
        self.policy = policy
        self.max_reviews = max_reviews
        self.disabled = disabled
        # Sharp-edge 6: when ``repo_root`` is supplied, the middleware
        # runs arch-lint and business-rule checks against the files
        # the agent touched and surfaces the structured output to the
        # reviewer as evidence. Falls back to diff-summary-only when
        # no repo_root is configured.
        self.repo_root = repo_root
        self.arch_edges = arch_edges

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

        Sources (in order of cost and added during PR 5 / sharp-edge 6):

        - ``diff_summary`` — files written and write counts (cheap;
          always available).
        - ``arch_lint_output`` — when a ``repo_root`` is configured,
          run the in-process arch-lint over the touched files and
          surface the JSON-shaped result as a markdown block.
        - ``business_rules_output`` — same idea, against the
          business-rule checker.

        Returns an empty dict when nothing is worth sending so the
        prompt stays tight.
        """
        evidence: dict[str, str] = {}
        touched = _extract_touched_paths(messages)
        diff = _format_diff_summary(touched)
        if diff:
            evidence["diff_summary"] = diff

        if self.repo_root:
            arch = self._run_arch_check(touched)
            if arch:
                evidence["arch_lint_output"] = arch
            business = self._run_business_rule_check()
            if business:
                evidence["business_rules_output"] = business

        return evidence

    def _run_arch_check(self, touched: dict[str, int]) -> str:
        """Render arch-lint output for the touched files as markdown.

        Reads each file from disk and invokes ``check_file`` directly —
        no middleware involvement, no rejection. The point is to give
        the reviewer the same structural signal arch-lint already saw
        without re-running enforcement.
        """
        from pathlib import Path as _Path

        try:
            from deepagents_cli.arch_lint import check_file
        except Exception:  # noqa: BLE001
            return ""

        if not touched:
            return ""
        root = _Path(self.repo_root) if self.repo_root else None
        if root is None or not root.is_dir():
            return ""

        lines: list[str] = []
        for path in touched:
            target = (
                _Path(path) if _Path(path).is_absolute()
                else root / path.lstrip("/")
            )
            if not target.is_file():
                continue
            try:
                source = target.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            violations = check_file(path, source, edges=self.arch_edges)
            if not violations:
                lines.append(f"- `{path}`: clean")
                continue
            for v in violations:
                lines.append(f"- `{path}`: {v.summary()}")
        if not lines:
            return ""
        return "\n".join(lines)

    def _run_business_rule_check(self) -> str:
        """Render the repo-wide business-rule check status as markdown.

        We don't filter by touched files here — the invariants are
        repo-wide rules, and reporting only the files the agent
        touched would let the reviewer miss new violations the agent
        introduced indirectly.
        """
        try:
            from deepagents_cli.business_rule_checker import run_business_rules
        except Exception:  # noqa: BLE001
            return ""

        if not self.repo_root:
            return ""
        try:
            status, summary, violations = run_business_rules(self.repo_root)
        except Exception as exc:  # noqa: BLE001  # checker failure must not kill review
            logger.debug("Business-rule check failed during review: %s", exc)
            return ""
        if status == "not_configured":
            return ""

        lines = [f"**status:** `{status}` — {summary}"]
        block_violations = [v for v in violations if v.severity == "block"]
        warn_violations = [v for v in violations if v.severity == "warn"]
        if block_violations:
            lines.append("\nBlocking:")
            lines.extend(
                f"- `{v.invariant_id}` ({v.file or 'repo'}): {v.detail}"
                for v in block_violations[:10]
            )
        if warn_violations:
            lines.append("\nWarnings:")
            lines.extend(
                f"- `{v.invariant_id}` ({v.file or 'repo'}): {v.detail}"
                for v in warn_violations[:10]
            )
        return "\n".join(lines)

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
