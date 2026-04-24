"""Cumulative completion-token ceiling to break the single-shot dump loop.

Some agents respond to coding tasks by producing 30K-60K tokens of
reasoning in 2-4 steps without ever emitting a concrete solution or
calling tools — the "single-shot dump" failure pattern. The per-call
``max_tokens`` cap doesn't help because the agent makes multiple
oversized calls in sequence.

This middleware tallies cumulative completion tokens across all AI
messages in the conversation. When the total crosses
``soft_ceiling_tokens``, ``after_model`` injects a HumanMessage
telling the agent to stop analyzing and commit to a concrete answer,
then jumps back to the model. Fires at most ``max_interventions``
times per run (default 1) so repeated checklists / budget markers
don't pile on top of each other.

Reads token counts from ``AIMessage.usage_metadata.output_tokens`` when
available, falling back to a char/4 heuristic so it still works against
providers that don't populate usage metadata (e.g. some OpenRouter
shapes).

Wired via ``agent.py:create_cli_agent()`` alongside PreCompletionChecklist.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from langchain.agents.middleware.types import AgentMiddleware, AgentState, hook_config
from langchain_core.messages import AIMessage, HumanMessage

if TYPE_CHECKING:
    from langgraph.runtime import Runtime

logger = logging.getLogger(__name__)

_INTERVENTION_MARKER = "[OUTPUT-CEILING-INTERVENTION]"

_DEFAULT_INTERVENTION = f"""{_INTERVENTION_MARKER}

You've now produced {{tokens:,}} tokens of reasoning/analysis across this
task. That is substantially more than the task should need. Further
analysis is unlikely to help — what is missing is a concrete solution.

Stop analyzing. Right now, emit the files, code, or commands that answer
the task. Use tools (write_file, edit_file, execute) to produce the
artifact. Do not re-derive anything you have already worked out. If you
are unsure of a detail, make the most reasonable choice and move on; you
can refine after the solution exists.

Your next response should be tool calls that produce the actual solution,
not more prose.
"""


def _count_ai_output_tokens(messages: list[Any]) -> int:
    """Sum completion tokens across every AIMessage in the conversation.

    Prefers `usage_metadata.output_tokens` when populated. Falls back to a
    char-length / 4 heuristic so providers without usage metadata still
    get roughly-right counts. Never raises — returns best-effort total.
    """
    total = 0
    for msg in messages:
        if not isinstance(msg, AIMessage):
            continue
        usage = getattr(msg, "usage_metadata", None) or {}
        reported = usage.get("output_tokens") if isinstance(usage, dict) else None
        if isinstance(reported, int) and reported > 0:
            total += reported
            continue
        # Fallback: approximate from content length
        content = msg.content
        if isinstance(content, str):
            total += len(content) // 4
        elif isinstance(content, list):
            for block in content:
                text = block.get("text") if isinstance(block, dict) else None
                if isinstance(text, str):
                    total += len(text) // 4
    return total


def _count_interventions(messages: list[Any]) -> int:
    """How many ceiling interventions have already been injected."""
    return sum(
        1
        for msg in messages
        if isinstance(msg, HumanMessage) and _INTERVENTION_MARKER in str(msg.content)
    )


class OutputCeilingMiddleware(AgentMiddleware):
    """Force a concrete-solution nudge when cumulative completion tokens spike.

    Targets the single-shot-dump pattern: agents that produce 30-60K tokens
    of analysis in a handful of steps without actually writing code. The
    middleware counts cumulative AI output across the conversation; when it
    crosses ``soft_ceiling_tokens``, it injects a nudge and forces another
    model turn. Fires at most ``max_interventions`` times.

    Args:
        soft_ceiling_tokens: Cumulative completion-token threshold that
            triggers intervention. Default 25000 — large enough to allow
            real reasoning on hard tasks, small enough to catch pathological
            dumps before they consume the budget.
        max_interventions: How many times the middleware can fire per run.
            Default 1 — one nudge is usually enough; repeated nudges
            become noise and crowd out real tool output.
        intervention_template: The HumanMessage content injected on
            intervention. Must contain the ``[OUTPUT-CEILING-INTERVENTION]``
            marker for idempotent counting. Supports ``{tokens}`` interpolation.
        disabled: If True, the middleware no-ops. Useful for interactive
            CLI mode where big analysis is expected.
    """

    DEFAULT_SOFT_CEILING_TOKENS = 25000
    DEFAULT_MAX_INTERVENTIONS = 1

    def __init__(
        self,
        *,
        soft_ceiling_tokens: int = DEFAULT_SOFT_CEILING_TOKENS,
        max_interventions: int = DEFAULT_MAX_INTERVENTIONS,
        intervention_template: str = _DEFAULT_INTERVENTION,
        disabled: bool = False,
    ) -> None:
        if soft_ceiling_tokens < 1:
            msg = "soft_ceiling_tokens must be >= 1"
            raise ValueError(msg)
        if max_interventions < 1:
            msg = "max_interventions must be >= 1"
            raise ValueError(msg)
        if _INTERVENTION_MARKER not in intervention_template:
            msg = (
                f"intervention_template must contain marker "
                f"{_INTERVENTION_MARKER!r}"
            )
            raise ValueError(msg)
        self.soft_ceiling_tokens = soft_ceiling_tokens
        self.max_interventions = max_interventions
        self.intervention_template = intervention_template
        self.disabled = disabled

    @hook_config(can_jump_to=["model"])
    def after_model(
        self,
        state: AgentState[Any],
        runtime: Runtime[Any],  # noqa: ARG002
    ) -> dict[str, Any] | None:
        """Intercept after each model turn; nudge if output spikes."""
        return self._maybe_intervene(state)

    @hook_config(can_jump_to=["model"])
    async def aafter_model(
        self,
        state: AgentState[Any],
        runtime: Runtime[Any],  # noqa: ARG002
    ) -> dict[str, Any] | None:
        """Async version."""
        return self._maybe_intervene(state)

    def _maybe_intervene(
        self,
        state: AgentState[Any],
    ) -> dict[str, Any] | None:
        if self.disabled:
            return None
        messages = state.get("messages", [])

        interventions = _count_interventions(messages)
        if interventions >= self.max_interventions:
            return None

        cumulative = _count_ai_output_tokens(messages)
        if cumulative < self.soft_ceiling_tokens:
            return None

        logger.info(
            "OutputCeiling: intervening at %d tokens (ceiling=%d, pass %d/%d)",
            cumulative,
            self.soft_ceiling_tokens,
            interventions + 1,
            self.max_interventions,
        )
        content = self.intervention_template.format(tokens=cumulative)
        return {
            "messages": [HumanMessage(content=content)],
            "jump_to": "model",
        }


__all__ = ["OutputCeilingMiddleware"]
