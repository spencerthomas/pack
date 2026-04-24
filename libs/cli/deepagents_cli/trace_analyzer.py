"""Trace analyzer — categorize a failed trial into a promotable lesson.

Phase E.1 of the agent-harness roadmap. Takes a Harbor trial directory
(or equivalent inputs) plus an optional ``ReviewVerdict`` and produces
a ``TraceInsight`` naming the category of failure and what durable
artifact should capture it.

Categories (frozen at this phase):

- ``missing_context`` — agent behaved reasonably given its input but
  lacked a documented rule or example that would have helped. Promote
  to a domain-pack ``rules.md`` entry.
- ``missing_rule`` — agent made a structural choice that should be
  prevented by an arch-lint rule or a scope policy. Promote to
  ``arch_lint.PACKAGE_EDGES`` or a policy preset.
- ``missing_tool`` — agent tried to do something the tool surface
  doesn't support cleanly (e.g. multi-step shell pipelines blocked by
  the allowlist). Promote to a new tool or an allowlist expansion.
- ``missing_example`` — agent reached a dead end that a golden
  example in a context pack would have resolved. Promote to
  ``<pack>/examples/``.
- ``model_capability_limit`` — agent's behaviour is indistinguishable
  from "the model isn't strong enough for this task." Not promotable;
  flag as a known limit.

The analyzer is deterministic (feature rules over trajectory shape,
reviewer verdict, and test outcome). No LLM call — LLM-powered
elaboration is a separate step that can build on this categorization.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

from deepagents_cli.reviewer import ReviewVerdict

logger = logging.getLogger(__name__)


CATEGORIES = frozenset(
    {
        "missing_context",
        "missing_rule",
        "missing_tool",
        "missing_example",
        "model_capability_limit",
    }
)


# --- Value types --------------------------------------------------------


@dataclass(frozen=True)
class TrialSignals:
    """Low-level features extracted from a trial.

    Attributes:
        task_name: Trial identifier (human-readable).
        passed: True when the verifier marked the trial as a pass.
        agent_timed_out: True when the agent hit its wall-clock limit.
        steps: Number of agent turns in the trajectory.
        prompt_tokens: Total input tokens sent to the model.
        completion_tokens: Total output tokens emitted by the model.
        tool_call_count: How many tool calls the agent made.
        scope_rejections: Count of ScopeEnforcement rejections in the
            run's tool-call history (proxy: messages mentioning the
            rejection marker).
        arch_rejections: Count of ArchLint rejections.
        checklist_cycles: How many PreCompletionChecklist cycles ran.
        has_reviewer_verdict: Whether a reviewer pass happened.
    """

    task_name: str
    passed: bool
    agent_timed_out: bool
    steps: int
    prompt_tokens: int
    completion_tokens: int
    tool_call_count: int
    scope_rejections: int
    arch_rejections: int
    checklist_cycles: int
    has_reviewer_verdict: bool


@dataclass(frozen=True)
class TraceInsight:
    """Analyzer output — category plus evidence and a promotion proposal.

    Attributes:
        category: One of ``CATEGORIES``.
        confidence: ``"low" | "medium" | "high"``. Low-confidence
            insights should always require human review before
            promotion; high-confidence can be auto-applied under
            governance (Phase E.3).
        summary: One-line human-readable description of what happened.
        evidence: Concrete signals that drove the categorization.
        proposed_promotion: A short suggestion of what durable artifact
            (doc, test, rule, example) should capture the lesson.
    """

    category: str
    confidence: str
    summary: str
    evidence: tuple[str, ...]
    proposed_promotion: str

    def __post_init__(self) -> None:
        if self.category not in CATEGORIES:
            allowed = ", ".join(sorted(CATEGORIES))
            msg = f"category must be one of [{allowed}], got {self.category!r}"
            raise ValueError(msg)
        if self.confidence not in {"low", "medium", "high"}:
            msg = f"confidence must be low/medium/high, got {self.confidence!r}"
            raise ValueError(msg)


# --- I/O ---------------------------------------------------------------


def extract_signals(trial_dir: str | Path) -> TrialSignals:
    """Read a Harbor trial directory and produce normalized signals.

    Tolerates missing or malformed files — anything we can't read
    contributes a zero/False default rather than raising, so the
    analyzer stays useful on partial data. The caller is responsible
    for verifying ``trial_dir`` exists.
    """
    root = Path(trial_dir)
    result_json_path = root / "result.json"
    traj_path = root / "agent" / "trajectory.json"

    passed = False
    agent_timed_out = False
    task_name = root.name

    if result_json_path.is_file():
        try:
            result = json.loads(result_json_path.read_text())
        except (OSError, json.JSONDecodeError):
            result = {}
        verifier = result.get("verifier_result") or {}
        rewards = verifier.get("rewards") or {}
        reward = rewards.get("reward")
        passed = reward == 1.0
        exc = result.get("exception_info") or {}
        if isinstance(exc, dict):
            agent_timed_out = "Timeout" in str(exc.get("type", ""))
        task_name = result.get("task_name", task_name) or task_name

    steps = 0
    prompt_tokens = 0
    completion_tokens = 0
    scope_rejections = 0
    arch_rejections = 0
    checklist_cycles = 0
    has_reviewer_verdict = False
    tool_call_count = 0

    if traj_path.is_file():
        try:
            traj = json.loads(traj_path.read_text())
        except (OSError, json.JSONDecodeError):
            traj = {}
        metrics = traj.get("final_metrics", {})
        prompt_tokens = int(metrics.get("total_prompt_tokens", 0))
        completion_tokens = int(metrics.get("total_completion_tokens", 0))
        steps_list = traj.get("steps", []) or []
        steps = len(steps_list)

        text_dump = traj_path.read_text(errors="replace")
        scope_rejections = text_dump.count("Scope violation")
        arch_rejections = text_dump.count("Arch-lint violation")
        checklist_cycles = text_dump.count("[PRECOMPLETION-CHECKLIST]")
        has_reviewer_verdict = "[REVIEWER VERDICT:" in text_dump
        # A rough proxy — counting JSON tool-call blocks is better but
        # the trajectory format varies by backend. Count lines that look
        # like tool names bracketed in the prose instead.
        tool_call_count = text_dump.count('"tool_calls"')

    return TrialSignals(
        task_name=task_name,
        passed=passed,
        agent_timed_out=agent_timed_out,
        steps=steps,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        tool_call_count=tool_call_count,
        scope_rejections=scope_rejections,
        arch_rejections=arch_rejections,
        checklist_cycles=checklist_cycles,
        has_reviewer_verdict=has_reviewer_verdict,
    )


# --- Categorizer --------------------------------------------------------


# Dump thresholds match the OutputCeiling defaults so the vocabulary
# stays consistent across the harness.
_DUMP_COMPLETION_FLOOR = 25_000
_DUMP_STEP_CEILING = 6


def analyze(
    signals: TrialSignals,
    *,
    verdict: ReviewVerdict | None = None,
) -> TraceInsight:
    """Produce a ``TraceInsight`` from trial signals (and optional verdict).

    Passing trials return a ``missing_context`` insight only when the
    verdict was ``request_changes`` — a pass with review concerns is
    still a "works but learnable" moment worth capturing.

    Resolution order is deliberate: structural signals (scope / arch
    rejections) beat behavioural signals (timeout / dump pattern) beat
    reviewer content. That matches the "fix architecture first" bias
    of the harness.
    """
    # --- 1. Architectural rejections imply a missing rule ------------
    if signals.arch_rejections > 0:
        return TraceInsight(
            category="missing_rule",
            confidence="high",
            summary=(
                f"Agent hit {signals.arch_rejections} arch-lint "
                "rejection(s). The rule is already encoded — the "
                "lesson is that context should have told the agent "
                "about it sooner."
            ),
            evidence=(
                f"arch_rejections={signals.arch_rejections}",
                f"steps={signals.steps}",
            ),
            proposed_promotion=(
                "Update the relevant context pack's rules.md with an "
                "explicit note about the package dependency direction "
                "so future agents don't try the same import."
            ),
        )

    # --- 2. Scope rejections imply a missing tool or policy gap ------
    if signals.scope_rejections > 2:
        return TraceInsight(
            category="missing_tool",
            confidence="medium",
            summary=(
                f"Agent attempted {signals.scope_rejections} out-of-"
                "scope writes. Either the task needs a wider scope or "
                "the agent is reaching for the wrong tool (e.g. should "
                "have used read-only inspection)."
            ),
            evidence=(
                f"scope_rejections={signals.scope_rejections}",
                f"passed={signals.passed}",
            ),
            proposed_promotion=(
                "Consider either (a) relaxing the policy's allowed_paths "
                "for this task type, or (b) adding a dedicated tool for "
                "the operation the agent kept trying to achieve via "
                "file writes."
            ),
        )

    # --- 3. Single-shot dump pattern --------------------------------
    if (
        not signals.passed
        and signals.steps <= _DUMP_STEP_CEILING
        and signals.completion_tokens >= _DUMP_COMPLETION_FLOOR
    ):
        return TraceInsight(
            category="missing_example",
            confidence="medium",
            summary=(
                f"Single-shot dump pattern: {signals.completion_tokens:,} "
                f"completion tokens in {signals.steps} steps, no solution. "
                "The agent needs a concrete example of how to decompose "
                "this kind of task instead of reasoning monolithically."
            ),
            evidence=(
                f"steps={signals.steps}",
                f"completion_tokens={signals.completion_tokens}",
            ),
            proposed_promotion=(
                "Add a worked example under the relevant context pack's "
                "examples/ directory showing the EXPECTED decomposition: "
                "read-then-plan-then-write, not reason-then-commit."
            ),
        )

    # --- 4. Agent timeout with productive steps ---------------------
    if signals.agent_timed_out and signals.tool_call_count > 5:
        return TraceInsight(
            category="missing_context",
            confidence="medium",
            summary=(
                "Agent worked through the budget with productive tool "
                "use but didn't converge. Usually means the task's "
                "verification criteria were implicit, so the agent "
                "couldn't tell it was done."
            ),
            evidence=(
                f"agent_timed_out={signals.agent_timed_out}",
                f"tool_call_count={signals.tool_call_count}",
                f"prompt_tokens={signals.prompt_tokens}",
            ),
            proposed_promotion=(
                "Add a done-condition checklist to the context pack's "
                "rules.md so the agent can self-verify mid-run instead "
                "of grinding until timeout."
            ),
        )

    # --- 5. Verdict-driven insight for passes with concerns ----------
    if signals.passed and verdict is not None and verdict.status != "approve":
        return TraceInsight(
            category="missing_context",
            confidence="low",
            summary=(
                "Trial passed verification but the reviewer flagged "
                "concerns. The fix didn't break anything this time — "
                "still worth capturing as a rule for next time."
            ),
            evidence=(
                f"verdict_status={verdict.status}",
                f"concerns={len(verdict.concerns)}",
            ),
            proposed_promotion=(
                "Review the verdict's required_fixes and promote any "
                "generalizable advice into the domain pack's rules.md."
            ),
        )

    # --- 6. No productive activity — model capability floor ---------
    if not signals.passed and signals.tool_call_count < 2:
        return TraceInsight(
            category="model_capability_limit",
            confidence="low",
            summary=(
                "Minimal productive activity before failure. Model did "
                "not engage with the task at all — usually an API hang "
                "or the model giving up."
            ),
            evidence=(
                f"tool_call_count={signals.tool_call_count}",
                f"completion_tokens={signals.completion_tokens}",
            ),
            proposed_promotion=(
                "Flag in known-limits.md; check provider health before "
                "re-running. Not a rule-level fix."
            ),
        )

    # --- 7. Fallback: something off, classifier can't pin it --------
    return TraceInsight(
        category="missing_context",
        confidence="low",
        summary=(
            "Trial did not pass and no specific pattern matched. "
            "Worth a human read of the reflection before deciding."
        ),
        evidence=(
            f"passed={signals.passed}",
            f"steps={signals.steps}",
        ),
        proposed_promotion=(
            "Read the trajectory and decide manually what kind of "
            "artifact would have helped."
        ),
    )


# --- Convenience -------------------------------------------------------


def analyze_trial(
    trial_dir: str | Path,
    *,
    verdict: ReviewVerdict | None = None,
) -> TraceInsight:
    """Helper: ``extract_signals`` + ``analyze`` in one call."""
    signals = extract_signals(trial_dir)
    return analyze(signals, verdict=verdict)


__all__ = [
    "CATEGORIES",
    "TraceInsight",
    "TrialSignals",
    "analyze",
    "analyze_trial",
    "extract_signals",
]
