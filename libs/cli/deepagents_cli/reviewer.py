"""ReviewerSubAgent — a second-pass critique of the main agent's work.

When a task policy marks ``require_reviewer=True``, the harness invokes
this sub-agent after the main agent signals completion. The reviewer
gets the task instruction plus the main agent's final messages and
returns a structured ``ReviewVerdict``. Depending on the verdict the
harness either allows termination, injects the concerns as a
HumanMessage for the main agent to address, or blocks the run entirely.

The reviewer is deliberately stateless and uses a distinct system prompt
focused on finding problems — not producing solutions. It does not have
tool access; it only reads what the main agent produced.

This module ships the reviewer + verdict types. The policy-gated
middleware that invokes the reviewer lives in ``reviewer_middleware.py``.

Phase C.1 + C.2 of the agent-harness roadmap.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel

logger = logging.getLogger(__name__)


# --- Types ---------------------------------------------------------------


VERDICT_STATUSES = frozenset({"approve", "request_changes", "block"})


@dataclass(frozen=True)
class ReviewVerdict:
    """Structured output of a reviewer pass.

    Attributes:
        status: One of ``approve`` (agent may terminate),
            ``request_changes`` (agent must address concerns and
            retry), or ``block`` (run cannot continue without human
            intervention).
        summary: One-paragraph human-readable assessment. Shown to the
            user; also injected back to the main agent on
            ``request_changes``.
        concerns: Specific issues the reviewer found. Empty when
            status is ``approve``.
        required_fixes: Concrete actions the main agent should take
            when status is ``request_changes``. Empty otherwise.
    """

    status: str
    summary: str
    concerns: tuple[str, ...] = ()
    required_fixes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.status not in VERDICT_STATUSES:
            allowed = ", ".join(sorted(VERDICT_STATUSES))
            msg = f"status must be one of [{allowed}], got {self.status!r}"
            raise ValueError(msg)

    def as_feedback_text(self) -> str:
        """Render the verdict as a HumanMessage the main agent can read.

        Only meaningful when ``status == "request_changes"`` or
        ``"block"``. On approve, the caller generally doesn't inject
        anything back into the conversation.
        """
        pieces: list[str] = [
            f"[REVIEWER VERDICT: {self.status.upper()}]",
            "",
            self.summary.strip() or "No summary provided.",
        ]
        if self.concerns:
            pieces.append("")
            pieces.append("Concerns:")
            pieces.extend(f"- {c}" for c in self.concerns)
        if self.required_fixes:
            pieces.append("")
            pieces.append("Required fixes before you declare done again:")
            pieces.extend(f"- {f}" for f in self.required_fixes)
        return "\n".join(pieces)


# --- Prompt ---------------------------------------------------------------


REVIEWER_SYSTEM_PROMPT = """\
You are a code reviewer. Your only job is to critique the work another
agent just completed — not to extend it, refactor it, or write more
code.

You are given:
- The original task the main agent was asked to do.
- The main agent's final response and the recent conversation that
  led to it.

Your job is to produce a verdict with one of three statuses:

- `approve`: the agent's work is correct, complete, and matches the
  task requirements. No action needed.
- `request_changes`: the agent's work has specific issues that must
  be fixed before it terminates. You must list each issue concretely.
- `block`: the work is fundamentally off-track — wrong approach,
  dangerous change, out-of-scope modification. Escalate to a human.

Output format: a JSON object with these fields only, wrapped in a
```json fenced block.

{
  "status": "approve" | "request_changes" | "block",
  "summary": "one paragraph assessment",
  "concerns": ["concrete issue 1", "concrete issue 2"],
  "required_fixes": ["specific action 1", "specific action 2"]
}

`concerns` and `required_fixes` must be present but may be empty lists
on approve. Do not include any other text outside the fenced JSON
block. The harness parses only the JSON."""


# --- Parsing -------------------------------------------------------------


_JSON_FENCE_RE = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL)
_NAKED_JSON_RE = re.compile(r"(\{[\s\S]*?\})", re.DOTALL)


def _extract_json(text: str) -> str | None:
    """Pull a JSON object out of the reviewer's response.

    Prefers a ``json``-fenced block so we don't match example JSON in a
    summary. Falls back to the first balanced-looking ``{...}`` if no
    fence is present — not perfect but the reviewer is instructed to
    use the fence.
    """
    fence_match = _JSON_FENCE_RE.search(text)
    if fence_match:
        return fence_match.group(1)
    naked_match = _NAKED_JSON_RE.search(text)
    if naked_match:
        return naked_match.group(1)
    return None


def parse_verdict(raw: str) -> ReviewVerdict:
    """Parse a reviewer response into a ``ReviewVerdict``.

    Returns a ``block`` verdict with the parse error as the concern if
    the response isn't valid JSON — that way the harness still has a
    useful signal even when the reviewer misbehaves.
    """
    extracted = _extract_json(raw)
    if extracted is None:
        return ReviewVerdict(
            status="block",
            summary="Reviewer output did not contain a JSON verdict.",
            concerns=(
                "Reviewer response was unparseable — see full text in "
                "the run log.",
            ),
        )
    try:
        payload = json.loads(extracted)
    except json.JSONDecodeError as exc:
        return ReviewVerdict(
            status="block",
            summary=f"Reviewer JSON failed to parse: {exc}",
            concerns=("Reviewer response was malformed.",),
        )

    if not isinstance(payload, dict):
        return ReviewVerdict(
            status="block",
            summary="Reviewer returned non-object JSON.",
        )

    status = str(payload.get("status", "")).strip() or "block"
    if status not in VERDICT_STATUSES:
        return ReviewVerdict(
            status="block",
            summary=f"Reviewer returned unknown status {status!r}.",
        )

    summary = str(payload.get("summary", "")).strip()

    def _as_tuple(key: str) -> tuple[str, ...]:
        value = payload.get(key)
        if isinstance(value, list):
            return tuple(str(v) for v in value if v)
        return ()

    return ReviewVerdict(
        status=status,
        summary=summary,
        concerns=_as_tuple("concerns"),
        required_fixes=_as_tuple("required_fixes"),
    )


# --- Reviewer agent -------------------------------------------------------


@dataclass
class ReviewerSubAgent:
    """Invoke a reviewer pass against the main agent's trajectory.

    The reviewer uses the same LLM provider as the main agent by
    default but gets an independent conversation with a
    review-focused system prompt. It does not receive tools; the
    review is advisory, produced from what the main agent wrote.

    Args:
        model: A LangChain ``BaseChatModel`` instance. Any model capable
            of producing JSON works; Anthropic/OpenAI/OpenRouter all do.
        system_prompt: Override the default review system prompt.
        instruction_prefix: Text prepended to the main agent's
            trajectory when assembling the reviewer's input. Useful for
            injecting policy-specific context (e.g. "This is a
            security-fix task — apply stricter review criteria").
    """

    model: BaseChatModel
    system_prompt: str = REVIEWER_SYSTEM_PROMPT
    instruction_prefix: str = ""

    def review(
        self,
        *,
        task_instruction: str,
        main_agent_messages: list[Any],
    ) -> ReviewVerdict:
        """Run the reviewer synchronously; return a structured verdict.

        Args:
            task_instruction: The original task the main agent received.
            main_agent_messages: The main agent's recent messages — at
                minimum the final AIMessage, ideally the last 3-5 turns
                for context. The reviewer doesn't need the full
                trajectory and sending it all wastes tokens.

        Returns:
            A ``ReviewVerdict``. Never raises — malformed model output
            becomes a ``block`` verdict with the parse error as the
            concern.
        """
        review_messages = self._build_review_messages(
            task_instruction=task_instruction,
            main_agent_messages=main_agent_messages,
        )
        try:
            response = self.model.invoke(review_messages)
        except Exception as exc:  # noqa: BLE001  # reviewer failures must not kill the run
            logger.warning("Reviewer invocation failed: %s", exc)
            return ReviewVerdict(
                status="block",
                summary=f"Reviewer model invocation raised: {exc}",
                concerns=("Reviewer could not produce a verdict.",),
            )
        text = _extract_response_text(response)
        return parse_verdict(text)

    async def areview(
        self,
        *,
        task_instruction: str,
        main_agent_messages: list[Any],
    ) -> ReviewVerdict:
        """Async variant of ``review``."""
        review_messages = self._build_review_messages(
            task_instruction=task_instruction,
            main_agent_messages=main_agent_messages,
        )
        try:
            response = await self.model.ainvoke(review_messages)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Async reviewer invocation failed: %s", exc)
            return ReviewVerdict(
                status="block",
                summary=f"Reviewer model invocation raised: {exc}",
                concerns=("Reviewer could not produce a verdict.",),
            )
        text = _extract_response_text(response)
        return parse_verdict(text)

    def _build_review_messages(
        self,
        *,
        task_instruction: str,
        main_agent_messages: list[Any],
    ) -> list[Any]:
        """Assemble the message list for the reviewer invocation."""
        trajectory_dump = _render_trajectory(main_agent_messages)
        prefix = self.instruction_prefix.strip()
        prefix_text = f"{prefix}\n\n" if prefix else ""
        user_text = (
            f"{prefix_text}## Task given to the main agent\n\n"
            f"{task_instruction.strip()}\n\n"
            f"## Main agent's recent trajectory\n\n{trajectory_dump}\n\n"
            "Produce your verdict as JSON now."
        )
        return [
            SystemMessage(content=self.system_prompt),
            HumanMessage(content=user_text),
        ]


# --- Helpers --------------------------------------------------------------


def _render_trajectory(messages: list[Any], max_chars: int = 12_000) -> str:
    """Flatten the main agent's recent messages into reviewer-readable text.

    Keeps the tail of the conversation (most recent first by default
    since that's where the "done" signal is). Truncates to ``max_chars``
    total so an over-long trajectory doesn't blow the reviewer's
    context window.
    """
    lines: list[str] = []
    total = 0
    for msg in messages:
        role = _role_label(msg)
        content = msg.content if hasattr(msg, "content") else str(msg)
        if isinstance(content, list):
            content = "\n".join(
                part.get("text", "") if isinstance(part, dict) else str(part)
                for part in content
            )
        elif not isinstance(content, str):
            content = str(content)
        chunk = f"[{role}]\n{content.strip()}\n"
        total += len(chunk)
        if total > max_chars:
            lines.append("[... earlier messages truncated ...]")
            break
        lines.append(chunk)
    return "\n".join(lines)


def _role_label(msg: Any) -> str:
    if isinstance(msg, AIMessage):
        return "agent"
    if isinstance(msg, HumanMessage):
        return "user"
    if isinstance(msg, SystemMessage):
        return "system"
    return type(msg).__name__.lower()


def _extract_response_text(response: Any) -> str:
    """Pull text content out of a model response message."""
    content = getattr(response, "content", response)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                text = block.get("text")
                if isinstance(text, str):
                    parts.append(text)
            else:
                parts.append(str(block))
        return "\n".join(parts)
    return str(content)


__all__ = [
    "REVIEWER_SYSTEM_PROMPT",
    "ReviewVerdict",
    "ReviewerSubAgent",
    "VERDICT_STATUSES",
    "parse_verdict",
]
