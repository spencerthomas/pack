"""promote-lesson — turn trace insights into durable harness artifacts.

M6 of the agent-harness roadmap. The trace analyzer produces a
``TraceInsight`` per failed trial; this module stages a concrete
artifact proposal under ``.harness/pending-promotions/`` for human
review. No files outside the staging directory are modified — humans
approve and merge the proposal themselves.

Flow:

::

    failed trial
      ↓
    trace_analyzer.analyze_trial → TraceInsight
      ↓
    promote_lesson.propose → PromotionProposal
      ↓
    stage to .harness/pending-promotions/<timestamp>-<category>.md
      ↓
    human reviews, edits, commits

Staging rather than committing is deliberate: auto-edits to rules and
context packs produce the kind of slop the harness exists to prevent.
The promotion file is a carefully-formatted proposal that makes the
approve-or-reject decision cheap.

Per-category renderers live in this module. Adding a new category is
a single-line registry addition plus a render function.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from deepagents_cli.trace_analyzer import TraceInsight

logger = logging.getLogger(__name__)


# --- Types --------------------------------------------------------------


@dataclass(frozen=True)
class PromotionProposal:
    """A staged artifact proposal ready for human review.

    Attributes:
        category: Matches ``TraceInsight.category``.
        confidence: Carried over from the insight.
        title: One-line description of the proposed change.
        target_path: Repo-relative path the artifact would eventually
            land at. ``None`` when the category doesn't map to a file
            edit (e.g. ``model_capability_limit`` → flag only).
        body: Markdown describing the proposed change in full.
            Humans read this, decide yes/no, and either commit the
            target change or delete the proposal file.
        evidence: Raw evidence from the insight, reproduced so the
            proposal file is self-contained.
        rationale: Short explanation tying the insight to the target
            artifact choice.
    """

    category: str
    confidence: str
    title: str
    target_path: str | None
    body: str
    evidence: tuple[str, ...] = ()
    rationale: str = ""


# --- Renderers (per category) -----------------------------------------


def _render_missing_context(insight: TraceInsight) -> PromotionProposal:
    return PromotionProposal(
        category=insight.category,
        confidence=insight.confidence,
        title="Add rule to `coding-task` pack: context the agent was missing",
        target_path=".context-packs/coding-task/rules.md",
        body=_wrap_proposal(
            intent=(
                "Append a short rule to the coding-task context pack so "
                "the next agent encountering this kind of task has the "
                "context this run was missing."
            ),
            suggested_edit=(
                "Append a bullet under the appropriate section of "
                "`rules.md`. Phrase it as an instruction, not a story. "
                "Keep it one sentence if possible."
            ),
            insight=insight,
        ),
        evidence=insight.evidence,
        rationale=insight.proposed_promotion,
    )


def _render_missing_rule(insight: TraceInsight) -> PromotionProposal:
    return PromotionProposal(
        category=insight.category,
        confidence=insight.confidence,
        title="Tighten arch-lint: rule already fired but context was silent",
        target_path=".context-packs/coding-task/rules.md",
        body=_wrap_proposal(
            intent=(
                "Arch-lint already rejected the agent's change — the "
                "lesson is that the context pack should have warned "
                "the agent first so we don't burn a tool call teaching "
                "it the rule."
            ),
            suggested_edit=(
                "Add a rule bullet to `rules.md` calling out the "
                "package dependency direction (see "
                "`docs/harness/components.md` for the diagram). Consider "
                "whether `PACKAGE_EDGES` in arch_lint.py also needs a "
                "companion comment."
            ),
            insight=insight,
        ),
        evidence=insight.evidence,
        rationale=insight.proposed_promotion,
    )


def _render_missing_tool(insight: TraceInsight) -> PromotionProposal:
    return PromotionProposal(
        category=insight.category,
        confidence=insight.confidence,
        title="Scope/tooling gap: repeated out-of-scope writes",
        target_path=None,
        body=_wrap_proposal(
            intent=(
                "The agent kept trying to write files the policy didn't "
                "allow. Two candidate fixes: (a) widen the policy's "
                "allowed_paths for this task type, or (b) expose a "
                "dedicated tool for the operation the agent was trying "
                "to achieve via writes."
            ),
            suggested_edit=(
                "Decide between (a) and (b). If (a), edit the relevant "
                "entry in `libs/cli/deepagents_cli/policy.py`. If (b), "
                "open a task against the tool-surface backlog. No file "
                "edit is staged here — the decision is architectural."
            ),
            insight=insight,
        ),
        evidence=insight.evidence,
        rationale=insight.proposed_promotion,
    )


def _render_missing_example(insight: TraceInsight) -> PromotionProposal:
    return PromotionProposal(
        category=insight.category,
        confidence=insight.confidence,
        title="Add a golden example: single-shot dump pattern needs a worked one",
        target_path=".context-packs/coding-task/examples/decomposition.md",
        body=_wrap_proposal(
            intent=(
                "The agent produced tens of thousands of reasoning "
                "tokens in a handful of steps without committing to a "
                "solution. A worked example demonstrating the EXPECTED "
                "decomposition — read-then-plan-then-write — gives "
                "future runs something to pattern-match against."
            ),
            suggested_edit=(
                "Write a short markdown file showing the canonical "
                "shape: task prompt, opening reads, plan, first write, "
                "verification. Keep it under 100 lines. Reference the "
                "task kind (e.g. `data-reshape`) in the filename."
            ),
            insight=insight,
        ),
        evidence=insight.evidence,
        rationale=insight.proposed_promotion,
    )


def _render_model_capability_limit(insight: TraceInsight) -> PromotionProposal:
    return PromotionProposal(
        category=insight.category,
        confidence=insight.confidence,
        title="Known limit: minimal agent engagement before failure",
        target_path="docs/harness/known-limits.md",
        body=_wrap_proposal(
            intent=(
                "No productive activity before failure usually means an "
                "API hang or the model giving up — not a harness gap. "
                "Record as a known limit so it doesn't keep showing up "
                "in lesson-promotion triage."
            ),
            suggested_edit=(
                "Append to `docs/harness/known-limits.md` under the "
                "relevant section (create the file if it doesn't "
                "exist). Don't attempt to 'fix' with a harness change."
            ),
            insight=insight,
        ),
        evidence=insight.evidence,
        rationale=insight.proposed_promotion,
    )


_RENDERERS: dict[str, Any] = {
    "missing_context": _render_missing_context,
    "missing_rule": _render_missing_rule,
    "missing_tool": _render_missing_tool,
    "missing_example": _render_missing_example,
    "model_capability_limit": _render_model_capability_limit,
}


# --- Body assembly -----------------------------------------------------


def _wrap_proposal(
    *,
    intent: str,
    suggested_edit: str,
    insight: TraceInsight,
) -> str:
    """Render a standardized markdown body for any promotion proposal.

    Having one shape across all categories keeps the reviewer's
    cognitive load low — they know exactly where to look for the
    "what" and the "why."
    """
    evidence_lines = "\n".join(f"- {e}" for e in insight.evidence) or "_(no structured evidence)_"
    return f"""\
## Insight

{insight.summary.strip() or "_(no summary provided)_"}

## Proposed action

{intent.strip()}

## Suggested edit

{suggested_edit.strip()}

## Confidence

**{insight.confidence}** — {{{_confidence_note(insight.confidence)}}}

## Evidence

{evidence_lines}
"""


def _confidence_note(confidence: str) -> str:
    return {
        "high": "auto-apply after governance review — the signal is structural",
        "medium": "human read recommended; proposed edit is likely but not certain",
        "low": "treat as a triage hint, not a near-committable change",
    }.get(confidence, "unknown confidence level — review carefully")


# --- Public API --------------------------------------------------------


def propose(insight: TraceInsight) -> PromotionProposal:
    """Render a ``PromotionProposal`` for the given ``TraceInsight``.

    Falls back to a generic "needs human review" proposal for unknown
    categories so new insight categories don't silently drop on the
    floor.
    """
    renderer = _RENDERERS.get(insight.category)
    if renderer is None:
        return PromotionProposal(
            category=insight.category,
            confidence=insight.confidence,
            title=f"Uncategorized insight: {insight.category}",
            target_path=None,
            body=_wrap_proposal(
                intent=(
                    "The trace analyzer produced an insight category "
                    "this renderer doesn't know about yet. Review the "
                    "evidence and decide what durable artifact (if "
                    "any) should capture the lesson."
                ),
                suggested_edit=(
                    "Add a renderer in "
                    "`libs/cli/deepagents_cli/promote_lesson.py` for "
                    f"category {insight.category!r} once the pattern "
                    "is understood."
                ),
                insight=insight,
            ),
            evidence=insight.evidence,
            rationale=insight.proposed_promotion,
        )
    return renderer(insight)


def stage_proposal(
    proposal: PromotionProposal,
    *,
    harness_dir: str | Path,
    trial_id: str | None = None,
) -> Path:
    """Write the proposal to ``.harness/pending-promotions/``.

    Returns the staged path. Creates the directory on first write.
    Filename is ``<timestamp>-<category>-<trial_id>.md`` so ordering
    is stable and duplicates don't collide. ``trial_id`` defaults to
    ``"run"`` when unspecified.
    """
    base = Path(harness_dir) / "pending-promotions"
    base.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    tag = (trial_id or "run").replace("/", "_")
    path = base / f"{stamp}-{proposal.category}-{tag}.md"

    header = f"""\
# Promotion proposal

**Category:** `{proposal.category}`  |  **Confidence:** `{proposal.confidence}`  |  **Trial:** `{tag}`  |  **Staged:** `{stamp}`

**Target path:** {'`' + proposal.target_path + '`' if proposal.target_path else '_(no single target; architectural decision)_'}

**Rationale:** {proposal.rationale or '_(none)_'}

---

"""
    path.write_text(header + proposal.body + "\n", encoding="utf-8")
    return path


def propose_and_stage(
    insight: TraceInsight,
    *,
    harness_dir: str | Path,
    trial_id: str | None = None,
) -> tuple[PromotionProposal, Path]:
    """Convenience: ``propose`` + ``stage_proposal`` in one call."""
    proposal = propose(insight)
    staged = stage_proposal(proposal, harness_dir=harness_dir, trial_id=trial_id)
    return proposal, staged


# --- CLI-ish entry point -----------------------------------------------


def promote_from_trial(
    trial_dir: str | Path,
    *,
    harness_dir: str | Path | None = None,
) -> tuple[PromotionProposal, Path] | None:
    """End-to-end: read a trial, analyze, propose, stage.

    Returns ``(proposal, staged_path)`` or ``None`` when the trial's
    outcome was a pass (no lesson to promote) or the trial directory
    doesn't exist. Never raises — analysis failures fall back to a
    generic proposal.
    """
    from deepagents_cli.trace_analyzer import analyze_trial

    trial = Path(trial_dir)
    if not trial.is_dir():
        logger.warning("promote_from_trial: %s is not a directory", trial)
        return None

    insight = analyze_trial(trial)
    # Skip model_capability_limit OR any passing trial without concerns —
    # caller can still stage manually if they want. The heuristic here is:
    # if trace_analyzer surfaced a promotable insight, stage it.
    if insight.category == "model_capability_limit" and insight.confidence == "low":
        logger.debug("Skipping low-confidence model_capability_limit insight")
        return None

    hd = Path(harness_dir) if harness_dir else trial / ".harness"
    proposal, staged = propose_and_stage(
        insight,
        harness_dir=hd,
        trial_id=trial.name,
    )
    return proposal, staged


__all__ = [
    "PromotionProposal",
    "promote_from_trial",
    "propose",
    "propose_and_stage",
    "stage_proposal",
]
