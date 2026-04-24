"""Tests for trace analyzer (Phase E.1)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from deepagents_cli.reviewer import ReviewVerdict
from deepagents_cli.trace_analyzer import (
    CATEGORIES,
    TraceInsight,
    TrialSignals,
    analyze,
    analyze_trial,
    extract_signals,
)


def _signals(**overrides: object) -> TrialSignals:
    defaults: dict[str, object] = {
        "task_name": "test",
        "passed": False,
        "agent_timed_out": False,
        "steps": 10,
        "prompt_tokens": 10_000,
        "completion_tokens": 3_000,
        "tool_call_count": 5,
        "scope_rejections": 0,
        "arch_rejections": 0,
        "checklist_cycles": 1,
        "has_reviewer_verdict": False,
    }
    defaults.update(overrides)
    return TrialSignals(**defaults)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# TraceInsight validation
# ---------------------------------------------------------------------------


def test_insight_rejects_unknown_category() -> None:
    with pytest.raises(ValueError, match="category"):
        TraceInsight(
            category="not-a-real-category",
            confidence="high",
            summary="",
            evidence=(),
            proposed_promotion="",
        )


def test_insight_rejects_unknown_confidence() -> None:
    with pytest.raises(ValueError, match="confidence"):
        TraceInsight(
            category="missing_context",
            confidence="maybe",
            summary="",
            evidence=(),
            proposed_promotion="",
        )


# ---------------------------------------------------------------------------
# analyze — category dispatch
# ---------------------------------------------------------------------------


def test_arch_rejections_beat_everything() -> None:
    # Even with other signals present, arch rejections should win.
    insight = analyze(
        _signals(
            arch_rejections=3,
            agent_timed_out=True,
            tool_call_count=20,
            completion_tokens=50_000,
        )
    )
    assert insight.category == "missing_rule"
    assert insight.confidence == "high"


def test_scope_rejections_imply_missing_tool() -> None:
    insight = analyze(_signals(scope_rejections=5))
    assert insight.category == "missing_tool"


def test_single_shot_dump_is_missing_example() -> None:
    insight = analyze(
        _signals(
            passed=False,
            steps=4,
            completion_tokens=35_000,
            tool_call_count=2,
        )
    )
    assert insight.category == "missing_example"
    assert "dump" in insight.summary.lower()


def test_timeout_with_productive_steps_is_missing_context() -> None:
    insight = analyze(
        _signals(
            agent_timed_out=True,
            tool_call_count=15,
            steps=30,
        )
    )
    assert insight.category == "missing_context"
    assert "done-condition" in insight.proposed_promotion.lower() or \
           "checklist" in insight.proposed_promotion.lower()


def test_pass_with_verdict_concerns_is_missing_context() -> None:
    verdict = ReviewVerdict(
        status="request_changes",
        summary="tests thin",
        concerns=("no error handling",),
    )
    insight = analyze(
        _signals(passed=True, steps=15),
        verdict=verdict,
    )
    assert insight.category == "missing_context"
    assert insight.confidence == "low"


def test_no_productive_activity_is_model_capability_limit() -> None:
    insight = analyze(
        _signals(
            passed=False,
            tool_call_count=0,
            completion_tokens=0,
            steps=1,
        )
    )
    assert insight.category == "model_capability_limit"
    assert "api hang" in insight.summary.lower() or "giving up" in insight.summary.lower()


def test_fallback_is_missing_context_low_confidence() -> None:
    # Pass with no verdict concerns shouldn't trigger rule 5, and no
    # other rule fits — fallback applies.
    insight = analyze(_signals(passed=False, steps=20, tool_call_count=10))
    assert insight.category == "missing_context"
    assert insight.confidence == "low"


# ---------------------------------------------------------------------------
# Passing trials usually don't produce promotable insights
# ---------------------------------------------------------------------------


def test_pass_without_verdict_concerns_still_classifies() -> None:
    # A clean pass with no concerns hits the fallback, which is fine —
    # the caller decides whether to act.
    insight = analyze(_signals(passed=True, tool_call_count=10, steps=15))
    assert insight.category in CATEGORIES


def test_approved_verdict_on_pass_does_not_promote() -> None:
    verdict = ReviewVerdict(status="approve", summary="lgtm")
    insight = analyze(_signals(passed=True, steps=15), verdict=verdict)
    # Approve + pass shouldn't land in missing_context via rule 5 —
    # only non-approve verdicts trigger that path.
    assert insight.confidence == "low"


# ---------------------------------------------------------------------------
# Confidence levels align with evidence quality
# ---------------------------------------------------------------------------


def test_high_confidence_for_encoded_rule_rejection() -> None:
    insight = analyze(_signals(arch_rejections=2))
    assert insight.confidence == "high"


def test_low_confidence_for_fallback() -> None:
    insight = analyze(_signals())
    assert insight.confidence == "low"


# ---------------------------------------------------------------------------
# extract_signals — I/O robustness
# ---------------------------------------------------------------------------


def test_extract_signals_missing_result_json(tmp_path: Path) -> None:
    trial = tmp_path / "t"
    trial.mkdir()
    signals = extract_signals(trial)
    assert signals.passed is False
    assert signals.agent_timed_out is False


def test_extract_signals_corrupt_result_json(tmp_path: Path) -> None:
    trial = tmp_path / "t"
    trial.mkdir()
    (trial / "result.json").write_text("{{ not json")
    signals = extract_signals(trial)
    assert signals.passed is False


def test_extract_signals_happy_path(tmp_path: Path) -> None:
    trial = tmp_path / "my-task"
    (trial / "agent").mkdir(parents=True)
    (trial / "result.json").write_text(
        json.dumps(
            {
                "task_name": "my-task",
                "verifier_result": {"rewards": {"reward": 1.0}},
                "exception_info": None,
            }
        )
    )
    (trial / "agent" / "trajectory.json").write_text(
        json.dumps(
            {
                "steps": [{"step_id": i} for i in range(7)],
                "final_metrics": {
                    "total_prompt_tokens": 5000,
                    "total_completion_tokens": 300,
                },
            }
        )
    )
    signals = extract_signals(trial)
    assert signals.passed is True
    assert signals.steps == 7
    assert signals.prompt_tokens == 5000
    assert signals.completion_tokens == 300


def test_extract_signals_detects_agent_timeout(tmp_path: Path) -> None:
    trial = tmp_path / "t"
    (trial / "agent").mkdir(parents=True)
    (trial / "result.json").write_text(
        json.dumps(
            {
                "verifier_result": {"rewards": {"reward": 0.0}},
                "exception_info": {"type": "AgentTimeoutError"},
            }
        )
    )
    (trial / "agent" / "trajectory.json").write_text(
        json.dumps(
            {
                "steps": [],
                "final_metrics": {
                    "total_prompt_tokens": 0,
                    "total_completion_tokens": 0,
                },
            }
        )
    )
    signals = extract_signals(trial)
    assert signals.agent_timed_out is True
    assert signals.passed is False


def test_extract_signals_counts_trajectory_markers(tmp_path: Path) -> None:
    trial = tmp_path / "t"
    (trial / "agent").mkdir(parents=True)
    (trial / "result.json").write_text(
        json.dumps({"verifier_result": {"rewards": {"reward": 0.0}}})
    )
    traj_text = json.dumps(
        {
            "steps": [],
            "final_metrics": {
                "total_prompt_tokens": 100,
                "total_completion_tokens": 50,
            },
        }
    )
    # Append marker strings the extractor scans for
    traj_text += "\n\nScope violation: ...\nScope violation: ...\n"
    traj_text += "Arch-lint violation: ...\n"
    traj_text += "[PRECOMPLETION-CHECKLIST]\n[REVIEWER VERDICT: APPROVE]\n"
    (trial / "agent" / "trajectory.json").write_text(traj_text)

    signals = extract_signals(trial)
    assert signals.scope_rejections == 2
    assert signals.arch_rejections == 1
    assert signals.checklist_cycles == 1
    assert signals.has_reviewer_verdict is True


# ---------------------------------------------------------------------------
# analyze_trial convenience
# ---------------------------------------------------------------------------


def test_analyze_trial_combines_extract_and_analyze(tmp_path: Path) -> None:
    trial = tmp_path / "t"
    (trial / "agent").mkdir(parents=True)
    (trial / "result.json").write_text(
        json.dumps({"verifier_result": {"rewards": {"reward": 1.0}}})
    )
    (trial / "agent" / "trajectory.json").write_text(
        json.dumps(
            {
                "steps": [{} for _ in range(10)],
                "final_metrics": {
                    "total_prompt_tokens": 10_000,
                    "total_completion_tokens": 500,
                },
            }
        )
    )
    insight = analyze_trial(trial)
    assert insight.category in CATEGORIES
